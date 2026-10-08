"""How the add-on copes with a build of large parts, measured inside Blender.

    blender --background --python this_file -- [parts] [triangles per part] [copies] [memory GB]

Builds ``parts`` different meshes of about that many triangles each (default
8 parts of 1.5 M), optionally with ``copies`` linked duplicates (what Alt+D
makes) of each in other rotations, switches monitoring on and drives the
add-on the way Blender's timer would.  What matters for the feel is the
longest single slice: that is how long the interface would not respond.
``memory GB`` sets the add-on's Memory preference for the run (0, the
default, leaves it automatic: a fifth of what the machine has).
"""

import importlib
import sys
import time

import bpy
import numpy as np

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bb = importlib.import_module(PKG)
monitor = bb.monitor
core = importlib.import_module(PKG + ".core")

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
N_PARTS = int(ARGS[0]) if len(ARGS) > 0 else 8
TRIS = int(ARGS[1]) if len(ARGS) > 1 else 1500000
COPIES = int(ARGS[2]) if len(ARGS) > 2 else 0
MEMORY_GB = float(ARGS[3]) if len(ARGS) > 3 else 0.0
VOLUME = np.array([380.0, 284.0, 380.0])


def blob_mesh(name, tris, radius, seed):
    """A bumpy closed triangle mesh, made without Python loops."""
    rng = np.random.default_rng(seed)
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
    v = (d * (radius * (1.0 + 0.22 * bump))[:, None]).astype(np.float32)
    s = np.arange(segs)
    top = np.stack([np.zeros(segs, dtype=np.int64), 1 + s, 1 + (s + 1) % segs], axis=1)
    r = np.arange(rings - 2)[:, None]
    a = 1 + r * segs + s[None, :]
    b = 1 + r * segs + (s[None, :] + 1) % segs
    c = a + segs
    e = b + segs
    mid = np.concatenate([np.stack([a, c, e], -1).reshape(-1, 3), np.stack([a, e, b], -1).reshape(-1, 3)])
    last = len(v) - 1
    off = 1 + (rings - 2) * segs
    bot = np.stack([np.full(segs, last), off + (s + 1) % segs, off + s], axis=1)
    f = np.concatenate([top, mid, bot]).astype(np.int32)
    f = f[rng.permutation(len(f))]
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(v))
    me.loops.add(3 * len(f))
    me.polygons.add(len(f))
    me.vertices.foreach_set('co', v.ravel())
    me.loops.foreach_set('vertex_index', f.ravel())
    me.polygons.foreach_set('loop_start', np.arange(0, 3 * len(f), 3, dtype=np.int32))
    me.update()
    return me


def rotation(rng):
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    return q


