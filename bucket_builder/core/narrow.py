# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow phase: exact collision / clearance between many object pairs at once.

Every pair in a batch is traversed together.  A "row" is one candidate
(node of A, node of B) pair tagged with the index of the object pair it belongs
to; a whole frontier of rows is expanded and culled with a few NumPy calls, so
the Python overhead is paid per tree level and not per object pair or per
triangle.

Coordinates: each mesh is stored posed (rotation and scale applied, translation
not).  Inside a pair everything is expressed in A's frame, i.e. B is shifted by
``rel = translation(B) - translation(A)``.  That is why dragging an object
costs nothing but a different ``rel``.

An object can also *borrow* a pose that was fitted for another rotation or
scale of its mesh (see ``world``).  Then every box read from that pose is
moved into the object's orientation first (``_move_boxes``), and its
triangles are posed from the mesh's own vertices (``_corners``).  The boxes
get looser by that, the triangles are exactly the ones a fitted pose holds.

Pipeline for one batch
    1. coarse   descend both trees keeping node pairs closer than the largest
                threshold, down to a resolution of a fraction of that threshold
    2. dive     follow the few most promising rows to the triangles to get a
                real upper bound on the distance of every pair
    3. fine     continue to the triangles, keeping only overlapping boxes
                (possible intersections) and rows that could beat the bound
    4. exact    triangle/triangle intersection segments and exact distances
    5. report   classify each pair and collect what the overlay draws

What the overlay gets for a colliding pair is the intersection curve plus the
surface patches of both parts that lie within a few millimetres of the other
part.  That region is found by the coarse traversal, so its size does not
depend on how finely the parts are tessellated (the triangles actually cut by
the curve would be an almost invisible band on a dense mesh).

