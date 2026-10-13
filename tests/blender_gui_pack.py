"""The Pack panel in a real window: the operator runs modal, in a thread,
with progress, and the parts end up packed.

    tests/gui_on_xvfb.sh <gui blender> <out dir> blender_gui_pack.py
"""

import importlib
import os
import sys
import time
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common  # noqa: E402

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
OUT = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else '/tmp'
T0 = time.perf_counter()
STATE = {}


def log(*a):
    print(f'[{time.perf_counter() - T0:6.2f}s] ' + ' '.join(str(x) for x in a), flush=True)


def win():
    return bpy.context.window_manager.windows[0]


def area():
    return next(a for a in win().screen.areas if a.type == 'VIEW_3D')


def shot(name):
    gui_common.screenshot(win(), os.path.join(OUT, name + '.png'))
    log('screenshot', name)


def step_setup():
    # only the panels this test looks at, so that they fit the sidebar
    for cls in list(bc.ui.CLASSES):
        if issubclass(cls, bpy.types.Panel) and cls.__name__ not in ('BUCKETBUILDER_PT_pack',
                                                                     'BUCKETBUILDER_PT_main'):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    sc = win().scene
    for i in range(12):
        bpy.ops.mesh.primitive_monkey_add(size=40, location=(60 + 3 * i, 60, 30 + 2 * i))
    for i in range(8):
        bpy.ops.mesh.primitive_torus_add(major_radius=18, minor_radius=5, location=(80, 80 + 2 * i, 40))
    for i in range(10):
        bpy.ops.mesh.primitive_cylinder_add(radius=8, depth=60, location=(100 + i, 50, 40))
    bpy.ops.object.select_all(action='SELECT')
    st = sc.bucket_builder
    st.detect_collisions = True
    st.monitor_volume = True
    st.clearance_mm = st.pack_gap - 0.01
    sp = area().spaces.active
    sp.show_region_ui = True
    sp.overlay.show_floor = False
    r3 = sp.region_3d
    from mathutils import Euler, Vector
    r3.view_location = Vector((190, 142, 60))
    r3.view_rotation = Euler((1.1, 0, 0.6)).to_quaternion()
    r3.view_distance = 700
    sp.clip_end = 10000
    return 3.0


def step_before():
    for r in area().regions:
        if r.type == 'UI':
            r.active_panel_category = 'Bucket'
    return 1.0


def step_before_shot():
    o = bpy.data.objects['Suzanne']
    STATE['before'] = tuple(o.matrix_world.translation)
    with bpy.context.temp_override(window=win(), area=area()):
        bpy.ops.ed.undo_push(message='parts made')     # (scripts make no undo steps of their own)
    shot('gui_pack_0_before')
    return 0.3


def step_start():
    with bpy.context.temp_override(window=win(), area=area()):
        r = bpy.ops.bucketbuilder.pack('INVOKE_DEFAULT', selected_only=True)
    log('invoke ->', r)
    assert r == {'RUNNING_MODAL'}, r
    STATE['t'] = time.perf_counter()
    return 1.0


def step_wait():
    st = win().scene.bucket_builder
    if not st.pack_last:
        if time.perf_counter() - STATE['t'] > 300:
            raise AssertionError('packing did not finish')
        STEPS.insert(0, step_wait)
        return 0.5
    log('result:', st.pack_last)
    assert st.pack_last.startswith('Packed 30 of 30'), st.pack_last
    return 6.0                                  # (the monitor checks the new layout)


def step_after():
    mon = bc.monitor.get(win().scene)
    s = mon.status()
    log('monitor:', {k: s[k] for k in ('collisions', 'clearance', 'partly_out', 'outside', 'busy')})
    shot('gui_pack_1_after')
    assert s['collisions'] == 0 and s['clearance'] == 0 and s['partly_out'] == 0 and s['outside'] == 0, s
    return 0.2


def step_undo():
    with bpy.context.temp_override(window=win(), area=area()):
        bpy.ops.ed.undo()
    return 1.0


def step_undone():
    o = bpy.data.objects['Suzanne']
    now = tuple(o.matrix_world.translation)
    log('after undo:', now, 'before packing:', STATE['before'])
    assert max(abs(a - b) for a, b in zip(now, STATE['before'])) < 1e-4, 'undo did not put the part back'
    return 0.2


STEPS = [step_setup, step_before, step_before_shot, step_start, step_wait, step_after, step_undo, step_undone]


def runner():
    if not STEPS:
        log('GUI PACK TEST OK')
        os._exit(0)
    step = STEPS.pop(0)
    try:
        return step() or 0.2
    except Exception:
        traceback.print_exc()
        print('GUI TEST FAILED', flush=True)
        try:
            shot('gui_failure')
        except Exception:
            pass
        os._exit(1)


bpy.app.timers.register(runner, first_interval=1.0)
