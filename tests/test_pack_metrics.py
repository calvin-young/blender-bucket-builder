"""Checks pack.metrics against analytic volumes and an independent method.

    python3 tests/test_pack_metrics.py

The independent method slices a mesh with a plane, adds the oriented section
segments into the section's area, and integrates that area over height with
Gauss-Legendre points between the mesh's vertex heights (the area is
piecewise quadratic there, so three points are exact).  It shares no code
with the divergence-theorem formula in metrics.py.
"""

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))
sys.path.insert(0, HERE)

import meshes  # noqa: E402
from pack import metrics  # noqa: E402
from pack.meshio import Mesh  # noqa: E402

FAILS = []
RNG = np.random.default_rng(7)


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def close(a, b, tol, msg):
    err = float(np.max(np.abs(np.asarray(a) - np.asarray(b))))
    check(err <= tol, '%s (max error %.2e, limit %.0e)' % (msg, err, tol))


def mk(vf, name='m'):
    return Mesh(np.asarray(vf[0], float), np.asarray(vf[1], np.int32), name)


def pose(L=None, t=(0, 0, 0)):
    M = np.eye(4)
    if L is not None:
        M[:3, :3] = L
    M[:3, 3] = t
    return M


def rotation(rng):
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1
    return q


# ------------------------------------------------------------ the reference

def section_area(P, z):
    """Area of the solid's cross-section at height z (P: (n, 3, 3) world triangles,
    outward winding), from oriented segments."""
    zz = P[:, :, 2]
    cross = (zz.min(axis=1) < z) & (zz.max(axis=1) > z)
    area = 0.0
    for tri in P[cross]:
        pts = []
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            if (a[2] - z) * (b[2] - z) < 0:
                s = (z - a[2]) / (b[2] - a[2])
                pts.append(a[:2] + s * (b[:2] - a[:2]))
            elif a[2] == z and (tri[(i + 2) % 3][2] - z) * (b[2] - z) < 0:
                pts.append(a[:2])        # only if the vertex lies exactly on the plane
        if len(pts) != 2:
            continue
        p, q = pts
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        d = q - p
        left = np.array([-d[1], d[0]])
        if left @ (-n[:2]) < 0:
            p, q = q, p
        area += 0.5 * (p[0] * q[1] - p[1] * q[0])
    return area


GAUSS_X = np.array([-np.sqrt(3 / 5), 0.0, np.sqrt(3 / 5)])
GAUSS_W = np.array([5 / 9, 8 / 9, 5 / 9])


def reference_V(P, hs):
    """V(h) for the heights hs by integrating section areas."""
    zs = np.unique(np.concatenate([P[:, :, 2].ravel(), np.asarray(hs, float)]))
    cum = np.zeros(zs.size)
    for i in range(zs.size - 1):
        a, b = zs[i], zs[i + 1]
        mid, half = 0.5 * (a + b), 0.5 * (b - a)
        cum[i + 1] = cum[i] + half * sum(w * section_area(P, mid + half * x)
                                         for x, w in zip(GAUSS_X, GAUSS_W))
    return np.interp(hs, zs, cum)       # hs are breakpoints, so this is exact


def world_triangles(mesh, M):
    return (mesh.verts @ M[:3, :3].T + M[:3, 3])[mesh.tris]


def bands_of(mesh, M, z0, dz, nb):
    r = metrics.measure_parts([('p', mesh, M)], [-1e3, -1e3, z0], [1e3, 1e3, z0 + dz * nb], dz, nb)[0]
    return r


# ------------------------------------------------------------------- tests

