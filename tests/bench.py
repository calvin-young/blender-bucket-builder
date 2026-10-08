"""Timing of the collision world on build-sized scenes (NumPy only)."""

import os
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))   # import the core without bpy
sys.path.insert(0, HERE)

from core import World, OK, CLEAR, COLLIDE  # noqa: E402
import meshes  # noqa: E402


def pct(a, q):
    return float(np.percentile(np.asarray(a), q))


def report(name, times, extra=''):
    t = np.asarray(times) * 1000.0
    print(f'{name:<44s} n={len(t):4d}  median {np.median(t):6.2f} ms  p95 {pct(t, 95):6.2f}  '
          f'max {t.max():6.2f}  {extra}')


def make_parts(rng, tris_per_part):
    """A few part shapes in millimetres, roughly 40 mm across."""
    k = max(8, int(np.sqrt(tris_per_part / 2.0)))
    parts = {
        'blob': meshes.blob(rng, 20.0, 2 * k, k, 0.25),
        'torus': meshes.torus(16.0, 6.0, 2 * k, k),
        'brick': meshes.grid_box((40.0, 30.0, 20.0), max(2, int(np.sqrt(tris_per_part / 12.0)))),
        'sphere': meshes.uv_sphere(18.0, 2 * k, k),
    }
    return parts


