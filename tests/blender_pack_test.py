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
