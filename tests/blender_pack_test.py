"""The Pack panel's operator in Blender, judged by the add-on's own monitor.

    BLENDER_USER_RESOURCES=... blender --background --python tests/blender_pack_test.py
"""

import importlib
import time

import bpy
import numpy as np

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor, props = bc.monitor, bc.props
FAILS = []


def check(cond, label, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if detail else ''))
    if not cond:
        FAILS.append(label)


def settle():
    sc = bpy.context.scene
    mon = monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    assert mon.settle(sc, vl.depsgraph, vl), 'monitor never settled'
    return mon


def main():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    sc = bpy.context.scene
    st = sc.bucket_builder
    rng = np.random.default_rng(1)
    bpy.ops.mesh.primitive_cube_add(size=25, location=(50, 50, 12.5))
    cube = bpy.context.active_object
    objs = [cube]
    for i in range(9):                                    # linked copies, all in a heap
        o = cube.copy()
        sc.collection.objects.link(o)
        o.location = rng.uniform(40, 60, 3)
        objs.append(o)
    for i in range(4):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=15, location=rng.uniform(40, 60, 3))
        objs.append(bpy.context.active_object)
    for i in range(3):
        bpy.ops.mesh.primitive_torus_add(major_radius=20, minor_radius=5, location=rng.uniform(40, 60, 3))
        objs.append(bpy.context.active_object)
    objs[-1].bucket_builder_pack_mode = 'FREE'
    bpy.ops.mesh.primitive_cube_add(size=1, location=(190, 142, 5))
    plate = bpy.context.active_object
    plate.scale = (200, 120, 10)
    plate.rotation_euler = (0, 0, 0.2)
    plate.bucket_builder_pack_lock = True
    bpy.context.view_layer.update()
    plate_before = np.array(plate.matrix_world)

    st.pack_gap = 3.0
    st.pack_wall_gap = 5.0
    st.pack_max_density = 0.0
    st.pack_time = 8.0
    r = bpy.ops.bucketbuilder.pack(selected_only=False)
    check(r == {'FINISHED'}, 'the operator finishes', r)
    check(np.allclose(np.array(plate.matrix_world), plate_before), 'the locked plate did not move')
    print('  ', st.pack_last)
    check(st.pack_last.startswith('Packed 17 of 17'), 'every part placed')

    st.detect_collisions = True
    st.monitor_volume = True
    st.use_clearance = True
    st.clearance_mm = 2.99
    st.use_wall_clearance = True
    st.wall_clearance_mm = 4.99
    mon = settle()
    s = mon.status()
    check(s['collisions'] == 0 and s['clearance'] == 0, 'the monitor finds no collision or gap under 3 mm', s)
    check(s['partly_out'] == 0 and s['outside'] == 0 and s['near_wall'] == 0,
          'everything inside, the wall gap kept', s)

    # improve the layout the scene has: every part kept, no new problem, no higher
    h0 = float(st.pack_last.split('height ')[1].split(' mm')[0])
    where = {o.name: np.array(o.matrix_world) for o in objs}
    st.pack_time = 5.0
    r = bpy.ops.bucketbuilder.pack(selected_only=False, improve=True)
    print('  ', st.pack_last)
    check(r == {'FINISHED'} and st.pack_last.startswith('Packed 17 of 17'), 'Improve Current Layout keeps every part')
    h1 = float(st.pack_last.split('height ')[1].split(' mm')[0])
    check(h1 <= h0 + 1e-6, 'and the build is no higher (%.1f, was %.1f mm)' % (h1, h0))
    mon = settle()
    s = mon.status()
    check(s['collisions'] == 0 and s['clearance'] == 0 and s['partly_out'] == 0 and s['outside'] == 0
          and s['near_wall'] == 0, 'and the monitor is still content', s)
    # a part moved onto another: it is placed again, the others stay
    a, b = objs[0], objs[1]
    a.matrix_world = b.matrix_world.copy()
    bpy.context.view_layer.update()
    keep = {o.name: np.array(o.matrix_world) for o in objs[2:]}
    r = bpy.ops.bucketbuilder.pack(selected_only=False, improve=True)
    mon = settle()
    s = mon.status()
    check(s['collisions'] == 0 and s['clearance'] == 0, 'a part dropped onto another is placed again', s)
    print('   (', sum(1 for o in objs[2:] if np.allclose(np.array(o.matrix_world), keep[o.name])),
          'of', len(objs) - 2, 'others did not move)')

    # a part that cannot fit is put beside the volume, with a warning
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    huge = bpy.context.active_object
    huge.scale = (500, 50, 50)
    bpy.ops.object.select_all(action='DESELECT')
    huge.select_set(True)
    r = bpy.ops.bucketbuilder.pack(selected_only=True)
    lo, hi = mon.volume_box(st)
    check(huge.location.x - 250 >= hi[0], 'a part too large ends up beside the volume', tuple(huge.location))
    check('left beside the volume' in st.pack_last and 'too large' in st.pack_last, 'and says why', st.pack_last)
    check(' in 0.' in st.pack_last or ' in 1.' in st.pack_last, 'and decides that quickly', st.pack_last)

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        raise SystemExit(1)
    print('ALL OK')


main()
