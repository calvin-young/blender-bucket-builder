# SPDX-License-Identifier: GPL-3.0-or-later
"""An independent check of a layout, sharing no code with the engine
(`bucket_builder/core/`) or the referee built on it (docs/AUDIT.md, A1).

Slow and simple on purpose.  Every part's surface is sampled: all its
corners, and points scattered over each triangle about `spacing` apart.
Those are real points of the surfaces, so the distance between two
samples is never less than the true distance between the parts: when two
samples of different parts are closer than the gap, the parts certainly
are too.  That direction is what matters, a referee that calls a bad
layout clean.  The other direction is bounded by the sampling: two parts
closer than the gap by less than about `spacing` may be missed, so a
comparison with the referee allows that much.

A part wholly inside another (no surfaces near) is found by a ray parity
test of one of its corners, for pairs whose boxes nest.

NumPy only: the nearest samples are found through a grid of cells.

    python3 tests/ref_slow.py layout.3mf --gap 1.0 [--spacing 0.5]
"""

import argparse
import math
import os
import sys

import numpy as np



class Grid:
    """Points hashed into cubic cells; nearest distance from other points, up to a bound."""

    def __init__(self, P, cell):
        self.cell = float(cell)
        self.P = P
        c = np.floor(P / self.cell).astype(np.int64)
        self.lo = c.min(axis=0) - 1
        c -= self.lo
        self.dims = c.max(axis=0) + 2
        key = (c[:, 0] * self.dims[1] + c[:, 1]) * self.dims[2] + c[:, 2]
        order = np.argsort(key, kind='stable')
        self.key = key[order]
        self.Ps = P[order]

    def nearest(self, Q, bound, chunk=4_000_000):
        """Smallest distance from any point of Q to the grid's points (inf if none within `bound`)."""
        if len(Q) == 0:
            return math.inf
        c = np.floor(Q / self.cell).astype(np.int64) - self.lo
        best = math.inf
        r = int(math.ceil(bound / self.cell))
        offs = [(x, y, z) for x in range(-r, r + 1) for y in range(-r, r + 1) for z in range(-r, r + 1)]
        for dx, dy, dz in offs:
            cc = c + (dx, dy, dz)
            ok = np.all((cc >= 0) & (cc < self.dims), axis=1)
            if not ok.any():
                continue
            qi = np.flatnonzero(ok)
            k = (cc[qi, 0] * self.dims[1] + cc[qi, 1]) * self.dims[2] + cc[qi, 2]
            a = np.searchsorted(self.key, k, 'left')
            b = np.searchsorted(self.key, k, 'right')
            n = b - a
            m = n > 0
            qi, a, n = qi[m], a[m], n[m]
            if len(qi) == 0:
                continue
            # every (query, point) pair of these cells, a block at a time
            cum = np.cumsum(n)
            start = 0
            while start < len(qi):
                stop = int(np.searchsorted(cum, (cum[start - 1] if start else 0) + chunk, 'right'))
                stop = max(stop, start + 1)
                nn = n[start:stop]
                rep_q = np.repeat(qi[start:stop], nn)
                base = np.repeat(a[start:stop] - np.concatenate([[0], np.cumsum(nn)[:-1]]), nn)
                idx = base + np.arange(len(rep_q))
                d2 = ((Q[rep_q] - self.Ps[idx]) ** 2).sum(axis=1)
                best = min(best, float(d2.min()))
                start = stop
        best = math.sqrt(best) if best < math.inf else math.inf
        return best if best <= bound else math.inf


def world_tris(mesh, M):
    V = np.asarray(mesh.verts, float) @ np.asarray(M, float)[:3, :3].T + np.asarray(M, float)[:3, 3]
    return V, V[np.asarray(mesh.tris)]