def test_volumes():
    print('volumes')
    b = mk(meshes.box((2.0, 3.0, 4.0)))
    check(abs(metrics.mesh_stats(b)[0] - 24.0) < 1e-12, 'box 2 x 3 x 4 = 24')
    # mesh volume is invariant to where the mesh is (rounding aside)
    far = Mesh(b.verts + 1e5, b.tris)
    check(abs(metrics.mesh_stats(far)[0] - 24.0) < 1e-6, 'a box 1e5 away keeps its volume')
    s = mk(meshes.uv_sphere(1.0, 64, 32))
    exact = metrics.mesh_stats(s)[0]
    check(abs(exact - 4 / 3 * np.pi) / (4 / 3 * np.pi) < 1e-2, 'uv sphere within 1 % of 4/3 pi (it is inscribed)')
    L = np.diag([2.0, 3.0, 0.5])
    r = metrics.measure_parts([('s', s, pose(L, (1, 2, 3)))], [-9, -9, -9], [9, 9, 9], 1.0)[0]
    close(r.volume, exact * 3.0, 1e-9, 'scaled sphere volume = det * volume')
    mir = metrics.measure_parts([('s', s, pose(np.diag([-1.0, 1, 1])))], [-9, -9, -9], [9, 9, 9], 1.0)[0]
    close(mir.volume, exact, 1e-9, 'a mirrored part has the same positive volume')
    check(not mir.inside_out, 'a mirrored part is not reported as inside out')
    flipped = Mesh(s.verts, s.tris[:, ::-1].copy())
    r = metrics.measure_parts([('f', flipped, pose())], [-9, -9, -9], [9, 9, 9], 1.0)[0]
    check(r.inside_out, 'reversed winding is reported')
    close(r.volume, exact, 1e-9, 'an inside-out mesh has its volume taken as positive')
    close(r.bounds[-1], exact, 1e-9, 'and its bands are positive too')


def test_box_exact():
    print('a box on band boundaries')
    b = mk(meshes.box((10.0, 10.0, 5.0)))
    # z from 2 to 7, bands of 1 starting at 0: exact integers
    r = bands_of(b, pose(t=(0, 0, 4.5)), 0.0, 1.0, 10)
    close(np.diff(r.bounds), [0, 0, 100, 100, 100, 100, 100, 0, 0, 0], 1e-9, 'bands of a box on the grid')
    r = bands_of(b, pose(t=(0, 0, 4.25)), 0.0, 1.0, 10)     # 1.75 .. 6.75
    close(np.diff(r.bounds), [0, 25, 100, 100, 100, 100, 75, 0, 0, 0], 1e-9, 'bands of a box off the grid')
    r = bands_of(b, pose(t=(0, 0, 1.0)), 0.0, 1.0, 10)      # -1.5 .. 3.5: pokes below the floor
    close(np.diff(r.bounds), [100, 100, 100, 50, 0, 0, 0, 0, 0, 0], 1e-9, 'a box below the floor')
    close([r.below, r.above], [150.0, 0.0], 1e-9, 'volume below the floor is reported')
    r = bands_of(b, pose(t=(0, 0, 9.0)), 0.0, 1.0, 10)      # 6.5 .. 11.5: pokes above
    close([r.below, r.above], [0.0, 150.0], 1e-9, 'volume above the last band is reported')


def test_against_slicing():
    print('against the independent slicing method')
    cases = {
        'sphere': mk(meshes.uv_sphere(5.0, 14, 8)),
        'torus (concave)': mk(meshes.torus(8.0, 2.5, 16, 8)),
        'cylinder': mk(meshes.cylinder(3.0, 11.0, 18)),
        'blob': mk(meshes.blob(RNG, 6.0, 12, 7, 0.35)),
    }
    for name, m in cases.items():
        for k in range(3):
            L = rotation(RNG) * RNG.uniform(0.6, 1.8) if k else None
            M = pose(L, RNG.uniform(-5, 5, 3) + [0, 0, 12])
            P = world_triangles(m, M)
            z0, dz, nb = float(P[:, :, 2].min()) - 1.7, 1.3, 40
            hs = z0 + dz * np.arange(nb + 1)
            ref_V = reference_V(P, hs)
            r = bands_of(m, M, z0, dz, nb)
            close(r.bounds, np.abs(ref_V), 1e-8 * max(1.0, abs(ref_V[-1])),
                  '%s, pose %d: V(h) at %d boundaries' % (name, k, nb + 1))
            close(np.abs(ref_V[-1]), r.volume, 1e-8 * max(1.0, r.volume), '%s, pose %d: total' % (name, k))


