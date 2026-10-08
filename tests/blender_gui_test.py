"""Interactive test in a real Blender window (run under a virtual display).

    blender --enable-event-simulate --python this_file -- <output directory>

Nothing is ticked by hand here: the add-on's own timer and its dependency-graph
handler do the work, the viewport draws through the registered draw handlers
and a part is dragged with simulated mouse events, exactly as a user would.
Screenshots are written to the output directory.
"""

import importlib
import math
import os
import sys
import time
import traceback

import bpy
from mathutils import Quaternion, Vector

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("build_check"))
bc = importlib.import_module(PKG)
monitor = bc.monitor
core = importlib.import_module(PKG + ".core")

OUT = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else '/tmp'
STATE = {'log': [], 'drag': [], 'fail': None, 't0': time.perf_counter()}
NAMES = {core.OK: 'OK', core.CLEAR: 'CLEAR', core.COLLIDE: 'COLLIDE'}


def log(*a):
    msg = ' '.join(str(x) for x in a)
    STATE['log'].append(msg)
    print(f'[{time.perf_counter() - STATE["t0"]:6.2f}s] {msg}', flush=True)


def win():
    return bpy.context.window_manager.windows[0]


def view_area():
    return next(a for a in win().screen.areas if a.type == 'VIEW_3D')


def view_region():
    return next(r for r in view_area().regions if r.type == 'WINDOW')


def shot(name):
    path = os.path.join(OUT, name + '.png')
    with bpy.context.temp_override(window=win(), screen=win().screen):
        bpy.ops.screen.screenshot(filepath=path)
    log('screenshot', path)


def look(target, direction, distance):
    space = view_area().spaces.active
    rv3d = space.region_3d
    rv3d.view_perspective = 'PERSP'
    rv3d.view_location = target
    rv3d.view_rotation = Vector(direction).normalized().to_track_quat('Z', 'Y')
    rv3d.view_distance = distance
    space.clip_start = 1.0
    space.clip_end = 20000.0
    space.lens = 50


def pair_state(a, b):
    mon = monitor.get(bpy.context.scene if bpy.context.scene else win().scene)
    if mon is None:
        return 'none'
    w = mon.world
    sa, sb = w.slot(a.session_uid), w.slot(b.session_uid)
    if sa is None or sb is None:
        return 'none'
    pr = w.pairs.get((sa, sb) if sa < sb else (sb, sa))
    if pr is None or pr.stale:
        return 'OK'
    return NAMES[pr.state]


def step_setup():
    w = win()
    with bpy.context.temp_override(window=w):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=28, location=(90, 110, 60))
        bpy.context.active_object.name = 'Ball'
        bpy.ops.mesh.primitive_torus_add(major_radius=30, minor_radius=11, major_segments=72,
                                         minor_segments=24, location=(128, 110, 66), rotation=(0.5, 0.3, 0.0))
        bpy.context.active_object.name = 'Ring'
        bpy.ops.mesh.primitive_cube_add(size=50, location=(230, 90, 40))
        bpy.context.active_object.name = 'Block'
        bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=14, depth=70, location=(230, 90, 103))
        bpy.context.active_object.name = 'Pin'
        bpy.ops.mesh.primitive_monkey_add(size=60, location=(330, 200, 60), rotation=(0, 0, -0.6))
        bpy.context.active_object.name = 'Head'
        bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=22, depth=60, location=(60, 240, 30))
        bpy.context.active_object.name = 'Cone'
        bpy.ops.object.select_all(action='DESELECT')
    area = view_area()
    space = area.spaces.active
    space.show_region_ui = True
    space.overlay.show_floor = False
    space.overlay.show_axis_x = False
    space.overlay.show_axis_y = False
    try:
        space.shading.type = 'SOLID'
    except Exception:
        pass
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    log('scene built; window', w.width, 'x', w.height)
    return 0.5


def step_enable():
    win().scene.build_check.enabled = True
    for region in view_area().regions:
        if region.type == 'UI':
            try:
                region.active_panel_category = 'Build'
            except Exception as ex:
                log('could not switch sidebar tab:', ex)
    log('monitoring enabled; waiting for the add-on timer')
    return 1.5