Whether two parts intersect has to be right, always.  The batch above is
bounded per pair (``CAP_FINE``, ``CAP_OVERLAP``), and two finely tessellated
surfaces that nearly coincide over a large area (a part and its copy a
fraction of a millimetre apart, a shell nested closely in another) have
millions of triangle pairs with overlapping boxes.  When a pair runs into a
cap without an intersection having been found, its result is flagged
``unproven`` and the question is settled by ``scan``: an exhaustive search
that stops at the first hit and can be continued a slice at a time.
"""

import time

import numpy as np

from . import tritri

OK, CLEAR, COLLIDE = 0, 1, 2

BLOCK_ELEMS = 1 << 17        # box tests handled by one vectorised pass
CAP_REGION = 12000           # coarse rows kept per pair
CAP_FINE = 200000            # rows per pair before a pair is treated as "dense"
CAP_OVERLAP = 120000         # box-overlapping triangle pairs tested per pair
CAP_EXACT = 12000            # exact distance evaluations per pair, first pass
CAP_EXACT_MAX = 40000        # ... when the verdict depends on it
CAP_SEARCH = 40000           # distance-search rows per pair, first pass (CAP_FINE in the second)
CAP_SEARCH_QUICK = 2500      # distance-search rows per pair while something is moving
CAP_EXACT_QUICK = 640        # exact distance evaluations per pair while moving
BEAM = 8
BEAM_DENSE = 48
BEAM_SKETCH = 24             # rows followed per pair when only looking for one intersection
EXACT_CHUNK = 2048
REGION_RES = 0.3             # coarse resolution as a fraction of the threshold
VIZ_MIN, VIZ_MAX = 4.0, 10.0 # width of the hatched collision region, in units of viz_pad
TRI_CAP = 30000              # triangles handed to the overlay per side
TRI_CAP_LIVE = 6000          # ... while something is moving (rebuilt on every step)
SEG_CAP = 60000              # intersection segments handed to the overlay
SEG_CAP_LIVE = 8000
BOX_CAP = 1200               # proxy boxes handed to the overlay per side
BOX_CAP_LIVE = 300
SCAN_BLOCK = 2048            # node pairs the exhaustive search expands at a time
SCAN_SEGS = 4000             # intersection segments it keeps once it has found some
BIG = np.float32(1e30)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def corners(w, pose_rows, leaf):
    """Posed triangle corners, float32 (K, 3 corners, 3 coords)."""
    t = np.take(w.TIDX.data, np.take(w.PTB, pose_rows) + leaf, axis=0)
    v = t + np.take(w.PVB, pose_rows)[:, None]
    return np.take(w.VERT.data, v.ravel(), axis=0).reshape(-1, 3, 3)


def _pose_local(loc, L9):
    """Mesh-space points ``loc`` (K, 3) under a rotation/scale given as nine
    coefficients (scalars, or one column per point).  This is the expression
    of ``World._posed``, so the result is bit for bit what a fitted pose
    holds."""
    x = np.ascontiguousarray(loc[:, 0])
    y = np.ascontiguousarray(loc[:, 1])
    z = np.ascontiguousarray(loc[:, 2])
    out = np.empty((loc.shape[0], 3), dtype=np.float32)
    for c in range(3):
        t = x * L9[3 * c]
        t += y * L9[3 * c + 1]
        t += z * L9[3 * c + 2]
        out[:, c] = t
    return out


def _corners(w, pose, pid, leaf, side=None):
    """Posed corners, float32 (K, 3, 3), of triangle ``leaf`` on one side of
    the pairs ``pid``.  ``side`` is None, or (flags, L9, lbase) per pair for
    sides that borrow their pose."""
    pr = np.take(pose, pid)
    t = np.take(w.TIDX.data, np.take(w.PTB, pr) + leaf, axis=0)
    if side is not None:
        flag, L9, lb = side
        m = np.take(flag, pid)
        if m.any():
            if m.all():
                loc = np.take(w.LVERT.data, (t + np.take(lb, pid)[:, None]).ravel(), axis=0)
                return _pose_local(loc, np.repeat(np.take(L9, pid, axis=1), 3, axis=1)
                                   ).reshape(-1, 3, 3)
            out = np.empty((pid.shape[0], 3, 3), dtype=np.float32)
            e = np.flatnonzero(~m)
            b = np.flatnonzero(m)
            v = t[e] + np.take(w.PVB, pr[e])[:, None]
            out[e] = np.take(w.VERT.data, v.ravel(), axis=0).reshape(-1, 3, 3)
            pb = pid[b]
            loc = np.take(w.LVERT.data, (t[b] + np.take(lb, pb)[:, None]).ravel(), axis=0)
            out[b] = _pose_local(loc, np.repeat(np.take(L9, pb, axis=1), 3, axis=1)
                                 ).reshape(-1, 3, 3)
            return out
    v = t + np.take(w.PVB, pr)[:, None]
    return np.take(w.VERT.data, v.ravel(), axis=0).reshape(-1, 3, 3)


class _Borrow:
    """What a batch needs to know about the sides that borrow a pose: how to
    move its boxes into the object's orientation and how to pose the object's
    own triangles.  Index 0 is side A, 1 is side B."""

    def __init__(self, w, sa, sb):
        self.w = w
        self.slots = (sa, sb)
        self.flag = (w.O_VIRT[sa], w.O_VIRT[sb])
        self.any = (bool(self.flag[0].any()), bool(self.flag[1].any()))
        self.R = [None, None]
        self.A = [None, None]
        self.pad = [None, None]
        self.side = [None, None]
        for k in range(2):
            if not self.any[k]:
                continue
            s = self.slots[k]
            R9 = np.ascontiguousarray(w.O_REL[s].reshape(-1, 9).T)
            self.R[k] = R9
            self.A[k] = np.abs(R9)
            self.pad[k] = 2.0 * w.O_PAD[s]      # (boxes are handled doubled, see _move_boxes)
            self.side[k] = (self.flag[k], np.ascontiguousarray(w.O_L[s].reshape(-1, 9).T), w.O_LB[s])

    def one(self, p, k):
        """(L, lbase, rel, pad) of one side of pair ``p``, or None if that
        side has a pose of its own."""
        slot = int(self.slots[k][p])
        w = self.w
        if not w.O_VIRT[slot]:
            return None
        return w.O_L[slot], int(w.O_LB[slot]), w.O_REL[slot], float(w.O_PAD[slot])


def _move_boxes(X, ps, flag, R9, A9, pad2):
    """Boxes ``X`` (6, children, rows) with those of borrowing sides moved into
    their object's orientation: the box around the transformed box (centre
    through the matrix, half extent through its absolute value) plus a little
    slack for rounding.  Empty boxes come out as NaN, which fails every test.
    """
    m = np.take(flag, ps)
    if not m.any():
        return X
    whole = bool(m.all())
    sel = ps if whole else ps[m]
    S = X if whole else X[:, :, m]
    r = np.take(R9, sel, axis=1)
    a = np.take(A9, sel, axis=1)
    pd = np.take(pad2, sel)
    cx = S[0] + S[3]                     # twice the centre
    cy = S[1] + S[4]
    cz = S[2] + S[5]
    ex = S[3] - S[0]                     # twice the half extent
    ey = S[4] - S[1]
    ez = S[5] - S[2]
    out = np.empty_like(S)
    for i in range(3):
        c = r[3 * i] * cx
        c += r[3 * i + 1] * cy
        c += r[3 * i + 2] * cz
        e = a[3 * i] * ex
        e += a[3 * i + 1] * ey
        e += a[3 * i + 2] * ez
        e += pd
        np.subtract(c, e, out=out[i])
        np.add(c, e, out=out[3 + i])
    out *= 0.5
    if whole:
        return out
    X[:, :, m] = out
    return X


def _move_rows(b, rel, pad):
    """(lo, hi) of boxes ``b`` (n, 6) moved by one matrix ``rel`` (3, 3)."""
    c = (b[:, :3] + b[:, 3:]) @ (0.5 * rel.T)
    e = (b[:, 3:] - b[:, :3]) @ (0.5 * np.abs(rel).T) + pad
    return c - e, c + e


def _pair_tris(w, pose_a, pose_b, rel64t, pid, ia, ib, xf=None):
    """Float64 corners of both triangles of each row, in A's frame, laid out
    (3 coords, 3 corners, K) as ``tritri`` expects."""
    if xf is None:
        P = tritri.to_soa(corners(w, np.take(pose_a, pid), ia))
        Q = tritri.to_soa(corners(w, np.take(pose_b, pid), ib))
    else:
        P = tritri.to_soa(_corners(w, pose_a, pid, ia, xf.side[0]))
        Q = tritri.to_soa(_corners(w, pose_b, pid, ib, xf.side[1]))
    Q += np.take(rel64t, pid, axis=1)[:, None, :]
    return P, Q


def _test_block(w, p, a, b, sa, sb, blk_a, blk_b, relt, thr2_p, eps2, want_cd, out, xf=None):
    """Test every child pair of the given rows (all with the same step sizes).

    The children of a node are neighbours in memory, so one gather per parent
    fetches all of them; the box arithmetic then runs on contiguous arrays with
    the row index last.  Only surviving children get index arithmetic.
    """
    data = w.BOX.data
    va = data.reshape(data.shape[0] >> sa, 1 << sa, 6)
    vb = data.reshape(data.shape[0] >> sb, 1 << sb, 6)
    chunk = max(256, BLOCK_ELEMS >> (sa + sb))
    for s in range(0, p.shape[0], chunk):
        ps = p[s:s + chunk]
        as_ = a[s:s + chunk]
        bs = b[s:s + chunk]
        A = np.ascontiguousarray(np.take(va, np.take(blk_a, ps) + as_, axis=0).transpose(2, 1, 0))
        B = np.ascontiguousarray(np.take(vb, np.take(blk_b, ps) + bs, axis=0).transpose(2, 1, 0))
        if xf is not None:
            if xf.any[0]:
                A = _move_boxes(A, ps, xf.flag[0], xf.R[0], xf.A[0], xf.pad[0])
            if xf.any[1]:
                B = _move_boxes(B, ps, xf.flag[1], xf.R[1], xf.A[1], xf.pad[1])
        r = np.take(relt, ps, axis=1)
        acc = None
        for c in range(3):
            d1 = A[c][:, None, :] - B[3 + c][None, :, :]
            d1 -= r[c]
            d2 = B[c][None, :, :] - A[3 + c][:, None, :]
            d2 += r[c]
            np.maximum(d1, d2, out=d1)
            np.maximum(d1, 0.0, out=d1)
            d1 *= d1
            if acc is None:
                acc = d1
            else:
                acc += d1
        keep = acc <= np.maximum(np.take(thr2_p, ps), eps2)
        # nonzero in parent-major order, so rows of one pair stay together
        kk, ja, jb = np.nonzero(np.ascontiguousarray(keep.transpose(2, 0, 1)))
        if kk.shape[0] == 0:
            continue
        out[0].append(np.take(ps, kk))
        out[1].append((np.take(as_, kk) << sa) + ja)
        out[2].append((np.take(bs, kk) << sb) + jb)
        out[3].append(acc[ja, jb, kk])
        if want_cd:
            cd = None
            for c in range(3):
                d = (B[c][jb, kk] + B[3 + c][jb, kk]) - (A[c][ja, kk] + A[3 + c][ja, kk])
                d *= 0.5
                d += r[c][kk]
                d *= d
                cd = d if cd is None else cd + d
            out[4].append(cd)


def _step(w, pid, ia, ib, sa_p, sb_p, lvl_a, lvl_b, relt, thr2_p, eps2, want_cd=False, xf=None):
    """Descend rows by per-pair steps and keep the children that pass.

    ``lvl_a`` / ``lvl_b`` give, per pair, the arena row where the level the
    children live on starts.  A child passes when its squared box distance is
    within ``thr2_p`` of its pair, or when the boxes overlap.
    """
    n = pid.shape[0]
    out = ([], [], [], [], [])
    if n:
        blk_a = lvl_a >> sa_p
        blk_b = lvl_b >> sb_p
        code_p = sa_p * 4 + sb_p
        rc = np.take(code_p, pid)
        c0 = int(rc[0])
        if (rc == c0).all():
            _test_block(w, pid, ia, ib, c0 >> 2, c0 & 3, blk_a, blk_b, relt, thr2_p, eps2,
                        want_cd, out, xf)
        else:
            for code in np.unique(rc):
                sel = np.flatnonzero(rc == code)
                code = int(code)
                _test_block(w, np.take(pid, sel), np.take(ia, sel), np.take(ib, sel),
                            code >> 2, code & 3, blk_a, blk_b, relt, thr2_p, eps2, want_cd, out, xf)
    k = 5 if want_cd else 4
    if not out[0]:
        e = np.zeros(0, dtype=np.int64)
        f = np.zeros(0, dtype=np.float32)
        return (e, e, e, f, f)[:k]
    if len(out[0]) == 1:
        return tuple(o[0] for o in out[:k])
    return tuple(np.concatenate(o) for o in out[:k])


def _base_step(rows):
    if rows <= 64:
        return 3
    if rows <= 4096:
        return 2
    return 1


def _steps(w, pose_a, pose_b, h_a, h_b, live, rows, floor):
    """Choose how many levels each pair descends on each side this pass.

    The side with clearly larger boxes goes first, so the two frontiers keep
    comparable box sizes even between a coarse and a finely tessellated mesh.
    """
    sz_a = w.SZ[pose_a, h_a]
    sz_b = w.SZ[pose_b, h_b]
    want_a = (h_a > 0) & (sz_a > floor)
    want_b = (h_b > 0) & (sz_b > floor)
    both = want_a & want_b
    only_a = both & (sz_a > 2.0 * sz_b)
    only_b = both & (sz_b > 2.0 * sz_a)
    base = _base_step(rows)
    sa = np.where(want_a & ~only_b & live, np.minimum(base, h_a), 0)
    sb = np.where(want_b & ~only_a & live, np.minimum(base, h_b), 0)
    return sa, sb, want_a | want_b


def _top_k(pid, keys, k):
    """Indices of the ``k`` best rows of every pair.  ``keys`` are sort keys,
    least significant first (as for ``np.lexsort``), without the pair id."""
    order = np.lexsort(tuple(keys) + (pid,))
    ps = np.take(pid, order)
    new = np.empty(ps.shape[0], dtype=bool)
    new[0] = True
    np.not_equal(ps[1:], ps[:-1], out=new[1:])
    start = np.flatnonzero(new)
    rank = np.arange(ps.shape[0]) - np.repeat(start, np.diff(np.append(start, ps.shape[0])))
    return order[rank < k]


def _runs(pid):
    """Start index and length of each run of equal pair ids."""
    n = pid.shape[0]
    start = np.concatenate(([0], np.flatnonzero(pid[1:] != pid[:-1]) + 1))
    return start, np.diff(np.append(start, n))


def _beam(pid, lb2, cd2, k):
    """Roughly the ``k`` most promising rows of every pair (smallest box
    distance, ties broken by centre distance) without sorting the whole
    frontier: only rows close to their pair's minimum are sorted."""
    n = pid.shape[0]
    if n <= 4 * k:
        cand = np.arange(n)
    else:
        start, length = _runs(pid)
        gmin = np.minimum.reduceat(lb2, start)
        cand = np.flatnonzero(lb2 <= np.repeat(gmin * 1.1 + 1e-30, length))
        if cand.shape[0] > 16384:
            cand = cand[::cand.shape[0] // 16384 + 1]
    keys = (np.take(lb2, cand),) if cd2 is None else (np.take(cd2, cand), np.take(lb2, cand))
    return np.take(cand, _top_k(np.take(pid, cand), keys, k))


def _bounds(pid, npid):
    """(order, bounds) so that ``order[bounds[p]:bounds[p + 1]]`` are the rows
    of pair ``p``.  Rows usually arrive grouped already, so no sort is needed."""
    n = pid.shape[0]
    if n == 0 or (pid[1:] >= pid[:-1]).all():
        order = None
        sorted_pid = pid
    else:
        order = np.argsort(pid, kind='stable')
        sorted_pid = np.take(pid, order)
    return order, np.searchsorted(sorted_pid, np.arange(npid + 1))


def _rows_of(order, bounds, p):
    if order is None:
        return slice(int(bounds[p]), int(bounds[p + 1]))
    return order[bounds[p]:bounds[p + 1]]


def _cat(parts, k=4):
    if not parts:
        e = np.zeros(0, dtype=np.int64)
        f = np.zeros(0, dtype=np.float32)
        return (e, e, e, f, f)[:k]
    if len(parts) == 1:
        return parts[0]
    return tuple(np.concatenate(c) for c in zip(*parts))


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def _coarse(w, pose_a, pose_b, relt, dmax2, floor, eps2, pids, xf=None):
    """Rows (node pairs) closer than the threshold for the pairs ``pids``,
    refined until the boxes are about ``floor`` in size.  ``dmax2`` and
    ``floor`` may be scalars or one value per pair."""
    npid = pose_a.shape[0]
    h_a = w.PH[pose_a].copy()
    h_b = w.PH[pose_b].copy()
    thr = np.empty(npid, dtype=np.float32)
    thr[:] = dmax2
    zero = np.zeros(npid, dtype=np.int64)
    pid = pids
    start = np.zeros(pid.shape[0], dtype=np.int64)
    pid, ia, ib, lb2 = _step(w, pid, start, start, zero, zero,
                             w.LVL[pose_a, h_a], w.LVL[pose_b, h_b], relt, thr, eps2, xf=xf)
    live = np.zeros(npid, dtype=bool)
    live[pids] = True
    region = []
    while pid.shape[0]:
        sa, sb, want = _steps(w, pose_a, pose_b, h_a, h_b, live, pid.shape[0], floor)
        cnt = np.bincount(pid, minlength=npid)
        stop = live & (~want | (cnt > CAP_REGION))
        if stop.any():
            m = np.take(stop, pid)
            region.append((pid[m], ia[m], ib[m], lb2[m]))
            live &= ~stop
            m = ~m
            pid, ia, ib, lb2 = pid[m], ia[m], ib[m], lb2[m]
            if pid.shape[0] == 0:
                break
            sa[stop] = 0
            sb[stop] = 0
        h_a = h_a - sa
        h_b = h_b - sb
        pid, ia, ib, lb2 = _step(w, pid, ia, ib, sa, sb,
                                 w.LVL[pose_a, h_a], w.LVL[pose_b, h_b], relt, thr, eps2, xf=xf)
    return _cat(region), h_a, h_b


def _dive(w, rows, h_a, h_b, pose_a, pose_b, relt, beam, xf=None):
    """Greedy beam search from the given rows down to triangle pairs."""
    pid, ia, ib, lb2 = rows
    npid = pose_a.shape[0]
    sel = _beam(pid, lb2, None, beam)
    pid, ia, ib = pid[sel], ia[sel], ib[sel]
    h_a = h_a.copy()
    h_b = h_b.copy()
    big = np.full(npid, BIG, dtype=np.float32)
    while True:
        present = np.zeros(npid, dtype=bool)
        present[pid] = True
        if not (present & ((h_a > 0) | (h_b > 0))).any():
            break
        sa = np.where(present, np.minimum(2, h_a), 0)
        sb = np.where(present, np.minimum(2, h_b), 0)
        h_a = h_a - sa
        h_b = h_b - sb
        pid, ia, ib, lb2, cd2 = _step(w, pid, ia, ib, sa, sb,
                                      w.LVL[pose_a, h_a], w.LVL[pose_b, h_b],
                                      relt, big, 0.0, want_cd=True, xf=xf)
        if pid.shape[0] == 0:
            break
        sel = _beam(pid, lb2, cd2, beam)
        pid, ia, ib = pid[sel], ia[sel], ib[sel]
    return pid, ia, ib


def _fine(w, rows, h_a, h_b, pose_a, pose_b, relt, thr2, eps2, cap=None, truncate=False,
          xf=None):
    """Descend the given rows to triangle pairs.

    A row survives while its boxes overlap (within ``eps2``) or its box
    distance is within the pair's ``thr2``.  When a pair exceeds ``cap`` rows:

    * ``truncate=False`` (used when completeness matters, i.e. for finding
      intersections): the pair stops here and is returned as "dense";
    * ``truncate=True`` (used for the distance search): only its ``cap`` most
      promising rows continue, and the pair is flagged as cut short.

    Returns (leaf rows, dense rows, flagged pairs, heights a, heights b,
    left2): ``left2`` is, per pair, the smallest squared box distance among
    the rows that truncation left out (inf if none), i.e. a proven lower
    bound for everything that was not followed.
    """
    if cap is None:
        cap = CAP_FINE
    npid = pose_a.shape[0]
    pid, ia, ib, lb2 = rows
    keep = lb2 <= np.maximum(np.take(thr2, pid), eps2)
    pid, ia, ib, lb2 = pid[keep], ia[keep], ib[keep], lb2[keep]
    h_a = h_a.copy()
    h_b = h_b.copy()
    live = np.ones(npid, dtype=bool)
    flagged = np.zeros(npid, dtype=bool)
    left2 = np.full(npid, np.inf, dtype=np.float32)
    leaves = []
    dense_rows = []
    prev = None                 # rows per pair one pass ago, and the levels descended since
    lev = None
    while pid.shape[0]:
        at_leaf = live & (h_a == 0) & (h_b == 0)
        cnt = np.bincount(pid, minlength=npid)
        too_many = live & ~at_leaf & (cnt > cap)
        if not truncate and prev is not None:
            # A pair that is going to exceed the cap by far is stopped as soon
            # as that can be told: its rows are many already and, at the rate
            # they have just multiplied, the levels still to go will take them
            # well past it.  (Two surfaces that nearly coincide multiply
            # fourfold per level; finding that out at the cap itself costs
            # most of what such a pair costs.)
            big = live & ~at_leaf & ~too_many & (cnt > cap // 8) & (prev > 500) & (lev > 0)
            if big.any():
                b = np.flatnonzero(big)
                rate = np.log2(cnt[b] / prev[b]) / lev[b]
                ahead = cnt[b] * np.exp2(np.clip(rate, 0.0, 2.0) * (h_a[b] + h_b[b]))
                too_many[b[ahead > 4.0 * cap]] = True
        prev = cnt
        if truncate and too_many.any():
            m_over = np.take(too_many, pid)
            over = np.flatnonzero(m_over)
            best = over[_top_k(np.take(pid, over), (np.take(lb2, over),), cap)]
            m = ~m_over
            m[best] = True
            out = ~m
            np.minimum.at(left2, pid[out], lb2[out])
            pid, ia, ib, lb2 = pid[m], ia[m], ib[m], lb2[m]
            flagged |= too_many
            too_many = np.zeros(npid, dtype=bool)
        stop = at_leaf | too_many
        if stop.any():
            m = np.take(at_leaf, pid)
            if m.any():
                leaves.append((pid[m], ia[m], ib[m], lb2[m]))
            if too_many.any():
                md = np.take(too_many, pid)
                dense_rows.append((pid[md], ia[md], ib[md], lb2[md]))
                flagged |= too_many
                m |= md
            live &= ~stop
            m = ~m
            pid, ia, ib, lb2 = pid[m], ia[m], ib[m], lb2[m]
            if pid.shape[0] == 0:
                break
        sa, sb, _ = _steps(w, pose_a, pose_b, h_a, h_b, live, pid.shape[0], -1.0)
        lev = sa + sb
        h_a = h_a - sa
        h_b = h_b - sb
        pid, ia, ib, lb2 = _step(w, pid, ia, ib, sa, sb,
                                 w.LVL[pose_a, h_a], w.LVL[pose_b, h_b], relt, thr2, eps2, xf=xf)
    return _cat(leaves), _cat(dense_rows), flagged, h_a, h_b, left2


class _Best:
    """Per-pair running minimum distance with the points that realise it."""

    def __init__(self, npid):
        self.d2 = np.full(npid, np.inf)
        self.pa = np.zeros((npid, 3))
        self.pb = np.zeros((npid, 3))

    def update(self, pid, d2, cp, cq):
        """``cp`` / ``cq`` are (3, K)."""
        if pid.shape[0] == 0:
            return
        order = np.lexsort((d2, pid))
        ps = np.take(pid, order)
        first = np.empty(ps.shape[0], dtype=bool)
        first[0] = True
        np.not_equal(ps[1:], ps[:-1], out=first[1:])
        sel = order[first]
        p = np.take(pid, sel)
        better = np.take(d2, sel) < self.d2[p]
        sel = sel[better]
        p = p[better]
        self.d2[p] = d2[sel]
        self.pa[p] = cp[:, sel].T
        self.pb[p] = cq[:, sel].T


def _eval_rows(w, pose_a, pose_b, rel64t, pid, ia, ib, eps_exact, best, xf=None):
    """Exact test of a few triangle-pair rows; updates ``best``."""
    P, Q = _pair_tris(w, pose_a, pose_b, rel64t, pid, ia, ib, xf)
    hit, seg = tritri.tri_tri_intersect(P, Q, eps_exact)
    d2, cp, cq = tritri.tri_tri_distance(P, Q)
    if hit.any():
        mid = seg.mean(axis=1).T
        d2[hit] = 0.0
        cp[:, hit] = mid
        cq[:, hit] = mid
    best.update(pid, d2, cp, cq)


def _node_tris(w, pose, h, nodes, shift=None, cap=None, virt=None):
    """Triangles under the given nodes of one pose as float32 (n, 3, 3).
    ``virt`` (see ``_Borrow.one``) is given when the object borrows the pose."""
    nt = int(w.PNT[pose])
    start = nodes << h
    length = np.minimum(start + (1 << h), nt) - start
    ok = length > 0
    start = start[ok]
    length = length[ok]
    tot = int(length.sum())
    if tot == 0:
        return np.zeros((0, 3, 3), dtype=np.float32)
    first = np.cumsum(length) - length
    leaf = np.repeat(start - first, length) + np.arange(tot)
    if cap is not None and tot > cap:
        leaf = leaf[::tot // cap + 1]
    t = np.take(w.TIDX.data, int(w.PTB[pose]) + leaf, axis=0)
    if virt is None:
        tri = np.take(w.VERT.data, t.ravel() + int(w.PVB[pose]), axis=0).reshape(-1, 3, 3)
    else:
        loc = np.take(w.LVERT.data, t.ravel() + virt[1], axis=0)
        tri = _pose_local(loc, virt[0].reshape(9)).reshape(-1, 3, 3)
    if shift is not None:
        tri += shift
    return tri


def box_tris(lo, hi):
    """Twelve triangles for each axis-aligned box: (n, 3) x2 -> (12 n, 3, 3)."""
    n = lo.shape[0]
    c = np.empty((n, 8, 3), dtype=np.float32)
    for i in range(8):
        c[:, i, 0] = hi[:, 0] if i & 1 else lo[:, 0]
        c[:, i, 1] = hi[:, 1] if i & 2 else lo[:, 1]
        c[:, i, 2] = hi[:, 2] if i & 4 else lo[:, 2]
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return c[:, f, :].reshape(n * 12, 3, 3)


def _unique(ids, size):
    """Sorted unique non-negative ids below ``size`` (mark and collect; faster
    than ``np.unique`` for the dense id sets that occur here)."""
    if ids.shape[0] * 16 < size:
        return np.unique(ids)
    mark = np.zeros(size, dtype=bool)
    mark[ids] = True
    return np.flatnonzero(mark)


def box_edges(lo, hi):
    """The twelve edges of a box as float32 segments (12, 2, 3)."""
    c = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]],
                  [lo[0], hi[1], lo[2]], [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
                  [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]], dtype=np.float32)
    e = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7]
    return c[e].reshape(12, 2, 3)