def samples(V, T, spacing, rng):
    """Corners plus points scattered over the triangles, about `spacing` apart."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    n = np.floor(area / (spacing * spacing) * 2.0 + rng.random(len(T))).astype(np.int64)
    pts = [V]
    if n.sum():
        idx = np.repeat(np.arange(len(T)), n)
        r1, r2 = rng.random(len(idx)), rng.random(len(idx))
        s = np.sqrt(r1)
        u, v = 1 - s, s * (1 - r2)
        w = s * r2
        pts.append(u[:, None] * a[idx] + v[:, None] * b[idx] + w[:, None] * c[idx])
    # (edges too: two parts that touch edge to edge meet there first)
    e = np.concatenate([b - a, c - b, a - c])
    o = np.concatenate([a, b, c])
    ln = np.linalg.norm(e, axis=1)
    k = np.floor(ln / spacing).astype(np.int64)
    if k.sum():
        idx = np.repeat(np.arange(len(e)), k)
        t = rng.random(len(idx))
        pts.append(o[idx] + t[:, None] * e[idx])
    return np.concatenate(pts)


def inside(p, T):
    """Ray parity of point p against triangles T (n, 3, 3), along a fixed odd direction."""
    d = np.array([0.3127, 0.5381, 0.7829])           # (no symmetry: never along an edge or a diagonal of a box)
    d = d / np.linalg.norm(d)
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    e1, e2 = b - a, c - a
    h = np.cross(d, e2)
    det = np.einsum('ij,ij->i', e1, h)
    ok = np.abs(det) > 1e-12
    f = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = p - a
    u = f * np.einsum('ij,ij->i', s, h)
    q = np.cross(s, e1)
    v = f * (q @ d)
    t = f * np.einsum('ij,ij->i', e2, q)
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
    return int(hit.sum()) % 2 == 1


class Verdict:
    def __init__(self):
        self.close = []        # (i, j, sampled distance) closer than the gap
        self.enclosed = []     # (inner, outer)
        self.outside = []      # parts reaching past the volume
        self.near_wall = []
        self.dist = {}         # (i, j) -> smallest sampled distance, for pairs whose boxes come within the gap

    @property
    def ok(self):
        return not (self.close or self.enclosed or self.outside or self.near_wall)

    def pairs(self):
        return {(min(i, j), max(i, j)) for i, j, _ in self.close} | \
            {(min(i, j), max(i, j)) for i, j in self.enclosed}

    def __repr__(self):
        return 'slow check: %d pairs closer than the gap, %d enclosed, %d outside, %d near a wall' % (
            len(self.close), len(self.enclosed), len(self.outside), len(self.near_wall))


def check(items, vlo, vhi, gap, wall_gap=0.0, spacing=0.5, seed=0):
    """items: [(name, mesh, 4 x 4)].  Pairs whose samples come closer than
    `gap` (certainly too close), parts wholly inside another, parts past
    the volume or nearer a side wall than `wall_gap` (by their corners)."""
    rng = np.random.default_rng(seed)
    vlo, vhi = np.asarray(vlo, float), np.asarray(vhi, float)
    size = float(np.max(vhi - vlo))
    tol = 1e-6 * size          # (the referee's own allowance below the gap)
    out = Verdict()
    parts = []
    for i, (_, mesh, M) in enumerate(items):
        V, T = world_tris(mesh, M)
        lo, hi = V.min(axis=0), V.max(axis=0)
        if np.any(lo < vlo - tol) or np.any(hi > vhi + tol):
            out.outside.append(i)
        w = np.array([wall_gap, wall_gap, 0.0])
        if wall_gap > 0 and (np.any(lo < vlo + w - tol) or np.any(hi > vhi - w + tol)):
            out.near_wall.append(i)
        parts.append(dict(V=V, T=T, lo=lo, hi=hi, P=None, tree=None))
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            if np.any(a['lo'] > b['hi'] + gap) or np.any(b['lo'] > a['hi'] + gap):
                continue
            for p in (a, b):
                if p['P'] is None:
                    p['P'] = samples(p['V'], p['T'], spacing, rng)
                    p['tree'] = Grid(p['P'], max(gap, 4 * spacing) / 2)
            # only the samples of a that lie near b's box can be near b
            m = np.all((a['P'] >= b['lo'] - gap) & (a['P'] <= b['hi'] + gap), axis=1)
            d = math.inf
            if m.any():
                d = b['tree'].nearest(a['P'][m], gap)
            out.dist[(i, j)] = d
            if d < gap - tol:
                out.close.append((i, j, d))
                continue
            for inner, outer in ((a, b), (b, a)):
                if np.all(inner['lo'] >= outer['lo']) and np.all(inner['hi'] <= outer['hi']):
                    if inside(inner['V'][0], outer['T']):
                        out.enclosed.append((i, j) if inner is a else (j, i))
    return out


def compare(items, vlo, vhi, gap, wall_gap=0.0, spacing=0.5, timeout=600.0):
    """The slow check against the referee: (problems, slow verdict, referee pairs).
    `problems` lists every disagreement:
      * a pair the slow check finds too close that the referee passed: the
        referee is wrong (samples are real points);
      * a pair the referee faults whose samples are all at least
        gap + spacing apart, and which is not one inside the other: the
        sampling would have seen it, so one of the two is wrong."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, '..', 'bucket_builder'))
    from pack import referee
    slow = check(items, vlo, vhi, gap, wall_gap, spacing)
    w = referee._world(items, np.asarray(vlo, float), np.asarray(vhi, float), gap, wall_gap, timeout)
    ref = set()
    for key in w.viol:
        a, b = w.uid(key[0]), w.uid(key[1])
        ref.add((min(a, b), max(a, b)))
    problems = []
    for pr in sorted(slow.pairs() - ref):
        problems.append('referee passed pair %d %d, but samples are %.3f mm apart' % (pr + (slow.dist.get(pr, 0.0),)))
    enclosed = slow.pairs()
    for pr in sorted(ref - enclosed):
        d = slow.dist.get(pr, math.inf)
        if d >= gap + spacing:
            problems.append('referee faults pair %d %d, but no samples come within %.3f mm (closest %.3f)' % (
                pr + (gap + spacing, d)))
    return problems, slow, ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--gap', type=float, required=True)
    ap.add_argument('--wall-gap', type=float, default=0.0)
    ap.add_argument('--spacing', type=float, default=0.5)
    ap.add_argument('--volume', type=float, nargs=3, default=(380.0, 284.0, 380.0))
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, '..', 'bucket_builder'))
    from pack import meshio
    sc = meshio.read(a.file)
    items = [('p%d' % k, it.mesh, it.matrix) for k, it in enumerate(sc.items)]
    problems, slow, ref = compare(items, (0, 0, 0), a.volume, a.gap, a.wall_gap, a.spacing)
    print('%r; the referee faults %d pairs' % (slow, len(ref)))
    for p in problems:
        print('  DISAGREE: ' + p)
    print('AGREE' if not problems else 'DISAGREE')
    for i, j, d in slow.close:
        print('  pair %d %d: samples %.3f mm apart' % (i, j, d))
    for i, j in slow.enclosed:
        print('  part %d inside part %d' % (i, j))
    sys.exit(0 if not problems else 1)


if __name__ == '__main__':
    main()
