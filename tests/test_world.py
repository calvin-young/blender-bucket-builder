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
    """World-space float64 triangles of an object exactly as the world sees them."""
    slot = w.slot(uid)
    return w.part_corners(slot).astype(np.float64) + w.O_T[slot]


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


def incremental_tests(rng, borrow=False, room=0, edits_wanted=140):
    """Many objects, random edits; results must equal a world built from scratch.

    ``borrow``: every mesh may borrow a pose when it is turned (normally only
    large ones do).  ``room``: memory for about that many poses only."""
    parts = {
        'box': meshes.box((1.0, 0.7, 0.5)),
        'gbox': meshes.grid_box((0.9, 0.6, 0.6), 5),
        'sph': meshes.uv_sphere(0.5, 16, 8),
        'tor': meshes.torus(0.5, 0.18, 16, 8),
        'cyl': meshes.cylinder(0.25, 1.2, 16),
    }
    names = list(parts)

    one = max(World.pose_size(len(v), len(f)) for v, f in parts.values())

    def fresh(objs, thr, vol, plain=True):
        w = World()
        w.set_scale(8.0, 0.01)
        w.set_thresholds(*thr)
        if vol is not None:
            w.set_volume(*vol)
        if borrow and not plain:
            w.fit_now = 0
        for k, (v, f) in parts.items():
            w.add_geom(k, v, f)
        if room and not plain:
            w.set_cache_limit(w.geom_bytes + room * one)
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
    w = fresh(objs, thr, vol, plain=False)
    edits = 0
    nviol = 0
    borrowed = 0
    for it in range(edits_wanted):
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
        # sometimes a few frames of a drag first, as in a live edit; sometimes
        # with a tiny budget so work is spread over several steps
        if it % 4 == 1:
            for _ in range(int(rng.integers(1, 4))):
                w.step(idle=False)
            borrowed += w.stats()['borrowing']
        if it % 3 == 0:
            steps = 0
            while w.step(budget=0.0005, hot_budget=0.001):
                steps += 1
                assert steps < 10000
        else:
            while w.step():
                pass
        if room:
            assert w.pose_bytes + w.geom_bytes <= w.cache_limit, (it, w.pose_bytes)
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
    assert w.BOX.live == 0 and w.TIDX.live == 0 and w.pose_bytes == 0, (
        w.BOX.live, w.TIDX.live, w.pose_bytes)
    assert not w.pairs and not w.viol and not w.oob
    assert not (w._virt_todo or w._borrowing or w._oob_geom or w._demand), 'state left behind'
    assert bool(borrowed) == bool(borrow), borrowed
    assert not room or w.evictions > 0
    how = ('' if not borrow else ', every mesh borrowing when turned') + (
        f', memory for {room} poses' if room else '')
    print(f'incremental edits checked against a fresh world: {edits} '
          f'(violating pairs seen: {nviol}){how}')


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
        P = posed_tris(w, 'o')
        w.need_poses([w.slot('o')])            # poses are built on demand
        pose = int(w.O_POSE[w.slot('o')])
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


