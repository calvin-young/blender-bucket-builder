# SPDX-License-Identifier: GPL-3.0-or-later
"""Pattern solver: copies of one part on a lattice, all in one orientation
or in pairs with the second copy turned 180 degrees ("double lattices"),
and families of similar parts on the lattice of their envelope
(docs/AUTOPACK.md §8.4).

Where a second copy may sit next to a first is one correlation of the
part's grids: the copy at cell offset d is clear of the first when its
dilated grid (kernel) meets none of the first one's solid cells,

    free(d)  <=>  sum_x solid(x) kernel(x - d) = 0,

one FFT for every d at once.  A lattice n1 v1 + n2 v2 + n3 v3 is a valid
pattern when every lattice vector is free (copies further apart than the
part's own size plus the gap are always free).  The search:

1. in the floor plane: the nearest free offset along each of 90 directions
   gives candidate vectors; pairs of them, smallest area first, are tried
   until one makes a lattice whose every vector (in a reduced basis, up to
   the reach of the grids) is free;
2. upwards: the lowest v3 over the plane lattice's cell that keeps every
   vector free (copies that nest stack closer than their height);
3. into the bucket: lattice points whose part lies inside the volume less
   the wall gap and clear of what is there already, for 16 phases of the
   plane lattice; the q lowest of the best phase.

A lattice packs copies far more evenly than greedy does one at a time, and
finding one costs a few FFTs instead of one per copy.  Every pattern is
valid on the grid by construction (each pair of copies is a lattice vector
apart); the referee checks the final layout as for any other.
"""

import math

import numpy as np

from .bucket import fast_size, irfftn, rfftn

N_DIRECTIONS = 90
PHASES = 4                # per plane vector: 4 x 4 shifts of the lattice are tried


class Envelope:
    """Several part grids at fixed cell offsets from each other, as one grid
    (solid and kernel the union of theirs): a motif of copies (a part and
    its partner turned 180 degrees), or the members of a family of similar
    parts at one place (the lattice of the envelope is valid whichever
    member stands in it).  `shifts[j]`: where item j's grid starts."""

    def __init__(self, items):
        sh = np.array([s for _, s in items], np.int64).reshape(-1, 3)
        sh -= sh.min(0)
        hi = np.max(sh + np.array([g.solid.shape for g, _ in items]), axis=0)
        S = np.zeros(tuple(hi), bool)
        K = np.zeros(tuple(hi), bool)
        for (g, _), d in zip(items, sh):
            sl = tuple(slice(int(a), int(a) + n) for a, n in zip(d, g.solid.shape))
            S[sl] |= g.solid
            K[sl] |= g.kernel
        self.solid, self.kernel = S, K
        self.shifts = [tuple(int(x) for x in d) for d in sh]
        idx = [np.nonzero(S.any(axis=tuple(a for a in range(3) if a != ax)))[0] for ax in range(3)]
        self.s_lo = np.array([i[0] for i in idx])
        self.s_hi = np.array([i[-1] + 1 for i in idx])


def cross_free(ga, gb):
    """(free, off): free[t + off] is True where grid gb at cell offset t is
    clear of grid ga at 0."""
    S = ga.solid.astype(np.float64)
    K = gb.kernel[::-1, ::-1, ::-1].astype(np.float64)
    full = [a + b - 1 for a, b in zip(S.shape, K.shape)]
    s = [fast_size(n) for n in full]
    conv = irfftn(rfftn(S, s) * rfftn(K, s), s)[:full[0], :full[1], :full[2]]
    return conv < 0.5, np.array(K.shape) - 1


def _sphere(n):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = math.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)


def contacts(free, off, n_dir=400):
    """The nearest free offset along each of n_dir directions over the
    sphere (where a second grid touches the first, at the gap)."""
    D = _sphere(n_dir)
    R = int(max(free.shape)) + 2
    r = np.arange(0, 2 * R) * 0.5
    P = np.rint(r[None, :, None] * D[:, None, :]).astype(np.int64)        # (dirs, steps, 3)
    ok = _free_at(free, off, P.reshape(-1, 3)).reshape(P.shape[:2])
    hit = ok.any(1)
    first = np.argmax(ok, axis=1)
    out = P[np.nonzero(hit)[0], first[hit]]
    return np.unique(out, axis=0)


def pairings(ga, gb, n=3, n_dir=400):
    """The n offsets t at which gb sits next to ga most compactly (smallest
    bounding box of the two), each clear of the other: candidates for the
    second copy of a motif."""
    free, off = cross_free(ga, gb)
    T = contacts(free, off, n_dir)
    if not len(T):
        return []
    lo = np.minimum(ga.s_lo, gb.s_lo + T)
    hi = np.maximum(ga.s_hi, gb.s_hi + T)
    vol = np.prod(hi - lo, axis=1)
    return [T[j] for j in np.argsort(vol, kind='stable')[:n]]


class Lattice:
    __slots__ = ('v1', 'v2', 'v3', 'free', 'off', 'reach')

    def det(self):
        return abs(float(np.linalg.det(np.stack([self.v1, self.v2, self.v3]).astype(float))))


