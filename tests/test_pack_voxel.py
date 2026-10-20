"""Checks pack.voxel: conservative surfaces and solids, cavities, dilation
against brute force, and that the NumPy and SciPy paths agree.

    python3 tests/test_pack_voxel.py
"""

import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))
sys.path.insert(0, HERE)

import meshes  # noqa: E402
from pack import voxel  # noqa: E402

FAILS = []
RNG = np.random.default_rng(11)
SCIPY = voxel._ndi


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def numpy_only(fn, *a, **k):
    old = voxel._ndi
    voxel._ndi = None
    try:
        return fn(*a, **k)
    finally:
        voxel._ndi = old


def rotation(rng):
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1
    return q


def cells_of(points, origin, h):
    return np.floor((points - origin) / h).astype(int)


def inside_points(kind, n, rng):
    """Random points inside a test solid (in its own frame)."""
    if kind == 'sphere':
        p = rng.normal(size=(n, 3))
        p *= (rng.random(n) ** (1 / 3) * 9.97 / np.linalg.norm(p, axis=1))[:, None]
        return p
    if kind == 'box':
        return (rng.random((n, 3)) - 0.5) * np.array([12.0, 7.0, 5.0])
    raise ValueError(kind)


def surface_points(v, f, n, rng):
    P = v[f]
    a = np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)
    i = rng.choice(len(f), n, p=a / a.sum())
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)
    return (1 - s)[:, None] * P[i, 0] + (s * (1 - r2))[:, None] * P[i, 1] + (s * r2)[:, None] * P[i, 2]


def cup(R=10.0, r=7.0, H=20.0, floor=3.0, segs=48):
    """A closed cup: outer cylinder, inner cylinder open at the top, joined by
    a ring at the rim.  Its cavity is open to the outside."""
    ang = np.linspace(0, 2 * np.pi, segs, endpoint=False)
    c, s = np.cos(ang), np.sin(ang)
    v = np.concatenate([
        np.stack([R * c, R * s, np.zeros(segs)], 1),           # 0: outer bottom
        np.stack([R * c, R * s, np.full(segs, H)], 1),         # segs: outer top
        np.stack([r * c, r * s, np.full(segs, H)], 1),         # 2 segs: inner top
        np.stack([r * c, r * s, np.full(segs, floor)], 1),     # 3 segs: inner bottom
        [[0, 0, 0], [0, 0, floor]]])
    cb, cf = 4 * segs, 4 * segs + 1
    f = []
    for i in range(segs):
        j = (i + 1) % segs
        ob, ot, it, ib = i, segs + i, 2 * segs + i, 3 * segs + i
        ob2, ot2, it2, ib2 = j, segs + j, 2 * segs + j, 3 * segs + j
        f += [[ob, ob2, ot2], [ob, ot2, ot]]          # outer wall
        f += [[ot, ot2, it2], [ot, it2, it]]          # rim
        f += [[it, it2, ib2], [it, ib2, ib]]          # inner wall (facing in)
        f += [[ib, ib2, cf]]                          # cavity floor (up)
        f += [[ob2, ob, cb]]                          # bottom (down)
    return v, np.array(f)


def test_conservative():
    print('every point of a part lies in a solid cell')
    for kind, vf in (('sphere', meshes.uv_sphere(10.0, 32, 16)), ('box', meshes.box((12.0, 7.0, 5.0)))):
        v, f = np.asarray(vf[0], float), np.asarray(vf[1])
        for h in (1.0, 0.7, 2.5):
            A = rotation(RNG) * RNG.uniform(0.6, 1.6)
            g = voxel.voxelize(v, f, A, h, 0.0)
            P = inside_points(kind, 4000, RNG)
            if kind == 'sphere':
                # (the UV sphere is inscribed in the round one: keep points inside the mesh)
                P *= 0.985
            S = surface_points(v, f, 4000, RNG)
            ok = True
            for pts in (P, S):
                c = cells_of(pts @ A.T, g.origin, h)
                ok &= bool(g.solid[c[:, 0], c[:, 1], c[:, 2]].all())
            true_vol = abs(np.linalg.det(A)) * (4 / 3 * math.pi * 10 ** 3 * 0.99 if kind == 'sphere' else 12 * 7 * 5)
            ratio = g.n_solid * h ** 3 / true_vol
            check(ok and 1.0 <= ratio < 1.0 + 9.0 * h / (abs(np.linalg.det(A)) ** (1 / 3) * 5),
                  '%s at h=%.1f: points covered, solid %.2f x the volume' % (kind, h, ratio))


