"""Large meshes inside Blender (run with: blender --background --python this_file).

What the add-on does differently for parts too large to handle in one go:
reading mesh data the fast way, sorting in worker threads, borrowing poses
while a part is rotated, the memory limit, and the warnings about geometry
that is in the scene but is not checked.
"""

import importlib
import sys
import threading
import time

import bpy
import numpy as np

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bb = importlib.import_module(PKG)
monitor = bb.monitor
props = bb.props
overlay = bb.overlay
core = importlib.import_module(PKG + ".core")
OK, CLEAR, COLLIDE = core.OK, core.CLEAR, core.COLLIDE

CHECKS = []


def check(cond, label, detail=''):
    detail = str(detail)
    CHECKS.append((bool(cond), label))
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if detail else ''))
    if not cond:
        raise AssertionError(label + ' ' + detail)


def settle():
    sc = bpy.context.scene
    mon = monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    assert mon.settle(sc, vl.depsgraph, vl), 'monitor never settled'
    return mon


def tick():
    sc = bpy.context.scene
    mon = monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    mon.tick(sc, vl.depsgraph, vl, live=False)
    return mon


def update():
    bpy.context.view_layer.update()


class held_workers:
    """While this is open the worker threads take meshes but do not start
    sorting them, so that what happens "while a mesh is being sorted" does
    not depend on how fast the machine is."""

    def __enter__(self):
        self.go = threading.Event()
        self.real = monitor._sort_job

        def held(co, tri):
            self.go.wait(60.0)
            return self.real(co, tri)

        monitor._sort_job = held
        return self

    def __exit__(self, *exc):
        monitor._sort_job = self.real
        self.go.set()


def read_all(mon):
    """Tick until every queued mesh has been read (that can take more than
    one slice)."""
    for _ in range(500):
        if not mon.queue:
            break
        mon = tick()
    return mon


def blob(name, tris, radius, loc, seed=0):
    """A bumpy closed mesh of about ``tris`` triangles (triangles only)."""
    rng = np.random.default_rng(seed)
    rings = max(4, int(round(np.sqrt(tris / 4.0))))
    segs = 2 * rings
    th = np.pi * np.arange(1, rings) / rings
    ph = 2 * np.pi * np.arange(segs) / segs
    T, P = np.meshgrid(th, ph, indexing='ij')
    d = np.stack([np.sin(T) * np.cos(P), np.sin(T) * np.sin(P), np.cos(T)], axis=-1).reshape(-1, 3)
    d = np.concatenate([[[0.0, 0.0, 1.0]], d, [[0.0, 0.0, -1.0]]])
    k = rng.normal(size=(4, 3))
    bump = sum(np.sin(3.0 * d @ k[i] + i) for i in range(4)) / 4.0
    v = d * (radius * (1.0 + 0.2 * bump))[:, None]
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
    f = np.concatenate([top, mid, bot])
    me = bpy.data.meshes.new(name)
    me.from_pydata(v.tolist(), [], f.tolist())
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    bpy.context.collection.objects.link(ob)
    return ob


def slow_read(ob):
    """The mesh the plain, slow way: the reference for ``monitor.read_mesh``."""
    dg = bpy.context.evaluated_depsgraph_get()
    oe = ob.evaluated_get(dg)
    me = oe.to_mesh()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get('co', co)
    me.calc_loop_triangles()
    idx = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
    me.loop_triangles.foreach_get('vertices', idx)
    oe.to_mesh_clear()
    return co.reshape(-1, 3), idx.reshape(-1, 3)


def state(mon, a, b):
    w = mon.world
    sa, sb = w.slot(a.session_uid), w.slot(b.session_uid)
    if sa is None or sb is None:
        return None
    pr = w.pairs.get((sa, sb) if sa < sb else (sb, sa))
    return OK if pr is None or pr.stale else pr.state


