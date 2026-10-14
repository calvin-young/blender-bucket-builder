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


def job_folders():
    import tempfile
    return {n for n in os.listdir(tempfile.gettempdir()) if n.startswith('bucket_builder_pack_')}


JOBS0 = job_folders()          # (what was there before this test packed anything)


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
    STATE.setdefault('ticks', 0)
    STATE['ticks'] += 1
    if bc.packing.JOB['running']:
        STATE['seen_running'] = True
        STATE['max_done'] = max(STATE.get('max_done', 0), bc.packing.JOB['done'])
        if 'mid' not in STATE and bc.packing.JOB['done'] >= 8:
            STATE['mid'] = True
            shot('gui_pack_0b_during')
    if not st.pack_last:
        if time.perf_counter() - STATE['t'] > 300:
            raise AssertionError('packing did not finish')
        STEPS.insert(0, step_wait)
        return 0.5
    log('result:', st.pack_last, '| ticks of this test while it ran:', STATE['ticks'],
        '| progress seen up to', STATE.get('max_done'))
    assert STATE['ticks'] >= 5, 'Blender did not run its timers while packing (the window froze)'
    assert STATE.get('max_done', 0) > 0, 'no progress was reported'
    proc = bc.packing.python_exe()
    log('packing process:', proc)
    assert st.pack_last.startswith('Packed 30 of 30'), st.pack_last
    return 6.0                                  # (the monitor checks the new layout)


def step_after():
    mon = bc.monitor.get(win().scene)
    s = mon.status()
    log('monitor:', {k: s[k] for k in ('collisions', 'clearance', 'partly_out', 'outside', 'busy')})
    shot('gui_pack_1_after')
    assert s['collisions'] == 0 and s['clearance'] == 0 and s['partly_out'] == 0 and s['outside'] == 0, s
    left = job_folders() - JOBS0
    assert not left, 'a finished run left its job folder behind: %s' % sorted(left)
    log('no job folder left after a finished run')
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
    return 0.5


def step_start2():
    win().scene.bucket_builder.pack_last = ''
    with bpy.context.temp_override(window=win(), area=area()):
        bpy.ops.object.select_all(action='SELECT')
        r = bpy.ops.bucketbuilder.pack('INVOKE_DEFAULT', selected_only=True)
    assert r == {'RUNNING_MODAL'}, r
    STATE['t'] = time.perf_counter()
    return 0.5


def step_stop_midway():
    if bc.packing.JOB['done'] < 5:
        if time.perf_counter() - STATE['t'] > 120:
            raise AssertionError('no progress in the second run')
        STEPS.insert(0, step_stop_midway)
        return 0.3
    with bpy.context.temp_override(window=win(), area=area()):
        bpy.ops.bucketbuilder.pack_stop()
    log('stop pressed at', bc.packing.JOB['done'], 'parts')
    STATE['t'] = time.perf_counter()
    return 0.5


def step_stopped():
    st = win().scene.bucket_builder
    if not st.pack_last:
        if time.perf_counter() - STATE['t'] > 15:
            raise AssertionError('it did not stop within 15 s')
        STEPS.insert(0, step_stopped)
        return 0.3
    log('after stop (%.1f s):' % (time.perf_counter() - STATE['t']), st.pack_last)
    assert 'left beside the volume' in st.pack_last and 'stopped' in st.pack_last, st.pack_last
    assert not bc.packing.JOB['running']
    left = job_folders() - JOBS0
    assert not left, 'a stopped run left its job folder behind: %s' % sorted(left)
    log('no job folder left after a stopped run')
    return 0.2


def step_timing():
    with bpy.context.temp_override(window=win(), area=area()):
        r = bpy.ops.bucketbuilder.pack_timing('INVOKE_DEFAULT')
    assert r == {'RUNNING_MODAL'}, r
    STATE['t'] = time.perf_counter()
    return 1.0


def step_timing_wait():
    f = bc.packing.TIMING.get('fraction', 0.0)
    if bc.packing.TIMING['proc'] is not None and f > 0.3 and 'tshot' not in STATE:
        STATE['tshot'] = True
        log('measuring: %.0f %%, badge:' % (100 * f), bc.packing.badge_state())
        shot('gui_pack_2_measuring')
    if bc.packing.TIMING['proc'] is not None:
        if time.perf_counter() - STATE['t'] > 900:
            raise AssertionError('the speed measurement did not finish')
        STEPS.insert(0, step_timing_wait)
        return 2.0
    txt = bpy.data.texts.get("Bucket Builder speed")
    text = txt.as_string() if txt else ''
    log('speed measurement (%.0f s):' % (time.perf_counter() - STATE['t']))
    print(text, flush=True)
    assert 'FFT correlation' in text and 'Greedy packer' in text and 'bureau-day' in text, text[-500:]
    assert '@@' not in text, 'progress markers left in the copied text'
    assert STATE.get('tshot'), 'progress never passed 30 %'
    b = bc.packing.badge_state()
    assert b is not None and b[0] == 'Packer speed measured', b
    return 1.0


def step_timing_shot():
    shot('gui_pack_3_measured')
    return 0.2


STEPS = [step_setup, step_before, step_before_shot, step_start, step_wait, step_after, step_undo, step_undone,
         step_start2, step_stop_midway, step_stopped, step_timing, step_timing_wait, step_timing_shot]


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