def cache_tests(rng):
    """Poses are a cache.  With a memory limit far too small for the scene
    the least recently used ones are dropped and re-fitted on demand; the
    results must be the same as without a limit."""
    parts = {
        'blob': meshes.blob(rng, 0.5, 96, 48, 0.3),
        'tor': meshes.torus(0.5, 0.18, 96, 48),
        'gbox': meshes.grid_box((0.9, 0.6, 0.6), 40),
        'sph': meshes.uv_sphere(0.5, 96, 48),
    }
    names = list(parts)

    def build(limit):
        w = World()
        w.set_scale(8.0, 0.01)
        w.set_thresholds(0.0, 0.2)
        w.set_volume((-1.6, -1.6, -1.6), (1.6, 1.6, 1.6), 0.1)
        w.cache_limit = limit
        for k, (v, f) in parts.items():
            w.add_geom(k, v, f)
        return w

    def snapshot(w):
        out = {}
        for (a, b), pr in w.viol.items():
            ua, ub = w.uid(a), w.uid(b)
            # (how finely the hatched region is resolved depends on what else
            # is solved in the same batch, so it is not compared)
            out[(min(ua, ub), max(ua, ub))] = (pr.state, round(pr.dist, 6), pr.nseg,
                                               pr.tri_a is not None and len(pr.tri_a) > 0)
        oob = {w.uid(s): (r.state, None if r.tris is None else len(r.tris)) for s, r in w.oob.items()}
        wall = {w.uid(s): (round(r.dist, 6), None if r.tris is None else len(r.tris))
                for s, r in w.wall.items()}
        return out, oob, wall

    one = max(World.pose_size(len(v), len(f)) for v, f in parts.values())
    ref = build(None)
    lim = build(None)
    lim.set_cache_limit(lim.geom_bytes + 5 * one)     # room for about five poses of twenty-six
    worlds = (ref, lim)
    n_obj = 26
    mats = {}
    for i in range(n_obj):
        mats[i] = (names[i % len(names)], meshes.matrix(meshes.rot(rng), rng.uniform(-1.7, 1.7, 3)))
        for w in worlds:
            w.add_object(i, *mats[i])
    peak = 0
    for it in range(60):
        if it:
            uid = int(rng.integers(n_obj))
            g, M = mats[uid]
            M = M.copy()
            if it % 4 == 0:
                M[:3, :3] = meshes.rot(rng)
            else:
                M[:3, 3] += rng.normal(0, 0.3, 3)
            mats[uid] = (g, M)
            for w in worlds:
                w.set_matrix(uid, M)
        for w in worlds:
            if it % 2:
                w.step(idle=False)                 # a frame of a drag first
            while w.step(budget=0.002):
                pass
        got, want = snapshot(lim), snapshot(ref)
        for k in range(3):
            assert got[k] == want[k], (it, k, sorted(set(got[k].items()) ^ set(want[k].items()))[:6])
        peak = max(peak, lim.pose_bytes)
        assert lim.pose_bytes + lim.geom_bytes <= lim.cache_limit, (it, lim.pose_bytes)
        assert lim.BOX.live * 24 == lim.pose_bytes
        # the storage itself stays within the limit too, not just what is in use
        assert lim.BOX.nbytes + lim.geom_bytes <= lim.cache_limit, (it, lim.BOX.nbytes)
    assert ref.evictions == 0 and lim.evictions > 20, lim.evictions
    assert len(ref._poses) > 12 and len(lim._poses) < len(ref._poses) // 2, (len(ref._poses), len(lim._poses))
    # a part without neighbours and inside the volume never needs a pose
    w = build(None)
    w.add_object('alone', 'blob', meshes.matrix(meshes.rot(rng)))
    w.add_object('far', 'tor', meshes.matrix(meshes.rot(rng), (40.0, 0.0, 0.0)))
    while w.step():
        pass
    assert w.pose_bytes == 0 and not w._poses and w.oob[w.slot('far')].state == OUTSIDE
    print(f'pose cache: results identical with room for 5 of {len(ref._poses)} poses '
          f'({lim.evictions} evictions, {lim.flushes} flushes, {ref.pose_bytes / 1e6:.1f} MB unlimited, '
          f'{peak / 1e6:.1f} MB at most with the limit)')


def check_volume(w, uid, label):
    """What is reported as outside the volume / inside the wall margin must be
    exactly the triangles that are."""
    slot = w.slot(uid)
    vlo, vhi = w.volume
    P = posed_tris(w, uid)
    r = w.oob.get(slot)
    lo = P.min(axis=(0, 1))
    hi = P.max(axis=(0, 1))
    inside = (lo >= vlo - w.eps_len).all() and (hi <= vhi + w.eps_len).all()
    outside = (lo > vhi + w.eps_len).any() or (hi < vlo - w.eps_len).any()
    want = None if inside else (OUTSIDE if outside else PARTIAL)
    got = None if r is None else r.state
    assert got == want, (label, uid, got, want)
    n = 0
    if want == PARTIAL:
        out = ((P < vlo - w.eps_len) | (P > vhi + w.eps_len)).any(axis=(1, 2))
        assert r.tris is not None and len(r.tris) == int(out.sum()), (
            label, uid, None if r.tris is None else len(r.tris), int(out.sum()))
        n += 1
    inner = w.inner_box()
    wr = w.wall.get(slot)
    if inner is not None and wr is not None:
        band = ((P < inner[0] - w.eps_len) | (P > inner[1] + w.eps_len)).any(axis=(1, 2))
        assert wr.tris is not None and len(wr.tris) == int(band.sum()) > 0, (
            label, uid, None if wr.tris is None else len(wr.tris), int(band.sum()))
        n += 1
    return n