def test_cloud():
    print('large meshes: the cloud path is conservative and close to the exact one')
    v, f = meshes.uv_sphere(10.0, 64, 32)
    v, f = np.asarray(v, float), np.asarray(f)
    for h in (1.0, 0.7):
        for scale in (1.0, 1.7):
            A = rotation(RNG) * scale
            cl = voxel.Cloud(v, f, voxel.cloud_cell(h, scale))
            g = voxel.voxelize(v, f, A, h, 0.0, cloud=cl)
            ref = voxel.voxelize(v, f, A, h, 0.0)
            S = surface_points(v, f, 4000, RNG)
            P = inside_points('sphere', 4000, RNG) * 0.985
            ok = True
            for pts in (P, S):
                c = cells_of(pts @ A.T, g.origin, h)
                ok &= bool(g.solid[c[:, 0], c[:, 1], c[:, 2]].all())
            check(ok and ref.n_solid <= g.n_solid <= 1.6 * ref.n_solid,
                  'h=%.1f, scale %.1f: points covered; %d cells against %d from the triangles'
                  % (h, scale, g.n_solid, ref.n_solid))


def test_soup():
    print('turned pieces cut once (a Soup) cover the part as well')
    for kind, vf in (('sphere', meshes.uv_sphere(10.0, 32, 16)), ('box', meshes.box((12.0, 7.0, 5.0)))):
        v, f = np.asarray(vf[0], float), np.asarray(vf[1])
        base = rotation(RNG) * RNG.uniform(0.7, 1.4)
        for h in (1.0, 0.7):
            soup = voxel.Soup(v, f, base, h)
            ok = soup.ok
            for _ in range(3):
                A = rotation(RNG) @ base
                g = voxel.voxelize(v, f, A, h, 0.0, align='min', soup=soup)
                ref = voxel.voxelize(v, f, A, h, 0.0, align='min')
                P = inside_points(kind, 3000, RNG) * (0.985 if kind == 'sphere' else 1.0)
                for pts in (P, surface_points(v, f, 3000, RNG)):
                    c = cells_of(pts @ A.T, g.origin, h)
                    ok &= bool(g.solid[c[:, 0], c[:, 1], c[:, 2]].all())
                ok &= abs(g.n_solid - ref.n_solid) <= 0.01 * ref.n_solid + 4
            check(ok, '%s at h=%.1f: every point covered, as many cells as cutting each time' % (kind, h))


def test_cavities():
    print('open cavities stay empty, sealed voids are solid')
    v, f = cup()
    g = voxel.voxelize(v, f, np.eye(3), 1.0, 0.0)
    # a point in the middle of the cavity
    c = cells_of(np.array([[0.0, 0.0, 12.0]]), g.origin, 1.0)[0]
    check(not g.solid[tuple(c)], 'the inside of a cup is free')
    c = cells_of(np.array([[8.5, 0.0, 10.0]]), g.origin, 1.0)[0]
    check(g.solid[tuple(c)], 'its wall is solid')
    # hollow sphere: outer shell and a reversed inner shell (a sealed void)
    vo, fo = meshes.uv_sphere(10.0, 32, 16)
    vi, fi = meshes.uv_sphere(6.0, 32, 16)
    v2 = np.concatenate([vo, vi])
    f2 = np.concatenate([fo, fi[:, ::-1] + len(vo)])
    g = voxel.voxelize(v2, f2, np.eye(3), 1.0, 0.0)
    c = cells_of(np.zeros((1, 3)), g.origin, 1.0)[0]
    check(g.solid[tuple(c)], 'the sealed void of a hollow ball counts as solid')
    # an open box (one face missing) is closed when asked
    v, f = meshes.grid_box((10.0, 10.0, 10.0), 2)
    f_open = np.asarray(f)[2:]                    # a hole of one 5 mm square
    g_open = voxel.voxelize(v, f_open, np.eye(3), 1.0, 0.0)
    g_closed = voxel.voxelize(v, f_open, np.eye(3), 1.0, 0.0, close_cells=3)
    c = cells_of(np.zeros((1, 3)), g_open.origin, 1.0)[0]
    check(not g_open.solid[tuple(c)], 'an open box without closing is hollow (the fill runs in)')
    c = cells_of(np.zeros((1, 3)), g_closed.origin, 1.0)[0]
    check(g_closed.solid[tuple(c)] and g_closed.closed, 'closing fills it')


def cube_distance(d):
    return math.sqrt(sum(max(abs(int(x)) - 1, 0) ** 2 for x in d))


