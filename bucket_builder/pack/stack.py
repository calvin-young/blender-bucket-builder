# SPDX-License-Identifier: GPL-3.0-or-later
"""Stacks of parts that nest (docs/AUTOPACK.md §8.4): chips laid side by side
as they would be by hand, each in the hollow of the one before it.

A lattice needs one spacing for all, so different chips sit as far apart as the
deepest pair needs, and a voxel grid adds its slack on top of that.  Here each
pair gets its own spacing:

1. every part is posed in the family's frame, and seen along the thinnest axis
   as two height maps, its top and its bottom;
2. a's top grown by the gap (as a ball would roll over it), minus b's bottom,
   gives how far b must sit above a: the spacing of that pair, for every pair
   at once in a few array operations;
3. the parts are put in the order that makes those spacings small, which also
   puts similar parts next to each other;
4. the chain is cut into stacks and laid in the volume, on its side or upright,
   whichever leaves the lowest build;
5. the exact engine then moves every part along its stack to the exact gap.

NumPy and the engine only.  Spacings are upper bounds on the grid of the height
maps, with a small margin, so step 5 starts from a valid layout.
"""

import math

import numpy as np

from .compact import Compactor

CELL = 1.5                 # mm, of the height maps
MARGIN = 0.25              # mm added to a spacing before the exact engine sets it
EXACT_MARGIN = 0.02        # mm kept over the exact spacing
MAX_ORDERED = 600          # parts up to which the order is searched pair by pair
MIN_NEST = 0.85            # a pair's spacing must be under this share of the part's thickness
FLIPS = [np.diag(d) for d in ((1.0, 1.0, 1.0), (-1.0, -1.0, 1.0), (1.0, -1.0, -1.0), (-1.0, 1.0, -1.0))]


class Pose:
    """One part in the family's frame: rotation R (mesh to frame, the stacking
    axis is the frame's third), centered on its box across the stack."""

    def __init__(self, mesh, R):
        self.mesh = mesh
        self.R = np.asarray(R, float)
        V = np.asarray(mesh.verts, float) @ self.R.T
        lo, hi = V.min(0), V.max(0)
        self.center = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0.0])
        self.lo, self.hi = lo - self.center, hi - self.center


def _surface_points(V, T, h):
    """Points on the triangles, no more than about h apart."""
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    e = np.maximum(np.maximum(np.linalg.norm(B - A, axis=1), np.linalg.norm(C - B, axis=1)), np.linalg.norm(A - C, axis=1))
    n = np.clip(np.ceil(e / h).astype(int), 1, 64)
    out = [V]
    for k in np.unique(n):
        s = n == k
        i, j = np.meshgrid(np.arange(k + 1), np.arange(k + 1), indexing='ij')
        keep = i + j <= k
        a, b = (i[keep] / k)[:, None, None], (j[keep] / k)[:, None, None]
        out.append((a * A[s][None] + b * B[s][None] + (1 - a - b) * C[s][None]).reshape(-1, 3))
    return np.concatenate(out)


def height_maps(pose, n, h=CELL):
    """(top, bottom) of a posed part on an n x n grid of cells h, part centered;
    cells it doesn't cover are -inf and +inf."""
    V = np.asarray(pose.mesh.verts, float) @ pose.R.T - pose.center
    P = _surface_points(V, np.asarray(pose.mesh.tris), h)
    ij = np.floor(P[:, :2] / h).astype(int) + n // 2
    ok = ((ij >= 0) & (ij < n)).all(1)
    top = np.full((n, n), -np.inf)
    bot = np.full((n, n), np.inf)
    np.maximum.at(top, (ij[ok, 0], ij[ok, 1]), P[ok, 2])
    np.minimum.at(bot, (ij[ok, 0], ij[ok, 1]), P[ok, 2])
    return top, bot


