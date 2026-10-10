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

    check(not st.detect_collisions and not st.monitor_volume and monitor.get(sc) is None,
          'a new scene has both switches off, and nothing is monitored')
    check(bc.overlay.badge_text(None) == ('OFF', 'Not checking collisions', ['Not checking the build volume']),
          'and the status says so', bc.overlay.badge_text(None))
    st.detect_collisions = True
    st.monitor_volume = True
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
    tray.hide_viewport = True         # the collection disabled in viewports (the screen icon)
    update()
    mon = settle()
    check(mon.status()['objects'] == 4 and mon.disabled == 1,
          'a collection that is disabled in viewports takes its parts out of the check',
          (mon.status()['objects'], mon.disabled))
    tray.hide_viewport = False
    update()
    mon = settle()
    check(mon.status()['objects'] == 5 and mon.disabled == 0, 'enabled again, its part returns')
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
    check(badge(s)[:2] == ('FAIL', '1 Part Outside Build Volume'), 'the verdict names it', badge(s))
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
    check(badge(s)[:2] == ('WARN', '1 Part Within Wall Gap'), 'it is a warning, not a failure', badge(s))
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
    check(p is not None and names[:4] == ['HP MJF 5600', 'HP MJF 1200', 'HP MJF 580', 'HP MJF 580+'],
          'the built-in printers, in the owner\'s order', names)
    check(mon.status()['printer'] == 'Custom volume, 380 x 284 x 380 mm',
          'a volume set by hand is described by its size', mon.status()['printer'])
    idx = names.index('HP MJF 580+')
    check(bpy.ops.bucketbuilder.profile_apply(index=idx) == {'FINISHED'}, 'profile applied')
    check(np.allclose(st.volume_size, (332.6, 198.7, 267.8), atol=1e-4) and st.printer == 'HP MJF 580+',
          'volume and name follow the profile', (tuple(st.volume_size), st.printer))
    mon = settle()
    check(mon.status()['printer'] == 'HP MJF 580+', 'the verdict names the printer', mon.status()['printer'])
    lo, hi = mon.volume_box(st)
    check(np.allclose(hi - lo, (332.6, 198.7, 267.8), atol=1e-4), 'and the checked volume is that printer\'s')
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
    # the disk button, as it is used: the size typed over the printer's
    # (which turns the printer into "Custom"), then stored in that printer
    st.volume_size = (310.0, 200.0, 250.0)
    check(st.printer == 'Custom' and st.printer_last == 'My Printer', 'a size typed over a printer',
          (st.printer, st.printer_last))
    check(bpy.ops.bucketbuilder.profile_update() == {'FINISHED'} and st.printer == 'My Printer',
          'profile updated from the size that was typed', st.printer)
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
    check(names == ['HP MJF 5600', 'HP MJF 1200', 'HP MJF 580', 'HP MJF 580+', 'HP Jet Fusion 5000',
                    'Bench Printer'] and p.profiles_version == props.PROFILES_VERSION,
          'a version 1.0 library is brought up to date, the user\'s printers kept', names)
    check(tuple(p.profiles[4].size) == (380.0, 284.0, 300.0) and tuple(p.profiles[5].size) == (200.0, 333.0, 268.0),
          'with their sizes')
    props.seed_profiles(p)
    check([x.name for x in p.profiles] == names, 'and only once')
    # ... and one made by version 1.1, where the owner had typed his own 580+
    p.profiles.clear()
    for name, size in (('HP MJF 4XXX/5XXX', (380, 284, 380)), ('HP MJF 5XX', (332, 190, 248)),
                       ('HP MJF 580+', (333, 199, 268)), ('HP MJF 1200', (320, 165, 230)),
                       ('Bench Printer', (200, 333, 268))):
        item = p.profiles.add()
        item.name = name
        item.size = size
    p.profiles_version = 2
    props.seed_profiles(p)
    names = [x.name for x in p.profiles]
    check(names == ['HP MJF 5600', 'HP MJF 1200', 'HP MJF 580', 'HP MJF 580+', 'Bench Printer']
          and tuple(p.profiles[3].size) == (333.0, 199.0, 268.0),
          'a version 1.1 library gets the new names and order; a size the user typed stays',
          (names, tuple(p.profiles[3].size)))
    bc.ops.apply_volume(st, 'HP MJF 4XXX/5XXX', (380.0, 284.0, 380.0))
    check(props.current_printer_name(st) == 'HP MJF 5600', 'and a scene saved with 1.1 shows the new name')
    p.profiles.remove(4)
    p.profiles[3].size = (332.6, 198.7, 267.8)
    for name, size in (('HP Jet Fusion 5000', (380, 284, 300)), ('Bench Printer', (200, 333, 268))):
        item = p.profiles.add()
        item.name = name
        item.size = size
    names = [x.name for x in p.profiles]
    # ... and a scene saved with version 1.0, which names its printer the old way
    bc.ops.apply_volume(st, 'HP Jet Fusion 5600', (380.0, 284.0, 380.0))
    check(props.current_printer_name(st) == 'HP MJF 5600', 'a scene saved with version 1.0 shows the '
          'printer\'s new name', props.current_printer_name(st))
    st.volume_size = (380.0, 284.0, 380.0)             # touching the size without changing it
    check(st.printer != 'Custom', 'and its volume still counts as that printer\'s', st.printer)
    check(bpy.ops.bucketbuilder.profile_update() == {'FINISHED'} and st.printer == 'HP MJF 5600',
          'the profile operators find it under the new name', st.printer)
    bc.ops.apply_volume(st, 'HP Jet Fusion 580 / 540', (332.0, 190.0, 248.0))
    check(props.current_printer_name(st) == 'HP MJF 580', 'the same for the 500 series',
          props.current_printer_name(st))
    bc.ops.apply_volume(st, 'HP Jet Fusion 5000', (380.0, 284.0, 250.0))
    mon = settle()
    lo, hi = mon.volume_box(st)
    check(np.allclose(hi - lo, (380, 284, 250)) and props.current_printer_name(st) == 'HP Jet Fusion 5000',
          'a scene whose old printer has no successor keeps its volume and its name',
          (hi - lo, props.current_printer_name(st)))
    p.profiles.remove(5)
    p.profiles.remove(4)
    bpy.ops.bucketbuilder.profile_apply(index=0)
    mon = settle()
    check(st.printer == 'HP MJF 5600' and tuple(st.volume_size) == (380.0, 284.0, 380.0),
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
    check(badge(s)[:2] == ('WARN', '1 Clearance Warning'), 'the verdict is a clearance warning', badge(s))
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
    check(badge(s)[:2] == ('FAIL', '1 Collision Detected'), 'the verdict is a collision', badge(s))
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

    # ------------------------------------------------------- the timer's pace
    # How much of the main thread the background work takes depends on what
    # the user is doing: short slices with pauses while they edit or turn the
    # view, long ones back to back while they wait; and the viewport is asked
    # to show progress only every so often.  The decisions, with the work
    # itself replaced by a stand-in that never finishes:
    vl = bpy.context.view_layer
    seen = {}
    real_tick, real_tag = monitor.Monitor.tick, monitor.tag_redraw_all

    def fake_tick(self, scene_, depsgraph, view_layer, live, relaxed=False, pace=0.0):
        seen.update(relaxed=relaxed, pace=pace)
        self.need_resync = self.params_dirty = False
        self.world._dirty[0] = True               # work that is still there afterwards
        self.last_tick_ms = seen.get('took', 5.0)
        return seen.get('changed', False)

    def fake_tag():
        seen['tags'] = seen.get('tags', 0) + 1
        real_tag()

    monitor.Monitor.tick, monitor.tag_redraw_all = fake_tick, fake_tag
    try:
        mon.last_hot = 0.0
        monitor._pace['user'] = 0.0
        wish = monitor._tick_scene(sc, vl)
        check(seen['relaxed'] and wish == 0.0, 'nobody doing anything: long slices, one after the other',
              (seen, wish))
        monitor.user_active()                      # (the overlay calls this when a view is turned)
        wish = monitor._tick_scene(sc, vl)
        check(not seen['relaxed'] and wish == monitor.ACTIVE_PAUSE,
              'the view is being turned: short slices with pauses', (seen, wish))
        monitor._pace['user'] = 0.0
        mon.last_hot = time.perf_counter()
        wish = monitor._tick_scene(sc, vl)
        check(not seen['relaxed'] and wish == monitor.ACTIVE_PAUSE, 'the same just after an edit', (seen, wish))
        mon.last_hot = 0.0
        seen['took'] = 0.05                        # a slice that did nothing
        wish = monitor._tick_scene(sc, vl)
        check(wish == monitor.ACTIVE_PAUSE, 'a slice that gets nothing done does not spin', wish)
        seen['took'] = 5.0
        # progress is shown at a limited rate while the work goes on ...
        seen.update(changed=True, tags=0)
        monitor._pace['shown'] = 0.0
        monitor._pace['redraw'] = 0.0
        monitor._tick_scene(sc, vl)
        check(seen['tags'] == 1 and not mon.redraw_due, 'a first result is shown at once', seen)
        monitor._pace['shown'] = time.perf_counter()      # (the redraw has happened)
        for _ in range(5):
            monitor._tick_scene(sc, vl)
        check(seen['tags'] == 1 and mon.redraw_due, 'the next ones wait: no redraw after every slice', seen)
        monitor._pace['shown'] = time.perf_counter() - monitor.REDRAW_MIN - 0.01
        monitor._tick_scene(sc, vl)
        check(seen['tags'] == 2 and not mon.redraw_due, 'until a tenth of a second has passed', seen)
        # ... less often when a redraw is slow ...
        monitor._pace['redraw'] = 0.3
        check(abs(monitor.redraw_gap() - 0.6) < 1e-9, 'a slow redraw is asked for less often',
              monitor.redraw_gap())
        monitor._pace['redraw'] = 30.0
        check(monitor.redraw_gap() == monitor.REDRAW_MAX, 'but once a second at least', monitor.redraw_gap())
        monitor._pace['redraw'] = 0.0
        # ... and the final state at once
        monitor._pace['shown'] = time.perf_counter()
        seen.update(changed=True)

        def last_tick(self, scene_, depsgraph, view_layer, live, relaxed=False, pace=0.0):
            self.world._dirty.clear()
            self.last_tick_ms = 5.0
            return True

        monitor.Monitor.tick = last_tick
        wish = monitor._tick_scene(sc, vl)
        check(seen['tags'] == 3 and not mon.busy and wish != 0.0, 'the finished result is shown without waiting',
              (seen, wish))
    finally:
        monitor.Monitor.tick, monitor.tag_redraw_all = real_tick, real_tag
    mon.world.invalidate_all()
    mon = settle()
    check(mon.status()['collisions'] == 1, 'and the real work still gets done', mon.status())
    # the long slice: BOOST times the usual, or a share of what Blender takes for a pass
    p = props.prefs()
    base = p.budget_ms / 1000.0
    got = {}
    real_step = mon.world.step
    mon.world.step = lambda budget=0.01, hot_budget=0.016, idle=True: got.update(budget=budget, hot=hot_budget)
    try:
        dg = vl.depsgraph
        mon.tick(sc, dg, vl, live=False)
        check(abs(got['budget'] - base) < 1e-9 and got['hot'] >= base, 'a normal slice has the time set in '
              'the preferences', got)
        mon.tick(sc, dg, vl, live=False, relaxed=True, pace=0.004)
        check(abs(got['budget'] - monitor.BOOST * base) < 1e-9 and got['hot'] == got['budget'],
              'a relaxed one is longer', got)
        mon.tick(sc, dg, vl, live=False, relaxed=True, pace=0.2)
        check(abs(got['budget'] - monitor.SHARE * 0.2) < 1e-9,
              'and follows Blender\'s own pace when redraws are slow', got)
        mon.tick(sc, dg, vl, live=False, relaxed=True, pace=5.0)
        check(abs(got['budget'] - monitor.MAX_SLICE) < 1e-9, 'up to a limit', got)
    finally:
        mon.world.step = real_step

    # ---------------------------------------------------------- save / reload
    path = os.path.join(tempfile.mkdtemp(), 'build.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    sc = scene()
    st = sc.bucket_builder
    check(st.detect_collisions and st.monitor_volume, 'both switches stay on in the saved file')
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

    # ------------------------------------------------- the two switches
    st = scene().bucket_builder
    sc = scene()
    badge = bc.overlay.badge_text
    a, b, c = (bpy.data.objects[n] for n in ('PartA', 'PartB', 'PartC'))
    a.location = (15.0, 100.0, 100.0)             # A through a wall, and B into A
    b.location = (40.0, 100.0, 100.0)
    update()
    mon = settle()
    s = mon.status()
    check(s['collisions'] == 1 and s['partly_out'] == 1 and badge(s)[:2] == ('FAIL', '1 Part Outside Build Volume')
          and '1 Collision Detected' in badge(s)[2], 'both checks on: both problems, the volume first',
          badge(s))
    kinds = [q['kind'] for q in mon.problems()]
    check(kinds == ['PARTIAL', 'COLLIDE'], 'the list is in order of severity', kinds)
    solves = mon.world.stats()['pairs']
    st.detect_collisions = False
    mon = settle()
    s = mon.status()
    check(s['collisions'] == 0 and s['partly_out'] == 1 and not s['pairs'] and not mon.view().viol
          and [q['kind'] for q in mon.problems()] == ['PARTIAL'],
          'collisions switched off: only the build volume is reported', s)
    check('Not checking collisions' in badge(s)[2] and mon.world.pose_bytes == 0,
          'the status says what is not checked, and the fitted trees are given back', badge(s))
    b.location = (160.0, 100.0, 100.0)            # moved while collisions are off
    a.location = (100.0, 100.0, 100.0)
    update()
    mon = settle()
    s = mon.status()
    check(badge(s)[:2] == ('OK', 'Inside Build Volume') and mon.world.last_pairs == 0,
          'with only the volume monitored a clean build is "inside", and no pair is solved', badge(s))
    b.location = (135.0, 100.0, 100.0)
    update()
    st.detect_collisions = True
    mon = settle()
    check(mon.status()['collisions'] == 1 and mon.world.stats()['pairs'] == solves,
          'switched on again: the parts that moved meanwhile are checked', mon.status())
    st.monitor_volume = False
    a.location = (15.0, 100.0, 100.0)
    b.location = (50.0, 100.0, 100.0)
    update()
    mon = settle()
    s = mon.status()
    check(s['partly_out'] == 0 and s['collisions'] == 1 and not s['volume'] and s['printer'] == ''
          and 'Not checking the build volume' in badge(s)[2],
          'the volume switched off: it is neither checked nor named, whatever its checkboxes say', s)
    st.detect_collisions = True
    a.location = (100.0, 100.0, 100.0)
    b.location = (160.0, 100.0, 100.0)
    update()
    mon = settle()
    check(badge(mon.status())[:2] == ('OK', 'No Collisions'), 'collisions only: a clean build is "no collisions"',
          badge(mon.status()))
    st.monitor_volume = True
    st.use_volume = False
    mon = settle()
    s = mon.status()
    check(not s['volume'] and s['printer'] and props.volume_shown(st) and not props.volume_checked(st),
          'under the main switch the two checkboxes still work on their own', s)
    st.use_volume = True
    b.location = (135.0, 100.0, 100.0)
    update()
    mon = settle()

    # ---------------------------------------------------- ignoring a problem
    c.location = (100.0, 147.0, 100.0)            # C 7 mm from A: nothing; then 3 mm: a warning
    update()
    c.location = (100.0, 143.0, 100.0)
    update()
    mon = settle()
    kinds = [q['kind'] for q in mon.problems()]
    check(kinds == ['COLLIDE', 'CLEAR'], 'a collision and a clearance warning to work with', kinds)

    def row(kind):
        q = next(q for q in mon.problems() if q['kind'] == kind)
        return dict(kind=q['key'][0], a=q['names'][0], b=q['names'][1])

    def level(kind):
        return next(q['ignored'] for q in mon.problems() if q['kind'] == kind)

    check(bpy.ops.bucketbuilder.ignore_problem(**row('COLLIDE')) == {'FINISHED'}, 'ignore the collision')
    s = mon.status()
    check(level('COLLIDE') == 1 and s['collisions'] == 0 and s['clearance'] == 1 and s['ignored_problems'] == 1
          and len(mon.problems()) == 2, 'it stays in the list, marked, and no longer counts', s)
    check(badge(s)[:2] == ('WARN', '1 Clearance Warning') and '1 problem ignored' in badge(s)[2],
          'the verdict is about what is left, and says that something is ignored', badge(s))
    check(bpy.ops.bucketbuilder.ignore_problem(**row('CLEAR')) == {'FINISHED'}, 'ignore the warning too')
    s = mon.status()
    check(badge(s)[:2] == ('OK', 'Build OK') and '2 problems ignored' in badge(s)[2],
          'nothing left that counts: the build is fine, with a reminder', badge(s))
    r1 = bpy.ops.bucketbuilder.step_problem(direction=1)
    check(r1 == {'FINISHED'}, 'Next still goes somewhere when every problem is ignored')
    # both parts moved together: it is still the same problem
    for o in (a, b):
        o.location.z += 12.5
    update()
    mon = settle()
    check(level('COLLIDE') == 1, 'moved together, the collision is the one that was ignored', mon.status())
    check(level('CLEAR') == 0, 'the warning with the part that stayed behind has changed, and counts again')
    c.location.z += 12.5
    update()
    mon = settle()
    check(level('CLEAR') == 1, 'as it was when it was ignored, it is ignored again')
    # one part moved against the other: the problem has changed
    b.location.x += 1.0
    update()
    mon = settle()
    check(level('COLLIDE') == 0 and mon.status()['collisions'] == 1,
          'a part moved against the other: the collision counts again', mon.status())
    # for good
    check(bpy.ops.bucketbuilder.ignore_problem(lock=True, **row('COLLIDE')) == {'FINISHED'}, 'lock it')
    b.location.x -= 3.0
    update()
    mon = settle()
    check(level('COLLIDE') == 2 and mon.status()['collisions'] == 0, 'locked, it stays ignored when the parts move')
    check(bpy.ops.bucketbuilder.ignore_problem(lock=True, **row('COLLIDE')) == {'FINISHED'}
          and level('COLLIDE') == 1, 'the lock taken off: ignored as it is now')
    check(bpy.ops.bucketbuilder.ignore_problem(**row('COLLIDE')) == {'FINISHED'}
          and level('COLLIDE') == 0 and mon.status()['collisions'] == 1, 'and switched off: it counts')
    # a part and the build volume
    a.location.x = 10.0
    update()
    mon = settle()
    check(bpy.ops.bucketbuilder.ignore_problem(**row('PARTIAL')) == {'FINISHED'} and level('PARTIAL') == 1
          and mon.status()['partly_out'] == 0 and mon.walls()[0] == (False,) * 6,
          'a part outside the volume can be ignored: no wall is marked for it', mon.status())
    a.location.x = 9.0
    update()
    mon = settle()
    check(level('PARTIAL') == 0 and mon.status()['partly_out'] == 1, 'moved, it counts again')
    # the list is saved with the scene, undone with it, and forgets parts that are gone
    check(len(st.ignored_problems) == 2, 'two entries are kept', len(st.ignored_problems))
    extra = add_cube('Leaver', (300.0, 100.0, 15.0), 40.0)            # through the floor
    update()
    mon.need_resync = True            # (the timer does this every second or two: it is how a new name is seen)
    mon = settle()
    check(bpy.ops.bucketbuilder.ignore_problem(kind='V', a='Leaver', b='') == {'FINISHED'}
          and len(st.ignored_problems) == 3, 'a third one', len(st.ignored_problems))
    bpy.data.objects.remove(extra)
    update()
    mon = settle()
    check(bc.ops.prune_ignored(sc) == 1 and len(st.ignored_problems) == 2,
          'an entry about a part that is gone is dropped', len(st.ignored_problems))
    check(bpy.ops.bucketbuilder.count_all_problems() == {'FINISHED'} and len(st.ignored_problems) == 0
          and mon.status()['ignored_problems'] == 0, 'and all of them at once')
    # A lock on two parts that are too close is not a lock on their collision.
    a.location = (100.0, 100.0, 100.0)
    b.location = (300.0, 100.0, 100.0)
    c.location = (100.0, 143.0, 100.0)
    update()
    mon = settle()
    check([q['kind'] for q in mon.problems()] == ['CLEAR'], 'two parts too close', mon.problems())
    check(bpy.ops.bucketbuilder.ignore_problem(lock=True, **row('CLEAR')) == {'FINISHED'} and level('CLEAR') == 2,
          'the warning ignored for good')
    c.location.y = 141.0
    update()
    mon = settle()
    check(level('CLEAR') == 2 and mon.status()['clearance'] == 0, 'closer, and still ignored')
    c.location.y = 138.0
    update()
    mon = settle()
    check(level('COLLIDE') == 0 and mon.status()['collisions'] == 1 and badge(mon.status())[0] == 'FAIL',
          'pushed into each other: that is a collision, and it counts', mon.status())
    c.location.y = 143.0
    update()
    mon = settle()
    check(level('CLEAR') == 2 and bpy.ops.bucketbuilder.count_all_problems() == {'FINISHED'},
          'apart again: the lock on the warning is still there')
    # ... and neither is a plain "ignore", when the collision comes from a setting
    check(bpy.ops.bucketbuilder.ignore_problem(**row('CLEAR')) == {'FINISHED'} and level('CLEAR') == 1, 'ignored')
    mon.world.pairs[next(iter(mon.world.viol))].state = bc.core.COLLIDE          # (as a search that ends later would)
    mon.world.version += 1
    check(level('COLLIDE') == 0, 'the same two parts found to collide after all: not what was ignored')
    mon.world.pairs[next(iter(mon.world.viol))].state = bc.core.CLEAR
    mon.world.version += 1
    check(level('CLEAR') == 1 and bpy.ops.bucketbuilder.count_all_problems() == {'FINISHED'}, 'back as it was')
    # Ignoring must hold whatever scale the objects carry: a part whose mesh
    # is a thousand times too large with an object scale of 0.001 (an STL in
    # metres, imported with that scale) is where it failed half the time.
    import mathutils
    a.data.transform(mathutils.Matrix.Scale(1000.0, 4))
    a.scale = (0.001, 0.001, 0.001)
    a.rotation_euler = (0.3, 0.2, 0.9)
    update()
    mon = settle()
    stuck = moved = 0
    rng = np.random.default_rng(5)
    for trial in range(8):
        shift = rng.uniform(-60.0, 60.0, 3)
        for o in (a, c):
            o.location = tuple(np.array(o.location) + shift)
        update()
        mon = settle()
        kinds = [q['kind'] for q in mon.problems()]
        if kinds != ['CLEAR']:
            continue
        bpy.ops.bucketbuilder.ignore_problem(**row('CLEAR'))
        stuck += level('CLEAR') == 1
        c.location.y += 0.4                               # 0.4 mm closer or further: another problem
        update()
        mon = settle()
        kinds = [q['kind'] for q in mon.problems()]
        moved += kinds == ['CLEAR'] and level('CLEAR') == 0
        c.location.y -= 0.4
        update()
        mon = settle()
        bpy.ops.bucketbuilder.count_all_problems()
        for o in (a, c):
            o.location = tuple(np.array(o.location) - shift)
    check(stuck >= 6 and stuck == moved, 'with an object scale of 0.001: ignored where it is, and not when it moves',
          (stuck, moved))
    a.scale = (1.0, 1.0, 1.0)
    a.rotation_euler = (0.0, 0.0, 0.0)
    a.data.transform(mathutils.Matrix.Scale(0.001, 4))
    update()
    mon = settle()

    # An exporter makes a dependency graph of its own, in which every object
    # counts as changed: none of the monitor's business.
    reads = []
    real_load = bc.monitor.Monitor._load_geometry
    bc.monitor.Monitor._load_geometry = lambda self, obj, *args: (reads.append(obj.name), real_load(self, obj, *args))[1]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                r = bpy.ops.wm.stl_export(filepath=os.path.join(tmp, 'build.stl'), evaluation_mode='DAG_EVAL_VIEWPORT')
            except Exception as ex:
                r = str(ex)
        mon = settle()
    finally:
        bc.monitor.Monitor._load_geometry = real_load
    check(r != {'FINISHED'} or not reads, 'an export does not make the monitor read the parts again', (r, reads))
    check(mon.world.object_count == 3, 'and no part drops out of the check', mon.world.object_count)
    # the export button says what must not be overlooked
    a.location.x = 5.0
    update()
    mon = settle()
    check(any('outside the build volume' in line for line in bc.ops.export_warnings(sc)),
          'the export button warns about a part outside the build volume', bc.ops.export_warnings(sc))
    a.location.x = 100.0
    update()
    mon = settle()
    check(not bc.ops.export_warnings(sc), 'and has nothing to say about a build that is inside', bc.ops.export_warnings(sc))
    a.location = (100.0, 100.0, 100.0)
    b.location = (135.0, 100.0, 100.0)
    c.location = (100.0, 200.0, 100.0)
    update()
    mon = settle()
    # parts left out with the Ignore switch are counted where nobody can miss it
    c.bucket_builder_ignore = True
    mon = settle()
    s = mon.status()
    check(s['ignored_parts'] == 1 and '1 part ignored' in badge(s)[2], 'an ignored part is in the verdict', badge(s))
    c.bucket_builder_ignore = False
    mon = settle()
    check(mon.status()['ignored_parts'] == 0 and mon.status()['collisions'] == 1, 'and gone from it')

    # ------------------------------------------------- a scene from 1.0 / 1.1
    st.detect_collisions = False
    st.monitor_volume = False
    st.property_unset('detect_collisions')
    st.property_unset('monitor_volume')
    st.enabled = True                              # what such a file holds
    monitor.migrate_scenes()
    check(st.detect_collisions and st.monitor_volume and not st.enabled,
          'a scene saved with one switch for everything has both switches on')
    mon = settle()
    check(mon.status()['collisions'] == 1, 'and is monitored as before')

    # ------------------------------------------------------- off / on / reload
    geoms = mon.world.stats()['geoms']
    st.detect_collisions = False
    st.monitor_volume = False
    check(monitor.get(scene()) is None and mon.paused and mon.world.pose_bytes == 0,
          'both switches off: the monitor is paused')
    b.location.x = 200.0                           # things happen while it is paused
    me = a.data
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get('co', co)
    me.vertices.foreach_set('co', co * 1.5)
    me.update()
    update()
    check(a.session_uid in mon.stale and mon.paused, 'a paused monitor only notes what changes')
    st.detect_collisions = True
    st.monitor_volume = True
    mon2 = settle()
    lo, hi = mon2.world.part_bounds(mon2.world.slot(a.session_uid))
    check(mon2 is mon and not mon.paused and mon.status()['objects'] == 3 and mon.status()['collisions'] == 0
          and abs(float(hi[0] - lo[0]) - 60.0) < 0.5,
          'switched on again it picks up where it was, with what changed meanwhile',
          (mon.status(), float(hi[0] - lo[0])))
    me.vertices.foreach_set('co', co)
    me.update()
    b.location.x = 135.0
    update()
    mon = settle()
    check(mon.status()['collisions'] == 1 and mon.world.stats()['geoms'] == geoms, 'and goes on as before')

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
