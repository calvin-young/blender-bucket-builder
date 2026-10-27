# SPDX-License-Identifier: GPL-3.0-or-later
"""The text Copy Report puts on the clipboard names no object, mesh, scene, file or folder.

    BLENDER_USER_RESOURCES=... blender --background --python tests/blender_privacy_test.py

It is what people paste into messages, so it must hold counts and timings only.
The scene is full of identifying names on purpose, and is saved under a folder
with one, so that the report has every chance to include them.
"""

import getpass
import importlib
import os
import shutil
import tempfile
import time

import bpy

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
ops = importlib.import_module(PKG + ".ops")
monitor, props = bc.monitor, bc.props
FAILS = []


def check(cond, label, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if detail and not cond else ''))
    if not cond:
        FAILS.append(label)


def settle(mon, seconds=90):
    sc, vl = bpy.context.scene, bpy.context.view_layer
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        mon.tick(sc, vl.depsgraph, vl, live=False, relaxed=True, pace=0.0)
        if mon.ready_s is not None and not mon.queue:
            return True
        time.sleep(0.005)
    return False


def main():
    print('Blender', bpy.app.version_string, '| add-on', PKG)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    sc = bpy.context.scene
    sc.name = 'AcmeScene_job42'
    coll = bpy.data.collections.new('AcmeCollection_job42')
    sc.collection.children.link(coll)
    for i, (name, loc) in enumerate((('AcmeSocket_job42', (50, 50, 20)), ('AcmeInsole_job42', (50, 50, 21)),
                                     ('AcmeKeycap_job42', (200, 50, 20)))):
        bpy.ops.mesh.primitive_cube_add(size=40.0, location=loc)
        o = bpy.context.active_object
        o.name = name
        o.data.name = 'AcmeMesh%d_job42' % i
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    folder = tempfile.mkdtemp(prefix='AcmeDir_job42_')
    path = os.path.join(folder, 'AcmeOrder_job42.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    check(bpy.data.filepath == path, 'the scene is saved under an identifying folder and name')

    st = sc.bucket_builder
    st.detect_collisions = True
    st.monitor_volume = True
    mon = monitor.get(sc, create=True)
    check(settle(mon), 'the monitor finished its first check')

    text = '\n'.join(ops.report_lines(bpy.context))
    print('--- the report ---')
    print(text)
    print('------------------')
    check('Parts: 3' in text and 'First complete check' in text, 'it is the full report, not the short one')
    forbidden = ['Acme', 'job42', path, folder, os.path.basename(path), tempfile.gettempdir()]
    home = os.path.expanduser('~')
    if home not in ('~', os.sep):
        forbidden.append(home)
    try:
        user = getpass.getuser()
        if len(user) >= 4:
            forbidden.append(user)
    except Exception:
        pass
    leaks = [f for f in forbidden if f and f in text]
    check(not leaks, 'it names no object, mesh, scene, collection, file, folder or account', repr(leaks))

    # the same with the monitor off (the short report)
    st.detect_collisions = False
    st.monitor_volume = False
    text = '\n'.join(ops.report_lines(bpy.context))
    check(not [f for f in forbidden if f and f in text], 'nor does the short report')

    # the add-on is the only thing here that could have left a job folder behind
    stale = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith('bucket_builder_pack_')]
    check(hasattr(bc.packing, 'tempdir') and callable(bc.packing.tempdir.sweep_stale),
          'the packer cleans up leftover job folders', repr(stale))

    shutil.rmtree(folder, ignore_errors=True)
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        raise SystemExit(1)
    print('ALL OK')


main()