def _clip_box(tris, shift, lo, hi, slack):
    """The box (lo, hi) if the patch reaches outside it by more than ``slack``,
    else None.

    A patch is made of whole triangles.  On a finely tessellated part they all
    lie next to the other part anyway; a long triangle of a coarse CAD mesh
    can run far away from it, and the overlay then trims the patch to the box.
    """
    if tris is None or len(tris) == 0:
        return None
    pts = tris.reshape(-1, 3)
    plo = pts.min(axis=0)
    phi = pts.max(axis=0)
    if shift is not None:
        plo = plo + shift
        phi = phi + shift
    if (plo < lo - slack).any() or (phi > hi + slack).any():
        return lo, hi
    return None


def _region_side(w, pose, h, nodes, shift, tri_cap=TRI_CAP, box_cap=BOX_CAP, virt=None):
    """Geometry the overlay draws for a set of near nodes, plus their bounds.

    The triangles are returned in the object's own frame (no translation); the
    bounds have ``shift`` added, i.e. they are in the frame of the pair.
    """
    nodes = _unique(nodes, (int(w.PNT[pose]) >> h) + 2)
    boxes = np.take(w.BOX.data, int(w.LVL[pose, h]) + nodes, axis=0)
    if virt is None:
        blo, bhi = boxes[:, :3], boxes[:, 3:]
    else:
        blo, bhi = _move_rows(boxes, virt[2], virt[3])
    lo = blo.min(axis=0).astype(np.float64)
    hi = bhi.max(axis=0).astype(np.float64)
    ntri = int(min(nodes.shape[0] << h, w.PNT[pose]))
    if ntri <= tri_cap:
        tris = _node_tris(w, pose, h, nodes, None, None, virt)
    else:
        # Too many triangles to hand over: draw boxes of coarser ancestors
        # instead (flat patches still look like the surface).
        up = 0
        anc = nodes
        H = int(w.PH[pose])
        while anc.shape[0] > box_cap and h + up < H:
            up += 1
            anc = np.unique(anc >> 1)
        b = np.take(w.BOX.data, int(w.LVL[pose, h + up]) + anc, axis=0)
        tris = box_tris(b[:, :3], b[:, 3:])
        if virt is not None:
            # the boxes of a borrowed pose turn with the part
            tris = (tris.reshape(-1, 3) @ virt[2].T).reshape(-1, 3, 3)
    if shift is not None:
        lo = lo + shift
        hi = hi + shift
    return tris, lo, hi