def free_offsets(pg):
    """(free, off): free[d + off] is True where a copy at cell offset d is
    clear of one at 0 (the arrays' reach; further away is always free)."""
    S = pg.solid.astype(np.float64)
    K = pg.kernel[::-1, ::-1, ::-1].astype(np.float64)
    full = [a + b - 1 for a, b in zip(S.shape, K.shape)]
    s = [fast_size(n) for n in full]
    conv = irfftn(rfftn(S, s) * rfftn(K, s), s)[:full[0], :full[1], :full[2]]
    off = np.array(K.shape) - 1
    return conv < 0.5, off


def _free_at(free, off, D):
    """Whether offsets D (n, 3) are free."""
    idx = np.asarray(D, np.int64) + off
    inside = np.all((idx >= 0) & (idx < np.array(free.shape)), axis=1)
    out = np.ones(len(idx), dtype=bool)
    if inside.any():
        j = idx[inside]
        out[inside] = free[j[:, 0], j[:, 1], j[:, 2]]
    return out


def _reduce2(a, b):
    """Lagrange-Gauss reduction of a 2-D basis (integer vectors)."""
    a, b = np.array(a, np.int64), np.array(b, np.int64)
    if a @ a > b @ b:
        a, b = b, a
    for _ in range(64):
        m = int(round((a @ b) / max(a @ a, 1)))
        if m == 0:
            break
        b = b - m * a
        if a @ a > b @ b:
            a, b = b, a
        else:
            break
    return a, b


def _combos(basis, reach, extra=None):
    """Integer combinations of the basis vectors (not all zero) that may be
    within `reach` (cells, per axis), as (n, 3) offsets."""
    B = np.stack(basis).astype(np.int64)
    k = len(basis)
    lens = np.sqrt((B.astype(float) ** 2).sum(1))
    r = float(np.max(reach)) * math.sqrt(3.0)
    ns = [int(min(8, math.ceil(r / max(L, 1e-9)) + 1)) for L in lens]
    grids = np.meshgrid(*[np.arange(-n, n + 1) for n in ns], indexing='ij')
    N = np.stack([g.ravel() for g in grids], 1)
    N = N[np.any(N != 0, axis=1)]
    if extra is not None:
        N = N[extra(N)]
    D = N @ B
    near = np.all(np.abs(D) <= np.asarray(reach), axis=1)
    return D[near]


def plane_lattice(free, off, reach):
    """(v1, v2) in the floor plane (dz = 0) with the smallest area, or None."""
    # the nearest free offset along each direction
    cand = []
    R = int(max(reach[0], reach[1])) + 2
    for t in np.linspace(0.0, math.pi, N_DIRECTIONS, endpoint=False):
        r = np.arange(1, 2 * R + 1) * 0.5
        P = np.stack([np.rint(r * math.cos(t)), np.rint(r * math.sin(t)), np.zeros_like(r)], 1).astype(np.int64)
        P = P[np.any(P != 0, axis=1)]
        ok = _free_at(free, off, P)
        if ok.any():
            cand.append(tuple(P[int(np.argmax(ok))]))
    cand = np.unique(np.array(cand, np.int64), axis=0)
    if len(cand) < 2:
        return None
    i, j = np.triu_indices(len(cand), 1)
    area = np.abs(cand[i, 0] * cand[j, 1] - cand[i, 1] * cand[j, 0])
    keep = area > 0
    i, j, area = i[keep], j[keep], area[keep]
    # cheap necessary test for all pairs at once: a few small combinations
    # (in any basis of the lattice they are lattice vectors)
    A, Bv = cand[i], cand[j]
    ok = np.ones(len(i), dtype=bool)
    for m, n in ((1, 1), (1, -1), (2, 1), (1, 2), (2, -1), (1, -2)):
        ok &= _free_at(free, off, m * A + n * Bv)
    i, j, area = i[ok], j[ok], area[ok]
    for p in np.argsort(area, kind='stable')[:4000]:
        a, b = _reduce2(cand[i[p], :2], cand[j[p], :2])
        v1 = np.array([a[0], a[1], 0])
        v2 = np.array([b[0], b[1], 0])
        D = _combos([v1, v2], reach)
        if _free_at(free, off, D).all():
            return v1, v2
    return None


def _cheap_ok(free, off, v1, v2, V3):
    """A necessary test for candidate stacking vectors V3 (n, 3): the lattice
    vectors a v1 + b v2 + n3 v3 with |a|, |b| <= 2 and n3 = 1, 2 are free."""
    V3 = np.asarray(V3, np.int64)
    small = np.array([[a, b] for a in range(-2, 3) for b in range(-2, 3)])
    P2 = small @ np.stack([v1, v2]).astype(np.int64)
    keep = np.ones(len(V3), dtype=bool)
    for n3 in (1, 2):
        idx = np.nonzero(keep)[0]
        if not idx.size:
            break
        D = P2[None, :, :] + n3 * V3[idx, None, :]
        keep[idx] = _free_at(free, off, D.reshape(-1, 3)).reshape(len(idx), -1).all(1)
    return keep