def build_scene(rng, n_obj, tris_per_part, spacing, clear=5.0, verbose=True):
    w = World()
    w.set_scale(400.0, 0.5)
    w.set_thresholds(0.0, clear)
    w.set_volume((0, 0, 0), (380, 284, 380))
    parts = make_parts(rng, tris_per_part)
    t0 = time.perf_counter()
    ntri = 0
    for name, (v, f) in parts.items():
        w.add_geom(name, v, f)
        ntri += len(f)
    t_geom = time.perf_counter() - t0
    names = list(parts)
    pos = []
    nx = int(380 // spacing)
    ny = int(284 // spacing)
    i = 0
    objs = {}
    t0 = time.perf_counter()
    while i < n_obj:
        x = (i % nx + 0.5) * spacing
        y = ((i // nx) % ny + 0.5) * spacing
        z = (i // (nx * ny) + 0.5) * spacing
        g = names[i % len(names)]
        M = meshes.matrix(meshes.rot(rng), (x, y, z))
        w.add_object(i, g, M)
        objs[i] = (g, M)
        i += 1
    t_add = time.perf_counter() - t0
    t0 = time.perf_counter()
    steps = 0
    while w.step(budget=0.02):
        steps += 1
    t_scan = time.perf_counter() - t0
    st = w.stats()
    if verbose:
        print(f'scene: {n_obj} objects, {st["triangles"] / 1e6:.2f} M triangles '
              f'({st["unique_triangles"] / 1e6:.2f} M unique), {st["pairs"]} candidate pairs, '
              f'{st["bytes"] / 1e6:.0f} MB cache')
        print(f'  sort unique meshes {t_geom * 1000:.0f} ms ({t_geom / ntri * 1e9:.0f} ns/tri), '
              f'pose all objects {t_add * 1000:.0f} ms, first full scan {t_scan * 1000:.0f} ms '
              f'in {steps + 1} steps, problems: {w.counts()}')
    return w, objs


def drag(w, objs, uid, path, label):
    g, M0 = objs[uid]
    times = []
    states = {OK: 0, CLEAR: 0, COLLIDE: 0}
    npairs = []
    for d in path:
        M = M0.copy()
        M[:3, 3] += d
        t0 = time.perf_counter()
        w.set_matrix(uid, M)
        w.step(idle=False)              # what happens on every frame of a drag
        times.append(time.perf_counter() - t0)
        worst = OK
        slot = w.slot(uid)
        n = 0
        for o in w.adj[slot]:
            key = (slot, o) if slot < o else (o, slot)
            worst = max(worst, w.pairs[key].state)
            n += 1
        npairs.append(n)
        states[worst] += 1
    t0 = time.perf_counter()
    while w.step():                     # exact pass once the part is released
        pass
    settle = (time.perf_counter() - t0) * 1000
    w.set_matrix(uid, M0)
    while w.step():
        pass
    report(label, times, f'neighbours ~{np.mean(npairs):.0f}  frames OK/CLEAR/COLLIDE '
           f'{states[OK]}/{states[CLEAR]}/{states[COLLIDE]}  settle {settle:.0f} ms')
    return times


def spin(w, objs, uid, n, label, rng):
    g, M0 = objs[uid]
    times = []
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    for i in range(n):
        a = 0.03 * (i + 1)
        R = np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * (K @ K)
        M = M0.copy()
        M[:3, :3] = R @ M0[:3, :3]
        t0 = time.perf_counter()
        w.set_matrix(uid, M)
        w.step(idle=False)
        times.append(time.perf_counter() - t0)
    w.set_matrix(uid, M0)
    while w.step():
        pass
    report(label, times)


def overlay_load(w):
    """Triangles and segments the overlay would have to draw for the current problems."""
    tris = segs = 0
    for pr in w.viol.values():
        for t in (pr.tri_a, pr.tri_b):
            if t is not None:
                tris += len(t)
        if pr.segs is not None:
            segs += len(pr.segs)
    return tris, segs


def storm(rng, n_parts=50, tris_small=100000, tris_big=400000, steps=60):
    """A new part imported into the middle of a full build: it lands on dozens
    of parts at once and is then dragged out of the way."""
    print()
    w = World()
    w.set_scale(400.0, 0.5)
    w.set_thresholds(0.0, 5.0)
    w.set_volume((0, 0, 0), (380, 284, 380))
    parts = make_parts(rng, tris_small)
    for name, (v, f) in parts.items():
        w.add_geom(name, v, f)
    names = list(parts)
    # parts packed around the middle of the volume
    side = int(np.ceil(n_parts ** (1 / 3)))
    k = 0
    for i in range(side ** 3):
        if k >= n_parts:
            break
        x, y, z = i % side, (i // side) % side, i // (side * side)
        t = np.array([190.0, 142.0, 190.0]) + (np.array([x, y, z]) - 0.5 * (side - 1)) * 48.0
        w.add_object(k, names[k % len(names)], meshes.matrix(meshes.rot(rng), t))
        k += 1
    while w.step(budget=0.05):
        pass
    kb = max(8, int(np.sqrt(tris_big / 2.0)))
    v, f = meshes.blob(rng, 75.0, 2 * kb, kb, 0.3)           # a large part, about 150 mm across
    w.add_geom('new', v, f)
    t0 = time.perf_counter()
    M0 = meshes.matrix(meshes.rot(rng), (190.0, 142.0, 190.0))
    w.add_object('new', 'new', M0)
    t_add = time.perf_counter() - t0
    t0 = time.perf_counter()
    w.step(idle=False)
    t_first = time.perf_counter() - t0
    slot = w.slot('new')

    def touching():
        n = c = 0
        for o in w.adj[slot]:
            pr = w.pairs[(slot, o) if slot < o else (o, slot)]
            n += 1
            c += pr.state == COLLIDE and not pr.stale
        return n, c

    n, c = touching()
    st = w.stats()
    print(f'import storm: {n_parts} parts of ~{tris_small // 1000}k triangles, new part '
          f'{len(f) // 1000}k triangles ({st["triangles"] / 1e6:.1f} M in all, {st["bytes"] / 1e6:.0f} MB)')
    print(f'  new part lands on {c} parts ({n} neighbours): posed in {t_add * 1000:.0f} ms, '
          f'first answer after {t_first * 1000:.0f} ms, overlay load {overlay_load(w)}')
    times, coll, load, late = [], [], [], []
    for i in range(steps):
        M = M0.copy()
        M[:3, 3] += np.array([1.0, 0.25, 0.1]) * (i + 1) * (330.0 / steps)   # out through a wall
        t0 = time.perf_counter()
        w.set_matrix('new', M)
        w.step(idle=False)
        times.append(time.perf_counter() - t0)
        coll.append(sum(1 for o in w.adj[slot]
                        if w.pairs[(slot, o) if slot < o else (o, slot)].state == COLLIDE))
        load.append(overlay_load(w)[0])
        late.append(len(w._pend_hot))             # answers that arrive a step later
    t = np.array(times) * 1000
    coll = np.array(coll)
    for lo, hi in ((20, 999), (8, 19), (1, 7), (0, 0)):
        m = (coll >= lo) & (coll <= hi)
        if m.any():
            label = f'{lo}+' if hi == 999 else (f'{lo}-{hi}' if hi != lo else f'{lo}')
            print(f'  dragging it out, colliding with {label:>5s} parts: {int(m.sum()):3d} steps, '
                  f'median {np.median(t[m]):7.1f} ms, worst {t[m].max():7.1f} ms, '
                  f'overlay triangles ~{int(np.median(np.array(load)[m])) // 1000}k, '
                  f'pairs answered a step late: at most {int(np.array(late)[m].max())}')
    t0 = time.perf_counter()
    while w.step():
        pass
    print(f'  settled {((time.perf_counter() - t0) * 1000):.0f} ms after release')


def main():
    rng = np.random.default_rng(5)
    quick = '--quick' in sys.argv
    if '--storm' in sys.argv:
        storm(rng, 50, 20000, 100000)
        storm(rng, 50, 100000, 400000)
        return
    configs = [(60, 20000, 46.0), (120, 100000, 46.0)]
    if not quick:
        configs.append((300, 100000, 44.0))
    for n_obj, tpp, spacing in configs:
        print()
        w, objs = build_scene(rng, n_obj, tpp, spacing)
        uid = n_obj // 2
        # a wandering drag that passes close to and through the neighbours
        steps = 240
        tt = np.linspace(0, 1, steps)
        path = np.stack([55 * np.sin(2 * np.pi * tt), 40 * np.sin(4 * np.pi * tt),
                         30 * np.sin(6 * np.pi * tt)], axis=1)
        drag(w, objs, uid, path, 'drag one part through its neighbours')
        # tiny motions around a spot where it is within clearance of a neighbour
        jitter = np.cumsum(rng.normal(0, 0.05, (120, 3)), axis=0) + np.array([4.0, 0, 0])
        drag(w, objs, uid, jitter, 'small moves near a neighbour')
        far = np.stack([np.zeros(60), np.zeros(60), 2000 + np.linspace(0, 100, 60)], axis=1)
        drag(w, objs, uid, far, 'drag far away from everything')
        spin(w, objs, uid, 60, 'rotate one part (re-fit + check)', rng)

    if not quick:
        print()
        print('two very large parts')
        w = World()
        w.set_scale(400.0, 0.5)
        w.set_thresholds(0.0, 5.0)
        t0 = time.perf_counter()
        v, f = meshes.blob(rng, 60.0, 1000, 500, 0.2)
        print(f'  (mesh generated: {len(f) / 1e6:.2f} M triangles)')
        t0 = time.perf_counter()
        w.add_geom('big', v, f)
        t_geom = time.perf_counter() - t0
        t0 = time.perf_counter()
        w.add_object(0, 'big', meshes.matrix(meshes.rot(rng), (100, 100, 100)))
        w.add_object(1, 'big', meshes.matrix(meshes.rot(rng), (240, 100, 100)))
        t_pose = time.perf_counter() - t0
        w.step()
        print(f'  sort {t_geom * 1000:.0f} ms, pose two objects {t_pose * 1000:.0f} ms')
        objs = {1: ('big', meshes.matrix(np.eye(3), (240, 100, 100)))}
        w.set_matrix(1, objs[1][1])
        w.step()
        tt = np.linspace(0, 1, 160)
        path = np.stack([-40 * tt, 8 * np.sin(6 * tt), 5 * np.sin(9 * tt)], axis=1)
        drag(w, objs, 1, path, 'drag a 1M-triangle part into another')
        spin(w, objs, 1, 20, 'rotate a 1M-triangle part', rng)


if __name__ == '__main__':
    main()