def test_long_triangles():
    print('triangles that span many bands')
    # a thin tall prism: its side triangles cross dozens of bands
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 60], [1, 0, 60], [0, 1, 60]], float)
    f = np.array([[0, 2, 1], [3, 4, 5], [0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4], [2, 0, 3], [2, 3, 5]])
    m = Mesh(v, f.astype(np.int32))
    r = bands_of(m, pose(t=(0, 0, 0.3)), 0.0, 1.0, 70)
    check(abs(r.volume - 30.0) < 1e-9, 'prism volume 30')
    exp = np.zeros(70)
    exp[:60] = 0.5
    exp[0] = 0.5 * 0.7
    exp[60] = 0.5 * 0.3
    close(np.diff(r.bounds), exp, 1e-9, 'prism bands (60 bands crossed by one triangle)')
    # a triangle fan with a single long skinny triangle in a tilted position
    M = pose(rotation(RNG), (0, 0, 40))
    P = world_triangles(m, M)
    z0 = float(P[:, :, 2].min()) - 0.3
    hs = z0 + 1.0 * np.arange(72)
    close(bands_of(m, M, z0, 1.0, 71).bounds, np.abs(reference_V(P, hs)), 1e-8, 'tilted prism V(h)')


def test_copies_share_work():
    print('copies of one pose')
    m = mk(meshes.torus(6.0, 2.0, 20, 10))
    ts = [(0, 0, 3.0), (10, 5, 3.0), (-4, 2, 7.77), (0, 0, 20.1)]
    parts = [('c%d' % i, m, pose(t=t)) for i, t in enumerate(ts)]
    together = metrics.measure_parts(parts, [-50, -50, 0], [50, 50, 50], 1.0)
    for i, p in enumerate(parts):
        alone = metrics.measure_parts([p], [-50, -50, 0], [50, 50, 50], 1.0)[0]
        close(together[i].bounds, alone.bounds, 1e-9, 'copy %d alone = in a group' % i)
    close(together[0].bounds, together[1].bounds, 1e-12, 'moving a copy in XY changes nothing')


def test_status_and_report():
    print('inside / partial / outside, density, warnings')
    b = mk(meshes.box((10.0, 10.0, 10.0)), 'cube')
    vlo, vhi = [0, 0, 0], [100, 100, 100]
    parts = [('in', b, pose(t=(20, 20, 5))),            # z 0 .. 10, exactly on the floor
             ('in2', b, pose(t=(50, 50, 25))),          # z 20 .. 30
             ('half', b, pose(t=(98, 50, 25))),         # x 93 .. 103: partly out
             ('far', b, pose(t=(300, 0, 5)))]           # wholly outside
    rep = metrics.measure(parts, vlo, vhi, band=1.0)
    check((rep.n_inside, rep.n_partial, rep.n_outside) == (2, 1, 1), 'statuses 2 / 1 / 1: %s'
          % ((rep.n_inside, rep.n_partial, rep.n_outside),))
    close(rep.volume, 3000.0, 1e-9, 'volume counts inside and partial parts, not the outside one')
    close(rep.volume_outside, 1000.0, 1e-9, 'volume outside is kept apart')
    close(rep.height, 30.0, 1e-9, 'height is the highest point above the floor')
    close(rep.density, 3000.0 / (100 * 100 * 30), 1e-12, 'density = volume / (area x height)')
    check(any('partly outside' in w for w in rep.warnings), 'a warning sits with the density when a part is partly out')
    check(rep.partial_parts == ['half'] and rep.outside_parts == ['far'], 'the offenders are named')
    # the layers add up to the volume of the counted parts
    close(rep.layer_volume.sum(), 3000.0, 1e-9, 'layer volumes add up')
    close(rep.layer_density[:10], 3 * 100 / (100 * 100 * 1.0) * np.ones(10) / 3 * 1, 1e-12,
          'layer density of the bottom cube: 100 mm^2 of 10000')
    clean = metrics.measure(parts[:2], vlo, vhi)
    check(not clean.warnings, 'no warnings for a clean build')
    # an empty-ish build
    e = metrics.measure([], vlo, vhi)
    check(e.density is None and e.n_parts == 0, 'an empty build has no density')
    # a part with zero triangles
    z = metrics.measure([('nothing', Mesh(np.zeros((0, 3)), np.zeros((0, 3), np.int32)), pose())], vlo, vhi)
    check(z.volume == 0.0, 'a mesh without triangles counts as nothing')