def grown(top, gap, h=CELL):
    """The top surface raised by a ball of radius gap: nothing whose bottom is
    above this comes closer than the gap.  A neighbor cell counts from its far
    side, so it stays an upper bound on the grid."""
    r = int(math.ceil(gap / h)) + 1
    out = top.copy()
    n = top.shape[0]
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            d = h * math.hypot(max(abs(dx) - 1, 0), max(abs(dy) - 1, 0))
            if d >= gap:
                continue
            lift = math.sqrt(gap * gap - d * d)
            sx = slice(max(dx, 0), n + min(dx, 0)), slice(max(dy, 0), n + min(dy, 0))
            ss = slice(max(-dx, 0), n + min(-dx, 0)), slice(max(-dy, 0), n + min(-dy, 0))
            out[sx] = np.maximum(out[sx], top[ss] + lift)
    return out


def spacings(tops, bots, gap):
    """Matrix S: S[a, b] is how far b's origin must sit above a's along the stack."""
    m = len(tops)
    G = np.stack([grown(t, gap) for t in tops])
    B = np.stack(bots)
    S = np.zeros((m, m))
    for a in range(m):
        D = G[a][None] - B
        D[~np.isfinite(D)] = -np.inf
        S[a] = D.reshape(m, -1).max(1)
    return S + MARGIN


