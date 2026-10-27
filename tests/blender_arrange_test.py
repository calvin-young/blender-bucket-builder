# SPDX-License-Identifier: GPL-3.0-or-later
"""The arrange tools (bucket_builder/arrange.py), in background Blender.

    blender --background --python tests/blender_arrange_test.py
"""

import importlib
import math
import sys
import time

import bpy
import numpy as np
from mathutils import Euler, Matrix

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bb = importlib.import_module(PKG)
arrange, monitor = bb.arrange, bb.monitor
FAILS = []


def check(cond, label, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if not (isinstance(detail, str) and detail == '') else ''))
    if not cond:
        FAILS.append(label)


def box(name, size, loc, rot=(0.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    return o


def world(o):
    bpy.context.view_layer.update()
    return arrange._world_mesh(o, bpy.context.evaluated_depsgraph_get())[0]


def extents(o):
    p = world(o)
    return p.max(axis=0) - p.min(axis=0)


def only(*objs):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def main():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    st = bpy.context.scene.bucket_builder

    # ---- drop to floor: each part on its own
    a = box('PartA', (10, 20, 30), (50, 50, 80), (0.3, 0.2, 0.1))
    b = box('PartB', (15, 15, 15), (120, 50, 150))
    only(a, b)
    check(bpy.ops.bucketbuilder.drop_to_floor() == {'FINISHED'}, 'drop to floor runs')
    za, zb = world(a)[:, 2].min(), world(b)[:, 2].min()
    check(abs(za) < 1e-4 and abs(zb) < 1e-4, 'each selected part rests on the floor', (za, zb))
    check(abs(b.location.x - 120) < 1e-9 and abs(b.location.y - 50) < 1e-9, 'and only moved down')
    km = [kmi for km, kmi in arrange._keymaps if kmi.idname == 'bucketbuilder.drop_to_floor']
    check(km and km[0].type == 'DOWN_ARROW' and km[0].ctrl, 'Ctrl+Down Arrow drops to the floor')

    # ---- minimize box volume: a turned box comes back square to the axes
    c = box('PartC', (40, 20, 6), (200, 150, 100), (0.4, 0.7, 1.1))
    vol0 = extents(c).prod()
    only(c)
    bpy.ops.bucketbuilder.minimize_box()
    e = extents(c)
    check(abs(e.prod() - 40 * 20 * 6) < 0.02 * 40 * 20 * 6 and vol0 > 1.5 * e.prod(),
          'the smallest box of a turned box is the box itself', (round(vol0), e.round(2)))
    check(bool(e[2] == e.min()), 'with its shortest side upright', e.round(2))
    center = 0.5 * (world(c).min(axis=0) + world(c).max(axis=0))
    check(np.allclose(center, (200, 150, 100), atol=1e-6), 'turned about its own center', center)

    # ---- minimize height: a standing slab lies down
    d = box('PartD', (30, 4, 50), (250, 60, 100), (0.0, 0.0, 0.5))
    only(d)
    bpy.ops.bucketbuilder.minimize_height()
    check(abs(extents(d)[2] - 4.0) < 1e-3, 'a standing slab is laid flat', extents(d).round(3))
    # a cone: lowest lying on its side, not standing on its base
    bpy.ops.mesh.primitive_cone_add(vertices=64, radius1=10, depth=60, location=(300, 200, 100))
    cone = bpy.context.active_object
    only(cone)
    bpy.ops.bucketbuilder.minimize_height()
    h = extents(cone)[2]
    check(h < 21.0, 'a tall cone ends up on its side', round(h, 2))

    # ---- tilt off axis: a cube's faces leave every axis
    f = box('PartF', (20, 20, 20), (100, 200, 100))
    only(f)
    bpy.ops.bucketbuilder.tilt_off_axis()
    p, tris = arrange._world_mesh(f, bpy.context.evaluated_depsgraph_get(), triangles=True)
    dirs, _ = arrange.flat_faces(p, tris)
    ang = np.degrees(arrange.off_axis_angles(np.eye(3)[None], dirs)[0])
    check(len(dirs) == 6 and ang.min() >= 19.5, 'every face of the cube is at least 20 degrees off axis',
          ang.round(1))
    turn = math.degrees(f.matrix_world.to_quaternion().angle)
    check(turn < 50.0, 'with a modest turn', round(turn, 1))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=10, location=(50, 200, 100))
    ball = bpy.context.active_object
    m0 = ball.matrix_world.copy()
    only(ball)
    bpy.ops.bucketbuilder.tilt_off_axis()
    check(ball.matrix_world == m0, 'a ball has no large flat faces and is left as it is')

    # ---- snapping to the build volume
    st.detect_collisions = True
    st.monitor_volume = True
    mon = monitor.get(bpy.context.scene, create=True)
    vl = bpy.context.view_layer
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    mon.settle(bpy.context.scene, vl.depsgraph, vl)
    n_parts = mon.status()['objects']
    st.snap_to_volume = True
    helper = arrange.find_helper(bpy.context.scene)
    check(helper is not None and len(helper.data.edges) == 12 and len(helper.data.polygons) == 0,
          'switched on: a wireframe of the volume', helper)
    lo, hi = arrange.volume_box(bpy.context)
    v = np.array([vv.co for vv in helper.data.vertices])
    check(np.allclose(v.min(axis=0), lo) and np.allclose(v.max(axis=0), hi), 'on the volume', (lo, hi))
    check(helper.hide_select and helper.hide_render and not helper.hide_get(), 'not selectable, not rendered, visible')
    mon.need_resync = True
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    mon.settle(bpy.context.scene, vl.depsgraph, vl)
    s = mon.status()
    check(s['objects'] == n_parts and s['ignored_parts'] == 0, 'and neither checked nor counted as ignored', s['objects'])
    check(helper not in bb.ops.ignored_objects(bpy.context.scene), 'nor listed among ignored parts')
    st.volume_size = (300.0, 200.0, 250.0)
    v = np.array([vv.co for vv in helper.data.vertices])
    check(np.allclose(v.max(axis=0) - v.min(axis=0), (300, 200, 250)), 'it follows the volume', v.max(axis=0))
    st.volume_align = 'CENTER_XY'
    v = np.array([vv.co for vv in helper.data.vertices])
    check(np.allclose(v.min(axis=0), (-150, -100, 0)), 'also when the volume moves', v.min(axis=0))
    only(a)
    bpy.ops.object.select_all(action='SELECT')
    check(not helper.select_get(), 'Select All does not pick it')
    st.snap_to_volume = False
    check(arrange.find_helper(bpy.context.scene) is None and not any(
        m.name.startswith(arrange.HELPER_NAME) for m in bpy.data.meshes), 'switched off: gone, mesh and all')

    print(f'\n{"ARRANGE TEST OK" if not FAILS else "ARRANGE TEST FAILED"}')
    if FAILS:
        sys.exit(1)


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('ARRANGE TEST FAILED')
    sys.exit(1)