def borrow_tests(rng):
    """A part that is rotated or scaled keeps using the pose it had, with its
    boxes moved as they are looked at, until things are quiet.  Whatever is
    worked out that way must be what a fitted pose gives."""
    gens = [
        lambda: meshes.box((1.0, 0.8, 0.6)),
        lambda: meshes.grid_box((1.0, 0.8, 0.6), 6),
        lambda: meshes.uv_sphere(0.6, 20, 10),
        lambda: meshes.torus(0.6, 0.2, 20, 10),
        lambda: meshes.blob(rng, 0.6, 18, 9, 0.3),
        lambda: meshes.cylinder(0.3, 1.4, 20),
    ]

    def turn(trial):
        L = meshes.rot(rng)
        if trial % 4 == 0:
            L = L @ np.diag(rng.uniform(0.6, 1.6, 3))       # scaled as well, unevenly
        return L

    stats = {}
    n_borrow = n_quick = n_vol = n_small_angle = n_exact = 0
    for trial in range(360):
        w = World()
        w.fit_now = 0                                 # every mesh may borrow, however small
        w.set_scale(2.0, 0.01)
        clear = float(rng.choice([0.0, 0.05, 0.15, 0.4]))
        coll = float(rng.choice([0.0, 0.0, 0.02]))
        w.set_thresholds(coll, clear)
        w.set_volume((-1.1, -1.1, -1.1), (1.1, 1.1, 1.1), 0.12 if trial % 2 else 0.0)
        w.add_geom('a', *gens[rng.integers(len(gens))]())
        w.add_geom('b', *gens[rng.integers(len(gens))]())
        w.add_object('A', 'a', meshes.matrix(meshes.rot(rng), (5.0, 0.0, 0.0)))
        w.add_object('B', 'b', meshes.matrix(meshes.rot(rng), (-5.0, 0.0, 0.0)))
        sa, sb = w.slot('A'), w.slot('B')
        w.need_poses([sa, sb])                        # both have a fitted pose ...
        while w.step():
            pass
        # ... and are then turned (and moved next to each other)
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        small = 0.25 if trial % 7 == 0 else 1.0       # small part: may end up inside
        Mb = meshes.matrix(turn(trial) * small, direction * rng.uniform(0.0, 1.9))
        if trial % 5 == 0:
            # only slightly turned, as at the start of a rotation with the mouse
            a = rng.uniform(0.002, 0.2)
            c, s_ = np.cos(a), np.sin(a)
            Mb[:3, :3] = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]]) @ w.part_matrix(sb)[:3, :3]
            n_small_angle += 1
        hot = True
        both = trial % 3 == 1
        w.set_matrix('B', Mb, hot=hot)
        if trial % 3 == 2:                            # turned again before anything was solved
            Mb = Mb.copy()
            Mb[:3, :3] = turn(trial) * small
            w.set_matrix('B', Mb, hot=hot)
        Ma = meshes.matrix(turn(trial + 1) if both else w.part_matrix(sa)[:3, :3],
                           rng.normal(0, 0.2, 3))
        w.set_matrix('A', Ma, hot=hot)
        assert w.O_VIRT[sb] and (w.O_VIRT[sa] or not both), (trial, w.O_VIRT[sa], w.O_VIRT[sb])
        key = (sa, sb)
        # one frame of a drag: the quick answer, from borrowed boxes
        w.step(idle=False)
        assert w.O_VIRT[sb]
        pr = w.pairs.get(key)
        assert pr is None or (not pr.stale and pr.loose)
        quick = (OK if pr is None else pr.state, None if pr is None else pr.dist)
        n_quick += 1
        n_vol += check_volume(w, 'A', trial) + check_volume(w, 'B', trial)
        if trial % 2 and pr is not None:
            # The complete answer straight from borrowed boxes.  The world
            # never asks for that (it fits poses for exact work, see below),
            # but it is the sharpest test of the moved boxes there is.
            narrow.solve(w, [key], True, None)
            check_pair(w, 'A', 'B', f'borrow trial {trial} (borrowing)', {})
            pr.refine = True
            w._pend_refine[key] = None
            n_exact += 1
        n_borrow += 1 + both
        while w.step():                               # quiet: results redone with fitted poses
            pass
        pr = w.pairs.get(key)
        assert pr is None or not (pr.loose or pr.stale or pr.refine), (trial, pr.loose, pr.stale)
        # (a part with nothing near it may go on borrowing: that costs nothing)
        assert pr is None or not (w.O_VIRT[sa] or w.O_VIRT[sb]), trial
        check_pair(w, 'A', 'B', f'borrow trial {trial} (settled)', stats)
        n_vol += check_volume(w, 'A', trial) + check_volume(w, 'B', trial)
        e_state = OK if pr is None else pr.state
        q_state, q_dist = quick
        if COLLIDE in (q_state, e_state):
            assert q_state == e_state, ('collision differs with borrowed boxes', trial)
        elif q_state == CLEAR:
            assert e_state == CLEAR and q_dist >= pr.dist * (1 - 2e-3) - 1e-9, (trial, q_dist)
        # and exactly what a world built from scratch says
        f = World()
        f.set_scale(2.0, 0.01)
        f.set_thresholds(coll, clear)
        f.set_volume(*w.volume, w.wall_margin)
        for name, uid in (('a', 'A'), ('b', 'B')):
            g, verts, tris = w.part_mesh(w.slot(uid))
            f.add_sorted(name, verts.copy(), tris.copy())
            f.add_object(uid, name, w.part_matrix(w.slot(uid)))
        while f.step():
            pass
        fr = f.pairs.get((f.slot('A'), f.slot('B')))
        assert (pr is None or pr.state == OK) == (fr is None or fr.state == OK), trial
        if pr is not None and pr.state != OK:
            assert (pr.state, round(pr.dist, 6), pr.nseg, pr.enclosed) == (
                fr.state, round(fr.dist, 6), fr.nseg, fr.enclosed), (trial, pr.dist, fr.dist)
    print(f'borrowed poses: {n_borrow} turned or scaled parts ({n_small_angle} only slightly), '
          f'states OK/CLEAR/COLLIDE = {stats.get(OK, 0)}/{stats.get(CLEAR, 0)}/{stats.get(COLLIDE, 0)} '
          f'(enclosed: {stats.get("enclosed", 0)}), {n_quick} quick answers consistent, '
          f'{n_exact} complete answers from borrowed boxes right, {n_vol} build-volume reports exact')

    # a part inside another one that borrows its pose: the question "is this
    # point inside?" has to be asked in the frame of the borrowed pose
    bar = meshes.grid_box((2.0, 0.4, 0.4), 6)
    ball = meshes.uv_sphere(0.1, 12, 6)
    quarter = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    tilt = meshes.rot(rng)
    for where, want in (((0.8, 0.0, 0.0), OK), ((0.0, 0.8, 0.0), COLLIDE)):
        for swap in (False, True):
            w = World()
            w.fit_now = 0
            w.set_scale(4.0, 0.01)
            w.set_thresholds(0.0, 0.02)
            w.add_geom('bar', *bar)
            w.add_geom('ball', *ball)
            names = ('ball', 'bar') if swap else ('bar', 'ball')
            w.add_object(names[0], names[0], meshes.matrix(tilt if names[0] == 'bar' else None,
                                                           (9.0, 0.0, 0.0) if names[0] == 'ball' else (0, 0, 0)))
            w.add_object(names[1], names[1], meshes.matrix(tilt if names[1] == 'bar' else None,
                                                           (9.0, 0.0, 0.0) if names[1] == 'ball' else (0, 0, 0)))
            w.need_poses([0, 1])
            while w.step():
                pass
            # the bar now lies along y; the ball is put where the bar was, or is
            w.set_matrix('bar', meshes.matrix(tilt @ quarter @ tilt.T @ tilt))
            w.set_matrix('ball', meshes.matrix(None, tilt @ quarter @ np.array(where) * 0 +
                                               tilt @ quarter @ np.array([where[1], -where[0], 0.0])))
            w.step(idle=False)
            assert w.O_VIRT[w.slot('bar')] and not w.unsettled
            pr = w.pairs.get((0, 1))
            if pr is not None:
                narrow.solve(w, [(0, 1)], True, None)      # the complete answer, still borrowing
            got = OK if pr is None else pr.state
            assert got == want, ('enclosed in a borrowing part', where, swap, got)
            if want == COLLIDE:
                assert pr.enclosed == (1 if swap else 2), (where, swap, pr.enclosed)
            check_pair(w, names[0], names[1], f'ball in bar {where} {swap}', {})

    # the moved boxes of a borrowed pose must contain what a fitted pose has
    # in them, at every level of the tree, rounding included
    worst = np.inf                                     # the closest call
    grow = []
    for trial in range(30):
        v, f = gens[trial % len(gens)]()
        w = World()
        w.fit_now = 0
        w.set_scale(2.0, 0.01)
        w.add_geom('g', v, f)
        w.add_object('base', 'g', meshes.matrix(meshes.rot(rng) @ np.diag(rng.uniform(0.5, 2.0, 3))))
        L = turn(trial)
        w.add_object('fit', 'g', meshes.matrix(L))
        w.need_poses([0, 1])
        w.add_object('user', 'g', meshes.matrix(meshes.rot(rng)))
        w.need_poses([2])
        w.set_matrix('user', meshes.matrix(L))
        slot = w.slot('user')
        w._detach(slot)                                # (it would simply join 'fit')
        w._link(slot, w._obj[0][3])
        w._borrow(slot, *w._rel_to(w._obj[0][3], w._obj[0][1], w._obj[slot][2]))
        pb, pf = w._obj[0][3], w._obj[1][3]
        g = pb.geom
        for h in range(g.H + 1):
            n = g.nreal[h]
            raw = w.BOX.data[pb.bbase + g.off[h]:pb.bbase + g.off[h] + n]
            tight = w.BOX.data[pf.bbase + g.off[h]:pf.bbase + g.off[h] + n]
            lo, hi = narrow._move_rows(raw, w.O_REL[slot], float(w.O_PAD[slot]))
            # the same through the routine the traversal uses
            X = np.ascontiguousarray(raw.T[:, None, :])
            flag = np.ones(1, dtype=bool)
            R9 = np.ascontiguousarray(w.O_REL[slot].reshape(9, 1))
            Y = narrow._move_boxes(X, np.zeros(n, dtype=np.int64), flag, R9, np.abs(R9),
                                   2.0 * w.O_PAD[slot:slot + 1])
            for blo, bhi in ((lo, hi), (Y[:3, 0].T, Y[3:, 0].T)):
                assert (blo <= tight[:, :3]).all() and (bhi >= tight[:, 3:]).all(), (trial, h)
                worst = min(worst, float((tight[:, :3] - blo).min()), float((bhi - tight[:, 3:]).min()))
            if n >= 16:
                grow.append(float(((hi - lo).sum(axis=1) / np.maximum(
                    (tight[:, 3:] - tight[:, :3]).sum(axis=1), 1e-12)).mean()))
    print(f'  moved boxes contain the fitted ones on every level (closest call {worst:.1e}; '
          f'they are {np.mean(grow):.2f} times as large on average)')

    # copies of one mesh in many rotations with memory for two poses only:
    # live answers come from borrowed poses, exact ones from fitted poses that
    # take turns in the cache, and nothing is reported differently
    big = meshes.blob(rng, 0.5, 64, 32, 0.3)
    one = World.pose_size(len(big[0]), len(big[1]))

    def build(limit):
        w = World()
        w.fit_now = 0
        w.set_scale(8.0, 0.01)
        w.set_thresholds(0.0, 0.2)
        w.set_volume((-1.6, -1.6, -1.6), (1.6, 1.6, 1.6), 0.1)
        w.add_geom('m', *big)
        if limit:
            w.set_cache_limit(w.geom_bytes + int(2.5 * one))
        return w

    ref, lim = build(False), build(True)
    count = 18
    mats = {}
    for i in range(count):
        mats[i] = meshes.matrix(meshes.rot(rng), rng.uniform(-1.5, 1.5, 3))
        for w in (ref, lim):
            w.add_object(i, 'm', mats[i])
    peak = 0
    for it in range(40):
        if it:
            uid = int(rng.integers(count))
            M = mats[uid].copy()
            if it % 3 == 0:
                M[:3, :3] = meshes.rot(rng)
            else:
                M[:3, 3] += rng.normal(0, 0.3, 3)
            mats[uid] = M
            for w in (ref, lim):
                w.set_matrix(uid, M)
        for w in (ref, lim):
            if it % 2:
                w.step(idle=False)
                if w is lim:
                    peak = max(peak, lim.stats()['borrowing'])
                    assert len(lim._poses) <= 2
            while w.step(budget=0.002):
                pass
        assert lim.pose_bytes + lim.geom_bytes <= lim.cache_limit and len(lim._poses) <= 2
        assert lim.BOX.nbytes + lim.geom_bytes <= lim.cache_limit
        assert set(lim.viol) == set(ref.viol), (it, set(lim.viol) ^ set(ref.viol))
        for k, pr in ref.viol.items():
            pl = lim.viol[k]
            assert pl.state == pr.state and pl.nseg == pr.nseg and pl.enclosed == pr.enclosed, (it, k)
            assert abs(pl.dist - pr.dist) <= 2e-3 * pr.dist + 1e-9, (it, k, pl.dist, pr.dist)
        assert {s: r.state for s, r in lim.oob.items()} == {s: r.state for s, r in ref.oob.items()}
        for s, r in ref.oob.items():
            assert (r.tris is None) == (lim.oob[s].tris is None)
            assert r.tris is None or len(r.tris) == len(lim.oob[s].tris), (it, s)
        assert set(lim.wall) == set(ref.wall)
    assert peak >= 1 and lim.evictions > 20 and ref.evictions == 0, (peak, lim.evictions)
    print(f'  {count} copies of one mesh, memory for two poses: results the same '
          f'(up to {peak} borrowing during an edit, {lim.evictions} poses dropped and fitted again)')

    # a pose shared by two parts: the one that is turned gets its own beside it
    w = build(False)
    M = meshes.matrix(meshes.rot(rng), (0.0, 0.0, 0.0))
    w.add_object('p', 'm', M)
    M2 = M.copy()
    M2[:3, 3] = (0.9, 0.0, 0.0)
    w.add_object('q', 'm', M2)
    while w.step():
        pass
    assert len(w._poses) == 1
    M3 = M2.copy()
    M3[:3, :3] = meshes.rot(rng)
    w.set_matrix('q', M3)
    w.step(idle=False)
    assert w.O_VIRT[w.slot('q')] and len(w._poses) == 1
    while w.step():
        pass
    assert not w.O_VIRT[w.slot('q')] and len(w._poses) == 2
    w.remove_object('p')
    assert len(w._poses) == 1
    w.remove_object('q')
    w.drop_unused_geoms()
    assert w.BOX.live == 0 and w.pose_bytes == 0 and not w._virt_todo and not w._borrowing

    # a large mesh is fitted in pieces: no single step takes it all
    v, f = meshes.blob(rng, 0.5, 400, 200, 0.3)                 # about 160 000 triangles
    w = World()
    w.set_scale(8.0, 0.01)
    w.set_thresholds(0.0, 0.2)
    w.add_geom('g', v, f)
    w.add_object(0, 'g', meshes.matrix(meshes.rot(rng)))
    w.add_object(1, 'g', meshes.matrix(meshes.rot(rng), (0.9, 0.0, 0.0)))
    steps = 0
    while w.step(budget=0.0005, hot_budget=0.0005):
        steps += 1
        assert steps < 5000
    assert steps >= 6 and w.pairs[(0, 1)].state == COLLIDE, steps
    M = w.part_matrix(1)
    M[:3, :3] = meshes.rot(rng)
    w.set_matrix(1, M)
    w.step(idle=False, budget=0.0005, hot_budget=0.0005)        # answered at once, borrowing
    assert w.O_VIRT[1] and not w.pairs[(0, 1)].stale
    steps = 0
    while w.step(budget=0.0005, hot_budget=0.0005):
        steps += 1
    assert steps >= 4 and not w.O_VIRT[1] and len(w._poses) == 2
    print(f'  fits are spread over steps ({steps} for a mesh of {len(f)} triangles)')