def step_check_initial():
    mon = monitor.get(win().scene)
    assert mon is not None, 'timer did not create the monitor'
    s = mon.status()
    log('status from the timer alone:', s)
    assert not s['busy'], 'background analysis did not finish'
    assert s['objects'] == 6 and s['collisions'] == 1 and s['clearance'] >= 1 and s['partly_out'] == 1, s
    shot('gui_1_overview')
    return 0.3


def step_closeup():
    look((112, 110, 64), (0.35, -0.8, 0.5), 200)
    return 0.5


def step_closeup_shot():
    shot('gui_2_collision_closeup')
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    return 0.5


def step_drag_begin():
    w = win()
    ring = bpy.data.objects['Ring']
    with bpy.context.temp_override(window=w):
        bpy.ops.object.select_all(action='DESELECT')
        ring.select_set(True)
        w.view_layer.objects.active = ring
    region = view_region()
    cx = region.x + region.width // 2
    cy = region.y + region.height // 2
    STATE['mouse'] = [cx, cy]
    STATE['ring_x0'] = ring.location.x
    w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=cx, y=cy)
    return 0.2


def step_drag_grab():
    w = win()
    x, y = STATE['mouse']
    w.event_simulate(type='G', value='PRESS', x=x, y=y)
    w.event_simulate(type='G', value='RELEASE', x=x, y=y)
    log('pressed G (modal translate)')
    return 0.2


def make_drag_step(dx, label=None):
    def step():
        w = win()
        STATE['mouse'][0] += dx
        x, y = STATE['mouse']
        w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        return 0.12

    def record():
        ring = bpy.data.objects['Ring']
        ball = bpy.data.objects['Ball']
        st = pair_state(ball, ring)
        mon = monitor.get(win().scene)
        STATE['drag'].append((round(ring.matrix_world.translation.x, 2), st, mon.world.version))
        log('drag: ring x =', round(ring.matrix_world.translation.x, 2), 'ball/ring:', st,
            '| last update', round(mon.last_tick_ms, 2), 'ms')
        if label:
            shot(label)
        return 0.05
    return [step, record]


def step_drag_confirm():
    w = win()
    x, y = STATE['mouse']
    w.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    w.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
    return 0.8


def step_after_drag():
    ring = bpy.data.objects['Ring']
    ball = bpy.data.objects['Ball']
    moved = ring.location.x - STATE['ring_x0']
    log('drag confirmed; ring moved by', round(moved, 2), '| final ball/ring:', pair_state(ball, ring))
    states = [s for _, s, _ in STATE['drag']]
    xs = [x for x, _, _ in STATE['drag']]
    assert abs(moved) > 5.0, 'the simulated drag did not move the part'
    assert len(set(xs)) > 3, 'the part did not move step by step during the drag'
    versions = [v for _, _, v in STATE['drag']]
    assert len(set(versions)) >= 4, f'results were not recomputed while dragging: {STATE["drag"]}'
    log('results were recomputed on', len(set(versions)), 'of', len(versions), 'drag steps;',
        'states:', ' > '.join(states))
    if len(set(states)) < 2:
        log('note: the pair stayed in one state for the whole drag')
    shot('gui_4_after_drag')
    return 0.3


def step_fix():
    bpy.data.objects['Pin'].location.z = 115
    bpy.data.objects['Head'].location = (300, 200, 60)
    bpy.data.objects['Ring'].location = (190, 110, 150)
    return 1.5


def step_clean_shot():
    mon = monitor.get(win().scene)
    s = mon.status()
    log('status after fixing:', s)
    assert s['collisions'] == 0 and s['partly_out'] == 0 and s['outside'] == 0 and not s['busy'], s
    shot('gui_5_clean')
    return 0.2


STEPS = [step_setup, step_enable, step_check_initial, step_closeup, step_closeup_shot,
         step_drag_begin, step_drag_grab]
for i, dx in enumerate([-30, -30, -30, -30, 30, 40, 40, 40, 40, 40, 40]):
    STEPS += make_drag_step(dx, 'gui_3_mid_drag' if i == 2 else None)
STEPS += [step_drag_confirm, step_after_drag, step_fix, step_clean_shot]


def runner():
    if not STEPS:
        log('GUI TEST OK')
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