def chain(S):
    """An order of the parts with small spacings: nearest neighbor from a few
    starts, improved by moving stretches (or-opt)."""
    m = len(S)
    if m > MAX_ORDERED:
        return list(range(m))
    C = S.copy()
    np.fill_diagonal(C, np.inf)
    best, cost = None, np.inf
    for s in range(0, m, max(1, m // 8)):
        left = np.ones(m, bool)
        left[s] = False
        o = [s]
        for _ in range(m - 1):
            row = np.where(left, C[o[-1]], np.inf)
            k = int(np.argmin(row))
            o.append(k)
            left[k] = False
        c = float(sum(C[a, b] for a, b in zip(o, o[1:])))
        if c < cost:
            best, cost = o, c
    o = best
    for _ in range(3):
        moved = False
        for i in range(m):
            k = o[i]
            rest = o[:i] + o[i + 1:]
            before = (C[o[i - 1], k] if i else 0.0) + (C[k, o[i + 1]] if i + 1 < m else 0.0) - \
                (C[o[i - 1], o[i + 1]] if 0 < i < m - 1 else 0.0)
            ins = np.empty(m)
            ins[0] = C[k, rest[0]]
            ins[-1] = C[rest[-1], k]
            r = np.array(rest)
            ins[1:-1] = C[r[:-1], k] + C[k, r[1:]] - C[r[:-1], r[1:]]
            j = int(np.argmin(ins))
            if ins[j] < before - 1e-6:
                rest.insert(j, k)
                o = rest
                moved = True
        if not moved:
            break
    return o


def _cut(order, S, poses, k):
    """order cut into k stacks of about equal length: [(members, positions)]."""
    pitch = [S[a, b] for a, b in zip(order, order[1:])]
    total = float(sum(pitch))
    out, start, run = [], 0, 0.0
    for i, p in enumerate(pitch):
        if run + p > total * (len(out) + 1) / k and len(out) < k - 1:
            out.append(order[start:i + 1])
            start = i + 1
            run += p
            continue
        run += p
    out.append(order[start:])
    res = []
    for ms in out:
        w = [0.0]
        for a, b in zip(ms, ms[1:]):
            w.append(w[-1] + S[a, b])
        res.append((ms, np.array(w)))
    return res


def _positions(ms, S):
    w = [0.0]
    for a, b in zip(ms, ms[1:]):
        w.append(w[-1] + S[a, b])
    return np.array(w)


def _cuts_by_part(order, S, poses):
    """Cuts that keep each part to stacks of its own (narrow parts don't widen
    the stacks of wide ones): one stack per part, then the longest split in
    two, again and again.  [] for a single part."""
    groups = {}
    for m in order:
        groups.setdefault(id(poses[m].mesh), []).append(m)
    if len(groups) < 2:
        return []
    runs = [list(g) for g in groups.values()]
    out = []
    while len(runs) <= len(order):
        out.append([(ms, _positions(ms, S)) for ms in runs])
        longest = max(range(len(runs)), key=lambda r: _length((runs[r], _positions(runs[r], S)), poses)[0])
        if len(runs[longest]) < 2:
            break
        ms = runs.pop(longest)
        half = (len(ms) + 1) // 2
        runs += [ms[:half], ms[half:]]
    return out


def _length(st, poses):
    ms, w = st
    lo = min(poses[m].lo[2] + w[i] for i, m in enumerate(ms))
    hi = max(poses[m].hi[2] + w[i] for i, m in enumerate(ms))
    return hi - lo, lo


def _shelves(stacks, poses, Q, room, gap):
    """Upright stacks side by side in rows, each as wide as its own parts:
    per stack {world axis: offset of the stack's axis from the volume's low
    corner (inside the wall gap)}, or None if they don't fit the floor."""
    foot = []
    for ms, _ in stacks:
        lo = np.min([np.minimum(Q @ poses[m].lo, Q @ poses[m].hi) for m in ms], axis=0)
        hi = np.max([np.maximum(Q @ poses[m].lo, Q @ poses[m].hi) for m in ms], axis=0)
        foot.append((lo, hi))
    out = [None] * len(stacks)
    x = y = row = 0.0
    for sn in sorted(range(len(stacks)), key=lambda n: -(foot[n][1][1] - foot[n][0][1])):
        lo, hi = foot[sn]
        w, d = hi[0] - lo[0], hi[1] - lo[1]
        if w > room[0] + 1e-9 or d > room[1] + 1e-9:
            return None
        if x > 0 and x + w > room[0] + 1e-9:
            x, y, row = 0.0, y + row + gap, 0.0
        if y + d > room[1] + 1e-9:
            return None
        out[sn] = {0: x - lo[0], 1: y - lo[1]}
        x += w + gap
        row = max(row, d)
    return out


def _axes():
    """Proper rotations of the frame whose third axis goes along +x, +y or +z."""
    out = []
    for ax in range(3):
        for u in range(3):
            if u == ax:
                continue
            v = 3 - ax - u
            for su in (1.0, -1.0):
                for sv in (1.0, -1.0):
                    Q = np.zeros((3, 3))
                    Q[ax, 2] = 1.0
                    Q[u, 0] = su
                    Q[v, 1] = sv
                    if np.linalg.det(Q) > 0:
                        out.append((ax, u, v, Q))
    return out


def plan(poses, S, order, vlo, vhi, gap, wall, max_z=None, turns='free'):
    """The lowest way to lay the chain in the volume: (height, world matrix per
    pose index, stacks, description), or None if it doesn't fit.  turns
    'z': stacks stand upright, turned about z only; 'fixed': not turned."""
    vlo, vhi = np.asarray(vlo, float), np.asarray(vhi, float)
    room = vhi - vlo - np.array([2 * wall, 2 * wall, 0.0])
    lo = np.min([p.lo for p in poses], axis=0)
    hi = np.max([p.hi for p in poses], axis=0)
    ext = hi - lo
    best = None
    tried = {}
    for ax, u, v, Q in _axes():
        if turns != 'free' and ax != 2:
            continue
        if turns == 'fixed' and not np.allclose(Q, np.eye(3)):
            continue
        eu, ev = ext[0], ext[1]
        shelf = None
        if ax == 2:
            # as many stacks as stand side by side, each as wide as its own
            # parts (a stack of narrow parts beside one of wide ones), the
            # most that fit giving the lowest build
            found = None
            cuts = [_cut(order, S, poses, k) for k in range(1, len(poses) + 1)]
            cuts += _cuts_by_part(order, S, poses)
            for cut in cuts:
                hk = max(_length(st, poses)[0] for st in cut)
                if hk > room[2] or (found is not None and hk >= found[1]):
                    continue
                offs = _shelves(cut, poses, Q, room, gap)
                if offs is not None:
                    found = (cut, hk, offs)
            if found is None:
                continue
            stacks, height, shelf = found
            layers = 1
        else:
            cu, cv = (u, v) if v == 2 else (v, u)   # cu across the floor, cv up
            ea, eb = (eu, ev) if v == 2 else (ev, eu)
            n_across = int((room[cu] + gap) // (ea + gap))
            if n_across < 1:
                continue
            key = (ax, cu)
            stacks = None
            for k in range(1, len(poses) + 1):
                got = tried.setdefault(key + (k,), _cut(order, S, poses, k))
                if max(_length(s, poses)[0] for s in got) <= room[ax]:
                    stacks = got
                    break
            if stacks is None:
                continue
            layers = int(math.ceil(len(stacks) / n_across))
            height = layers * eb + (layers - 1) * gap
            if height > room[2]:
                continue
        # (a tie goes to the build that leaves the most floor in one piece)
        score = (round(height, 3), len(stacks), ax)
        if best is None or score < best[0]:
            best = (score, ax, u, v, Q, stacks, height, layers, shelf)
    if best is None:
        return None
    _, ax, u, v, Q, stacks, height, layers, shelf = best
    M = {}
    # places across the stack, in world axes
    q_lo = np.minimum(Q @ lo, Q @ hi)
    q_ext = np.abs(Q @ ext)
    across = [a for a in range(3) if a != ax]
    if ax == 2:
        a0, a1 = across
        n0 = int((room[a0] + gap) // (q_ext[a0] + gap))
        spots = [(i, j) for i in range(n0) for j in range(int(math.ceil(len(stacks) / n0)))]
        lift = lambda spot: {a0: spot[0] * (q_ext[a0] + gap), a1: spot[1] * (q_ext[a1] + gap)}  # noqa: E731
    else:
        a0, a1 = (across[0], across[1]) if across[1] == 2 else (across[1], across[0])
        n0 = int((room[a0] + gap) // (q_ext[a0] + gap))
        spots = [(i, j) for j in range(layers) for i in range(n0)]
        lift = lambda spot: {a0: spot[0] * (q_ext[a0] + gap), a1: spot[1] * (q_ext[a1] + gap)}  # noqa: E731
    base = np.array([vlo[0] + wall, vlo[1] + wall, vlo[2]])
    for sn, (ms, w) in enumerate(stacks):
        offs = lift(spots[sn]) if shelf is None else shelf[sn]
        l0 = _length((ms, w), poses)[1]
        for i, m in enumerate(ms):
            T = np.zeros(3)
            for a, d in offs.items():
                T[a] = base[a] + d - (q_lo[a] if shelf is None else 0.0)
            T[ax] = base[ax] + (w[i] - l0)
            Rw = Q @ poses[m].R
            centered = T - Q @ poses[m].center
            X = np.eye(4)
            X[:3, :3] = Rw
            X[:3, 3] = centered
            M[m] = X
    what = '%d %s of %d' % (len(stacks), 'upright' if ax == 2 else 'on their side', len(poses))
    return height, M, [ms for ms, _ in stacks], what, ax


def exact_spacing(a, b, s0, gap):
    """The spacing of pose b above pose a at the exact gap, from the estimate s0
    (each pair on its own, so this doesn't grow with the build)."""
    Ma, Mb = np.eye(4), np.eye(4)
    Ma[:3, :3], Mb[:3, :3] = a.R, b.R
    Ma[:3, 3] = -a.center
    Mb[:3, 3] = -b.center + np.array([0.0, 0.0, s0])
    size = max(float(np.max(a.hi[:2] - a.lo[:2])), float(np.max(b.hi[:2] - b.lo[:2]))) + 10.0
    c = Compactor([(b.mesh, Mb), (a.mesh, Ma)], (-size, -size, -10.0 * size), (size, size, 10.0 * size), gap, 0.0)
    tops = [size, size]
    c.move(0, 2, tops)
    return float(c.M[0][2, 3])


def tighten(poses, M, stacks, ax, vlo, vhi, gap, wall, seconds=None):
    """Along every stack, first part first, to the exact gap; then every stack
    down and back as it fits.  Returns the new matrices."""
    import time
    deadline = None if seconds is None else time.perf_counter() + seconds
    idx = {m: j for j, m in enumerate(sorted(M))}
    c = Compactor([(poses[m].mesh, M[m]) for m in sorted(M)], vlo, vhi, gap, wall)
    tops = [c._extent(j)[1][2] - c.vlo[2] for j in range(c.n)]
    for ms in stacks:
        for m in ms[1:]:
            c.move(idx[m], ax, tops, deadline)
    return {m: c.M[idx[m]].copy() for m in M}


def build(meshes, rotations, vlo, vhi, gap, wall, tighten_s=None, log=None, turns='free'):
    """A stacked layout of these parts: {index: 4 x 4}, height and description,
    or None when they don't nest or don't fit.  rotations[i]: mesh to the
    family's frame, members already lined up with each other.  turns: 'free'
    (any), 'z' (about the vertical only: the stacks stand upright) or 'fixed'
    (rotations[i] is the part's own orientation, kept as it is)."""
    poses0 = [Pose(m, R) for m, R in zip(meshes, rotations)]
    # the thinnest axis of the frame is the stack's
    ext = np.mean([p.hi - p.lo for p in poses0], axis=0)
    ax = int(np.argmin(ext))
    if turns != 'free' and ax != 2:
        return None                    # (a stack along x or y would turn the parts over)
    if ax != 2:
        perm = {0: np.array([[0, 0, -1.0], [0, 1.0, 0], [1.0, 0, 0]]),
                1: np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])}[ax]
        rotations = [perm @ np.asarray(R, float) for R in rotations]
        poses0 = [Pose(m, R) for m, R in zip(meshes, rotations)]
    size = max(float(np.max(p.hi[:2] - p.lo[:2])) for p in poses0)
    n = int(math.ceil((size + 2 * gap) / CELL)) + 8
    # line each part up in the way it nests best on the first (a turn of the
    # frame by 180 degrees keeps a principal frame)
    ref_top, _ = height_maps(poses0[0], n)
    ref = grown(ref_top, gap)
    posed = []
    for i, (m, R) in enumerate(zip(meshes, rotations)):
        best = None
        for F in (FLIPS if turns == 'free' else FLIPS[:2] if turns == 'z' else FLIPS[:1]):
            p = Pose(m, F @ np.asarray(R, float))
            t, b = height_maps(p, n)
            with np.errstate(invalid='ignore'):
                D = ref - b
            D[~np.isfinite(D)] = -np.inf
            s = float(D.max())
            if best is None or s < best[0]:
                best = (s, p, t, b)
        posed.append(best)
    poses = [b[1] for b in posed]
    S = spacings([b[2] for b in posed], [b[3] for b in posed], gap)
    thick = float(np.mean([p.hi[2] - p.lo[2] for p in poses]))
    order = chain(S)
    mean_pitch = float(np.mean([S[a, b] for a, b in zip(order, order[1:])]))
    if log is not None:
        log.append('stack spacing %.2f mm on parts %.1f mm deep' % (mean_pitch, thick))
    if mean_pitch > MIN_NEST * (thick + gap):
        return None
    # the spacings of the chain at the exact gap, so the stacks are cut as they will really be
    for a, b in zip(order, order[1:]):
        S[a, b] = exact_spacing(poses[a], poses[b], float(S[a, b]), gap) + EXACT_MARGIN
    # and of a copy over another copy of the same part, posed alike: one
    # measurement per part covers every such pair (stacks of one part each)
    kinds = {}
    for m, p in enumerate(poses):
        kinds.setdefault((id(p.mesh), np.round(p.R, 9).tobytes()), []).append(m)
    if len(kinds) > 1:
        for ms in kinds.values():
            if len(ms) < 2:
                continue
            a, b = ms[0], ms[1]
            e = exact_spacing(poses[a], poses[b], float(S[a, b]), gap) + EXACT_MARGIN
            for x in ms:
                for y in ms:
                    if x != y:
                        S[x, y] = min(S[x, y], e)
    mean_pitch = float(np.mean([S[a, b] for a, b in zip(order, order[1:])]))
    if log is not None:
        log.append('stack spacing %.2f mm at the exact gap' % mean_pitch)
    got = plan(poses, S, order, vlo, vhi, gap, wall, turns=turns)
    if got is None:
        return None
    height, M, stacks, what, ax = got
    top = 0.0
    for m, X in M.items():
        V = np.asarray(meshes[m].verts, float) @ X[:3, :3].T + X[:3, 3]
        top = max(top, float(V[:, 2].max()) - float(vlo[2]))
    return {'matrices': M, 'height': top, 'what': what, 'stacks': stacks, 'order': order, 'pitch': mean_pitch}