# ---------------------------------------------------------------------------
# enclosure (one part completely inside another, surfaces not touching)
# ---------------------------------------------------------------------------

def _edge_side(xu, xv, yu, yv, pu, pv):
    """Which side of edge X->Y the point is on, as +1 / -1, evaluated on a
    canonical orientation of the edge so that the two triangles sharing it get
    exactly opposite answers (a point exactly on the edge counts for one only)."""
    swap = (xu > yu) | ((xu == yu) & (xv > yv))
    au = np.where(swap, yu, xu)
    av = np.where(swap, yv, xv)
    bu = np.where(swap, xu, yu)
    bv = np.where(swap, xv, yv)
    e = (bu - au) * (pv - av) - (bv - av) * (pu - au)
    side = np.where(e >= 0.0, 1.0, -1.0)
    return np.where(swap, -side, side), np.where(swap, -e, e)


def _ray_parity(w, pose, pts, axis):
    """Crossings (mod 2) of a ray from each point along +axis with a mesh.

    ``pose`` (n,) are pose indices, ``pts`` (n, 3) float64 points in that
    pose's frame.  Returns a bool array: True where the count is odd.
    """
    n = pose.shape[0]
    u = (axis + 1) % 3
    v = (axis + 2) % 3
    h = w.PH[pose].copy()
    rid = np.arange(n)
    idx = np.zeros(n, dtype=np.int64)
    box = w.BOX.data
    leaves = []
    while rid.shape[0]:
        b = np.take(box, np.take(w.LVL[pose, h], rid) + idx, axis=0)
        pr = np.take(pts, rid, axis=0)
        ok = ((b[:, u] <= pr[:, u]) & (pr[:, u] <= b[:, 3 + u]) &
              (b[:, v] <= pr[:, v]) & (pr[:, v] <= b[:, 3 + v]) &
              (b[:, 3 + axis] >= pr[:, axis]))
        rid = rid[ok]
        idx = idx[ok]
        if rid.shape[0] == 0:
            break
        at_leaf = np.take(h, rid) == 0
        if at_leaf.any():
            leaves.append((rid[at_leaf], idx[at_leaf]))
            rid = rid[~at_leaf]
            idx = idx[~at_leaf]
            if rid.shape[0] == 0:
                break
        present = np.zeros(n, dtype=bool)
        present[rid] = True
        s = np.where(present, np.minimum(_base_step(rid.shape[0]), h), 0)
        h = h - s
        sr = np.take(s, rid)
        cnt = np.left_shift(1, sr)
        src = np.repeat(np.arange(rid.shape[0]), cnt)
        first = np.cumsum(cnt) - cnt
        j = np.arange(src.shape[0]) - np.take(first, src)
        idx = (np.take(idx, src) << np.take(sr, src)) + j
        rid = np.take(rid, src)
    count = np.zeros(n, dtype=np.int64)
    if leaves:
        l_rid = np.concatenate([x[0] for x in leaves])
        l_idx = np.concatenate([x[1] for x in leaves])
        T = corners(w, np.take(pose, l_rid), l_idx).astype(np.float64)   # (K, 3, 3)
        P = np.take(pts, l_rid, axis=0)
        pu = P[:, u]
        pv = P[:, v]
        s0, e0 = _edge_side(T[:, 0, u], T[:, 0, v], T[:, 1, u], T[:, 1, v], pu, pv)
        s1, e1 = _edge_side(T[:, 1, u], T[:, 1, v], T[:, 2, u], T[:, 2, v], pu, pv)
        s2, e2 = _edge_side(T[:, 2, u], T[:, 2, v], T[:, 0, u], T[:, 0, v], pu, pv)
        tot = e0 + e1 + e2                     # twice the projected area (signed)
        inside = (s0 == s1) & (s1 == s2) & (tot != 0.0)
        with np.errstate(divide='ignore', invalid='ignore'):
            # depth of the surface along the ray at the point (barycentric)
            depth = (e1 * T[:, 0, axis] + e2 * T[:, 1, axis] + e0 * T[:, 2, axis]) / tot
        hit = inside & (depth > P[:, axis])
        if hit.any():
            count += np.bincount(l_rid[hit], minlength=n)
    return (count & 1).astype(bool)


