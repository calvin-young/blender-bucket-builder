"""Checks the collision world against brute force (every triangle pair)."""

import os
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))   # import the core without bpy
sys.path.insert(0, HERE)

from core import World, OK, CLEAR, COLLIDE, PARTIAL, OUTSIDE  # noqa: E402
from core import narrow, tritri  # noqa: E402
import meshes  # noqa: E402


def posed_tris(w, uid):
    """World-space float64 triangles of an object exactly as the world stores them."""
    slot = w.slot(uid)
    pose = int(w.O_POSE[slot])
    nt = int(w.PNT[pose])
    P = narrow.corners(w, np.full(nt, pose), np.arange(nt)).astype(np.float64)
    return P + w.O_T[slot]


def brute(PA, PB, eps):
    nA, nB = len(PA), len(PB)
    nhit = 0
    seglen = 0.0
    best = np.inf
    step = max(1, 200000 // nB)
    for s in range(0, nA, step):
        A = tritri.to_soa(np.repeat(PA[s:s + step], nB, axis=0))
        B = tritri.to_soa(np.tile(PB, (len(PA[s:s + step]), 1, 1)))
        hit, seg = tritri.tri_tri_intersect(A, B, eps)
        d2, _, _ = tritri.tri_tri_distance(A, B)
        nhit += int(hit.sum())
        seglen += float(np.linalg.norm(seg[:, 0] - seg[:, 1], axis=1).sum())
        d2 = np.where(hit, 0.0, d2)
        best = min(best, float(d2.min()))
    return nhit, seglen, float(np.sqrt(best))


def expect_enclosed(PA, PB):
    """Reference for 'one part completely inside the other' (winding number)."""
    a_lo, a_hi = PA.min(axis=(0, 1)), PA.max(axis=(0, 1))
    b_lo, b_hi = PB.min(axis=(0, 1)), PB.max(axis=(0, 1))
    if (a_lo >= b_lo).all() and (a_hi <= b_hi).all():
        return abs(winding(PB, PA[0, 0][None])[0]) > 0.5
    if (b_lo >= a_lo).all() and (b_hi <= a_hi).all():
        return abs(winding(PA, PB[0, 0][None])[0]) > 0.5
    return False


def expect_state(w, nhit, d, enclosed=False):
    coll = max(w.coll_thr, w.eps_touch)
    if nhit or d <= coll or enclosed:
        return COLLIDE
    if w.clear_thr > coll and d < w.clear_thr:
        return CLEAR
    return OK


def check_pair(w, ua, ub, label, stats):
    a, b = w.slot(ua), w.slot(ub)
    key = (a, b) if a < b else (b, a)
    PA, PB = posed_tris(w, ua), posed_tris(w, ub)
    nhit, seglen, d = brute(PA, PB, w.eps_exact)
    enclosed = (not nhit) and d > max(w.coll_thr, w.eps_touch) and expect_enclosed(PA, PB)
    want = expect_state(w, nhit, d, enclosed)
    pr = w.pairs.get(key)
    got = OK if pr is None else pr.state
    thr = max(w.clear_thr, w.coll_thr)
    borderline = (not nhit) and (abs(d - w.clear_thr) < 2e-3 * max(thr, 1e-9)
                                 or abs(d - max(w.coll_thr, w.eps_touch)) < 2e-3 * max(thr, 1e-9))
    if got != want and not borderline:
        raise AssertionError(f'{label}: state {got} != expected {want} (brute d={d}, hits={nhit}, '
                             f'reported d={None if pr is None else pr.dist})')
    stats[want] = stats.get(want, 0) + 1
    if pr is None or got != want:
        return
    assert not pr.stale, label
    if want == OK:
        return
    if enclosed:
        assert pr.enclosed in (1, 2), label
        stats['enclosed'] = stats.get('enclosed', 0) + 1
        return
    if nhit:
        assert pr.segs is not None and pr.nseg == nhit, (label, 'segments', pr.nseg, nhit)
        got_len = float(np.linalg.norm(pr.segs[:, 0] - pr.segs[:, 1], axis=1).sum())
        assert abs(got_len - seglen) <= 1e-4 * max(seglen, 1.0), (label, got_len, seglen)
        assert pr.dist == 0.0
    else:
        assert abs(pr.dist - d) <= 2e-3 * d + 2 * w.eps_len, (label, 'distance', pr.dist, d)
        # the reported closest points must be that far apart
        assert abs(np.linalg.norm(pr.pa - pr.pb) - pr.dist) < 1e-9, label
        assert pr.tri_a is not None and len(pr.tri_a) and len(pr.tri_b), (label, 'no region')


def random_pair_tests(rng):
    gens = [
        lambda: meshes.box((1.0, 0.8, 0.6)),
        lambda: meshes.grid_box((1.0, 0.8, 0.6), 6),
        lambda: meshes.uv_sphere(0.6, 20, 10),
        lambda: meshes.torus(0.6, 0.2, 20, 10),
        lambda: meshes.blob(rng, 0.6, 18, 9, 0.3),
        lambda: meshes.cylinder(0.3, 1.4, 20),
    ]
    stats = {}
    n = 0
    t_solve = 0.0
    for trial in range(420):
        w = World()
        w.set_scale(2.0, 0.01)
        clear = float(rng.choice([0.0, 0.05, 0.15, 0.4]))
        coll = float(rng.choice([0.0, 0.0, 0.02]))
        w.set_thresholds(coll, clear)
        va, fa = gens[rng.integers(len(gens))]()
        vb, fb = gens[rng.integers(len(gens))]()
        w.add_geom('a', va, fa)
        w.add_geom('b', vb, fb)
        sa = np.diag(rng.uniform(0.5, 1.5, 3)) if trial % 3 == 0 else np.eye(3)
        Ma = meshes.matrix(meshes.rot(rng) @ sa, rng.normal(0, 0.2, 3))
        # place B at a range of separations, biased towards near-contact
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        sb_ = np.eye(3) * (0.25 if trial % 7 == 0 else 1.0)     # small part: may end up inside
        Mb = meshes.matrix(meshes.rot(rng) @ sb_, direction * rng.uniform(0.0, 1.9))
        w.add_object('A', 'a', Ma)
        w.add_object('B', 'b', Mb)
        t0 = time.perf_counter()
        while w.step():
            pass
        t_solve += time.perf_counter() - t0
        check_pair(w, 'A', 'B', f'trial {trial}', stats)
        n += 1
    print(f'random pairs: {n}  states OK/CLEAR/COLLIDE = '
          f'{stats.get(OK, 0)}/{stats.get(CLEAR, 0)}/{stats.get(COLLIDE, 0)}  '
          f'(of which enclosed: {stats.get("enclosed", 0)})  '
          f'solve time total {t_solve * 1000:.0f} ms')


def special_pair_tests():
    stats = {}
    # two boxes: stacked exactly face to face (touching), then separated, then overlapping
    for gap, want in ((0.0, COLLIDE), (0.03, CLEAR), (0.2, OK), (-0.1, COLLIDE)):
        w = World()
        w.set_scale(2.0, 0.01)
        w.set_thresholds(0.0, 0.1)
        v, f = meshes.box((1, 1, 1))
        w.add_geom('box', v, f)
        w.add_object('A', 'box', meshes.matrix())
        w.add_object('B', 'box', meshes.matrix(None, (0.3, 0.2, 1.0 + gap)))
        while w.step():
            pass
        key = (w.slot('A'), w.slot('B'))
        pr = w.pairs.get(key)
        got = OK if pr is None else pr.state
        assert got == want, ('boxes gap', gap, got, want, None if pr is None else pr.dist)
        if want == CLEAR:
            assert abs(pr.dist - gap) < 1e-5, pr.dist
        check_pair(w, 'A', 'B', f'boxes gap {gap}', stats)

    # identical parts exactly on top of each other (duplicate in place)
    w = World()
    w.set_scale(2.0, 0.01)
    w.set_thresholds(0.0, 0.1)
    v, f = meshes.grid_box((1, 1, 1), 10)
    w.add_geom('g', v, f)
    w.add_object('A', 'g', meshes.matrix())
    w.add_object('B', 'g', meshes.matrix())
    while w.step():
        pass
    pr = w.pairs[(0, 1)]
    assert pr.state == COLLIDE, pr.state

    # fine parallel faces at a small gap (the pair-explosion case)
    for gap in (0.02, 0.099, 0.101):
        w = World()
        w.set_scale(2.0, 0.01)
        w.set_thresholds(0.0, 0.1)
        v, f = meshes.grid_box((1, 1, 0.2), 24)
        w.add_geom('g', v, f)
        w.add_object('A', 'g', meshes.matrix())
        w.add_object('B', 'g', meshes.matrix(None, (0.1, 0.05, 0.2 + gap)))
        t0 = time.perf_counter()
        while w.step():
            pass
        ms = (time.perf_counter() - t0) * 1000
        pr = w.pairs.get((0, 1))
        got = OK if pr is None else pr.state
        want = CLEAR if gap < 0.1 else OK
        assert got == want, ('plates', gap, got, None if pr is None else pr.dist)
        if want == CLEAR:
            assert abs(pr.dist - gap) < 1e-5, (gap, pr.dist)
        print(f'  parallel plates gap {gap}: state {got} in {ms:.1f} ms')
    print('special cases: ok')


def incremental_tests(rng):
    """Many objects, random edits; results must equal a world built from scratch."""
    parts = {
        'box': meshes.box((1.0, 0.7, 0.5)),
        'gbox': meshes.grid_box((0.9, 0.6, 0.6), 5),
        'sph': meshes.uv_sphere(0.5, 16, 8),
        'tor': meshes.torus(0.5, 0.18, 16, 8),
        'cyl': meshes.cylinder(0.25, 1.2, 16),
    }
    names = list(parts)

    def fresh(objs, thr, vol):
        w = World()
        w.set_scale(8.0, 0.01)
        w.set_thresholds(*thr)
        if vol is not None:
            w.set_volume(*vol)
        for k, (v, f) in parts.items():
            w.add_geom(k, v, f)
        for uid, (g, M) in objs.items():
            w.add_object(uid, g, M)
        while w.step(budget=10.0):
            pass
        return w

    def snapshot(w):
        out = {}
        for (a, b), pr in w.viol.items():
            ua, ub = w.uid(a), w.uid(b)
            flip = ua > ub
            key = (ub, ua) if flip else (ua, ub)
            nseg = 0 if pr.segs is None else len(pr.segs)
            out[key] = (pr.state, round(pr.dist, 6), nseg)
        oob = {w.uid(s): (r.state, None if r.tris is None else len(r.tris))
               for s, r in w.oob.items()}
        return out, oob

    objs = {}
    n_obj = 14
    for i in range(n_obj):
        M = meshes.matrix(meshes.rot(rng), rng.uniform(-1.6, 1.6, 3))
        objs[f'o{i}'] = (names[rng.integers(len(names))], M)
    thr = (0.0, 0.2)
    vol = (np.array([-1.5, -1.5, -1.5]), np.array([1.5, 1.5, 1.5]))
    w = fresh(objs, thr, vol)
    edits = 0
    nviol = 0
    for it in range(140):
        kind = rng.integers(8)
        uid = f'o{rng.integers(n_obj)}'
        if kind <= 3 and uid in objs:          # translate
            g, M = objs[uid]
            M = M.copy()
            M[:3, 3] += rng.normal(0, 0.25, 3)
            objs[uid] = (g, M)
            w.set_matrix(uid, M)
        elif kind == 4 and uid in objs:        # rotate / scale
            g, M = objs[uid]
            M = M.copy()
            M[:3, :3] = meshes.rot(rng) @ np.diag(rng.uniform(0.7, 1.3, 3))
            objs[uid] = (g, M)
            w.set_matrix(uid, M)
        elif kind == 5 and uid in objs:        # swap geometry
            g, M = objs[uid]
            g2 = names[rng.integers(len(names))]
            objs[uid] = (g2, M)
            w.add_geom(g2, *parts[g2])        # unused geometry is dropped, so re-register
            w.set_geometry(uid, g2)
        elif kind == 6:                        # remove / re-add
            if uid in objs:
                del objs[uid]
                w.remove_object(uid)
                for k, (v, f) in parts.items():   # geoms may have been dropped
                    w.add_geom(k, v, f)
            else:
                M = meshes.matrix(meshes.rot(rng), rng.uniform(-1.6, 1.6, 3))
                g = names[rng.integers(len(names))]
                objs[uid] = (g, M)
                w.add_geom(g, *parts[g])
                w.add_object(uid, g, M)
        elif kind == 7:                        # change thresholds / volume
            thr = (float(rng.choice([0.0, 0.03])), float(rng.choice([0.0, 0.1, 0.25])))
            w.set_thresholds(*thr)
            s = float(rng.choice([1.2, 1.5, 2.5]))
            vol = (np.array([-s, -s, -s]), np.array([s, s, s]))
            w.set_volume(*vol)
        else:
            continue
        # sometimes run with a tiny budget so work is spread over several steps
        if it % 3 == 0:
            steps = 0
            while w.step(budget=0.0005, hot_budget=0.001):
                steps += 1
                assert steps < 10000
        else:
            while w.step():
                pass
        ref_w = fresh(objs, thr, vol)
        got, got_oob = snapshot(w)
        want, want_oob = snapshot(ref_w)
        assert got == want, (it, kind, sorted(set(got.items()) ^ set(want.items())))
        assert got_oob == want_oob, (it, kind, got_oob, want_oob)
        edits += 1
        nviol += len(want)
    # storage must not leak: everything an object held is released again
    for uid in list(objs):
        w.remove_object(uid)
    w.drop_unused_geoms()
    assert w.BOX.live == 0 and w.VERT.live == 0 and w.TIDX.live == 0, (
        w.BOX.live, w.VERT.live, w.TIDX.live)
    assert not w.pairs and not w.viol and not w.oob
    print(f'incremental edits checked against a fresh world: {edits} '
          f'(violating pairs seen: {nviol})')


def volume_tests(rng):
    w = World()
    w.set_scale(4.0, 0.01)
    w.set_thresholds(0.0, 0.0)
    w.set_volume((-1, -1, -1), (1, 1, 1))
    v, f = meshes.uv_sphere(0.5, 24, 12)
    w.add_geom('s', v, f)
    cases = {
        'in': ((0.0, 0.0, 0.0), None),
        'edge': ((0.8, 0.0, 0.0), PARTIAL),
        'corner': ((0.9, 0.9, -0.9), PARTIAL),
        'out': ((3.0, 0.0, 0.0), OUTSIDE),
    }
    for uid, (t, _) in cases.items():
        w.add_object(uid, 's', meshes.matrix(meshes.rot(rng), t))
    while w.step():
        pass
    for uid, (t, want) in cases.items():
        r = w.oob.get(w.slot(uid))
        got = None if r is None else r.state
        assert got == want, (uid, got, want)
        if want == PARTIAL:
            P = posed_tris(w, uid)
            out = ((P < -1 - w.eps_len) | (P > 1 + w.eps_len)).any(axis=(1, 2))
            assert len(r.tris) == int(out.sum()), (uid, len(r.tris), int(out.sum()))
            # every reported triangle really has a corner outside
            rt = r.tris.astype(np.float64)
            assert ((rt < -1 - 1e-4) | (rt > 1 + 1e-4)).any(axis=(1, 2)).all()
    print('build volume: ok')


def wall_tests(rng):
    """Parts inside the volume but closer to a side wall than the margin."""
    w = World()
    w.set_scale(400.0, 0.5)
    w.set_thresholds(0.0, 0.0)
    vlo = np.array([0.0, 0.0, 0.0])
    vhi = np.array([380.0, 284.0, 380.0])
    w.set_volume(vlo, vhi, 5.0)
    v, f = meshes.uv_sphere(20.0, 32, 16)          # reaches exactly +-20 along x, y and z
    w.add_geom('s', v, f)
    cases = {                                       # position, gap to the nearest side wall or None
        'safe': ((190.0, 142.0, 190.0), None),
        'x_lo': ((23.0, 142.0, 190.0), 3.0),
        'x_hi': ((358.0, 142.0, 190.0), 2.0),
        'y_lo': ((190.0, 24.5, 190.0), 4.5),
        'y_hi': ((190.0, 263.0, 190.0), 1.0),
        'corner': ((22.0, 23.0, 190.0), 2.0),
        'floor': ((190.0, 142.0, 20.5), None),      # the floor and the top are not walls
        'top': ((190.0, 142.0, 359.5), None),
        'just_clear': ((25.01, 142.0, 190.0), None),
        'touching': ((20.0, 142.0, 190.0), 0.0),
        'through': ((15.0, 142.0, 190.0), None),    # crosses the wall: an error, not this warning
    }
    for uid, (t, _) in cases.items():
        w.add_object(uid, 's', meshes.matrix(None, t))
    while w.step():
        pass
    ilo, ihi = w.inner_box()
    assert np.allclose(ilo, (5, 5, 0)) and np.allclose(ihi, (375, 279, 380)), (ilo, ihi)
    for uid, (t, want) in cases.items():
        r = w.wall.get(w.slot(uid))
        got = None if r is None else r.dist
        assert (got is None) == (want is None), (uid, got, want)
        if want is not None:
            assert abs(got - want) < 1e-3, (uid, got, want)
            P = posed_tris(w, uid)
            band = ((P < ilo - w.eps_len) | (P > ihi + w.eps_len)).any(axis=(1, 2))
            assert len(r.tris) == int(band.sum()) > 0, (uid, len(r.tris), int(band.sum()))
            rt = r.tris.astype(np.float64)
            assert ((rt < ilo + 1e-3) | (rt > ihi - 1e-3)).any(axis=(1, 2)).all(), uid
            # the marker sits in the margin, not in the middle of the part
            assert ((r.center < ilo) | (r.center > ihi)).any(), (uid, r.center)
    assert w.oob[w.slot('through')].state == PARTIAL and w.slot('through') not in w.wall
    assert w.counts()[4] == 6, w.counts()

    # moving a part in and out of the margin, and changing the margin
    w.set_matrix('x_lo', meshes.matrix(None, (40.0, 142.0, 190.0)))
    w.step()
    assert w.slot('x_lo') not in w.wall
    w.set_matrix('x_lo', meshes.matrix(None, (24.0, 142.0, 190.0)))
    w.step()
    assert abs(w.wall[w.slot('x_lo')].dist - 4.0) < 1e-3
    w.set_volume(vlo, vhi, 1.5)
    w.step()
    near = {w.uid(s) for s in w.wall}
    assert near == {'y_hi', 'touching'}, near
    w.set_volume(vlo, vhi, 0.0)
    w.step()
    assert not w.wall and w.inner_box() is None
    w.set_volume(vlo, vhi, 5.0)
    w.step()
    assert len(w.wall) == 6
    w.remove_object('corner')
    assert len(w.wall) == 5
    w.set_volume(None, None)
    w.step()
    assert not w.wall and not w.oob

    # rotated parts: the gap is taken from the real extents of the mesh
    w = World()
    w.set_scale(400.0, 0.5)
    w.set_thresholds(0.0, 0.0)
    w.set_volume(vlo, vhi, 5.0)
    bv, bf = meshes.blob(rng, 20.0, 24, 12, 0.3)
    w.add_geom('b', bv, bf)
    n = 0
    count = 240
    for i in range(count):
        t = rng.uniform((15.0, 15.0, 30.0), (365.0, 269.0, 350.0))
        w.add_object(i, 'b', meshes.matrix(meshes.rot(rng), t))
    while w.step():
        pass
    for i in range(count):
        P = posed_tris(w, i)
        lo = P.min(axis=(0, 1))
        hi = P.max(axis=(0, 1))
        inside = (lo >= vlo).all() and (hi <= vhi).all()
        gap = min(lo[0] - vlo[0], lo[1] - vlo[1], vhi[0] - hi[0], vhi[1] - hi[1])
        r = w.wall.get(w.slot(i))
        want = inside and gap < 5.0 - 1e-3
        if abs(gap - 5.0) > 1e-3:
            assert (r is not None) == want, (i, gap, inside, r)
        if r is not None:
            assert abs(r.dist - max(gap, 0.0)) < 1e-3, (i, r.dist, gap)
            n += 1
    assert n >= 8, n
    print(f'wall clearance: ok ({n} of {count} rotated parts inside the margin)')


def winding(P, pts):
    """Generalised winding number of triangles P (m, 3, 3) around points (n, 3)."""
    out = np.empty(len(pts))
    for i, q in enumerate(pts):
        a = P[:, 0] - q
        b = P[:, 1] - q
        c = P[:, 2] - q
        la = np.linalg.norm(a, axis=1)
        lb = np.linalg.norm(b, axis=1)
        lc = np.linalg.norm(c, axis=1)
        num = np.einsum('ij,ij->i', a, np.cross(b, c))
        den = (la * lb * lc + np.einsum('ij,ij->i', a, b) * lc +
               np.einsum('ij,ij->i', b, c) * la + np.einsum('ij,ij->i', c, a) * lb)
        out[i] = np.sum(2.0 * np.arctan2(num, den)) / (4.0 * np.pi)
    return out


def enclosure_tests(rng):
    # --- the point-in-solid test against the winding number -----------------
    shapes = {
        'blob': meshes.blob(rng, 1.0, 28, 14, 0.3),
        'torus': meshes.torus(1.0, 0.35, 28, 12),
        'box': meshes.box((2.0, 2.0, 2.0)),
        'gbox': meshes.grid_box((2.0, 1.6, 1.2), 4),
        'cyl': meshes.cylinder(0.6, 2.0, 20),
    }
    total = 0
    n_in = 0
    for name, (v, f) in shapes.items():
        w = World()
        w.set_scale(4.0, 0.01)
        w.add_geom(name, v, f)
        w.add_object('o', name, meshes.matrix(meshes.rot(rng) if name != 'box' else None))
        pose = int(w.O_POSE[w.slot('o')])
        P = posed_tris(w, 'o')
        pts = rng.uniform(-1.6, 1.6, (400, 3))
        if name == 'box':
            # rays that run exactly along edges, through corners and along the
            # diagonals of the faces: every one must be counted exactly once
            grid = np.array([-2.0, -1.0, -0.5, 0.0, 0.3, 1.0, 2.0])
            g = np.array([[x, y, z] for x in grid for y in grid for z in grid])
            on_surface = (np.abs(g).max(axis=1) == 1.0)
            pts = np.concatenate([pts, g[~on_surface]])
        wn = winding(P, pts)
        sure = np.abs(np.abs(wn) - 0.5) > 0.2          # skip points on the surface
        got = narrow.points_inside(w, np.full(len(pts), pose), pts)
        want = np.abs(wn) > 0.5
        bad = np.flatnonzero(sure & (got != want))
        assert bad.size == 0, (name, pts[bad][:5], wn[bad][:5])
        total += int(sure.sum())
        n_in += int((want & sure).sum())
    print(f'point-in-solid: {total} points checked against the winding number ({n_in} inside)')

    # --- pairs ----------------------------------------------------------------
    def run(geo_a, Ma, geo_b, Mb, clear=0.05):
        w = World()
        w.set_scale(4.0, 0.01)
        w.set_thresholds(0.0, clear)
        w.add_geom('a', *geo_a)
        w.add_geom('b', *geo_b)
        w.add_object('A', 'a', Ma)
        w.add_object('B', 'b', Mb)
        while w.step():
            pass
        return w.pairs.get((0, 1))

    big = meshes.uv_sphere(1.0, 32, 16)
    small = meshes.uv_sphere(0.2, 16, 8)
    pr = run(big, meshes.matrix(), small, meshes.matrix(None, (0.3, 0.1, -0.2)))
    assert pr.state == COLLIDE and pr.enclosed == 2, ('small inside big', pr.state, pr.enclosed)
    pr = run(small, meshes.matrix(None, (0.3, 0.1, -0.2)), big, meshes.matrix())
    assert pr.state == COLLIDE and pr.enclosed == 1, ('small inside big, swapped', pr.state, pr.enclosed)
    assert pr.tri_a is not None and len(pr.tri_a) and len(pr.segs) == 12

    # hollow shell: outer sphere plus an inward-facing inner sphere.  A part in
    # the cavity is in free space, so it must not be reported.
    vo, fo = meshes.uv_sphere(1.0, 32, 16)
    vi, fi = meshes.uv_sphere(0.7, 32, 16)
    shell = (np.concatenate([vo, vi]), np.concatenate([fo, fi[:, ::-1] + len(vo)]))
    pr = run(shell, meshes.matrix(), small, meshes.matrix(None, (0.1, 0.0, 0.1)))
    assert pr is None or pr.state == OK, ('part in a cavity', None if pr is None else pr.state)
    pr = run(shell, meshes.matrix(), small, meshes.matrix(None, (0.1, 0.0, 0.1)), clear=0.5)
    assert pr.state == CLEAR and pr.enclosed == 0, ('part in a cavity, near the wall', pr.state)
    # ... but a part buried in the wall material is
    tiny = meshes.uv_sphere(0.05, 12, 6)
    pr = run(shell, meshes.matrix(), tiny, meshes.matrix(None, (0.85, 0.0, 0.0)))
    assert pr.state == COLLIDE and pr.enclosed == 2, ('part inside the wall', pr.state, pr.enclosed)

    # through the hole of a torus: inside its bounding box, not inside the solid
    tor = meshes.torus(1.0, 0.3, 32, 12)
    pr = run(tor, meshes.matrix(), small, meshes.matrix())
    assert pr is None or pr.state == OK, ('part in the hole of a torus', None if pr is None else pr.state)

    # switching the check off
    w = World()
    w.set_scale(4.0, 0.01)
    w.set_thresholds(0.0, 0.05)
    w.set_detect_enclosed(False)
    w.add_geom('a', *big)
    w.add_geom('b', *small)
    w.add_object('A', 'a', meshes.matrix())
    w.add_object('B', 'b', meshes.matrix())
    while w.step():
        pass
    assert w.pairs.get((0, 1)) is None or w.pairs[(0, 1)].state == OK
    w.set_detect_enclosed(True)
    while w.step():
        pass
    assert w.pairs[(0, 1)].state == COLLIDE
    print('enclosed parts: ok')


def quick_mode_tests(rng):
    """While a part is being dragged only the quick solve runs.  Collisions
    must be identical to the exact result; the clearance distance must never be
    below the exact one (it is an upper bound) and should be close to it."""
    gens = [
        lambda: meshes.grid_box((1.0, 0.8, 0.6), 8),
        lambda: meshes.uv_sphere(0.6, 28, 14),
        lambda: meshes.torus(0.6, 0.2, 28, 12),
        lambda: meshes.blob(rng, 0.6, 26, 13, 0.3),
        lambda: meshes.cylinder(0.3, 1.4, 28),
    ]
    n = n_coll = n_clear = n_missed = 0
    errs = []
    for trial in range(300):
        w = World()
        w.set_scale(2.0, 0.01)
        w.set_thresholds(0.0, 0.25)
        w.add_geom('a', *gens[rng.integers(len(gens))]())
        w.add_geom('b', *gens[rng.integers(len(gens))]())
        w.add_object('A', 'a', meshes.matrix(meshes.rot(rng)))
        w.add_object('B', 'b', meshes.matrix(meshes.rot(rng), (9.0, 0.0, 0.0)))
        while w.step():
            pass
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        w.set_matrix('B', meshes.matrix(meshes.rot(rng), direction * rng.uniform(0.2, 1.7)))
        w.step(idle=False)                        # one frame of a drag
        pr = w.pairs.get((0, 1))
        q_state = OK if pr is None else pr.state
        q_dist = None if pr is None else pr.dist
        assert pr is None or not pr.stale
        while w.step():                           # release: exact pass
            pass
        pr = w.pairs.get((0, 1))
        e_state = OK if pr is None else pr.state
        e_dist = None if pr is None else pr.dist
        n += 1
        if e_state == COLLIDE or q_state == COLLIDE:
            assert q_state == e_state, ('collision differs between quick and exact', trial, q_state, e_state)
            n_coll += 1
            continue
        if e_state == CLEAR:
            n_clear += 1
            if q_state != CLEAR:
                n_missed += 1
                assert e_dist > 0.25 * 0.8, ('quick pass missed a clear violation', trial, e_dist)
            else:
                assert q_dist >= e_dist - 1e-9, ('quick distance below exact', trial, q_dist, e_dist)
                errs.append((q_dist - e_dist) / e_dist)
        else:
            assert q_state == OK, ('quick pass reported a violation the exact pass does not', trial)
    errs = np.array(errs) if errs else np.zeros(1)
    print(f'quick (while dragging) vs exact: {n} moves, {n_coll} collisions identical, '
          f'{n_clear} clearance cases: distance over-estimate median {np.median(errs) * 100:.2f}% '
          f'p95 {np.percentile(errs, 95) * 100:.2f}% max {errs.max() * 100:.2f}%, '
          f'{n_missed} borderline warnings only shown after release')


def sketch_tests(rng):
    """With many pairs to answer at once (a part dropped onto a crowd) pairs
    are only sketched while the part moves.  A sketch must still get every
    collision right and must never report a violation that is not there; a
    clearance warning may be missing until the exact pass."""
    gens = [
        lambda: meshes.grid_box((1.0, 0.8, 0.6), 8),
        lambda: meshes.uv_sphere(0.6, 28, 14),
        lambda: meshes.torus(0.6, 0.2, 28, 12),
        lambda: meshes.blob(rng, 0.6, 26, 13, 0.3),
        lambda: meshes.cylinder(0.3, 1.4, 28),
    ]
    n = n_coll = n_sketched = n_clear = n_missed = n_enclosed = 0
    for trial in range(40):
        w = World()
        w.set_scale(8.0, 0.01)
        w.set_thresholds(0.0, 0.25)
        w.live_detail = 0                         # sketch everything ...
        w._learn_cost = lambda *a: None           # ... whatever it costs
        for k in range(len(gens)):
            w.add_geom(k, *gens[k]())
        w.add_geom('big', *meshes.blob(rng, 2.2, 40, 20, 0.25))
        count = 14
        for i in range(count):
            t = rng.uniform(-2.4, 2.4, 3)
            w.add_object(i, int(rng.integers(len(gens))), meshes.matrix(meshes.rot(rng), t))
        w.add_object('new', 'big', meshes.matrix(meshes.rot(rng), (30.0, 0.0, 0.0)))
        while w.step():
            pass
        base = {k: pr.state for k, pr in w.pairs.items() if not pr.stale}
        w.set_matrix('new', meshes.matrix(meshes.rot(rng), rng.uniform(-0.8, 0.8, 3)))
        w.step(idle=False, hot_budget=10.0)       # one frame of a drag, nothing left over
        slot = w.slot('new')
        quick = {}
        for o in w.adj[slot]:
            key = (slot, o) if slot < o else (o, slot)
            pr = w.pairs[key]
            assert not pr.stale and pr.lite and pr.refine, (trial, key, pr.stale, pr.lite)
            quick[key] = (pr.state, pr.dist, pr.nseg)
        while w.step():                           # release: exact pass
            pass
        for key, (q_state, q_dist, q_nseg) in quick.items():
            pr = w.pairs.get(key)
            e_state = OK if pr is None else pr.state
            assert pr is None or not (pr.lite or pr.refine or pr.stale)
            n += 1
            if e_state == COLLIDE or q_state == COLLIDE:
                assert q_state == e_state, ('collision differs between sketch and exact', trial, key)
                n_coll += 1
                n_enclosed += bool(pr.enclosed)
                if not pr.enclosed and pr.nseg > q_nseg:
                    n_sketched += 1               # the sketch had only part of the curve
            elif e_state == CLEAR:
                n_clear += 1
                if q_state == CLEAR:
                    # (the exact search stops once nothing can beat its bound by
                    # more than 0.1 %, so a lucky sketch may be that much lower)
                    assert q_dist >= pr.dist * (1.0 - 2e-3) - 1e-9, (
                        'sketch distance below exact', trial, key, q_dist, pr.dist)
                else:
                    n_missed += 1
            else:
                assert q_state == OK, ('sketch reported a violation that is not there', trial, key)
        # pairs not involving the moved part are untouched
        for key, st in base.items():
            if slot not in key:
                assert w.pairs[key].state == st
    assert n_coll > 100 and n_sketched > 50, (n_coll, n_sketched)
    print(f'sketched pairs vs exact: {n} pairs, {n_coll} collisions identical '
          f'({n_sketched} with only part of the curve, {n_enclosed} part-inside-part), '
          f'{n_clear} clearance cases of which {n_missed} only shown after release')


def scale_tests(rng):
    """The same build in millimetres and in metres must give the same answers."""
    parts = [meshes.blob(rng, 20.0, 24, 12, 0.3), meshes.torus(18.0, 6.0, 24, 12),
             meshes.grid_box((40.0, 30.0, 20.0), 6)]
    for trial in range(40):
        mats = [meshes.matrix(meshes.rot(rng), rng.uniform(-28, 28, 3)) for _ in parts]
        res = []
        for unit in (1.0, 0.001):
            w = World()
            w.set_scale(400.0 * unit, 0.5 * unit)
            w.set_thresholds(0.0, 5.0 * unit)
            w.set_volume((-40 * unit,) * 3, (40 * unit,) * 3)
            for i, (v, f) in enumerate(parts):
                w.add_geom(i, v * unit, f)
                M = mats[i].copy()
                M[:3, 3] *= unit
                w.add_object(i, i, M)
            while w.step():
                pass
            snap = {k: (pr.state, pr.dist / unit, pr.nseg) for k, pr in w.viol.items()}
            oob_ = {k: (r.state, len(r.tris) if r.tris is not None else 0) for k, r in w.oob.items()}
            res.append((snap, oob_))
        (s_mm, o_mm), (s_m, o_m) = res
        assert set(s_mm) == set(s_m), (trial, s_mm, s_m)
        for k in s_mm:
            assert s_mm[k][0] == s_m[k][0], (trial, k, s_mm[k], s_m[k])
            assert abs(s_mm[k][1] - s_m[k][1]) <= 2e-3 * max(s_mm[k][1], 1e-3), (trial, k, s_mm[k], s_m[k])
        assert {k: v[0] for k, v in o_mm.items()} == {k: v[0] for k, v in o_m.items()}, (trial, o_mm, o_m)
    print('millimetre and metre scenes agree: ok')


def odd_input_tests():
    """Meshes a real scene can contain must not break anything."""
    w = World()
    w.set_scale(10.0, 0.01)
    w.set_thresholds(0.0, 0.5)
    w.set_volume((-5, -5, -5), (5, 5, 5))
    one = (np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float), np.array([[0, 1, 2]]))
    degenerate = (np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 0, 0]], float),
                  np.array([[0, 1, 2], [0, 0, 0], [0, 3, 1]]))          # zero-area triangles
    soup_v, soup_f = meshes.box((1, 1, 1))
    soup = (soup_v[soup_f].reshape(-1, 3), np.arange(36).reshape(12, 3))   # unshared corners
    nan = (np.array([[0, 0, 0], [1, np.nan, 0], [0, 1, np.inf]], float), np.array([[0, 1, 2]]))
    flat = meshes.grid_box((1.0, 1.0, 0.0), 3)                          # zero thickness
    w.add_geom('one', *one)
    w.add_geom('deg', *degenerate)
    w.add_geom('soup', *soup)
    w.add_geom('nan', *nan)
    w.add_geom('flat', *flat)
    w.add_object(0, 'one', meshes.matrix())
    w.add_object(1, 'one', meshes.matrix(None, (0.2, 0.2, 0.1)))
    w.add_object(2, 'deg', meshes.matrix(None, (0.0, 0.0, 0.05)))
    w.add_object(3, 'soup', meshes.matrix(None, (0.3, 0.3, 0.0)))
    w.add_object(4, 'nan', meshes.matrix(None, (3.0, 0.0, 0.0)))
    w.add_object(5, 'flat', meshes.matrix(None, (0.0, 0.0, 0.3)))
    w.add_object(6, 'soup', np.zeros((4, 4)))                           # zero scale
    while w.step():
        pass
    assert w.pairs[(0, 1)].state == CLEAR and abs(w.pairs[(0, 1)].dist - 0.1) < 1e-6
    assert w.pairs[(0, 3)].state == COLLIDE
    assert np.isfinite([pr.dist for pr in w.viol.values()]).all()
    # lower one triangle through the other: coplanar (touching) half way down
    for i in range(30):
        z = round(0.1 - 0.01 * i, 6)
        w.set_matrix(1, meshes.matrix(None, (0.2, 0.2, z)))
        while w.step():
            pass
        pr = w.pairs[(0, 1)]
        if z == 0.0:
            assert pr.state == COLLIDE, ('coplanar overlap', pr.state, pr.dist)
        else:
            assert pr.state == CLEAR and abs(pr.dist - abs(z)) < 1e-6, (z, pr.state, pr.dist)
    print('odd inputs (single triangle, zero-area, unshared, non-finite, flat, zero scale): ok')


def main():
    rng = np.random.default_rng(11)
    special_pair_tests()
    enclosure_tests(rng)
    random_pair_tests(rng)
    volume_tests(rng)
    wall_tests(rng)
    odd_input_tests()
    scale_tests(rng)
    quick_mode_tests(rng)
    sketch_tests(rng)
    incremental_tests(rng)
    print('OK')


if __name__ == '__main__':
    main()