def dense_clearance_tests(rng):
    """Two plates with large, finely meshed faces a little apart: thousands of
    triangle pairs are all about as close as the closest.  Proving the
    smallest gap is then expensive, so the search is capped at first; it must
    still never get the verdict wrong."""
    n = 110
    v, f = meshes.grid_box((1.0, 1.0, 0.1), n)
    top = np.flatnonzero(np.isclose(v[:, 2], 0.05))
    # one vertex of the lower plate's top face, away from the edges, raised a little
    mid = top[np.argmin(np.abs(v[top, 0] - 0.2) + np.abs(v[top, 1] + 0.3))]
    vb = v.copy()
    vb[mid, 2] += 0.012
    R = meshes.rot(rng)
    repeated = 0
    for name, lower, gap, clear, want, dist in (
            ('just clear of the limit', v, 0.105, 0.1, OK, None),
            ('a bump inside the limit', vb, 0.105, 0.1, CLEAR, 0.093),
            ('just inside the limit', v, 0.099, 0.1, CLEAR, 0.099),
            ('well inside the limit', v, 0.105, 0.2, CLEAR, 0.105)):
        w = World()
        w.set_scale(2.0, 0.01)
        w.set_thresholds(0.0, clear)
        w.add_geom('lower', lower, f)
        w.add_geom('upper', v, f)
        w.add_object('L', 'lower', meshes.matrix(R))
        w.add_object('U', 'upper', meshes.matrix(R, R @ np.array([0.03, -0.02, 0.1 + gap])))
        t0 = time.perf_counter()
        while w.unsettled:
            w.step(idle=False)                          # the answer while dragging
        pr = w.pairs.get((0, 1))
        q_state = OK if pr is None else pr.state
        while w.step():
            pass
        ms = (time.perf_counter() - t0) * 1000
        pr = w.pairs.get((0, 1))
        got = OK if pr is None else pr.state
        assert got == want, (name, got, None if pr is None else pr.dist)
        assert q_state in (want, OK), (name, q_state)   # never a false alarm while dragging
        if dist is not None:
            assert abs(pr.dist - dist) <= 2e-3 * dist, (name, pr.dist, dist)
            assert pr.tri_a is not None and len(pr.tri_a)
        repeated += w.proofs_repeated
        print(f'  plates of {len(f)} triangles, {name}: state {got}'
              + (f', {pr.dist:.4f}' + (' (flagged approximate)' if pr.approx else '') if pr and want else '')
              + f', {ms:.0f} ms, searched again with large caps: {w.proofs_repeated}')
    assert repeated >= 1, repeated
    print('dense clearance: ok')


