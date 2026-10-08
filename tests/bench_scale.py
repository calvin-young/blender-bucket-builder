"""Timing and memory at the sizes of real builds (NumPy only).

    python3 tests/bench_scale.py parts 20 1500000
        20 different parts of 1.5 M triangles each (30 M in all)

    python3 tests/bench_scale.py instances 600 300000 12 [limit_gb]
        600 parts, copies of 12 different meshes of 300 k triangles each, every
        copy with its own rotation (180 M triangles in all); optionally with a
        memory limit for the cached data

What is measured: sorting the meshes, the first complete check (in the slices
the add-on's timer would run it in, so the longest slice is the longest freeze
of the interface), dragging and rotating one part, and memory.
"""

import os
import resource
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))   # import the core without bpy
sys.path.insert(0, HERE)

from core import World, OK, CLEAR, COLLIDE  # noqa: E402
import meshes  # noqa: E402

VOLUME = np.array([380.0, 284.0, 380.0])
PRETOUCH = True


def big_blob(rng, radius, tris, noise=0.22):
    """A bumpy closed surface with about ``tris`` triangles, built without
    Python loops.  Returns float32 vertices and int32 triangles."""
    rings = max(4, int(round(np.sqrt(tris / 4.0))))
    segs = 2 * rings
    th = np.pi * np.arange(1, rings) / rings
    ph = 2 * np.pi * np.arange(segs) / segs
    T, P = np.meshgrid(th, ph, indexing='ij')
    d = np.stack([np.sin(T) * np.cos(P), np.sin(T) * np.sin(P), np.cos(T)], axis=-1).reshape(-1, 3)
    d = np.concatenate([[[0.0, 0.0, 1.0]], d, [[0.0, 0.0, -1.0]]])
    k = rng.normal(size=(6, 3))
    bump = sum(np.sin(3.0 * d @ k[i] + i) for i in range(4)) / 4.0
    bump += 0.3 * sum(np.sin(9.0 * d @ k[i] + i) for i in range(4, 6)) / 2.0
    v = d * (radius * (1.0 + noise * bump))[:, None]
    s = np.arange(segs)
    top = np.stack([np.zeros(segs, dtype=np.int64), 1 + s, 1 + (s + 1) % segs], axis=1)
    r = np.arange(rings - 2)[:, None]
    a = 1 + r * segs + s[None, :]
    b = 1 + r * segs + (s[None, :] + 1) % segs
    c = a + segs
    e = b + segs
    mid = np.concatenate([np.stack([a, c, e], -1).reshape(-1, 3),
                          np.stack([a, e, b], -1).reshape(-1, 3)])
    last = len(v) - 1
    off = 1 + (rings - 2) * segs
    bot = np.stack([np.full(segs, last), off + (s + 1) % segs, off + s], axis=1)
    f = np.concatenate([top, mid, bot])
    # files rarely store triangles in a spatially coherent order
    f = f[rng.permutation(len(f))]
    return v.astype(np.float32), f.astype(np.int32)


