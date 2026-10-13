"""The Build Report panel in a real window: figures, warning and profile picture.

    tests/gui_on_xvfb.sh <gui blender> <out dir> blender_gui_report.py

Screenshots go to the output directory (gui_report_*.png); the test fails if
the profile picture does not make it into the panel.
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
monitor = bc.monitor
OUT = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else '/tmp'
T0 = time.perf_counter()


def log(*a):
    print(f'[{time.perf_counter() - T0:6.2f}s] ' + ' '.join(str(x) for x in a), flush=True)


def win():
    return bpy.context.window_manager.windows[0]


def area():
    return next(a for a in win().screen.areas if a.type == 'VIEW_3D')


def scroll_sidebar():
    for r in area().regions:
        if r.type == 'UI':
            with bpy.context.temp_override(window=win(), area=area(), region=r):
                try:
                    bpy.ops.view2d.scroll_down(deltay=3000)
                except Exception as ex:
                    log('scroll failed', ex)


def shot(name):
    gui_common.screenshot(win(), os.path.join(OUT, name + '.png'))
    log('screenshot', name)


def step_setup():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    # (only the report panel, so that it fits the sidebar of this small window)
    for cls in list(bc.ui.CLASSES):
        if issubclass(cls, bpy.types.Panel) and cls.__name__ != 'BUCKETBUILDER_PT_report':
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
    spots = [(60, 60, 30), (150, 60, 30), (240, 60, 30), (60, 150, 80), (150, 150, 80), (240, 150, 80)]
    bpy.ops.mesh.primitive_uv_sphere_add(radius=25, location=spots[0])
    bpy.ops.mesh.primitive_cube_add(size=50, location=spots[1])
    bpy.ops.mesh.primitive_torus_add(major_radius=22, minor_radius=8, location=spots[2])
    bpy.ops.mesh.primitive_monkey_add(size=60, location=spots[3])
    bpy.ops.mesh.primitive_cylinder_add(radius=18, depth=90, location=spots[4])
    bpy.ops.mesh.primitive_cone_add(radius1=24, depth=70, location=(370, 150, 80))     # through the wall
    sp = area().spaces.active
    sp.show_region_ui = True
    sp.overlay.show_floor = False
    return 0.5


def step_enable():
    st = win().scene.bucket_builder
    st.monitor_volume = True
    st.report_density = True
    for r in area().regions:
        if r.type == 'UI':
            r.active_panel_category = 'Bucket'
    return 3.0


def step_check():
    mon = monitor.get(win().scene)
    rep = mon.report
    log('report:', None if rep.result is None else (rep.result.n_parts, rep.result.density, rep.result.warnings))
    assert rep.result is not None and rep.result.n_parts == 6, 'the report did not appear on its own'
    assert rep.result.n_partial == 1, rep.result.n_partial
    assert rep.pixels is not None
    assert bc.icons.get('profile'), 'the profile icon does not exist in a window'
    scroll_sidebar()
    return 0.5


def step_shot1():
    shot('gui_report_1')
    return 0.2


def step_move():
    # lift a part: the density, height and the picture follow, with the timer alone
    mon = monitor.get(win().scene)
    before = (mon.report.result.height, mon.report.serial)
    bpy.data.objects['Cube'].location.z += 40.0
    return 2.5


def step_after_move():
    mon = monitor.get(win().scene)
    log('after the lift:', mon.report.result.height, mon.report.serial)
    assert mon.report.result.height > 100.0, mon.report.result.height
    scroll_sidebar()
    return 0.5


def step_shot2():
    shot('gui_report_2')
    return 0.2


STEPS = [step_setup, step_enable, step_check, step_shot1, step_move, step_after_move, step_shot2]


def runner():
    if not STEPS:
        log('GUI REPORT TEST OK')
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