def _full_ok(free, off, reach, v1, v2, v3):
    """Whether every vector of the lattice (v1, v2, v3) within reach is free."""
    lens = [float(np.linalg.norm(v1)), float(np.linalg.norm(v2))]
    n3m = int(math.ceil(reach[2] / max(abs(v3[2]), 1))) + 1
    wide = float(np.max(reach[:2])) + n3m * float(np.abs(v3[:2]).max()) + 1
    n1, n2 = (int(min(12, math.ceil(wide / max(L, 1e-9)) + 1)) for L in lens)
    g1, g2, g3 = np.meshgrid(np.arange(-n1, n1 + 1), np.arange(-n2, n2 + 1),
                             np.r_[np.arange(-n3m, 0), np.arange(1, n3m + 1)], indexing='ij')
    D = g1.reshape(-1, 1) * v1 + g2.reshape(-1, 1) * v2 + g3.reshape(-1, 1) * v3
    D = D[np.all(np.abs(D) <= reach, axis=1)]
    return bool(_free_at(free, off, D).all())


def stack_vector(free, off, reach, v1, v2):
    """The lowest v3 (dz > 0) over the plane lattice's cell for which every
    vector of the 3-D lattice is free (copies that nest stack closer than
    their height), or straight up and clear."""
    corners = np.array([[0, 0], v1[:2], v2[:2], v1[:2] + v2[:2]])
    lo, hi = corners.min(0), corners.max(0)
    X, Y = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1), indexing='ij')
    XY = np.stack([X.ravel(), Y.ravel()], 1)
    XY = XY[np.argsort((XY ** 2).sum(1), kind='stable')]     # (the most nearly vertical first)
    top = int(reach[2]) + 1
    for z0 in range(1, top, 8):                             # (8 heights at a time: most nest low or not at all)
        dz = np.arange(z0, min(z0 + 8, top))
        V = np.column_stack([np.tile(XY, (len(dz), 1)), np.repeat(dz, len(XY))])   # (by dz, then |xy|)
        V = V[_free_at(free, off, V)]
        V = V[_cheap_ok(free, off, v1, v2, V)]
        for v3 in V:
            if _full_ok(free, off, reach, v1, v2, v3):
                return v3
    return np.array([0, 0, top])       # (no nesting: one layer on the next, clear)


def lattice(pg):
    """The densest lattice found for copies of a part grid, or None."""
    free, off = free_offsets(pg)
    reach = np.array(free.shape) - off - 1                 # (offsets beyond this are always free)
    reach = np.maximum(reach, off)
    pl = plane_lattice(free, off, reach)
    if pl is None:
        return None
    L = Lattice()
    L.v1, L.v2 = pl
    L.v3 = stack_vector(free, off, reach, L.v1, L.v2)
    L.free, L.off, L.reach = free, off, reach
    return L


def place(bucket, pg, L, q):
    """Cell offsets of up to q copies on lattice L in the bucket, lowest
    first (by z, then y, then x), clear of what the bucket holds: the best of
    the phases tried, as (cells, height of the q-th copy's top above the floor)."""
    B = np.stack([L.v1, L.v2, L.v3]).astype(float)
    Binv = np.linalg.inv(B.T)
    base = bucket.ilo - pg.s_lo                              # (the part's lowest corner on the floor, at the walls)
    span = (bucket.ihi - pg.s_hi + 1) - base                 # (where cell offsets may go, from base)
    corners = np.array([[x, y, z] for x in (0, span[0]) for y in (0, span[1]) for z in (0, span[2])], float)
    top_of = lambda b: float((b[2] - bucket.M + pg.s_hi[2]) * bucket.h)
    phases = []
    for i in range(PHASES):
        for j in range(PHASES):
            ph = np.rint(i / PHASES * L.v1 + j / PHASES * L.v2).astype(np.int64)
            c = (corners - ph) @ Binv.T
            lo = np.floor(c.min(0)).astype(np.int64) - 1
            hi = np.ceil(c.max(0)).astype(np.int64) + 1
            g = np.meshgrid(*[np.arange(a, b + 1) for a, b in zip(lo, hi)], indexing='ij')
            N = np.stack([x.ravel() for x in g], 1)
            P = N @ B.astype(np.int64) + ph
            P = P[np.all((P >= 0) & (P <= span), axis=1)] + base
            if not len(P):
                continue
            P = P[np.lexsort((P[:, 0], P[:, 1], P[:, 2]))]
            n = min(len(P), q)
            phases.append(((-n, top_of(P[n - 1])), P))     # (what it would give in an empty bucket)
    phases.sort(key=lambda t: t[0])
    empty = not bucket.fine.any()
    best = None
    for est, P in phases:
        if best is not None and not est < best[0]:
            break                                           # (what is in the bucket only makes a phase worse)
        if empty:
            cells = list(P[:q])
        else:
            cells = []
            for b in P:
                if bucket.fits(pg, b):
                    cells.append(b)
                    if len(cells) == q:
                        break
        if not cells:
            continue
        key = (-len(cells), top_of(cells[-1]))
        if best is None or key < best[0]:
            best = (key, cells)
    if best is None:
        return [], 0.0
    return best[1], best[0][1]
