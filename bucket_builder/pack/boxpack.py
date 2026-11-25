# SPDX-License-Identifier: GPL-3.0-or-later
"""The box pass: a quick, exact first answer for easy builds (the owner, 2026-11-24).

Four copies of a socket that fit by their smallest boxes, 2 x 1 x 2, took the full search four
minutes and it still left one out: the voxel grid rounds every part up by about a cell, and with
376 mm of parts on a 380 mm axis that cell is the difference. So before the search: each part is
turned to its smallest bounding box (`minbox`), and the boxes are packed in real millimeters,
`gap` apart and `wall_gap` off the side walls, inside `pack_lo` / `pack_hi`. Boxes that keep their
distance keep their parts at that distance, so a box layout needs no further check.

Identical copies are tried as a grid in each of the box's turns (the fewest layers wins); a mix of
parts with an extreme-point heuristic from several starts. Only a layout that places every copy
counts: anything less is left to the full search, which then starts with the time it would have
had anyway.
"""

import itertools

import numpy as np

from . import minbox

_EPS = 1e-6


def _perms(mode, flips=False):
    """The axis permutations a part may take, as 3 x 3 rotations (det +1); with `flips` all 24
    quarter turns (the same boxes, but which way a face points matters for print quality)."""
    if mode == 'fixed':
        return [np.eye(3)]
    if mode == 'z':
        return [np.eye(3), np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])]
    out = []
    for p in itertools.permutations(range(3)):
        for signs in (itertools.product((1.0, -1.0), repeat=3) if flips else [(1.0, 1.0, 1.0)]):
            P = np.zeros((3, 3))
            for r, c in enumerate(p):
                P[r, c] = signs[r]
            if np.linalg.det(P) < 0:
                if flips:
                    continue
                P[2] = -P[2]
            out.append(P)
    return out


def _frame(part):
    """(R, posed vertices): the turn into the part's smallest box for its mode."""
    base = np.asarray(part.base, float)[:3, :3]
    V = np.asarray(part.mesh.verts, float) @ base.T
    if part.mode == 'free':
        R = minbox.rotation_min_volume(V)
    elif part.mode == 'z':
        R = _min_rect_z(V)
    else:
        R = np.eye(3)
    return R, V


def _min_rect_z(V):
    """A turn about z giving the smallest footprint rectangle."""
    best, bestR = None, np.eye(3)
    for deg in np.arange(0.0, 90.0, 1.0):
        a = np.radians(deg)
        c, s = np.cos(a), np.sin(a)
        R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        P = V[:, :2] @ R[:2, :2].T
        area = float(np.prod(P.max(0) - P.min(0)))
        if best is None or area < best - 1e-9:
            best, bestR = area, R
    return bestR


class _Kind:
    def __init__(self, i, part, face_cost=None):
        R, V = _frame(part)
        self.i = i
        self.turns = []                      # (rotation applied after base, box size, box min corner)
        base = np.asarray(part.base, float)[:3, :3]
        cost = []
        for P in _perms(part.mode, flips=face_cost is not None):
            Q = P @ R
            W = V @ Q.T
            lo, hi = W.min(axis=0), W.max(axis=0)
            self.turns.append((Q @ base, hi - lo, lo))
            if face_cost is not None:
                cost.append(face_cost(i, Q @ base))
        if cost:
            # only the turns that leave the least flat area facing up (a base down, not up: PQ-07)
            least = min(cost)
            self.turns = [t for t, c in zip(self.turns, cost) if c <= least + 1e-6]


def _matrix(L, lo_box, corner):
    M = np.eye(4)
    M[:3, :3] = L
    M[:3, 3] = np.asarray(corner, float) - lo_box
    return M