def main():
    print('Blender', bpy.app.version_string)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    sc = bpy.context.scene
    st = sc.bucket_builder
    p = props.prefs()
    p.memory_gb = 0.0
    st.use_volume = False
    st.clearance_mm = 5.0

    # ------------------------------------------------------------ reading
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=10, location=(500, 0, 0))
    quads = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=5, depth=20, location=(500, 50, 0))
    ngons = bpy.context.active_object
    tri = blob('TriOnly', 4000, 10.0, (500, 100, 0))
    arr = quads.modifiers.new('Arr', 'ARRAY')
    update()
    dg = bpy.context.evaluated_depsgraph_get()
    for ob, what in ((tri, 'triangles only'), (quads, 'quads, with a modifier'), (ngons, 'n-gons')):
        co0, tri0 = slow_read(ob)
        co1, tri1 = monitor.read_mesh(ob.evaluated_get(dg))
        check(np.array_equal(co0, co1) and np.array_equal(tri0, tri1),
              f'fast mesh reading gives the same data ({what})', (co1.shape, tri1.shape))
    for ob in (quads, ngons, tri):
        bpy.data.objects.remove(ob)

    # ------------------------------------------------------ worker threads
    small = blob('Small', 2000, 15.0, (-200, 0, 0))
    a = blob('BigA', 200000, 40.0, (0, 0, 0), seed=1)
    b = blob('BigB', 200000, 40.0, (60, 0, 0), seed=2)
    c = bpy.data.objects.new('BigA.copy', a.data)             # what Alt+D makes
    c.location = (0, 150, 0)
    bpy.context.collection.objects.link(c)
    update()
    check(len(a.data.polygons) > monitor.SYNC_TRIS, 'test meshes are large enough for the workers',
          len(a.data.polygons))
    st.enabled = True
    with held_workers():
        mon = read_all(tick())
        s = mon.status()
        check(len(mon.jobs) == 2 and mon.world.slot(a.session_uid) is None,
              'large meshes go to worker threads, one job per mesh', (len(mon.jobs), s['preparing']))
        check(mon.world.slot(small.session_uid) is not None, 'a small mesh is taken in at once')
        check(s['busy'] and s['preparing'] is not None and s['preparing'][1] == 4
              and overlay.badge_text(s)[0] == 'BUSY', 'status shows the preparation',
              (s['preparing'], overlay.badge_text(s)))
    t0 = time.perf_counter()
    mon = settle()
    stats = mon.world.stats()
    check(stats['objects'] == 4 and stats['geoms'] == 3 and not mon.jobs,
          'all parts in after the workers finished; the linked copy shares its mesh',
          (stats['objects'], stats['geoms'], f'{time.perf_counter() - t0:.2f} s'))
    check(state(mon, a, b) == COLLIDE and state(mon, a, c) == OK, 'large parts are checked',
          (state(mon, a, b), state(mon, a, c)))

    # a mesh changes, and another object disappears, while being sorted
    d = blob('BigD', 150000, 30.0, (0, -150, 0), seed=3)
    e = blob('BigE', 150000, 30.0, (150, -150, 0), seed=4)
    update()
    with held_workers():
        mon = read_all(tick())
        check(len(mon.jobs) == 2, 'two more jobs', len(mon.jobs))
        co = np.empty(len(d.data.vertices) * 3, dtype=np.float32)
        d.data.vertices.foreach_get('co', co)
        d.data.vertices.foreach_set('co', co * 1.5)
        d.data.update()
        d.update_tag()
        bpy.data.objects.remove(e)
        update()
        mon = read_all(tick())
        check(len(mon.jobs) == 3, 'the edited mesh is on its way as well, the old one still being sorted',
              len(mon.jobs))
    mon = settle()
    lo, hi = mon.world.part_bounds(mon.world.slot(d.session_uid))
    ref = slow_read(d)[0]
    check(np.allclose(hi - lo, ref.max(axis=0) - ref.min(axis=0), rtol=1e-5),
          'a mesh edited while it was being sorted ends up as edited', (hi - lo).round(2))
    stats = mon.world.stats()
    check(stats['objects'] == 5 and stats['geoms'] == 4 and not mon.error,
          'an object deleted while it was being sorted leaves nothing behind',
          (stats['objects'], stats['geoms'], mon.error))

    # ------------------------------------------------- rotating a large part
    w = mon.world
    sb = w.slot(b.session_uid)
    seen = []
    borrowed = 0
    for i in range(8):
        b.rotation_euler = (0.1 * (i + 1), 0.05 * (i + 1), 0.0)
        t0 = time.perf_counter()
        update()                                     # the handler runs in here
        seen.append((time.perf_counter() - t0) * 1000)
        borrowed += bool(w.O_VIRT[sb])
        check(state(mon, a, b) == COLLIDE, f'rotation step {i}: answer follows at once', seen[-1])
    check(borrowed == 8, 'while it is rotated a large part borrows its pose', borrowed)
    mon = settle()
    check(not w.O_VIRT[sb] and w.stats()['borrowing'] == 0, 'at rest it has a pose of its own again')
    pr = w.pairs[(min(w.slot(a.session_uid), sb), max(w.slot(a.session_uid), sb))]
    nseg = pr.nseg
    monitor.force_recheck(sc)
    mon = settle()
    w = mon.world
    sa, sb = w.slot(a.session_uid), w.slot(b.session_uid)
    pr = w.pairs[(min(sa, sb), max(sa, sb))]
    check(pr.state == COLLIDE and pr.nseg == nseg, 'and the result is what a fresh check gives',
          (pr.nseg, nseg))
    print(f'       (rotation steps took {min(seen):.1f} to {max(seen):.1f} ms for '
          f'{len(b.data.polygons)} triangles)')

    # ---------------------------------------------------------------- memory
    total = props.installed_memory()
    auto = props.automatic_memory_limit()
    check(total is None or total > 2 ** 28, 'installed memory is found', total)
    check(props.memory_limit() == auto and 2 ** 30 <= auto <= 16 * 2 ** 30
          and mon.world.cache_limit == auto, 'automatic limit: a fifth of it, within 1 and 16 GB',
          (auto / 2 ** 30, None if total is None else total / 2 ** 30))
    # room for the meshes and one pose and a half: copies in other rotations share
    copies = []
    for i in range(4):
        o = bpy.data.objects.new(f'BigA.rot{i}', a.data)
        o.location = (40.0 * i, 300, 0)
        o.rotation_euler = (0.7 * i + 0.3, 0.4 * i, 0.2)
        bpy.context.collection.objects.link(o)
        copies.append(o)
    update()
    mon = settle()
    want = {k: (pr.state, pr.nseg) for k, pr in mon.world.viol.items()}
    names = {mon.world.uid(s_): s_ for s_ in mon.world._slot_of.values()}
    check(mon.world.evictions == 0 and len(want) >= 3, 'with memory to spare nothing is dropped',
          len(want))
    one = core.World.pose_size(len(a.data.vertices), len(a.data.polygons))
    st_w = mon.world.stats()
    p.memory_gb = (st_w['geom_bytes'] + 4.6 * one) / 2 ** 30
    mon = settle()
    monitor.force_recheck(sc)
    mon = settle()
    w = mon.world
    check(w.cache_limit == props.memory_limit() and w.pose_bytes + w.geom_bytes <= w.cache_limit,
          'the Memory preference limits the cached data',
          (round(w.cache_limit / 1e6), round((w.pose_bytes + w.geom_bytes) / 1e6)))
    got = {}
    for (sa_, sb_), pr in w.viol.items():
        ka, kb = names[w.uid(sa_)], names[w.uid(sb_)]
        got[(min(ka, kb), max(ka, kb))] = (pr.state, pr.nseg)
    check(w.evictions >= 1 and got == want,
          'poses then take turns in the memory there is, with the same results',
          (w.evictions, len(got)))
    # far too little: the large parts cannot be checked, and that is said
    p.memory_gb = 0.004
    mon = settle()
    monitor.force_recheck(sc)
    mon = settle()
    s = mon.status()
    text = overlay.badge_text(s)
    check(s['skipped'] >= 6 and mon.world.slot(small.session_uid) is not None
          and mon.world.slot(a.session_uid) is None,
          'parts too large for the memory limit are left out', s['skipped'])
    check(text[0] == 'WARN' and any('not checked' in line for line in text[2]),
          'and the badge says so instead of showing a plain pass', text)
    p.memory_gb = 0.0
    mon = settle()
    monitor.force_recheck(sc)
    mon = settle()
    check(mon.status()['skipped'] == 0 and mon.world.slot(a.session_uid) is not None,
          'back to automatic: everything is checked again')
    for o in copies:
        bpy.data.objects.remove(o)

    # ------------------------------------------------ what is not checked
    monitor.SCAN_SECONDS = 0.0
    for o in (a, b, c, d):
        bpy.data.objects.remove(o)
    update()
    mon = settle()
    check(mon.status()['objects'] == 1 and not mon.unchecked
          and overlay.badge_text(mon.status())[0] == 'OK', 'a plain scene has no warnings',
          (mon.status()['objects'], mon.unchecked))
    try:
        ng = bpy.data.node_groups.new('GN', 'GeometryNodeTree')
        ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        nin = ng.nodes.new('NodeGroupInput')
        nout = ng.nodes.new('NodeGroupOutput')
        inst = ng.nodes.new('GeometryNodeInstanceOnPoints')
        cube = ng.nodes.new('GeometryNodeMeshCube')
        join = ng.nodes.new('GeometryNodeJoinGeometry')
        ng.links.new(nin.outputs[0], inst.inputs['Points'])
        ng.links.new(cube.outputs['Mesh'], inst.inputs['Instance'])
        ng.links.new(nin.outputs[0], join.inputs[0])
        ng.links.new(inst.outputs['Instances'], join.inputs[0])
        ng.links.new(join.outputs[0], nout.inputs[0])
        bpy.ops.mesh.primitive_plane_add(size=50, location=(0, -400, 0))
        gn = bpy.context.active_object
        gm = gn.modifiers.new('GN', 'NODES')
        gm.node_group = ng
        update()
        mon = settle()
        text = overlay.badge_text(mon.status())
        check(mon.unchecked == {'instances': 1} and text[:2] == ('WARN', 'Not Everything Checked')
              and any('unchecked instances' in line for line in text[2]),
              'instances that geometry nodes do not realize are reported', (mon.unchecked, text))
        # A hidden part is checked like any other, so its instances are
        # missing from the check just the same.  (Blender does not list them.)
        gn.hide_set(True)
        update()
        mon = settle()
        check(mon.status()['hidden'] == 1 and mon.unchecked == {'instances': 1},
              'also when the part that makes them is hidden', (mon.status()['hidden'], mon.unchecked))
        st.ignore_hidden = True
        mon = settle()
        check(mon.status()['objects'] == 1 and not mon.unchecked,
              'but not when hidden parts are ignored', (mon.status()['objects'], mon.unchecked))
        st.ignore_hidden = False
        gn.hide_set(False)
        update()
        mon = settle()
        check(mon.unchecked == {'instances': 1}, 'shown again, they are reported as before', mon.unchecked)
        real = ng.nodes.new('GeometryNodeRealizeInstances')
        ng.links.new(join.outputs[0], real.inputs[0])
        ng.links.new(real.outputs[0], nout.inputs[0])
        update()
        mon = settle()
        check(not mon.unchecked and mon.objs[gn.session_uid].ntri == 50,
              'realized, they are checked and the warning goes', (mon.unchecked, mon.objs[gn.session_uid].ntri))
        bpy.data.objects.remove(gn)
    except (AttributeError, KeyError, RuntimeError) as ex:
        print('       (geometry nodes not available in this build:', str(ex)[:80], ')')

    bpy.ops.object.text_add(location=(300, -400, 0))
    txt = bpy.context.active_object
    txt.data.extrude = 2.0
    bpy.ops.curve.primitive_bezier_curve_add(location=(350, -400, 0))    # a bare curve: no faces
    update()
    mon = settle()
    check(mon.unchecked == {'other': 1}, 'a text object is reported, a curve without faces is not',
          mon.unchecked)
    txt.hide_set(True)
    update()
    mon = settle()
    check(mon.unchecked == {'other': 1}, 'hidden, it is still part of the build and still reported',
          mon.unchecked)
    txt.hide_set(False)
    txt.hide_viewport = True
    update()
    mon = settle()
    check(not mon.unchecked, 'disabled in viewports, it is not', mon.unchecked)
    txt.hide_viewport = False
    update()
    mon = settle()
    txt.select_set(True)
    bpy.context.view_layer.objects.active = txt
    bpy.ops.object.select_all(action='DESELECT')
    txt.select_set(True)
    bpy.ops.bucketbuilder.ignore(ignore=True)
    mon = settle()
    check(not mon.unchecked, 'ignoring it clears the warning', mon.unchecked)

    coll = bpy.data.collections.new('Kit')
    part = blob('KitPart', 2000, 10.0, (0, 0, 0), seed=9)
    bpy.context.collection.objects.unlink(part)
    coll.objects.link(part)
    holder = bpy.data.objects.new('KitInstance', None)
    holder.instance_type = 'COLLECTION'
    holder.instance_collection = coll
    holder.location = (0, -500, 0)
    bpy.context.collection.objects.link(holder)
    update()
    mon = settle()
    text = overlay.badge_text(mon.status())
    check(mon.unchecked == {'collections': 1} and text[0] == 'WARN',
          'a collection instance is reported', (mon.unchecked, text))
    holder.hide_set(True)
    update()
    mon = settle()
    check(mon.unchecked == {'collections': 1}, 'a hidden one too', mon.unchecked)
    st.ignore_hidden = True
    mon = settle()
    check(not mon.unchecked, 'unless hidden parts are ignored', mon.unchecked)
    st.ignore_hidden = False
    holder.hide_set(False)
    update()
    mon = settle()
    check(mon.unchecked == {'collections': 1}, 'and it is reported again when shown', mon.unchecked)
    bpy.data.objects.remove(holder)
    update()
    mon = settle()
    check(not mon.unchecked and overlay.badge_text(mon.status())[0] == 'OK',
          'and the plain pass returns when it is gone', mon.unchecked)

    st.enabled = False
    print(f'\n{sum(1 for ok, _ in CHECKS if ok)} of {len(CHECKS)} checks passed')
    print('BLENDER LARGE TEST OK')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('BLENDER LARGE TEST FAILED')
    sys.exit(1)