def points_inside(w, pose, pts):
    """True where a point lies inside the closed surface of a pose (majority
    of three axis-aligned rays, so a single open edge does not flip it)."""
    votes = (_ray_parity(w, pose, pts, 0).astype(np.int8) +
             _ray_parity(w, pose, pts, 1) + _ray_parity(w, pose, pts, 2))
    return votes >= 2


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def solve(w, keys, exact=True, detail=None):
    """Evaluate the given object pairs and store their results in ``w.pairs``.

    Whether two parts intersect is decided exactly, with one way out: a pair
    with so many candidate triangle pairs that the search ran into its caps
    without finding an intersection comes back flagged ``unproven`` and must
    be settled with ``scan``.  ``exact=False`` is the mode for live edits:
    some things may be left for later, and such results are flagged
    ``refine`` so the caller redoes them with ``exact=True`` once the objects
    stop moving.

    * The clearance distance of pairs that do not intersect is the best value
      found by a capped search (an upper bound, usually within a few percent).
    * ``detail`` (a boolean per pair, or None for all) says which pairs get the
      full treatment.  The others are only *sketched*, which is far cheaper:
      one intersecting triangle pair proves a collision, without the complete
      intersection curve or the hatched region, and the clearance is judged
      from a quick guided search alone.  Sketched results are flagged
      ``lite``.  This is what keeps a part that lands on dozens of others
      responsive.
    """
    npid = len(keys)
    if npid == 0:
        return
    sa = np.fromiter((k[0] for k in keys), dtype=np.int64, count=npid)
    sb = np.fromiter((k[1] for k in keys), dtype=np.int64, count=npid)
    pose_a = w.O_POSE[sa]
    pose_b = w.O_POSE[sb]
    rel64 = w.O_T[sb] - w.O_T[sa]
    rel64t = np.ascontiguousarray(rel64.T)
    rel = rel64.astype(np.float32)
    relt = np.ascontiguousarray(rel.T)
    # sides that borrow a pose fitted for another rotation/scale
    xf = _Borrow(w, sa, sb) if (w.O_VIRT[sa].any() or w.O_VIRT[sb].any()) else None

    eps = w.eps_len
    eps2 = np.float32(eps * eps)
    coll = max(w.coll_thr, w.eps_touch)
    clear = w.clear_thr if w.clear_thr > coll else 0.0
    dmax = max(coll, clear)
    dlim2 = (dmax + eps) ** 2
    floor = REGION_RES * dmax if dmax > 4.0 * w.eps_touch else 0.0

    best = _Best(npid)
    collided = np.zeros(npid, dtype=bool)
    approx = np.zeros(npid, dtype=bool)     # intersection search hit a cap
    capped = np.zeros(npid, dtype=bool)     # ... so that not every candidate was tested
    # pairs an exhaustive search has already shown not to intersect or touch
    cleared = np.fromiter((getattr(w.pairs.get(k), 'no_cut', False) for k in keys), dtype=bool,
                          count=npid)
    short = np.zeros(npid, dtype=bool)      # distance search was cut short
    near = np.zeros(npid, dtype=bool)
    hit_rows = None
    zero = np.zeros(npid, dtype=np.int64)
    e_i = np.zeros(0, dtype=np.int64)
    region = (e_i, e_i, e_i, np.zeros(0, dtype=np.float32))
    h_a1 = zero
    h_b1 = zero
    region_c = None
    h_a2 = h_b2 = zero
    pad = w.viz_pad
    viz = min(max(clear, VIZ_MIN * pad), VIZ_MAX * pad)

    if exact or detail is None:
        lite = np.zeros(npid, dtype=bool)
    else:
        lite = ~np.asarray(detail, dtype=bool)
    full = ~lite
    sketch = np.zeros(npid, dtype=bool)     # sketched and found to intersect
    sk_rows = None
    live = not exact
    tri_cap = TRI_CAP_LIVE if live else w.tri_cap
    box_cap = BOX_CAP_LIVE if live else BOX_CAP
    seg_cap = SEG_CAP_LIVE if live else SEG_CAP

    with np.errstate(invalid='ignore', over='ignore'):
        # 0. sketched pairs: look for one intersecting triangle pair with a
        # beam search towards the most deeply overlapping boxes.  Finding one
        # proves the collision; nothing more is computed for such a pair now.
        if lite.any():
            lp = np.flatnonzero(lite)
            zs = np.zeros(lp.shape[0], dtype=np.int64)
            d_pid, d_ia, d_ib = _dive(w, (lp, zs, zs, np.zeros(lp.shape[0], dtype=np.float32)),
                                      w.PH[pose_a], w.PH[pose_b], pose_a, pose_b, relt, BEAM_SKETCH,
                                      xf)
            if d_pid.shape[0]:
                P, Q = _pair_tris(w, pose_a, pose_b, rel64t, d_pid, d_ia, d_ib, xf)
                hit, seg = tritri.tri_tri_intersect(P, Q, w.eps_exact)
                if seg.shape[0]:
                    sk_rows = (d_pid[hit], d_ia[hit], d_ib[hit], seg.astype(np.float32))
                    sketch[sk_rows[0]] = True

        # 1. every triangle pair whose boxes touch -> intersection segments
        # (all pairs except those just proven to intersect)
        if sketch.any():
            pids = np.flatnonzero(~sketch)
            zs = np.zeros(pids.shape[0], dtype=np.int64)
            start = (pids, zs, zs, np.zeros(pids.shape[0], dtype=np.float32))
        else:
            start = (np.arange(npid), zero, zero, np.zeros(npid, dtype=np.float32))
        leaf, dense_rows, dense, h_ad, h_bd, _ = _fine(
            w, start, w.PH[pose_a], w.PH[pose_b], pose_a, pose_b, relt,
            np.full(npid, -1.0, dtype=np.float32), eps2, xf=xf)
        l_pid, l_ia, l_ib, l_lb2 = leaf
        approx |= dense
        capped |= dense
        if l_pid.shape[0]:
            cnt = np.bincount(l_pid, minlength=npid)
            if (cnt > CAP_OVERLAP).any():
                approx |= cnt > CAP_OVERLAP
                capped |= cnt > CAP_OVERLAP
                sel = np.sort(_top_k(l_pid, (np.arange(l_pid.shape[0]),), CAP_OVERLAP))
                l_pid, l_ia, l_ib = l_pid[sel], l_ia[sel], l_ib[sel]
            hp, ha, hb, hs = [], [], [], []
            miss = np.ones(l_pid.shape[0], dtype=bool)
            for s in range(0, l_pid.shape[0], EXACT_CHUNK * 2):
                e = s + EXACT_CHUNK * 2
                P, Q = _pair_tris(w, pose_a, pose_b, rel64t, l_pid[s:e], l_ia[s:e], l_ib[s:e], xf)
                hit, seg = tritri.tri_tri_intersect(P, Q, w.eps_exact)
                if seg.shape[0]:
                    miss[s:e] = ~hit
                    hp.append(l_pid[s:e][hit])
                    ha.append(l_ia[s:e][hit])
                    hb.append(l_ib[s:e][hit])
                    hs.append(seg.astype(np.float32))
            if hp:
                hit_rows = (np.concatenate(hp), np.concatenate(ha),
                            np.concatenate(hb), np.concatenate(hs))
                collided[hit_rows[0]] = True
            # touching boxes without intersection: the closest candidates of
            # pairs that do not collide
            miss &= ~np.take(collided, l_pid)
            o_pid, o_ia, o_ib = l_pid[miss], l_ia[miss], l_ib[miss]
        else:
            o_pid = o_ia = o_ib = e_i

        if sk_rows is not None and sk_rows[0].shape[0]:
            hit_rows = sk_rows if hit_rows is None else tuple(
                np.concatenate((x, y)) for x, y in zip(hit_rows, sk_rows))
            collided[sk_rows[0]] = True

        if dense.any():
            d_pid, d_ia, d_ib = _dive(w, dense_rows, h_ad, h_bd, pose_a, pose_b, relt, BEAM_DENSE,
                                      xf)
            if d_pid.shape[0]:
                _eval_rows(w, pose_a, pose_b, rel64t, d_pid, d_ia, d_ib, w.eps_exact, best, xf)

        # 2. regions.  One coarse traversal finds, for the pairs that do not
        # intersect, where they come within the thresholds (the search region
        # for the clearance) and, for the pairs that do, the surface patches
        # within the display distance of the other part (what gets hatched).
        free = np.flatnonzero(~collided)
        free_full = np.flatnonzero(~collided & full)
        hit_p = np.flatnonzero(collided & full)
        show = hit_p.shape[0] > 0 and viz > 4.0 * w.eps_touch
        if floor > 0.0 and (free_full.shape[0] or show):
            thr_p = np.full(npid, dlim2, dtype=np.float32)
            flo_p = np.full(npid, floor, dtype=np.float32)
            if show:
                thr_p[hit_p] = (viz + eps) ** 2
                flo_p[hit_p] = REGION_RES * viz
            rows, h_a1, h_b1 = _coarse(w, pose_a, pose_b, relt, thr_p, flo_p, eps2,
                                       np.flatnonzero(full) if show else free_full, xf)
            if show:
                m = np.take(collided, rows[0])
                region_c = tuple(x[m] for x in rows)
                m = ~m
                region = tuple(x[m] for x in rows)
                h_a2, h_b2 = h_a1, h_b1
            else:
                region = rows
        else:
            region = (o_pid, o_ia, o_ib, np.zeros(o_pid.shape[0], dtype=np.float32))
            if show:
                region_c, h_a2, h_b2 = _coarse(w, pose_a, pose_b, relt,
                                               np.float32((viz + eps) ** 2), REGION_RES * viz,
                                               eps2, hit_p, xf)

        # 3. clearance of the pairs that do not intersect
        if free.shape[0]:
            if o_pid.shape[0]:
                sel = _top_k(o_pid, (np.arange(o_pid.shape[0]),), 256)
                _eval_rows(w, pose_a, pose_b, rel64t, o_pid[sel], o_ia[sel], o_ib[sel],
                           w.eps_exact, best, xf)
            lf = np.flatnonzero(~collided & lite)
            if lf.shape[0] and clear > 0.0:
                # sketched: the closest triangles a guided search comes across.
                # A value below the clearance is a true violation; anything
                # else is unproven and is checked properly later.
                zs = np.zeros(lf.shape[0], dtype=np.int64)
                d_pid, d_ia, d_ib = _dive(w, (lf, zs, zs, np.zeros(lf.shape[0], dtype=np.float32)),
                                          w.PH[pose_a], w.PH[pose_b], pose_a, pose_b, relt, BEAM, xf)
                if d_pid.shape[0]:
                    _eval_rows(w, pose_a, pose_b, rel64t, d_pid, d_ia, d_ib, w.eps_exact, best, xf)
            short[lf] = True
            r_pid = region[0]
            if r_pid.shape[0]:
                near[r_pid] = True
                d_pid, d_ia, d_ib = _dive(w, region, h_a1, h_b1, pose_a, pose_b, relt, BEAM, xf)
                if d_pid.shape[0]:
                    _eval_rows(w, pose_a, pose_b, rel64t, d_pid, d_ia, d_ib, w.eps_exact, best, xf)

                # prove the minimum: only rows that could beat the bound are
                # followed.  While something is moving the search is capped; a
                # pair whose search was cut short is redone exactly later.
                def prove(rows, cap_rows, cap_eval):
                    """Returns, per pair, the squared lower bound of whatever
                    was left out because of the caps (inf: nothing was)."""
                    u = np.sqrt(np.minimum(best.d2, dlim2))
                    thr = u - np.maximum(eps, 1e-3 * u)
                    thr2 = np.where(thr > 0.0, thr * thr, -1.0).astype(np.float32)
                    cand, _, _, _, _, left2 = _fine(w, rows, h_a1, h_b1, pose_a, pose_b, relt, thr2,
                                                    np.float32(-1.0), cap_rows, True, xf)
                    left2 = left2.astype(np.float64)
                    c_pid, c_ia, c_ib, c_lb2 = cand
                    if c_pid.shape[0] == 0:
                        return left2
                    order = np.argsort(c_lb2, kind='stable')
                    done = np.zeros(npid, dtype=np.int64)
                    for s in range(0, order.shape[0], EXACT_CHUNK * 2):
                        rows = order[s:s + EXACT_CHUNK * 2]
                        p = np.take(c_pid, rows)
                        u = np.sqrt(np.minimum(best.d2, dlim2))
                        t = u - np.maximum(eps, 1e-4 * u)
                        t2 = np.where(t > 0.0, t * t, -1.0)
                        lbr = np.take(c_lb2, rows)
                        m = lbr < np.take(t2, p)
                        if not m.any():
                            if lbr[0] >= t2.max():
                                break
                            continue
                        over = m & (np.take(done, p) >= cap_eval)
                        if over.any():
                            np.minimum.at(left2, p[over], lbr[over])
                            m &= ~over
                            if not m.any():
                                continue
                        rows = rows[m]
                        p = p[m]
                        P, Q = _pair_tris(w, pose_a, pose_b, rel64t, p,
                                          np.take(c_ia, rows), np.take(c_ib, rows), xf)
                        m = tritri.plane_gap2(P, Q) < np.take(t2, p)
                        done += np.bincount(p, minlength=npid)
                        if not m.any():
                            continue
                        if not m.all():
                            sel = np.flatnonzero(m)
                            p = p[sel]
                            P = np.take(P, sel, axis=2)
                            Q = np.take(Q, sel, axis=2)
                        d2, cp, cq = tritri.tri_tri_distance(P, Q)
                        best.update(p, d2, cp, cq)
                    return left2

                if not exact:
                    short |= np.isfinite(prove(region, CAP_SEARCH_QUICK, CAP_EXACT_QUICK))
                else:
                    # A pair whose search for an intersection was capped goes
                    # to ``scan`` next.  Most such pairs turn out to cut each
                    # other, and proving a distance for them (thousands of
                    # exact evaluations each) would be for nothing: the proof
                    # is made when the pair comes back from there cleared.
                    wait = capped & ~cleared & ~collided
                    if wait.any():
                        short |= wait
                        m = ~np.take(wait, region[0])
                        region_p = tuple(x[m] for x in region)
                    else:
                        region_p = region
                    # A first pass with moderate caps settles nearly every
                    # pair.  Where it was cut short, what was left out has a
                    # proven lower bound: only if that could still change the
                    # verdict (touching or not, too close or not) is the
                    # search repeated with large caps, which for two parts
                    # with big areas at about the limit takes long.
                    left2 = prove(region_p, CAP_SEARCH, CAP_EXACT)
                    cut = np.isfinite(left2)
                    if cut.any():
                        u = np.sqrt(np.minimum(best.d2, dlim2))
                        lo = np.sqrt(np.where(cut, left2, 0.0))
                        # (whether a pair whose intersection search was capped
                        # touches is for ``scan`` to say, and it has said no
                        # if the pair is here)
                        open_ = cut & (((u > coll) & (lo <= coll) & ~capped)
                                       | ((clear > 0.0) & (u >= clear) & (lo < clear)))
                        if open_.any():
                            w.proofs_repeated += int(open_.sum())
                            m = np.take(open_, region_p[0])
                            again = prove(tuple(x[m] for x in region_p), CAP_FINE, CAP_EXACT_MAX)
                            left2 = np.where(open_, again, left2)
                            cut = np.isfinite(left2)
                            lo = np.sqrt(np.where(cut, left2, 0.0))
                            u = np.sqrt(np.minimum(best.d2, dlim2))
                        # the value is flagged approximate if it is not proven
                        # to within a hundredth
                        short |= cut & (lo < u - np.maximum(eps, 1e-2 * u))

        # enclosure: a part completely inside another one, surfaces apart
        enclosed = np.zeros(npid, dtype=np.int8)      # 1: A inside B, 2: B inside A
        if w.detect_enclosed:
            cand = np.flatnonzero(~collided & (np.sqrt(best.d2) > coll))
            if cand.shape[0]:
                a_lo = w.O_LO[sa[cand]]
                a_hi = w.O_HI[sa[cand]]
                b_lo = w.O_LO[sb[cand]] + rel64[cand]
                b_hi = w.O_HI[sb[cand]] + rel64[cand]
                a_in_b = ((a_lo >= b_lo) & (a_hi <= b_hi)).all(axis=1)
                b_in_a = ((b_lo >= a_lo) & (b_hi <= a_hi)).all(axis=1) & ~a_in_b
                for flag, inner, outer, sign, code, ki, ko in (
                        (a_in_b, pose_a, pose_b, -1.0, 1, 0, 1), (b_in_a, pose_b, pose_a, 1.0, 2, 1, 0)):
                    sel = cand[flag]
                    if sel.shape[0] == 0:
                        continue
                    # one surface point of each inner part, in the outer part's frame
                    pts = _corners(w, inner, sel, np.zeros(sel.shape[0], dtype=np.int64),
                                   None if xf is None else xf.side[ki])[:, 0, :].astype(np.float64)
                    pts += sign * rel64[sel]
                    if xf is not None and xf.any[ko]:
                        # an outer part that borrows its pose is tested in the
                        # frame of that pose
                        so = xf.slots[ko][sel]
                        mv = w.O_VIRT[so]
                        if mv.any():
                            pts[mv] = np.einsum('kij,kj->ki', w.O_RELI[so[mv]], pts[mv])
                    inside = points_inside(w, outer[sel], pts)
                    enclosed[sel[inside]] = code

    # 4. report --------------------------------------------------------------
    dist = np.sqrt(best.d2)
    r_pid, r_ia, r_ib, r_lb2 = region
    if r_pid.shape[0]:
        r_order, r_bounds = _bounds(r_pid, npid)
    if hit_rows is not None:
        h_order, h_bounds = _bounds(hit_rows[0], npid)

    if region_c is not None and region_c[0].shape[0]:
        c_order, c_bounds = _bounds(region_c[0], npid)
    else:
        region_c = None

    for p in range(npid):
        res = w.pairs.get(keys[p])
        if res is None:
            continue
        res.stale = False
        res.approx = bool(approx[p])
        res.lite = bool(lite[p])
        res.refine = bool(lite[p])
        res.unproven = False
        res.enclosed = 0
        res.segs = None
        res.nseg = 0
        res.tri_a = res.tri_b = None
        res.clip_a = res.clip_b = None
        shift = rel[p]
        va = vb = None
        if xf is not None:
            va = xf.one(p, 0)
            vb = xf.one(p, 1)
        if collided[p]:
            sel = _rows_of(h_order, h_bounds, p)
            segs = hit_rows[3][sel]
            res.state = COLLIDE
            res.dist = 0.0
            pa_i = int(pose_a[p])
            pb_i = int(pose_b[p])
            pts = segs.reshape(-1, 3)
            lo = pts.min(axis=0).astype(np.float64)
            hi = pts.max(axis=0).astype(np.float64)
            res.nseg = int(segs.shape[0])
            diag = float(np.linalg.norm(hi - lo))
            csel = _rows_of(c_order, c_bounds, p) if region_c is not None else None
            if csel is not None and region_c[1][csel].shape[0]:
                # surface patches of both parts near the other part
                res.tri_a, lo_a, hi_a = _region_side(w, pa_i, int(h_a2[p]), region_c[1][csel],
                                                     None, tri_cap, box_cap, va)
                res.tri_b, lo_b, hi_b = _region_side(w, pb_i, int(h_b2[p]), region_c[2][csel],
                                                     shift, tri_cap, box_cap, vb)
                res.clip_a = _clip_box(res.tri_a, None, lo_b - viz, hi_b + viz, viz)
                res.clip_b = _clip_box(res.tri_b, shift, lo_a - viz, hi_a + viz, viz)
            else:
                # sketched, or no display distance to speak of: the triangles
                # the (partial) curve runs through
                res.tri_a = _node_tris(w, pa_i, 0, _unique(hit_rows[1][sel], int(w.PNT[pa_i]) + 1),
                                       None, tri_cap, va)
                res.tri_b = _node_tris(w, pb_i, 0, _unique(hit_rows[2][sel], int(w.PNT[pb_i]) + 1),
                                       None, tri_cap, vb)
                grow = max(pad, 0.15 * diag)
                res.clip_a = _clip_box(res.tri_a, None, lo - grow, hi + grow, grow)
                res.clip_b = _clip_box(res.tri_b, shift, lo - grow, hi + grow, grow)
            if segs.shape[0] > seg_cap:
                segs = segs[::segs.shape[0] // seg_cap + 1]
            res.segs = segs
            res.center = 0.5 * (lo + hi)
            res.radius = max(0.5 * diag, pad)
            res.pa = res.pb = res.center
            continue

        if enclosed[p]:
            # report the inner part: its outline box and a sample of its surface
            a_inner = enclosed[p] == 1
            pose_in = int(pose_a[p] if a_inner else pose_b[p])
            off = np.zeros(3) if a_inner else rel64[p]
            slot_in = sa[p] if a_inner else sb[p]
            lo = w.O_LO[slot_in] + off
            hi = w.O_HI[slot_in] + off
            res.state = COLLIDE
            res.dist = 0.0
            res.enclosed = int(enclosed[p])
            res.segs = box_edges(lo, hi)
            tris = _node_tris(w, pose_in, int(w.PH[pose_in]), np.zeros(1, dtype=np.int64),
                              None, tri_cap, va if a_inner else vb)
            if a_inner:
                res.tri_a = tris
            else:
                res.tri_b = tris
            res.center = 0.5 * (lo + hi)
            res.radius = max(0.5 * float(np.linalg.norm(hi - lo)), pad)
            res.pa = res.pb = res.center
            continue

        # a search that was cut short is redone exactly once things are quiet
        if short[p]:
            if exact:
                res.approx = True
            else:
                res.refine = True
        d = float(dist[p])
        # no intersection found, but not every candidate was looked at
        res.unproven = bool(capped[p] and not cleared[p]) and d > coll
        if d <= coll:
            state, thr_v = COLLIDE, coll
        elif clear > 0.0 and d < clear:
            state, thr_v = CLEAR, clear
        else:
            res.state = OK
            res.dist = d if np.isfinite(d) else float('inf')
            res.pa = res.pb = None
            continue
        res.state = state
        res.dist = d
        res.pa = best.pa[p].copy()
        res.pb = best.pb[p].copy()
        res.center = 0.5 * (res.pa + res.pb)
        res.radius = max(0.5 * d, pad)
        if not near[p]:
            continue
        sel = _rows_of(r_order, r_bounds, p)
        lb = r_lb2[sel]
        if lb.shape[0]:
            lim = np.float32((thr_v + eps) ** 2)
            keep = lb <= lim
            if not keep.all():
                sel = (np.arange(sel.start, sel.stop) if isinstance(sel, slice) else sel)[keep]
        nodes_a = r_ia[sel]
        if nodes_a.shape[0] == 0:
            continue
        pa_i = int(pose_a[p])
        pb_i = int(pose_b[p])
        tri_a, lo_a, hi_a = _region_side(w, pa_i, int(h_a1[p]), nodes_a, None, tri_cap, box_cap, va)
        tri_b, lo_b, hi_b = _region_side(w, pb_i, int(h_b1[p]), r_ib[sel], shift, tri_cap, box_cap,
                                         vb)
        res.tri_a = tri_a
        res.tri_b = tri_b
        res.clip_a = _clip_box(tri_a, None, lo_b - thr_v, hi_b + thr_v, thr_v)
        res.clip_b = _clip_box(tri_b, shift, lo_a - thr_v, hi_a + thr_v, thr_v)
        # where both parts are near each other: used to frame the view
        flo = np.maximum(lo_a, lo_b) - 0.5 * thr_v
        fhi = np.minimum(hi_a, hi_b) + 0.5 * thr_v
        if (fhi > flo).all():
            res.radius = max(res.radius, 0.5 * float(np.linalg.norm(fhi - flo)))
            res.center = 0.5 * (flo + fhi)


# ---------------------------------------------------------------------------
# the exhaustive search for an intersection
# ---------------------------------------------------------------------------

def scan_start(w, key):
    """State for ``scan``: the two roots, and nothing looked at yet."""
    a, b = key
    root = np.zeros(1, dtype=np.int64)
    return {'stack': [(root, root, int(w.PH[w.O_POSE[a]]), int(w.PH[w.O_POSE[b]]))], 'tested': 0}


def scan(w, key, state, deadline):
    """Look at every pair of triangles of two objects whose boxes come within
    the contact distance, until one pair intersects or touches.

    Both objects must have fitted poses.  The search is depth first over
    blocks of node pairs, so what it has to remember stays small however many
    candidates there are, and it gets to triangles at once: two surfaces that
    cut through each other all over are found out in the first block.  Two
    that nearly coincide without touching take as long as their millions of
    candidates take, which is why this can stop at ``deadline`` (seconds, as
    ``time.perf_counter``) and be called again with the same ``state``.

    Returns None if it ran out of time, False if the parts neither intersect
    nor touch, and otherwise what it found: ``('cut', segments (n, 2, 3)
    float32, triangles of a, triangles of b)`` or ``('touch', point on a,
    point on b, triangle of a, triangle of b)``, in the pair's frame.
    """
    a, b = key
    pose_a = w.O_POSE[[a]]
    pose_b = w.O_POSE[[b]]
    rel64 = (w.O_T[b] - w.O_T[a])[None, :]
    rel64t = np.ascontiguousarray(rel64.T)
    relt = np.ascontiguousarray(rel64.astype(np.float32).T)
    eps = w.eps_len
    coll = max(w.coll_thr, w.eps_touch)
    coll2 = coll * coll
    thr2 = np.full(1, (coll + eps) ** 2, dtype=np.float32)
    eps2 = np.float32(eps * eps)
    live = np.ones(1, dtype=bool)
    stack = state['stack']
    step = EXACT_CHUNK * 2
    first = True
    with np.errstate(invalid='ignore', over='ignore'):
        while stack:
            # (one block at least, so that a call always gets somewhere)
            if not first and time.perf_counter() > deadline:
                return None
            first = False
            ia, ib, ha, hb = stack.pop()
            n = ia.shape[0]
            if ha == 0 and hb == 0:
                # triangles: a piece now, the rest when its turn comes
                if n > step:
                    stack.append((ia[step:], ib[step:], 0, 0))
                    ia, ib = ia[:step], ib[:step]
                    n = step
                P, Q = _pair_tris(w, pose_a, pose_b, rel64t, np.zeros(n, dtype=np.int64), ia, ib)
                hit, seg = tritri.tri_tri_intersect(P, Q, w.eps_exact)
                state['tested'] += n
                if seg.shape[0]:
                    return 'cut', seg[:SCAN_SEGS].astype(np.float32), ia[hit][:SCAN_SEGS], ib[hit][:SCAN_SEGS]
                # contact without one piercing the other (coplanar, edge on edge)
                sel = np.flatnonzero(tritri.plane_gap2(P, Q) <= coll2)
                if sel.shape[0]:
                    d2, cp, cq = tritri.tri_tri_distance(np.take(P, sel, axis=2), np.take(Q, sel, axis=2))
                    k = int(np.argmin(d2))
                    if d2[k] <= coll2:
                        return 'touch', cp[:, k].copy(), cq[:, k].copy(), int(ia[sel[k]]), int(ib[sel[k]])
                continue
            if n > SCAN_BLOCK:
                # depth first: one half now, the other waits
                half = n // 2
                stack.append((ia[half:], ib[half:], ha, hb))
                ia, ib = ia[:half], ib[:half]
                n = half
                if n > SCAN_BLOCK:
                    stack.append((ia, ib, ha, hb))
                    continue
            h_a = np.array([ha])
            h_b = np.array([hb])
            sa, sb, _ = _steps(w, pose_a, pose_b, h_a, h_b, live, n, -1.0)
            h_a = h_a - sa
            h_b = h_b - sb
            _, ia, ib, _ = _step(w, np.zeros(n, dtype=np.int64), ia, ib, sa, sb,
                                 w.LVL[pose_a, h_a], w.LVL[pose_b, h_b], relt, thr2, eps2)
            if ia.shape[0]:
                stack.append((ia, ib, int(h_a[0]), int(h_b[0])))
    return False


def scan_report(w, key, res, found):
    """Turn a pair's result into a collision that ``scan`` has found."""
    a, b = key
    pose_a = int(w.O_POSE[a])
    pose_b = int(w.O_POSE[b])
    pad = w.viz_pad
    res.state = COLLIDE
    res.dist = 0.0
    res.approx = True             # the curve is not complete
    res.unproven = False
    res.refine = False
    res.enclosed = 0
    if found[0] == 'cut':
        _, segs, ta, tb = found
        pts = segs.reshape(-1, 3)
        lo = pts.min(axis=0).astype(np.float64)
        hi = pts.max(axis=0).astype(np.float64)
        res.segs = segs
        res.nseg = int(segs.shape[0])
        ta = _unique(ta, int(w.PNT[pose_a]) + 1)
        tb = _unique(tb, int(w.PNT[pose_b]) + 1)
    else:
        _, pa, pb, ta, tb = found
        lo = np.minimum(pa, pb)
        hi = np.maximum(pa, pb)
        res.segs = None
        res.nseg = 0
        ta = np.array([ta], dtype=np.int64)
        tb = np.array([tb], dtype=np.int64)
    if res.tri_a is None and res.tri_b is None:
        # (a pair that was already too close keeps the patches it shows)
        res.tri_a = _node_tris(w, pose_a, 0, ta)
        res.tri_b = _node_tris(w, pose_b, 0, tb)
        res.clip_a = res.clip_b = None
    if res.center is None or res.pa is None:
        res.center = 0.5 * (lo + hi)
        res.radius = max(0.5 * float(np.linalg.norm(hi - lo)), pad)
    res.pa = res.pb = 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# build volume
# ---------------------------------------------------------------------------

def _borrowed_rows(w, b, slots, pose, rid, idx, hr, mv):
    """Boxes ``b`` (n, 6) of one traversal step against the volume, with the
    rows of borrowing objects (``mv``) put right: moved into the object's
    orientation, and at the leaves replaced by the exact box of the triangle,
    so that a triangle is only reported if it really is out."""
    sel = np.flatnonzero(mv)
    sl = np.take(slots, rid[sel])
    raw = b[sel]
    empty = raw[:, 0] > raw[:, 3]
    R = w.O_REL[sl]
    c = np.einsum('kij,kj->ki', R, raw[:, :3] + raw[:, 3:]) * 0.5
    e = np.einsum('kij,kj->ki', np.abs(R), raw[:, 3:] - raw[:, :3]) * 0.5 + w.O_PAD[sl][:, None]
    out = np.concatenate([c - e, c + e], axis=1)
    leaf = hr[sel] == 0
    if leaf.any():
        k = np.flatnonzero(leaf)
        g = sl[k]
        t = np.take(w.TIDX.data, np.take(w.PTB, np.take(pose, rid[sel[k]])) + idx[sel[k]], axis=0)
        loc = np.take(w.LVERT.data, (t + w.O_LB[g][:, None]).ravel(), axis=0)
        L9 = np.repeat(np.ascontiguousarray(w.O_L[g].reshape(-1, 9).T), 3, axis=1)
        tri = _pose_local(loc, L9).reshape(-1, 3, 3)
        out[k, :3] = tri.min(axis=1)
        out[k, 3:] = tri.max(axis=1)
    out[empty, :3] = np.inf
    out[empty, 3:] = -np.inf
    b[sel] = out
    return b


def oob_solve(w, slots, vlo, vhi, tri_cap):
    """Triangles of the given objects that are not completely inside the box.

    One tree traversal against the box for all objects at once: nodes that are
    fully inside are discarded, nodes that are fully outside are taken whole,
    and only nodes straddling a wall are opened further.

    Returns (list of float32 (k, 3, 3) world-space triangle arrays,
             list of flags that are True when only the straddling band is given
             because the full outside part was too large).
    """
    n = slots.shape[0]
    pose = w.O_POSE[slots]
    virt = w.O_VIRT[slots]
    borrowing = bool(virt.any())
    t = w.O_T[slots]
    lo = (vlo[None, :] - t).astype(np.float32)
    hi = (vhi[None, :] - t).astype(np.float32)
    h = w.PH[pose].copy()
    rid = np.arange(n)
    idx = np.zeros(n, dtype=np.int64)
    box = w.BOX.data
    em = []
    while rid.shape[0]:
        b = np.take(box, np.take(w.LVL[pose, h], rid) + idx, axis=0)
        hr = np.take(h, rid)
        if borrowing:
            mv = np.take(virt, rid)
            if mv.any():
                with np.errstate(invalid='ignore', over='ignore'):
                    b = _borrowed_rows(w, b, slots, pose, rid, idx, hr, mv)
        blo = b[:, :3]
        bhi = b[:, 3:]
        l = np.take(lo, rid, axis=0)
        u = np.take(hi, rid, axis=0)
        drop = (blo[:, 0] > bhi[:, 0]) | ((blo >= l) & (bhi <= u)).all(axis=1)
        outside = ((blo > u) | (bhi < l)).any(axis=1)
        emit = ~drop & (outside | (hr == 0))
        if emit.any():
            em.append((rid[emit], idx[emit], hr[emit], outside[emit]))
        go = ~(drop | emit)
        rid = rid[go]
        idx = idx[go]
        if rid.shape[0] == 0:
            break
        present = np.zeros(n, dtype=bool)
        present[rid] = True
        s = np.where(present, np.minimum(_base_step(rid.shape[0]), h), 0)
        h = h - s
        sr = np.take(s, rid)
        cnt = np.left_shift(1, sr)
        src = np.repeat(np.arange(rid.shape[0]), cnt)
        first = np.cumsum(cnt) - cnt
        j = np.arange(src.shape[0]) - np.take(first, src)
        idx = (np.take(idx, src) << np.take(sr, src)) + j
        rid = np.take(rid, src)

    out = [np.zeros((0, 3, 3), dtype=np.float32) for _ in range(n)]
    band = [False] * n
    if not em:
        return out, band
    e_rid, e_idx, e_h, e_out = (np.concatenate(c) for c in zip(*em))
    for r in range(n):
        m = e_rid == r
        if not m.any():
            continue
        p = int(pose[r])
        nt = int(w.PNT[p])
        ii = e_idx[m]
        hh = e_h[m]
        oo = e_out[m]
        total = int((np.minimum((ii + 1) << hh, nt) - np.minimum(ii << hh, nt)).sum())
        if total > tri_cap:
            keep = (hh == 0) & ~oo
            ii = ii[keep]
            hh = hh[keep]
            band[r] = True
            if ii.shape[0] > tri_cap:
                ii = ii[::(ii.shape[0] // tri_cap + 1)]
                hh = hh[:ii.shape[0]]
        parts = []
        shift = t[r].astype(np.float32)
        slot = int(slots[r])
        vr = (w.O_L[slot], int(w.O_LB[slot])) if virt[r] else None
        for level in np.unique(hh):
            parts.append(_node_tris(w, p, int(level), ii[hh == level], shift, None, vr))
        if parts:
            out[r] = parts[0] if len(parts) == 1 else np.concatenate(parts)
    return out, band