def rss_mb():
    with open('/proc/self/statm') as fh:
        return int(fh.read().split()[1]) * resource.getpagesize() / 1e6


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def grid(n):
    """Cell size and cell centres for ``n`` parts filling the build volume."""
    s = (VOLUME.prod() / n) ** (1.0 / 3.0)
    while True:
        dims = np.maximum(1, np.floor(VOLUME / s)).astype(int)
        if dims.prod() >= n:
            break
        s *= 0.98
    cells = []
    for i in range(int(dims.prod())):
        x, y, z = i % dims[0], (i // dims[0]) % dims[1], i // (dims[0] * dims[1])
        cells.append((np.array([x, y, z]) + 0.5) * (VOLUME / dims))
    return s, np.array(cells)


def pcts(times):
    t = np.asarray(times) * 1000.0
    return f'median {np.median(t):7.1f} ms   p95 {np.percentile(t, 95):7.1f}   worst {t.max():7.1f}'


def first_check(w, budget=0.012):
    slices = []
    t0 = time.perf_counter()
    while True:
        t1 = time.perf_counter()
        more = w.step(budget=budget)
        slices.append(time.perf_counter() - t1)
        if not more:
            break
    return time.perf_counter() - t0, slices


def exercise(w, mats, uid, rng, cell):
    """Drag and rotate one part the way a user would."""
    g, M0 = mats[uid]
    out = {}
    steps = 90
    tt = np.linspace(0, 1, steps)
    path = np.stack([1.4 * cell * np.sin(2 * np.pi * tt), 1.0 * cell * np.sin(4 * np.pi * tt),
                     0.8 * cell * np.sin(6 * np.pi * tt)], axis=1)
    times = []
    nb = []
    for d in path:
        M = M0.copy()
        M[:3, 3] += d
        t0 = time.perf_counter()
        w.set_matrix(uid, M)
        w.step(idle=False)
        times.append(time.perf_counter() - t0)
        nb.append(len(w.adj[w.slot(uid)]))
    t0 = time.perf_counter()
    while w.step():
        pass
    settle = time.perf_counter() - t0
    print(f'  drag one part through its neighbours   {pcts(times)}   (~{np.mean(nb):.0f} neighbours; '
          f'exact results {settle * 1000:.0f} ms after release)')

    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    times = []
    for i in range(30):
        a = 0.03 * (i + 1)
        R = np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * (K @ K)
        M = M0.copy()
        M[:3, :3] = R @ M0[:3, :3]
        t0 = time.perf_counter()
        w.set_matrix(uid, M)
        w.step(idle=False)
        times.append(time.perf_counter() - t0)
    t0 = time.perf_counter()
    while w.step():
        pass
    settle = time.perf_counter() - t0
    print(f'  rotate one part                        {pcts(times)}   (exact results '
          f'{settle * 1000:.0f} ms after release)')
    w.set_matrix(uid, M0)
    while w.step():
        pass
    out['drag'] = times
    return out


def report_memory(w, label=''):
    st = w.stats()
    print(f'  memory{label}: meshes {w.geom_bytes / 1e6:.0f} MB, poses {st["pose_bytes"] / 1e6:.0f} MB '
          f'({st["poses"]} poses), process {rss_mb():.0f} MB now, {peak_mb():.0f} MB at peak'
          + (f', limit {st["cache_limit"] / 1e6:.0f} MB, {st["evictions"]} evictions'
             if st["cache_limit"] else ''))


def run(n_parts, tris, n_unique, limit_gb, rng):
    w = World()
    w.set_scale(400.0, 0.5)
    w.set_thresholds(0.0, 5.0)
    w.set_volume((0, 0, 0), VOLUME)
    if limit_gb:
        w.set_cache_limit(int(limit_gb * 1e9))
    cell, cells = grid(n_parts)
    radius = 0.46 * cell
    t_gen = time.perf_counter()
    t_sort = 0.0
    worst_sort = 0.0
    total_unique = 0
    for k in range(n_unique):
        v, f = big_blob(rng, radius, tris)
        t0 = time.perf_counter()
        w.add_geom(k, v, f)
        dt = time.perf_counter() - t0
        t_sort += dt
        worst_sort = max(worst_sort, dt)
        total_unique += len(f)
        del v, f
    t_gen = time.perf_counter() - t_gen - t_sort
    order = rng.permutation(len(cells))[:n_parts]
    mats = {}
    t0 = time.perf_counter()
    for i in range(n_parts):
        M = meshes.matrix(meshes.rot(rng), cells[order[i]])
        g = i % n_unique
        w.add_object(i, g, M)
        mats[i] = (g, M)
    t_add = time.perf_counter() - t0
    st = w.stats()
    print(f'{n_parts} parts, {n_unique} different meshes of {total_unique / n_unique / 1e6:.2f} M triangles: '
          f'{st["triangles"] / 1e6:.1f} M triangles in the build, {total_unique / 1e6:.1f} M unique '
          f'(test meshes generated in {t_gen:.0f} s)')
    print(f'  sorting the meshes: {t_sort:.1f} s in all ({t_sort / total_unique * 1e9:.0f} ns per triangle), '
          f'{worst_sort:.2f} s for one mesh; adding the parts {t_add * 1000:.0f} ms')
    if PRETOUCH:
        w.reserve()
        data = w.BOX.data
        for s in range(0, data.shape[0], 1 << 20):
            data[s:s + (1 << 20)] = 0.0
        del data
    t_scan, slices = first_check(w)
    nc, ncl, npart, nout, nwall = w.counts()
    sl = np.asarray(slices) * 1000
    print(f'  first complete check: {t_scan:.1f} s in {len(slices)} slices, longest slice {sl.max():.0f} ms, '
          f'median {np.median(sl):.0f} ms; {st["pairs"] if False else len(w.pairs)} neighbouring pairs, '
          f'{nc} collisions, {ncl} too close, {npart} partly outside')
    report_memory(w)
    # the part with the most neighbours near the middle of the volume
    mid = 0.5 * VOLUME
    best = min(mats, key=lambda u: float(np.linalg.norm(mats[u][1][:3, 3] - mid)))
    exercise(w, mats, best, rng, cell)
    report_memory(w, ' afterwards')


def main():
    args = sys.argv[1:]
    rng = np.random.default_rng(7)
    global PRETOUCH
    if 'cold' in args:
        # as it is: includes the first use of every page of memory
        args.remove('cold')
        PRETOUCH = False
    else:
        print('(pose storage written to once before timing: in the virtual machine these numbers '
              'come from,\n the first use of a page of memory is some twenty times slower than on '
              'real hardware)')
    if not args or args[0] == 'parts':
        n = int(args[1]) if len(args) > 1 else 20
        tris = int(args[2]) if len(args) > 2 else 1500000
        limit = float(args[3]) if len(args) > 3 else 0.0
        run(n, tris, n, limit, rng)
    elif args[0] == 'instances':
        n = int(args[1]) if len(args) > 1 else 600
        tris = int(args[2]) if len(args) > 2 else 300000
        uniq = int(args[3]) if len(args) > 3 else 12
        limit = float(args[4]) if len(args) > 4 else 0.0
        run(n, tris, uniq, limit, rng)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
