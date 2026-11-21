# SPDX-License-Identifier: GPL-3.0-or-later
"""Export Print File (#9): write the build through the 3MF add-on, read it back, same parts in the
same places. Without the add-on, Export writes nothing.

    BLENDER_USER_RESOURCES=... blender --background --python tests/blender_export_test.py
"""

import importlib
import os
import sys
import tempfile
import zipfile

import bpy
import numpy as np

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
FAILS = []


def check(cond, label, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if detail else ''))
    if not cond:
        FAILS.append(label)


def main():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    sc = bpy.context.scene
    st = sc.bucket_builder
    st.detect_collisions = True
    st.monitor_volume = True
    bpy.ops.mesh.primitive_cube_add(size=20, location=(50, 50, 10.0))     # resting on the floor
    a = bpy.context.active_object
    a.name = 'part-001'
    b = a.copy()                                                          # a linked copy: written once
    sc.collection.objects.link(b)
    b.name = 'part-002'
    b.location = (120, 60, 30)
    b.rotation_euler = (0.3, 0.2, 0.1)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=12, location=(200, 150, 40))
    c = bpy.context.active_object
    c.name = 'part-003 "quoted" & <odd>'
    bpy.context.view_layer.update()
    mon = bc.monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    mon.settle(sc, vl.depsgraph, vl)

    path = os.path.join(tempfile.mkdtemp(prefix='bbexport_'), 'build.3mf')
    if bc.export.threemf_api() is None:
        # without the 3MF add-on, Export writes nothing and says what is needed (the owner, 2026-11-19)
        try:
            r = bpy.ops.bucketbuilder.export_3mf(filepath=path)
        except RuntimeError:
            r = {'CANCELLED'}
        check(r == {'CANCELLED'} and not os.path.exists(path), 'without the 3MF add-on nothing is written', r)
        return
    r = bpy.ops.bucketbuilder.export_3mf(filepath=path)
    check(r == {'FINISHED'} and os.path.exists(path), 'the file is written', r)

    sys.path.insert(0, os.path.dirname(bc.__file__))
    from pack import meshio
    back = meshio.read(path)
    names = sorted(it.name for it in back.items)
    check(names == sorted([a.name, b.name, c.name]), 'every part, by name', names)
    for o in (a, b, c):
        it = next(it for it in back.items if it.name == o.name)
        V = np.asarray(it.mesh.verts, float) @ it.matrix[:3, :3].T + it.matrix[:3, 3]
        W = np.array([o.matrix_world @ v.co for v in o.data.vertices])
        check(np.allclose(np.sort(V, axis=0), np.sort(W, axis=0), atol=1e-4), '%s is where it was' % o.name)
    lo = min(float((np.asarray(it.mesh.verts, float) @ it.matrix[:3, :3].T + it.matrix[:3, 3])[:, 2].min()) for it in back.items)
    check(lo >= 0.0, 'nothing below the floor (lowest point %.9f)' % lo)
    with zipfile.ZipFile(path) as z:
        model = z.read('3D/3dmodel.model').decode()
    for key in ('bb:printer', 'bb:packing', 'bb:tool', 'bb:checks', 'bb:exported', 'urn:bucket-builder'):
        check(key in model, 'the build carries %s' % key)
    check(model.count('<vertices>') == 2, 'the linked copies share one mesh (%d meshes written)' % model.count('<vertices>'))
    check('operator' not in model, 'and no operator field')

    # a unitless scene: the 3MF fork reads a unit as a millimeter there (2.7.7-fork.16), and the parts
    # still land where they are (Bucket Builder asks the add-on its unit instead of assuming meters)
    sc.unit_settings.system = 'NONE'
    path2 = path.replace('build.3mf', 'unitless.3mf')
    r = bpy.ops.bucketbuilder.export_3mf(filepath=path2, thumbnail_mode='NONE', coordinate_precision=6)
    back = meshio.read(path2)
    it = next(it for it in back.items if it.name == a.name)
    V = np.asarray(it.mesh.verts, float) @ it.matrix[:3, :3].T + it.matrix[:3, 3]
    W = np.array([a.matrix_world @ v.co for v in a.data.vertices])
    check(r == {'FINISHED'} and np.allclose(np.sort(V, axis=0), np.sort(W, axis=0), atol=1e-3),
          'in a unitless scene too, at the same size and place', (V.min(axis=0), W.min(axis=0)))
    with zipfile.ZipFile(path2) as z:
        check('Metadata/thumbnail.png' not in z.namelist(), 'the dialog\'s options reach the add-on (no thumbnail)',
              z.namelist())
    sc.unit_settings.system = 'METRIC'


main()
print()
if FAILS:
    print('FAILED: %d' % len(FAILS))
    sys.exit(1)
print('ALL OK')