def test_dilation_brute_force():
    print('dilation against brute force')
    for trial in range(6):
        h = RNG.choice([1.0, 0.5, 2.0])
        gap = float(RNG.choice([0.3, 1.0, 2.0, 2.5, 3.7]))
        n = 13
        g = np.zeros((n, n, n), bool)
        pts = RNG.integers(5, 8, size=(4, 3))
        for p in pts:
            g[tuple(p)] = True
        D = voxel.dilate_gap(g, gap, h)
        S = np.argwhere(g)
        ok = True
        for c in np.ndindex(n, n, n):
            near = min(cube_distance(np.array(c) - s) * h for s in S) < gap
            if near != bool(D[c]):
                ok = False
                break
        check(ok, 'gap %.1f at h=%.1f: exactly the cells closer than the gap' % (gap, h))


def test_numpy_matches_scipy():
    print('NumPy and SciPy paths agree')
    if SCIPY is None:
        check(True, '(SciPy not installed: nothing to compare)')
        return
    for trial in range(4):
        g = RNG.random((30, 25, 28)) < RNG.choice([0.05, 0.2, 0.4])
        g[0] = g[-1] = False
        g[:, 0] = g[:, -1] = False
        g[:, :, 0] = g[:, :, -1] = False
        a = voxel.outside(g)
        b = numpy_only(voxel.outside, g)
        check(np.array_equal(a, b), 'outside, density trial %d' % trial)
        r = float(RNG.uniform(0.5, 6.0))
        a = voxel.dilate_ball(g, r)
        b = numpy_only(voxel.dilate_ball, g, r)
        check(np.array_equal(a, b), 'ball dilation r=%.2f' % r)
    v, f = cup()
    a = voxel.voxelize(v, f, rotation(RNG), 0.8, 2.0)
    b = numpy_only(voxel.voxelize, v, f, np.eye(3), 0.8, 2.0)
    a2 = voxel.voxelize(v, f, np.eye(3), 0.8, 2.0)
    check(np.array_equal(a2.solid, b.solid) and np.array_equal(a2.kernel, b.kernel), 'a whole cup')


def test_pool():
    print('coarse grids are conservative')
    g = RNG.random((17, 11, 9)) < 0.03
    for phase in ((0, 0, 0), (1, 2, 3), (3, 3, 3)):
        c = voxel.pool(g, 4, phase)
        ok = True
        for p in np.argwhere(g):
            q = (p + np.array(phase)) // 4
            ok &= bool(c[tuple(q)])
        check(ok and c.sum() <= g.sum(), 'phase %s' % (phase,))


def test_timing():
    print('timing')
    v, f = meshes.uv_sphere(25.0, 700, 350)
    t = time.perf_counter()
    g = voxel.voxelize(np.asarray(v), np.asarray(f), rotation(RNG), 1.0, 5.0)
    dt = time.perf_counter() - t
    print('       sphere r=25 mm, %d triangles, h=1, gap 5: %.2f s, grid %s' % (len(f), dt, g.shape))
    v, f = meshes.box((150.0, 100.0, 60.0))
    t = time.perf_counter()
    g = voxel.voxelize(np.asarray(v), np.asarray(f), rotation(RNG), 1.0, 5.0)
    dt = time.perf_counter() - t
    print('       box 150 x 100 x 60 mm (12 triangles), h=1, gap 5: %.2f s, grid %s' % (dt, g.shape))
    v, f = meshes.uv_sphere(40.0, 2000, 1000)
    v, f = np.asarray(v), np.asarray(f)
    t = time.perf_counter()
    voxel.voxelize(v, f, rotation(RNG), 1.0, 5.0)
    t1 = time.perf_counter() - t
    t = time.perf_counter()
    cl = voxel.Cloud(v, f, voxel.cloud_cell(1.0, 1.0))
    t2 = time.perf_counter() - t
    t = time.perf_counter()
    voxel.voxelize(v, f, rotation(RNG), 1.0, 5.0, cloud=cl)
    t3 = time.perf_counter() - t
    print('       sphere r=40, %.1f M triangles: %.2f s per orientation from triangles; cloud %.2f s once '
          '(%d points), then %.2f s per orientation' % (len(f) / 1e6, t1, t2, len(cl.centers), t3))
    if SCIPY is not None:
        t = time.perf_counter()
        numpy_only(voxel.voxelize, np.asarray(v), np.asarray(f), np.eye(3), 1.0, 5.0)
        print('       the same box, NumPy only: %.2f s' % (time.perf_counter() - t))


if __name__ == '__main__':
    test_conservative()
    test_cloud()
    test_soup()
    test_cavities()
    test_dilation_brute_force()
    test_numpy_matches_scipy()
    test_pool()
    test_timing()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