def _grid(kind, n, lo, hi, gap):
    """Identical copies on a grid: {copy index: 4 x 4} and height, or None."""
    best = None
    room = hi - lo
    for L, d, blo in kind.turns:
        if np.any(d > room + _EPS):
            continue
        cnt = np.floor((room + gap + _EPS) / (d + gap)).astype(int)
        per_layer = int(cnt[0] * cnt[1])
        if per_layer == 0 or per_layer * int(cnt[2]) < n:
            continue
        layers = -(-n // per_layer)
        height = layers * d[2] + (layers - 1) * gap
        if best is None or height < best[0] - _EPS:
            best = (height, L, d, blo, cnt)
    if best is None:
        return None
    height, L, d, blo, cnt = best
    out, boxes = {}, {}
    for c in range(n):
        z, r = divmod(c, int(cnt[0] * cnt[1]))
        y, x = divmod(r, int(cnt[0]))
        corner = lo + np.array([x, y, z]) * (d + gap)
        out[c] = _matrix(L, blo, corner)
        boxes[c] = (corner, d)
    return out, float(lo[2] + height), boxes


def _extreme_points(items, lo, hi, gap, prefer):
    """items: [(copy key, kind)], largest first. Places each box at the lowest free extreme point
    (z, then y, then x) in the first turn of `prefer` order that fits. Returns ({key: 4 x 4}, top) or
    None if a copy is left over."""
    placed = []                              # (min, max) of placed boxes, max grown by gap
    points = [lo.copy()]
    out, boxes = {}, {}
    top = lo[2]
    for key, kind in items:
        turns = sorted(kind.turns, key=lambda t: prefer(t[1]))
        done = False
        for pi in sorted(range(len(points)), key=lambda j: (points[j][2], points[j][1], points[j][0])):
            p = points[pi]
            for L, d, blo in turns:
                a, b = p, p + d
                if np.any(b > hi + _EPS):
                    continue
                if any(np.all(a < qb - _EPS) and np.all(b > qa - _EPS) for qa, qb in placed):
                    continue
                placed.append((a, b + gap))
                out[key] = _matrix(L, blo, a)
                boxes[key] = (a.copy(), d)
                top = max(top, float(b[2]))
                del points[pi]
                for ax in range(3):
                    q = a.copy()
                    q[ax] = b[ax] + gap
                    if np.all(q < hi - _EPS):
                        points.append(q)
                done = True
                break
            if done:
                break
        if not done:
            return None
    return out, top, boxes


def pack(parts, copies, pack_lo, pack_hi, gap, wall_gap=0.0, face_cost=None):
    """Every copy placed by its box, or None. Returns {'M': {(part, copy): 4 x 4 mm}, 'height': mm,
    'what': text}. face_cost(part, 3 x 3) -> a cost of a turn (flat area facing up), if it counts."""
    lo = np.asarray(pack_lo, float) + np.array([wall_gap, wall_gap, 0.0])
    hi = np.asarray(pack_hi, float) - np.array([wall_gap, wall_gap, 0.0])
    if np.any(hi <= lo):
        return None
    kinds = {i: _Kind(i, parts[i], face_cost) for i in sorted({q[0] for q in copies})}
    if len(kinds) == 1:
        (i, kind), = kinds.items()
        qs = [q for q in copies if q[0] == i]
        got = _grid(kind, len(qs), lo, hi, gap)
        if got is not None:
            M, top, B = got
            return {'M': {q: M[n] for n, q in enumerate(qs)}, 'height': top, 'what': 'boxes on a grid',
                    'boxes': {q: B[n] for n, q in enumerate(qs)}, 'limit': float(hi[2])}
    vol = {i: float(np.prod(k.turns[0][1])) for i, k in kinds.items()}
    items = sorted(((q, kinds[q[0]]) for q in copies), key=lambda t: -vol[t[0][0]])
    best = None
    # several starts: lowest box first, then the turn that leaves most room along x or y
    for prefer in (lambda d: (d[2], -d[0]), lambda d: (d[2], -d[1]), lambda d: (-d[2],), lambda d: (d[2],)):
        got = _extreme_points(items, lo, hi, gap, prefer)
        if got is not None and (best is None or got[1] < best[1] - _EPS):
            best = got
    if best is None:
        return None
    return {'M': best[0], 'height': best[1], 'what': 'boxes', 'boxes': best[2], 'limit': float(hi[2])}


def stretch(got, need):
    """Lift the boxes of a `pack` result so that they fill `need` mm of height from the lowest box:
    each box's low corner rises in proportion to its height above the lowest (boxes apart in z stay
    at least as far apart, those apart in x or y are not moved closer). Changes `got` in place and
    returns True, or False when nothing can rise (one layer) or it would pass `got['limit']`."""
    boxes = got['boxes']
    z0 = min(a[2] for a, _ in boxes.values())
    if need <= got['height'] - z0 + _EPS:
        return True
    def top(f):
        return max(z0 + f * (a[2] - z0) + d[2] for a, d in boxes.values())
    if top(1e6) < z0 + need - _EPS:
        return False                          # (a single layer: lifting cannot spread it)
    lo_f, hi_f = 1.0, 2.0
    while top(hi_f) < z0 + need:
        hi_f *= 2.0
    for _ in range(60):
        mid = 0.5 * (lo_f + hi_f)
        lo_f, hi_f = (mid, hi_f) if top(mid) < z0 + need else (lo_f, mid)
    f = hi_f
    if top(f) > got['limit'] + _EPS:
        return False
    for k, (a, d) in boxes.items():
        dz = (f - 1.0) * (a[2] - z0)
        got['M'][k][2, 3] += dz
        boxes[k] = (a + np.array([0.0, 0.0, dz]), d)
    got['height'] = top(f)
    return True
