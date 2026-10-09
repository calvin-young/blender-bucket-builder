"""End-to-end test inside Blender (run with: blender --background --python this_file).

Exercises the add-on exactly as installed: registration, the dependency-graph
handler that drives live updates, geometry changes, visibility, the build
volume, units, printer profiles, save / reload.
"""

import importlib
import math
import os
import sys
import tempfile
import time

import bpy
import numpy as np

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor = bc.monitor
props = bc.props
core = importlib.import_module(PKG + ".core")
OK, CLEAR, COLLIDE, PARTIAL, OUTSIDE = core.OK, core.CLEAR, core.COLLIDE, core.PARTIAL, core.OUTSIDE

CHECKS = []


def check(cond, label, detail=''):
    CHECKS.append((bool(cond), label))
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if detail else ''))
    if not cond:
        raise AssertionError(label + ' ' + str(detail))


def scene():
    return bpy.context.scene


def settle():
    """Run background slices until nothing is pending (timers do not run in
    background mode, so the test drives them)."""
    sc = scene()
    mon = monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    assert mon.settle(sc, vl.depsgraph, vl), 'monitor never settled'
    return mon


def update():
    """What Blender does after every step of an interactive edit."""
    bpy.context.view_layer.update()


def pair(mon, a, b):
    w = mon.world
    sa, sb = w.slot(a.session_uid), w.slot(b.session_uid)
    if sa is None or sb is None:
        return None
    return w.pairs.get((sa, sb) if sa < sb else (sb, sa))


def state(mon, a, b):
    pr = pair(mon, a, b)
    return OK if pr is None or pr.stale else pr.state


def oob(mon, a):
    r = mon.world.oob.get(mon.world.slot(a.session_uid))
    return None if r is None else r.state


def add_sphere(name, loc, radius=20.0, segs=48):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segs, ring_count=segs // 2, radius=radius, location=loc)
    o = bpy.context.active_object
    o.name = name
    return o