def grid(n):
    s = (VOLUME.prod() / n) ** (1.0 / 3.0)
    while True:
        dims = np.maximum(1, np.floor(VOLUME / s)).astype(int)
        if dims.prod() >= n:
            break
        s *= 0.98
    cells = [(np.array([i % dims[0], (i // dims[0]) % dims[1], i // (dims[0] * dims[1])]) + 0.5)
             * (VOLUME / dims) for i in range(int(dims.prod()))]
    return s, np.array(cells)


def drive(mon, label, until=None, limit=3600.0):
    """Tick like the timer does until the monitor is idle; report the slices."""
    sc = bpy.context.scene
    vl = bpy.context.view_layer
    slices = []
    first = None
    known = None
    t0 = time.perf_counter()
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    while time.perf_counter() - t0 < limit:
        t1 = time.perf_counter()
        mon.tick(sc, vl.depsgraph, vl, live=False)
        slices.append(time.perf_counter() - t1)
        now = time.perf_counter() - t0
        st = mon.status()
        if first is None and mon.world.pairs and any(not pr.stale for pr in mon.world.pairs.values()):
            first = now
        if known is None and not st['busy']:
            known = now
        if not mon.busy or (until is not None and until(mon)):
            break
        if mon.jobs and not (mon.queue or mon.world.busy):
            time.sleep(0.005)             # the timer would come back in 20 ms
    total = time.perf_counter() - t0
    sl = np.array(slices) * 1000
    print(f'{label}: {total:.1f} s; first results after {first if first is not None else float("nan"):.1f} s, '
          f'verdict final after {known if known is not None else float("nan"):.1f} s')
    print(f'    {len(sl)} slices on the main thread: median {np.median(sl):.0f} ms, 95 % under '
          f'{np.percentile(sl, 95):.0f} ms, longest {sl.max():.0f} ms')
    return sl


def main():
    print('Blender', bpy.app.version_string)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    rng = np.random.default_rng(7)
    total = N_PARTS * (1 + COPIES)
    cell, cells = grid(total)
    order = rng.permutation(len(cells))[:total]
    t0 = time.perf_counter()
    objs = []
    k = 0
    for i in range(N_PARTS):
        me = blob_mesh(f'Part{i:02d}', TRIS, 0.46 * cell, 100 + i)
        for j in range(1 + COPIES):
            ob = bpy.data.objects.new(f'Part{i:02d}.{j:03d}', me)
            ob.location = cells[order[k]]
            ob.rotation_mode = 'QUATERNION'
            ob.rotation_quaternion = rotation(rng)
            bpy.context.collection.objects.link(ob)
            objs.append(ob)
            k += 1
    bpy.context.view_layer.update()
    ntri = sum(len(o.data.polygons) for o in objs)
    print(f'{len(objs)} parts, {N_PARTS} different meshes of {len(objs[0].data.polygons)} triangles: '
          f'{ntri / 1e6:.1f} M triangles in the build (made in {time.perf_counter() - t0:.0f} s)')

    sc = bpy.context.scene
    st = sc.bucket_builder
    prefs = bb.props.prefs()
    old_memory = prefs.memory_gb
    prefs.memory_gb = MEMORY_GB
    st.enabled = True
    mon = monitor.get(sc, create=True)
    drive(mon, 'switching monitoring on')
    s = mon.status()
    stats = mon.world.stats()
    print(f'    {s["objects"]} parts checked, {stats["pairs"]} neighbouring pairs: {s["collisions"]} collisions, '
          f'{s["clearance"]} too close, {s["partly_out"]} partly outside; {s["skipped"]} left out')
    print(f'    cached data {stats["bytes"] / 1e6:.0f} MB (limit {stats["cache_limit"] / 1e6:.0f} MB), '
          f'{stats["evictions"]} poses dropped for want of memory')

    # move and rotate the part nearest to the middle, the way a modal edit does
    mid = 0.5 * VOLUME
    ob = min(objs, key=lambda o: float(np.linalg.norm(np.array(o.location) - mid)))
    home = np.array(ob.location)
    vl = bpy.context.view_layer
    steps = 40
    times = []
    for i in range(steps):
        tt = (i + 1) / steps
        ob.location = home + np.array([1.2 * cell * np.sin(2 * np.pi * tt), 0.8 * cell * np.sin(4 * np.pi * tt),
                                       0.5 * cell * np.sin(6 * np.pi * tt)])
        t1 = time.perf_counter()
        vl.update()                               # the add-on's handler runs in here
        times.append(time.perf_counter() - t1)
    t = np.array(times) * 1000
    print(f'dragging a part of {len(ob.data.polygons)} triangles: per step median {np.median(t):.1f} ms, '
          f'95 % under {np.percentile(t, 95):.1f} ms, longest {t.max():.1f} ms '
          f'(includes Blender\'s own update of the scene)')
    drive(mon, '    settling after the drag')
    q0 = np.array(ob.rotation_quaternion)
    times = []
    for i in range(20):
        a = 0.04 * (i + 1)
        dq = np.array([np.cos(a / 2), np.sin(a / 2) * 0.6, np.sin(a / 2) * 0.64, np.sin(a / 2) * 0.48])
        w0, x0, y0, z0 = dq
        w1, x1, y1, z1 = q0
        ob.rotation_quaternion = (w0 * w1 - x0 * x1 - y0 * y1 - z0 * z1, w0 * x1 + x0 * w1 + y0 * z1 - z0 * y1,
                                  w0 * y1 - x0 * z1 + y0 * w1 + z0 * x1, w0 * z1 + x0 * y1 - y0 * x1 + z0 * w1)
        t1 = time.perf_counter()
        vl.update()
        times.append(time.perf_counter() - t1)
    t = np.array(times) * 1000
    print(f'rotating it: per step median {np.median(t):.1f} ms, 95 % under {np.percentile(t, 95):.1f} ms, '
          f'longest {t.max():.1f} ms')
    drive(mon, '    settling after the rotation')
    if mon.error:
        print('error reported by the monitor:', mon.error)
    st.enabled = False
    prefs.memory_gb = old_memory
    print('LARGE BENCH DONE')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('LARGE BENCH FAILED')
    sys.exit(1)
