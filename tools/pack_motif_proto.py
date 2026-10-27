# SPDX-License-Identifier: GPL-3.0-or-later
# Run from the repository root: python3 tools/pack_motif_proto.py
"""Prototype: double lattices (every other copy turned 180 degrees) and the
plain lattice, on shapes chosen to need them.  Counts copies in a 5600 volume."""
import sys, time, math
sys.path.insert(0, 'bucket_builder')
import numpy as np
from pack import job as J, pattern, referee, synthetic as S
from pack.problem import Part, Settings

VOL = (380.0, 284.0, 380.0)


def prism(poly, depth, name):
    """Convex polygon (x, z) extruded along y."""
    P = np.asarray(poly, float)
    n = len(P)
    v = [(x, 0, z) for x, z in P] + [(x, depth, z) for x, z in P]
    f = []
    for i in range(1, n - 1):
        f += [(0, i + 1, i), (n, n + i, n + i + 1)]
    for i in range(n):
        j = (i + 1) % n
        f += [(i, j, n + j), (i, n + j, n + i)]
    m = S._mesh(np.array(v), np.array(f), name)
    # orient outward (fan from centroid test)
    c = m.verts.mean(0)
    V = m.verts[m.tris]
    nrm = np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0])
    flip = ((V.mean(1) - c) * nrm).sum(1) < 0
    t = m.tris.copy()
    t[flip] = t[flip][:, ::-1]
    return S._mesh(m.verts, t, name)


def box_at(lo, hi):
    b = S.box(*(np.array(hi) - lo))
    return S.transformed(b, np.eye(3), np.array(lo) + (np.array(hi) - lo) / 2)


def shapes():
    out = []
    out.append(('wedge', prism([(0, 0), (60, 0), (0, 25)], 30, 'wedge')))                    # door stop
    half = [(30 * math.cos(a), 30 * math.sin(a)) for a in np.linspace(0, math.pi, 25)]
    out.append(('half disc', prism(half, 6, 'half disc')))                                       # D plate
    out.append(('gusset', prism([(0, 0), (50, 0), (0, 50)], 6, 'gusset')))                       # triangle plate
    out.append(('trapezoid', prism([(0, 0), (60, 0), (45, 25), (15, 25)], 30, 'trapezoid')))      # keystone
    out.append(('tee', S.merge([box_at((0, 0, 0), (60, 6, 12)), box_at((24, 0, 12), (36, 6, 50))], 'tee')))
    out.append(('L plate', S.merge([box_at((0, 0, 0), (50, 6, 12)), box_at((0, 0, 12), (12, 6, 50))], 'L plate')))
    out.append(('angle', S.merge([box_at((0, 0, 0), (30, 60, 4)), box_at((0, 0, 4), (4, 60, 30))], 'angle')))
    head = S.cylinder(16, 4, 32)
    shank = S.transformed(S.cylinder(5, 30, 24), np.eye(3), (0, 0, 17))
    out.append(('peg', S.merge([head, shank], 'peg')))
    out.append(('chip', S.pringle()))
    return out


def rot180(ax):
    return np.diag([1.0 if i == ax else -1.0 for i in range(3)])


class G:          # a part grid stand-in for a motif of two poses
    pass


def dimer_grid(pa, pb, t):
    o = np.minimum(0, t)
    hi = np.maximum(np.array(pa.solid.shape), t + np.array(pb.solid.shape))
    shp = tuple(hi - o)
    S_ = np.zeros(shp, bool)
    K = np.zeros(shp, bool)
    for p, d in ((pa, -o), (pb, t - o)):
        sl = tuple(slice(a, a + n) for a, n in zip(d, p.solid.shape))
        S_[sl] |= p.solid
        K[sl] |= p.kernel
    g = G()
    g.solid, g.kernel = S_, K
    idx = [np.nonzero(S_.any(axis=tuple(a for a in range(3) if a != ax)))[0] for ax in range(3)]
    g.s_lo = np.array([i[0] for i in idx])
    g.s_hi = np.array([i[-1] + 1 for i in idx])
    return g, -o, t - o