def add_cube(name, loc, size=40.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.active_object
    o.name = name
    return o


def main():
    print('Blender', bpy.app.version_string, '| numpy', np.__version__, '| python', sys.version.split()[0])
    print('add-on module:', PKG)

    # ---------------------------------------------------------------- setup
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    sc = scene()
    st = sc.bucket_builder
    check(st is not None, 'scene settings registered')
    check(hasattr(bpy.types.Object, 'bucket_builder_ignore'), 'per-object ignore flag registered')

    a = add_sphere('PartA', (100, 100, 100))
    b = add_sphere('PartB', (160, 100, 100))
    c = add_cube('PartC', (100, 200, 100))
    update()

    st.enabled = True
    mon = settle()
    s = mon.status()
    check(s['objects'] == 3, 'three parts mirrored', s)
    check(mon.mm_per_unit == 1.0, 'units auto-detected as 1 unit = 1 mm', mon.unit_note)
    check(s['collisions'] == 0 and s['clearance'] == 0, 'clean scene has no problems', s)
    check(s['partly_out'] == 0 and s['outside'] == 0, 'all parts inside the default volume', s)

    # -------------------------------------------------- live updates (handler)
    # No manual tick here: the dependency-graph handler alone must produce the
    # result, as it does on every step of a drag in the viewport.
    b.location.x = 135.0
    update()
    check(state(mon, a, b) == COLLIDE, 'translation -> collision seen by the live handler',
          state(mon, a, b))
    pr = pair(mon, a, b)
    check(pr.nseg > 0 and pr.segs is not None, 'intersection curve available', pr.nseg)
    seg_world = pr.segs.reshape(-1, 3) + mon.world.O_T[mon.world.slot(a.session_uid)]
    # spheres r=20 centred x=100 and x=135 meet on the plane x=117.5
    check(abs(float(seg_world[:, 0].mean()) - 117.5) < 0.5, 'curve lies where the spheres meet',
          float(seg_world[:, 0].mean()))

    b.location.x = 143.0
    update()
    check(state(mon, a, b) == CLEAR, 'translation -> clearance warning (live)', state(mon, a, b))
    mon = settle()
    pr = pair(mon, a, b)
    check(abs(pr.dist - 3.0) < 0.15, 'clearance distance ~3 mm', pr.dist)

    b.location.x = 160.0
    update()
    check(state(mon, a, b) == OK, 'translation away -> clear (live)')

    # a long drag: many small steps, each handled by the handler
    t0 = time.perf_counter()
    n = 0
    worst = 0.0
    for x in np.linspace(160.0, 120.0, 81):
        t1 = time.perf_counter()
        b.location.x = float(x)
        update()
        worst = max(worst, time.perf_counter() - t1)
        n += 1
    dt = (time.perf_counter() - t0) / n * 1000
    check(state(mon, a, b) == COLLIDE, 'state correct at the end of a simulated drag')
    print(f'       simulated drag: {dt:.2f} ms per step on average, worst {worst * 1000:.2f} ms '
          f'(includes Blender\'s own update)')
    b.location.x = 160.0
    update()

    # ------------------------------------------------------ rotation / scale
    c.location = (100.0, 147.0, 100.0)      # cube face 7 mm from sphere A
    update()
    check(state(mon, a, c) == OK, 'cube 7 mm away is fine')
    c.rotation_euler = (0.0, 0.0, math.radians(45.0))   # corner now reaches the sphere
    update()
    check(state(mon, a, c) == COLLIDE, 'rotation -> collision (live)', state(mon, a, c))
    c.rotation_euler = (0.0, 0.0, 0.0)
    c.scale = (1.0, 1.25, 1.0)                           # face moves 5 mm closer: 2 mm gap
    update()
    check(state(mon, a, c) == CLEAR, 'scale -> clearance warning (live)', state(mon, a, c))
    mon = settle()
    check(abs(pair(mon, a, c).dist - 2.0) < 0.15, 'gap after scaling ~2 mm', pair(mon, a, c).dist)
    c.scale = (1.0, 1.0, 1.0)
    c.location = (100.0, 200.0, 100.0)
    update()
    mon = settle()
    check(mon.status()['clearance'] == 0 and mon.status()['collisions'] == 0, 'back to clean')

    # ------------------------------------------------------- geometry change
    stats0 = mon.world.stats()
    me = b.data
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get('co', co)
    me.vertices.foreach_set('co', co * 2.1)          # radius 20 -> 42: reaches 2 mm into A
    me.update()
    update()
    mon = settle()
    check(state(mon, a, b) == COLLIDE, 'edited mesh picked up (bigger sphere now collides)',
          state(mon, a, b))
    me.vertices.foreach_set('co', co)
    me.update()
    update()
    mon = settle()
    check(state(mon, a, b) == OK, 'mesh edit reverted -> clear')

    mod = c.modifiers.new('Arr', 'ARRAY')      # (works in every build, unlike Subdivision)
    mod.count = 3
    update()
    mon = settle()
    tri_c = mon.objs[c.session_uid].ntri
    check(tri_c == 12 * 3, 'modifier result is what gets checked (evaluated mesh)', tri_c)
    c.modifiers.remove(mod)
    update()
    mon = settle()
    check(mon.objs[c.session_uid].ntri == 12, 'modifier removed -> original mesh again')

    # ------------------------------------------------ shared data / instances
    d = a.copy()                      # linked duplicate: same mesh data
    d.name = 'PartA.linked'
    d.location = (40.0, 100.0, 100.0)
    bpy.context.collection.objects.link(d)
    update()
    mon = settle()
    stats1 = mon.world.stats()
    check(stats1['objects'] == 4, 'linked duplicate mirrored', stats1)
    check(stats1['unique_triangles'] == stats0['unique_triangles'],
          'linked duplicate shares the cached geometry', (stats0, stats1))
    e = a.copy()
    e.data = a.data.copy()            # full copy: separate but identical mesh
    e.name = 'PartA.copy'
    e.location = (40.0, 160.0, 100.0)
    bpy.context.collection.objects.link(e)
    update()
    mon = settle()
    check(mon.world.stats()['unique_triangles'] == stats0['unique_triangles'],
          'identical copy is recognised and shares geometry too')

    # ------------------------------------------------- visibility / ignoring
    # A hidden part is still in the build: it stays in the check unless the
    # user says otherwise.
    badge = bc.overlay.badge_text
    d.hide_set(True)
    update()
    mon = settle()
    s = mon.status()
    check(s['objects'] == 5 and s['hidden'] == 1 and s['hidden_problems'] == 0,
          'a hidden part stays in the check', s)
    check(badge(s)[2][0] == '5 parts (1 hidden), no collisions', 'and the verdict says how many are hidden',
          badge(s))
    d.location.x = 70.0               # moved, unseen, into part A
    update()
    check(state(mon, a, d) == COLLIDE, 'a hidden part that is moved is checked like any other (live)',
          state(mon, a, d))
    mon = settle()
    s = mon.status()
    hid = [q for q in mon.problems() if '(hidden)' in q['a'] or '(hidden)' in q['b']]
    check(s['hidden_problems'] == 1 and len(hid) == 1 and hid[0]['kind'] == 'COLLIDE'
          and 'PartA.linked (hidden)' in (hid[0]['a'], hid[0]['b']),
          'its problems are marked as involving a hidden part', (s['hidden_problems'], hid))
    check('1 problem with hidden parts' in badge(s)[2], 'and the verdict says so', badge(s))
    check(mon.world.slot(d.session_uid) in mon.hidden_slots(), 'the overlay knows which part not to shade')
    st.ignore_hidden = True
    mon = settle()
    s = mon.status()
    check(s['objects'] == 4 and s['hidden'] == 0 and s['collisions'] == 0,
          'with Ignore Hidden Parts it leaves the check', s)
    d.hide_set(False)
    update()
    mon = settle()
    check(mon.status()['objects'] == 5 and mon.status()['collisions'] == 1, 'shown again, it is back', mon.status())
    st.ignore_hidden = False
    d.location.x = 40.0
    update()
    mon = settle()
    check(mon.status()['collisions'] == 0 and not mon.ignore_hidden, 'and the setting is off again')
    # the eye of a collection hides its parts in the same way
    tray = bpy.data.collections.new('Tray')
    sc.collection.children.link(tray)
    for coll in list(d.users_collection):
        coll.objects.unlink(d)
    tray.objects.link(d)
    update()
    mon = settle()
    layer = bpy.context.view_layer.layer_collection.children['Tray']
    layer.hide_viewport = True
    update()
    mon = settle()
    s = mon.status()
    check(s['objects'] == 5 and s['hidden'] == 1, 'a part in a hidden collection is checked too', s)
    layer.hide_viewport = False
    layer.exclude = True              # not in this view layer at all
    update()
    mon = settle()
    s = mon.status()
    check(s['objects'] == 4 and s['hidden'] == 0, 'a collection excluded from the view layer is not in the build', s)
    layer.exclude = False
    update()
    mon = settle()
    check(mon.status()['objects'] == 5, 'included again, its part returns')
    # Disabled in viewports is different: Blender does not work such an object
    # out at all (its place, its modifiers), so there is nothing to check.
    d.hide_viewport = True
    update()
    mon = settle()
    check(mon.status()['objects'] == 4 and mon.disabled == 1,
          'a part disabled in viewports cannot be checked; it is counted as such', (mon.status(), mon.disabled))
    d.hide_viewport = False
    update()
    mon = settle()
    check(mon.status()['objects'] == 5 and mon.disabled == 0, 'enabled again, it returns')
    e.bucket_builder_ignore = True
    update()
    mon = settle()
    check(mon.world.stats()['objects'] == 4, 'ignored object leaves the check')
    e.bucket_builder_ignore = False
    mon = settle()
    bpy.data.objects.remove(e)
    bpy.data.objects.remove(d)
    bpy.data.collections.remove(tray)
    update()
    mon = settle()
    check(mon.world.stats()['objects'] == 3, 'deleted objects are dropped', mon.world.stats())

    # ---------------------------------------------------------- build volume
    check(oob(mon, a) is None, 'part inside the volume')
    a.location.x = 10.0               # sphere r=20 at x=10: sticks out through x=0
    update()
    check(oob(mon, a) == PARTIAL, 'part crossing a wall -> partly outside (live)', oob(mon, a))
    r = mon.world.oob[mon.world.slot(a.session_uid)]
    check(r.tris is not None and len(r.tris) > 0, 'out-of-volume triangles collected', len(r.tris))
    check(float(r.tris[:, :, 0].min()) < 0.0, 'they really are beyond the wall')
    s = mon.status()
    check(badge(s)[:2] == ('FAIL', 'Outside Build Volume'), 'the verdict names it', badge(s))
    # only the walls something goes through are marked (x low, y low, z low, x high, y high, z high)
    F, T = False, True
    check(mon.walls() == ((T, F, F, F, F, F), (F, F, F, F)), 'the wall it goes through is known', mon.walls())
    a.location.x = -100.0
    update()
    check(oob(mon, a) == OUTSIDE, 'part fully outside (live)')
    check(mon.walls()[0] == (T, F, F, F, F, F), 'outside altogether: beyond the same wall', mon.walls())
    a.location = (370.0, 275.0, 100.0)             # through two walls at once
    update()
    check(mon.walls()[0] == (F, F, F, T, T, F), 'a part in a corner goes through two walls', mon.walls())
    a.location = (100.0, 100.0, 370.0)             # through the top
    b.location = (160.0, 100.0, 10.0)              # and another one through the floor
    update()
    check(mon.walls()[0] == (F, F, T, F, F, T), 'top and floor, by different parts', mon.walls())
    a.location = (100.0, 100.0, 100.0)
    b.location = (160.0, 100.0, 100.0)
    update()
    check(oob(mon, a) is None and mon.walls()[0] == (F,) * 6, 'moved back inside: no wall is marked',
          mon.walls())
    st.volume_size = (90.0, 284.0, 380.0)      # shrink X: A (80..120) now crosses x=90
    mon = settle()
    check(oob(mon, a) == PARTIAL, 'changing the volume re-evaluates the parts')
    check(st.printer == 'Custom', 'hand-edited volume is labelled Custom', st.printer)
    st.use_volume = False
    mon = settle()
    check(not mon.world.oob, 'volume check can be switched off')
    st.use_volume = True
    st.volume_align = 'CENTER_XY'
    st.volume_size = (380.0, 284.0, 380.0)
    mon = settle()
    lo, hi = mon.volume_box(st)
    check(np.allclose(lo, (-190, -142, 0)) and np.allclose(hi, (190, 142, 380)), 'centred origin', (lo, hi))
    st.volume_align = 'CORNER'
    mon = settle()

    # --------------------------------------------------------- wall clearance
    check(mon.status()['near_wall'] == 0 and badge(mon.status())[:2] == ('OK', 'Build OK'),
          'wall clearance is off by default', badge(mon.status()))
    st.use_wall_clearance = True                       # 5 mm unless changed
    mon = settle()
    check(mon.status()['near_wall'] == 0, 'no part within 5 mm of a wall')
    a.location = (23.0, 100.0, 100.0)                  # sphere r=20: 3 mm from the x = 0 wall
    update()
    s = mon.status()
    check(s['near_wall'] == 1 and s['partly_out'] == 0, 'part 3 mm from a side wall -> warning (live)', s)
    r = mon.world.wall[mon.world.slot(a.session_uid)]
    check(abs(r.dist - 3.0) < 0.05, 'distance to the wall ~3 mm', r.dist)
    check(r.tris is not None and len(r.tris) > 0 and float(r.tris[:, :, 0].min()) < 5.0,
          'the geometry inside the margin is collected', len(r.tris))
    check(badge(s)[:2] == ('WARN', 'Wall Gap Warning') and '1 part close to a wall' in badge(s)[2],
          'it is a warning, not a failure', badge(s))
    check(mon.walls() == ((F,) * 6, (T, F, F, F)), 'the side it is too close to is known', mon.walls())
    a.location = (357.0, 262.0, 100.0)                 # 3 mm from x high, 2 mm from y high
    update()
    check(mon.walls() == ((F,) * 6, (F, F, T, T)), 'and both sides for a part in a corner', mon.walls())
    a.location = (23.0, 100.0, 100.0)
    update()
    wall = [p for p in mon.problems() if p['kind'] == 'WALL']
    check(len(wall) == 1 and wall[0]['a'] == 'PartA' and abs(wall[0]['dist_mm'] - 3.0) < 0.05,
          'listed among the problems with its distance', wall)
    a.location = (100.0, 100.0, 21.0)                  # 1 mm above the floor
    update()
    check(mon.status()['near_wall'] == 0, 'the floor is not a wall')
    a.location = (100.0, 100.0, 359.0)                 # 1 mm below the top
    update()
    check(mon.status()['near_wall'] == 0, 'neither is the top')
    a.location = (15.0, 100.0, 100.0)                  # through the wall: an error instead
    update()
    s = mon.status()
    check(s['partly_out'] == 1 and s['near_wall'] == 0 and badge(s)[0] == 'FAIL',
          'a part crossing the wall is an error, not a wall warning', s)
    a.location = (100.0, 100.0, 100.0)
    update()
    st.wall_clearance_mm = 90.0                        # A and B are 80 mm from a wall, C 64 mm
    mon = settle()
    check(mon.status()['near_wall'] == 3, 'changing the distance re-evaluates the parts', mon.status())
    lo, hi = mon.margin_box(st)
    check(np.allclose(lo, (90, 90, 0)) and np.allclose(hi, (290, 194, 380)), 'margin box: sides only', (lo, hi))
    st.use_volume = False
    mon = settle()
    check(mon.status()['near_wall'] == 0 and mon.margin_box(st) is None, 'off together with the volume check')
    st.use_volume = True
    st.use_wall_clearance = False
    st.wall_clearance_mm = 5.0
    mon = settle()
    check(mon.status()['near_wall'] == 0, 'wall clearance switched off again')

    # -------------------------------------------------------- printer profiles
    props.seed_profiles(props.prefs())
    p = props.prefs()
    names = [x.name for x in p.profiles]
    check(p is not None and names[:4] == ['HP MJF 4XXX/5XXX', 'HP MJF 5XX', 'HP MJF 580+', 'HP MJF 1200'],
          'the built-in printers', names)
    check(mon.status()['printer'] == 'Custom volume, 380 x 284 x 380 mm',
          'a volume set by hand is described by its size', mon.status()['printer'])
    idx = names.index('HP MJF 580+')
    check(bpy.ops.bucketbuilder.profile_apply(index=idx) == {'FINISHED'}, 'profile applied')
    check(np.allclose(st.volume_size, (198.7, 332.6, 267.8), atol=1e-4) and st.printer == 'HP MJF 580+',
          'volume and name follow the profile', (tuple(st.volume_size), st.printer))
    mon = settle()
    check(mon.status()['printer'] == 'HP MJF 580+', 'the verdict names the printer', mon.status()['printer'])
    lo, hi = mon.volume_box(st)
    check(np.allclose(hi - lo, (198.7, 332.6, 267.8), atol=1e-4), 'and the checked volume is that printer\'s')
    st.show_volume = False
    st.use_volume = False
    mon = settle()
    check(mon.status()['printer'] == '', 'no printer is named while the volume is neither shown nor checked')
    st.show_volume = True
    st.use_volume = True
    st.volume_size = (300.0, 200.0, 250.0)
    check(st.printer == 'Custom', 'editing the size makes it a custom volume again', st.printer)
    check(bpy.ops.bucketbuilder.profile_add('EXEC_DEFAULT', name='My Printer') == {'FINISHED'}, 'profile saved')
    check(any(x.name == 'My Printer' and tuple(x.size) == (300.0, 200.0, 250.0) for x in p.profiles),
          'new profile stored in the library')
    st.volume_size = (310.0, 200.0, 250.0)
    st.printer = 'My Printer'
    check(bpy.ops.bucketbuilder.profile_update() == {'FINISHED'}, 'profile updated')
    check(any(x.name == 'My Printer' and tuple(x.size) == (310.0, 200.0, 250.0) for x in p.profiles),
          'updated size stored')
    check(bpy.ops.bucketbuilder.profile_rename('EXEC_DEFAULT', name='Our Printer') == {'FINISHED'}
          and st.printer == 'Our Printer' and any(x.name == 'Our Printer' for x in p.profiles),
          'profile renamed', st.printer)
    check(bpy.ops.bucketbuilder.profile_remove('EXEC_DEFAULT') == {'FINISHED'}, 'profile removed')
    check(not any(x.name in ('My Printer', 'Our Printer') for x in p.profiles) and st.printer == 'Custom',
          'profile gone from the library', st.printer)

    # A library made by version 1.0 is brought up to date: its built-in
    # printers are replaced by the new list, the user's own are kept, and so
    # is a built-in one whose size the user changed.
    p.profiles.clear()
    for name, size in (('HP Jet Fusion 5600', (380, 284, 380)), ('HP Jet Fusion 5200', (380, 284, 380)),
                       ('HP Jet Fusion 5000', (380, 284, 300)),         # edited: it was 250 high
                       ('HP Jet Fusion 4200', (380, 284, 380)), ('HP Multi Jet Fusion 1200', (320, 165, 230)),
                       ('HP Jet Fusion 580 / 540', (332, 190, 248)), ('Bench Printer', (200, 333, 268))):
        item = p.profiles.add()
        item.name = name
        item.size = size
    p.profiles_seeded = True
    p.profiles_version = 0
    props.seed_profiles(p)
    names = [x.name for x in p.profiles]
    check(names == ['HP MJF 4XXX/5XXX', 'HP MJF 5XX', 'HP MJF 580+', 'HP MJF 1200', 'HP Jet Fusion 5000',
                    'Bench Printer'] and p.profiles_version == props.PROFILES_VERSION,
          'a version 1.0 library is brought up to date, the user\'s printers kept', names)
    check(tuple(p.profiles[4].size) == (380.0, 284.0, 300.0) and tuple(p.profiles[5].size) == (200.0, 333.0, 268.0),
          'with their sizes')
    props.seed_profiles(p)
    check([x.name for x in p.profiles] == names, 'and only once')
    # ... and a scene saved with version 1.0, which names its printer the old way
    bc.ops.apply_volume(st, 'HP Jet Fusion 5600', (380.0, 284.0, 380.0))
    check(props.current_printer_name(st) == 'HP MJF 4XXX/5XXX', 'a scene saved with version 1.0 shows the '
          'printer\'s new name', props.current_printer_name(st))
    st.volume_size = (380.0, 284.0, 380.0)             # touching the size without changing it
    check(st.printer != 'Custom', 'and its volume still counts as that printer\'s', st.printer)
    check(bpy.ops.bucketbuilder.profile_update() == {'FINISHED'} and st.printer == 'HP MJF 4XXX/5XXX',
          'the profile operators find it under the new name', st.printer)
    bc.ops.apply_volume(st, 'HP Jet Fusion 580 / 540', (332.0, 190.0, 248.0))
    mon = settle()
    lo, hi = mon.volume_box(st)
    check(np.allclose(hi - lo, (332, 190, 248)) and props.current_printer_name(st) == 'HP Jet Fusion 580 / 540',
          'a scene whose old printer has no successor keeps its volume and its name',
          (hi - lo, props.current_printer_name(st)))
    p.profiles.remove(5)
    p.profiles.remove(4)
    bpy.ops.bucketbuilder.profile_apply(index=0)
    mon = settle()
    check(st.printer == 'HP MJF 4XXX/5XXX' and tuple(st.volume_size) == (380.0, 284.0, 380.0),
          'back to the first printer', (st.printer, tuple(st.volume_size)))

    # ----------------------------------------------------------------- units
    st.unit_mode = 'SCENE'
    mon = settle()
    check(abs(mon.mm_per_unit - 1000.0) < 1e-9, 'scene units: default scene is metres', mon.mm_per_unit)
    sc.unit_settings.scale_length = 0.001
    st.unit_mode = 'AUTO'
    mon = settle()
    check(abs(mon.mm_per_unit - 1.0) < 1e-9, 'unit scale 0.001 -> 1 unit = 1 mm', mon.mm_per_unit)
    sc.unit_settings.scale_length = 1.0
    st.unit_mode = 'AUTO'
    mon = settle()

    # ---------------------------------------------------- thresholds / ignore
    b.location.x = 143.0              # 3 mm gap again
    update()
    mon = settle()
    check(state(mon, a, b) == CLEAR, 'clearance warning at 3 mm with 5 mm threshold')
    s = mon.status()
    check(badge(s)[:2] == ('WARN', 'Clearance Warning') and '1 clearance warning' in badge(s)[2],
          'the verdict is a clearance warning', badge(s))
    st.clearance_mm = 2.0
    mon = settle()
    check(state(mon, a, b) == OK, 'lowering the clearance threshold clears it')
    check(badge(mon.status())[:2] == ('OK', 'Build OK'), 'and the build is fine', badge(mon.status()))
    b.location.x = 140.01             # a hundredth of a millimetre apart
    update()
    mon = settle()
    check(state(mon, a, b) == CLEAR and not hasattr(st, 'collision_mm'),
          'there is no collision distance: parts that do not touch do not collide', state(mon, a, b))
    b.location.x = 140.0              # touching
    update()
    mon = settle()
    check(state(mon, a, b) == COLLIDE, 'parts that touch do', state(mon, a, b))
    b.location.x = 143.0
    update()
    st.clearance_mm = 5.0
    st.use_clearance = False
    mon = settle()
    check(state(mon, a, b) == OK, 'clearance check can be switched off')
    st.use_clearance = True
    mon = settle()

    # ---------------------------------------------------- problems / navigation
    b.location.x = 135.0
    update()
    mon = settle()
    probs = mon.problems()
    check(len(probs) >= 1 and probs[0]['kind'] == 'COLLIDE', 'problem list leads with the collision', probs[:1])
    s = mon.status()
    check(badge(s)[:2] == ('FAIL', 'Collision Detected') and badge(s)[2][0] == '1 collision',
          'the verdict is a collision', badge(s))
    check(abs(float(probs[0]['center'][0]) - 117.5) < 1.0, 'problem centre is at the interference')
    r1 = bpy.ops.bucketbuilder.step_problem(direction=1)
    check(r1 == {'FINISHED'} and st.problem_index == 0, 'next-problem operator runs', (r1, st.problem_index))
    check(a.select_get() and b.select_get(), 'navigation selects the parts involved')

    # ------------------------------------------------------ moving a selection
    # (A and B collide here.)  Parts that are moved together keep their
    # result.  Blender rounds every position to single precision on its own,
    # so the offset between two parts jitters in the last digits while they
    # are dragged together; that must not count as a move.
    pr = pair(mon, a, b)
    serial = pr.serial
    bpy.ops.object.select_all(action='DESELECT')
    for o in (a, b, c):
        o.select_set(True)
    how = 'the Move tool'
    solved = 0
    jitter = 0.0
    w = mon.world
    sa, sb = w.slot(a.session_uid), w.slot(b.session_uid)
    off0 = w.O_T[sb] - w.O_T[sa]
    for delta in ((12.345, -3.21, 7.77), (131.7, 60.1, 93.3), (-0.001, 0.002, 0.0005),
                  (-144.044, -56.892, -101.0705)):
        try:
            bpy.ops.transform.translate(value=delta)
        except RuntimeError:                      # no viewport to run the tool in
            how = 'their locations'
            for o in (a, b, c):
                o.location = [float(np.float32(x) + np.float32(d)) for x, d in zip(o.location, delta)]
            update()
        solved += w.last_pairs
        jitter = max(jitter, float(np.abs(w.O_T[sb] - w.O_T[sa] - off0).max()))
        check(pair(mon, a, b) is pr and pr.serial == serial and not pr.stale and pr.state == COLLIDE,
              f'three parts moved together by {delta}: their result is kept', (pr.serial, serial, pr.stale))
    check(solved == 0, f'nothing was solved again while they moved (through {how})', solved)
    print(f'       (their offset jittered by up to {jitter:.1e} units on the way)')
    a.location = (100.0, 100.0, 100.0)
    b.location = (135.0, 100.0, 100.0)
    c.location = (100.0, 200.0, 100.0)
    update()
    mon = settle()
    check(mon.status()['collisions'] == 1, 'and the build is as it was', mon.status())

    # ------------------------------------------- the timer and freed objects
    # Timers run at the start of a pass of Blender's main loop; the dependency
    # graph is only refreshed at its end.  After an operator that freed
    # objects (delete, undo, a change in "Adjust Last Operation") the graph
    # still lists them when the timer comes, and reading it then crashed
    # Blender (seen on Windows, in the search for instances).  This is that
    # state, and the slice the timer runs.
    extra = [add_cube(f'Gone{i}', (300.0, 40.0 * i, 300.0), 10.0) for i in range(5)]
    update()
    mon = settle()
    n0 = mon.status()['objects']
    for o in extra:
        bpy.data.objects.remove(o)               # freed, and nothing is told
    late = bpy.data.objects.new('Latecomer', c.data)
    late.location = (300.0, 250.0, 300.0)
    bpy.context.collection.objects.link(late)    # ... and one the graph has not seen
    scan_seconds = monitor.SCAN_SECONDS
    monitor.SCAN_SECONDS = 0.0
    mon.need_scan = True                         # the search for instances is due
    mon.last_scan = 0.0
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    mon.error = ''
    wish = monitor._tick_scene(sc, bpy.context.view_layer)
    check(not mon.error and not mon.need_scan, 'the timer copes with objects freed behind its back',
          (mon.error, wish))
    mon = settle()
    monitor.SCAN_SECONDS = scan_seconds
    s = mon.status()
    check(s['objects'] == n0 - 5 + 1 and mon.world.slot(late.session_uid) is not None,
          'and sees the scene as it is', s['objects'])
    bpy.data.objects.remove(late)
    update()
    mon = settle()

    # ---------------------------------------------------------- save / reload
    path = os.path.join(tempfile.mkdtemp(), 'build.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    sc = scene()
    st = sc.bucket_builder
    check(st.enabled, 'monitoring stays on in the saved file')
    mon = settle()
    s = mon.status()
    check(s['objects'] == 3 and s['collisions'] == 1, 'results rebuilt after loading the file', s)
    a = bpy.data.objects['PartA']
    b = bpy.data.objects['PartB']

    # ------------------------------------------------------------------- undo
    try:
        bpy.ops.ed.undo_push(message='before move')
        b.location.x = 200.0
        update()
        bpy.ops.ed.undo_push(message='after move')
        check(state(mon, a, b) == OK, 'moved away before undo')
        bpy.ops.ed.undo()
        sc = scene()
        mon = settle()
        a = bpy.data.objects['PartA']
        b = bpy.data.objects['PartB']
        check(state(mon, a, b) == COLLIDE, 'undo restores the collision', (b.location.x, state(mon, a, b)))
    except RuntimeError as ex:
        print('       (undo not available in this mode:', str(ex).strip()[:80], ')')

    # ------------------------------------------------------- off / on / reload
    st = scene().bucket_builder
    st.enabled = False
    check(monitor.get(scene()) is None, 'switching off frees the monitor')
    st.enabled = True
    mon = settle()
    check(mon.status()['objects'] == 3, 'switching on again works')

    import addon_utils
    addon_utils.disable(PKG)
    check(not hasattr(bpy.types.Scene, 'bucket_builder'), 'add-on unregisters cleanly')
    addon_utils.enable(PKG)
    check(hasattr(bpy.types.Scene, 'bucket_builder'), 'add-on registers again')

    print(f'\n{sum(1 for ok, _ in CHECKS if ok)} of {len(CHECKS)} checks passed')
    print('BLENDER TEST OK')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('BLENDER TEST FAILED')
    sys.exit(1)