def group_move_tests(rng):
    """Parts that are moved together keep their results: nothing between them
    is solved again, although their positions arrive rounded to single
    precision, each on its own (that is what Blender delivers).  A part that
    really moves against the others is noticed, however slowly it creeps."""
    from core.world import POS_TOL
    parts = {
        'gbox': meshes.grid_box((0.9, 0.6, 0.6), 5),
        'sph': meshes.uv_sphere(0.5, 16, 8),
        'tor': meshes.torus(0.5, 0.18, 16, 8),
        'cyl': meshes.cylinder(0.25, 1.2, 16),
    }
    names = list(parts)
    f32 = np.float32
    thr = 0.2

    def build(objs):
        w = World()
        w.set_scale(8.0, 0.01)
        w.set_thresholds(0.0, thr)
        for k, (v, f) in parts.items():
            w.add_geom(k, v, f)
        for uid, (g, R, t) in objs.items():
            w.add_object(uid, g, meshes.matrix(R, np.asarray(t, dtype=np.float64)))
        while w.step(budget=10.0):
            pass
        return w

    def snapshot(w):
        out = {}
        for (a, b), pr in w.pairs.items():
            if pr.state > OK:
                out[(w.uid(a), w.uid(b))] = (pr.state, pr.dist, 0 if pr.segs is None else len(pr.segs))
        return out

    def offsets_hold(w, label):
        """No result is for an offset further from the present one than the
        slack allows, unless the pair is waiting to be solved again."""
        worst = 0.0
        for (a, b), pr in w.pairs.items():
            want = w.solved_for((a, b))
            if want is None or pr.stale:
                continue
            tol = POS_TOL * max(np.abs(w.O_T[a]).max(), np.abs(w.O_T[b]).max())
            err = np.abs(w.O_T[b] - w.O_T[a] - want).max()
            assert err <= tol, (label, w.uid(a), w.uid(b), err, tol)
            worst = max(worst, err / tol if tol else 0.0)
        return worst

    moves = kept = redone = crept = 0
    for origin in (0.0, 300.0):       # around the origin, and where a millimetre scene has its parts
        n_obj = 26
        objs = {}
        for i in range(n_obj):
            t = (origin + rng.uniform(-1.9, 1.9, 3)).astype(f32)
            objs[f'o{i}'] = [names[rng.integers(len(names))], meshes.rot(rng), t]
        w = build(objs)
        uids = list(objs)
        for trial in range(9):
            k = [2, 3, 5, 9, n_obj // 2, n_obj - 1, n_obj, 4, n_obj][trial]
            group = [uids[i] for i in rng.permutation(n_obj)[:k]]
            inside = set(w.slot(u) for u in group)
            home = {u: objs[u][2].copy() for u in group}
            serial = {key: pr.serial for key, pr in w.pairs.items()
                      if key[0] in inside and key[1] in inside and not pr.stale}
            for step in range(4):
                # a drag: every position is its start plus the same offset,
                # rounded to single precision on its own
                d = rng.normal(0.0, 1.5, 3).astype(f32)
                if trial == 8 and step == 3:
                    d[:] = 0.0                    # ... and exactly back where they were
                for u in group:
                    objs[u][2] = home[u] + d
                    assert objs[u][2].dtype == f32
                    w.set_matrix(u, meshes.matrix(objs[u][1], objs[u][2].astype(np.float64)))
                w.step(idle=False)
                for key, ser in serial.items():
                    pr = w.pairs.get(key)
                    assert pr is not None and pr.serial == ser and not pr.stale, (
                        'solved again', origin, trial, step, key)
                offsets_hold(w, (origin, trial, step))
                moves += 1
            kept += len(serial)
            while w.step():
                pass
            got = snapshot(w)
            want = snapshot(build(objs))
            assert set(got) == set(want), (origin, trial, set(got) ^ set(want))
            for key, (state, dist, nseg) in want.items():
                if (key[0] in group) != (key[1] in group):
                    # one part moved against the other: solved for exactly
                    # where they are now
                    assert got[key][0] == state and got[key][2] == nseg, (origin, trial, key)
                    assert abs(got[key][1] - dist) <= 1e-9, (origin, trial, key, got[key], dist)
                    redone += 1
                else:
                    # moved together, now or earlier: worked out for positions
                    # that differ in the last digits
                    slack = 4.0 * POS_TOL * (abs(origin) + 6.0)
                    assert got[key][0] == state or abs(dist - thr) < slack, (origin, trial, key)
                    assert abs(got[key][1] - dist) <= slack, (origin, trial, key, got[key], dist)

        # one part of a group creeps against the others, a third of the slack
        # per step: the pair must be solved again before the slack is used up
        near = [key for key, pr in w.pairs.items() if pr.state == CLEAR and not pr.stale]
        assert near
        a, b = near[0]
        ua, ub = w.uid(a), w.uid(b)
        tol = POS_TOL * max(np.abs(w.O_T[a]).max(), np.abs(w.O_T[b]).max())
        home_a = objs[ua][2].astype(np.float64)
        home_b = objs[ub][2].astype(np.float64)
        e = rng.normal(size=3)
        e /= np.abs(e).max()
        solves = 0
        last = w.pairs[(a, b)].serial
        for step in range(1, 31):
            d = rng.normal(0.0, 0.5, 3)
            w.set_matrix(ua, meshes.matrix(objs[ua][1], home_a + d))
            w.set_matrix(ub, meshes.matrix(objs[ub][1], home_b + d + step * 0.34 * tol * e))
            w.step(idle=False)
            while w.step():
                pass
            offsets_hold(w, ('creep', origin, step))
            if w.pairs[(a, b)].serial != last:
                last = w.pairs[(a, b)].serial
                solves += 1
        assert 6 <= solves <= 16, solves
        crept += solves
        # ... and the slack is no more than a millionth of the coordinates:
        # a shift of two and a half millionths is a move
        t = w.O_T[b].copy()
        last = w.pairs[(a, b)].serial
        big = 2.5e-6 * max(np.abs(w.O_T[a]).max(), np.abs(t).max())
        w.set_matrix(ub, meshes.matrix(objs[ub][1], t + big * e))
        while w.step():
            pass
        assert w.pairs[(a, b)].serial != last, 'a real move went unnoticed'
    assert kept > 60 and redone > 15, (kept, redone)
    print(f'parts moved together: {moves} moves of 2 to 26 parts, results of {kept} pairs among them '
          f'kept without solving, {redone} results against the other parts identical to a fresh '
          f'world; a creeping part was caught {crept} times in 60 steps')


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
    dense_clearance_tests(rng)
    cache_tests(rng)
    borrow_tests(rng)
    group_move_tests(rng)
    incremental_tests(rng)
    incremental_tests(rng, borrow=True)
    incremental_tests(rng, borrow=True, room=3)
    print('OK')


if __name__ == '__main__':
    main()