def test_open_mesh_warning():
    print('open meshes')
    b = mk(meshes.box((10.0, 10.0, 10.0)), 'lid off')
    t = b.tris[2:].copy()                 # drop the two bottom triangles
    opened = Mesh(b.verts, t, 'lid off')
    rep = metrics.measure([('a', opened, pose(t=(10, 10, 5)))], [0, 0, 0], [100, 100, 100])
    check(rep.open_parts == ['a'] and any('not closed' in w for w in rep.warnings), 'an open box is reported')
    ok = metrics.measure([('a', b, pose(t=(10, 10, 5)))], [0, 0, 0], [100, 100, 100])
    check(not ok.open_parts, 'a closed box is not')
    topo = metrics.topology(opened)
    check(topo['boundary_edges'] == 4 and topo['flipped_edges'] == 0, 'topology finds the 4 boundary edges of the hole: %s' % topo)
    bad = Mesh(b.verts, np.vstack([b.tris[:-1], b.tris[-1][::-1][None]]).astype(np.int32))
    check(metrics.topology(bad)['flipped_edges'] > 0, 'topology finds a flipped triangle')
    check(sum(metrics.topology(b).values()) == 0, 'topology: a closed box is clean')


def test_spread():
    print('spread across layers')
    c = mk(meshes.box((10.0, 10.0, 10.0)), 'cube')
    vlo, vhi = [0, 0, 0], [60, 60, 80]
    # a cube lattice alternates X % and 0 %; the same volume as a tall slab does not
    lattice = [('c%d' % i, c, pose(t=(30, 30, 5 + 20 * i))) for i in range(4)]
    rep = metrics.measure(lattice, vlo, vhi, band=1.0, smooth_mm=5.0)
    slab = mk(meshes.box((10.0, 10.0, 40.0)))
    flat = metrics.measure([('s', slab, pose(t=(30, 30, 20)))], vlo, vhi, band=1.0, smooth_mm=5.0)
    check(rep.spread_cv > flat.spread_cv + 0.1, 'a cube lattice spreads worse than a slab of the same volume: %.2f vs %.2f'
          % (rep.spread_cv, flat.spread_cv))
    check(flat.spread_cv < 1e-9, 'a slab is perfectly even')
    close(rep.peak_layer, 100 / 3600.0, 1e-12, 'peak layer density')
    close(metrics.smooth(np.array([0, 0, 3.0, 0, 0]), 3), [0, 1.0, 1.0, 1.0, 0], 1e-12, 'moving average')


def test_timing():
    print('timing')
    s = mk(meshes.uv_sphere(20.0, 700, 350))            # about 490 k triangles
    nt = s.nt
    parts = [('p%d' % i, s, pose(t=(40 * (i % 8), 40 * (i // 8), 25))) for i in range(60)]
    t0 = time.perf_counter()
    rep = metrics.measure(parts, [-30, -30, 0], [400, 400, 400], 1.0)
    dt = time.perf_counter() - t0
    print('       60 copies of %d triangles (%.0f M in all): %.2f s' % (nt, 60 * nt / 1e6, dt))
    check(dt < 20, 'a report of 60 parts at %d triangles takes seconds' % nt)
    distinct = [('d%d' % i, Mesh(s.verts * (1 + 0.01 * i), s.tris), pose(t=(40 * (i % 8), 40 * (i // 8), 25 + 0.1 * i)))
                for i in range(6)]
    t0 = time.perf_counter()
    metrics.measure(distinct, [-30, -30, 0], [400, 400, 400], 1.0)
    dt = time.perf_counter() - t0
    print('       6 distinct meshes of %d triangles: %.2f s (%.0f ns per triangle)' % (nt, dt, dt / 6 / nt * 1e9))
    del rep


if __name__ == '__main__':
    for f in (test_volumes, test_box_exact, test_against_slicing, test_long_triangles,
              test_copies_share_work, test_status_and_report, test_open_mesh_warning,
              test_spread, test_timing):
        f()
    print()
    print('%d check(s) failed' % len(FAILS) if FAILS else 'ALL OK')
    sys.exit(1 if FAILS else 0)