def contacts(free, off, n_dir=400):
    """Nearest free offset along directions over the sphere."""
    i = np.arange(n_dir) + 0.5
    phi = np.arccos(1 - 2 * i / n_dir)
    th = math.pi * (1 + 5 ** 0.5) * i
    D = np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)
    R = int(max(free.shape)) + 2
    r = np.arange(0, 2 * R) * 0.5
    out = set()
    for d in D:
        P = np.rint(r[:, None] * d).astype(np.int64)
        ok = pattern._free_at(free, off, P)
        if ok.any():
            out.add(tuple(P[int(np.argmax(ok))]))
    return np.array(sorted(out), np.int64)


def best_motif(j, i, ka, kb, q, n_t=8):
    pa, pb = j.g.pose(i, ka).grid, j.g.pose(i, kb).grid
    S1 = pa.solid.astype(float)
    K2 = pb.kernel[::-1, ::-1, ::-1].astype(float)
    full = [a + b - 1 for a, b in zip(S1.shape, K2.shape)]
    s = [pattern.fast_size(n) for n in full]
    conv = pattern.irfftn(pattern.rfftn(S1, s) * pattern.rfftn(K2, s), s)[:full[0], :full[1], :full[2]]
    free, off = conv < 0.5, np.array(K2.shape) - 1          # free[t + off]: B at t clear of A at 0
    T = contacts(free, off)
    # compactness: the union's bounding box volume
    lo = np.minimum(pa.s_lo, pb.s_lo + T)
    hi = np.maximum(pa.s_hi, pb.s_hi + T)
    vol = np.prod(hi - lo, axis=1)
    best = None
    for t in T[np.argsort(vol, kind='stable')[:n_t]]:
        g, da, db = dimer_grid(pa, pb, t)
        L = pattern.lattice(g)
        if L is None:
            continue
        lay = j._fresh()
        cells, top = pattern.place(lay.bucket, g, L, q)
        n = 2 * len(cells)
        if best is None or n > best[0]:
            best = (n, t, L, cells, da, db, lay)
    return best


def main():
    s = Settings(vlo=(0, 0, 0), vhi=VOL, gap=2.0, wall_gap=2.0, max_density=None, time_budget=None)
    inner = np.array(VOL) - 4
    print('%-10s %7s %7s %7s  %s' % ('shape', 'array', 'lattice', 'double', 'notes'))
    for name, mesh in shapes():
        j = J.Job([Part(name, mesh, 10 ** 6, 'free')], s, threads=1, workers=1)
        j.cur = j._fresh()
        ks = sorted(j._distinct(0), key=lambda k: (j._z_extent(0, k), k))
        arr = 0
        for k in ks:
            ext = np.ptp(np.asarray(mesh.verts, float) @ np.asarray(j.g.orients(0)[k], float).T, axis=0)
            arr = max(arr, int(np.prod(np.floor((inner + 2) / (ext + 2)))))
        t0 = time.perf_counter()
        single = (0, None)
        for k in ks:
            cells, _ = j._pattern_cells(0, k, j._fresh(), 10 ** 6)
            if cells is not None and len(cells) > single[0]:
                single = (len(cells), k)
        t1 = time.perf_counter()
        O = j.g.orients(0)
        dbl = None
        for ka in ks[:6]:
            for ax in range(3):
                B = rot180(ax) @ O[ka]
                kb = next((k for k, A in enumerate(O) if np.allclose(A, B, atol=1e-9)), None)
                if kb is None:
                    continue
                r = best_motif(j, 0, ka, kb, 10 ** 6)
                if r and (dbl is None or r[0] > dbl[0]):
                    dbl = r + (ka, kb, ax)
        t2 = time.perf_counter()
        # exact check of a corner of the double lattice
        note = ''
        if dbl:
            n, t, L, cells, da, db, lay, ka, kb, ax = dbl
            items = []
            for c, cell in enumerate(cells[:30]):
                for kk, d in ((ka, da), (kb, db)):
                    b = np.asarray(cell) + d
                    p = j.g.pose(0, kk)
                    M = np.eye(4); M[:3, :3] = p.A; M[:3, 3] = lay.bucket.translation(p.grid, b)
                    items.append(('%s#%d' % (name, len(items)), mesh, M))
            v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
            note = 'turn about %s, t %s, referee %s' % ('xyz'[ax], list(t), 'clean' if v.ok else 'BAD %s' % v)
        print('%-10s %7d %7d %7d  single %.1f s, double %.1f s  %s' % (
            name, arr, single[0], 0 if dbl is None else dbl[0], t1 - t0, t2 - t1, note), flush=True)


main()
