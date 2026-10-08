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

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor = bc.monitor
core = importlib.import_module(PKG + ".core")

OUT = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else '/tmp'
STATE = {'log': [], 'drag': [], 'fail': None, 't0': time.perf_counter()}
NAMES = {core.OK: 'OK', core.CLEAR: 'CLEAR', core.COLLIDE: 'COLLIDE'}


PROBE = {'deps': [], 'draws': 0, 'on': False}


def _probe_deps(scene, depsgraph):
    if PROBE['on']:
        PROBE['deps'].append((time.perf_counter(), tuple(sorted(
            (type(u.id).__name__ + ('/T' if u.is_updated_transform else '') +
             ('/G' if u.is_updated_geometry else '')) for u in depsgraph.updates))))


def _probe_draw():
    if PROBE['on']:
        PROBE['draws'] += 1


bpy.app.handlers.depsgraph_update_post.append(_probe_deps)
bpy.types.SpaceView3D.draw_handler_add(_probe_draw, (), 'WINDOW', 'POST_PIXEL')


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


# With software OpenGL on a virtual display Blender cannot read its own front
# buffer back (the screenshot operator returns black).  When the display is an
# Xvfb started with -fbdir, its framebuffer file is read instead: that is
# exactly what a monitor would show.
XVFB_FB = os.environ.get('BUCKET_BUILDER_XVFB_FB', '')


def _xwd_to_png(src, dst):
    import struct
    import zlib
    import numpy as np
    data = open(src, 'rb').read()
    hdr = struct.unpack('>25I', data[:100])
    header_size, width, height = hdr[0], hdr[4], hdr[5]
    byte_order, bpp, bytes_per_line, ncolors = hdr[7], hdr[11], hdr[12], hdr[19]
    raw = np.frombuffer(data, dtype=np.uint8, count=bytes_per_line * height,
                        offset=header_size + ncolors * 12).reshape(height, bytes_per_line)
    px = raw[:, :width * (bpp // 8)].reshape(height, width, bpp // 8)
    rgb = np.ascontiguousarray(px[:, :, [2, 1, 0]] if byte_order == 0 else px[:, :, 1:4])
    rows = b''.join(b'\x00' + rgb[y].tobytes() for y in range(height))

    def chunk(tag, d):
        c = struct.pack('>I', len(d)) + tag + d
        return c + struct.pack('>I', zlib.crc32(tag + d) & 0xffffffff)

    with open(dst, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b'IDAT', zlib.compress(rows, 6)))
        f.write(chunk(b'IEND', b''))


def shot(name):
    path = os.path.join(OUT, name + '.png')
    if XVFB_FB and os.path.exists(XVFB_FB):
        _xwd_to_png(XVFB_FB, path)
    else:
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
        bpy.ops.mesh.primitive_monkey_add(size=60, location=(346, 200, 60), rotation=(0, 0, -0.6))
        bpy.context.active_object.name = 'Head'         # reaches through the x = 380 wall
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
    win().scene.bucket_builder.enabled = True
    for region in view_area().regions:
        if region.type == 'UI':
            try:
                region.active_panel_category = 'Bucket'
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
    PROBE['on'] = True
    PROBE['t_start'] = time.perf_counter()
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
        badge = bc.overlay.badge_text(mon.status())
        STATE['drag'].append((round(ring.matrix_world.translation.x, 2), st, mon.world.version))
        STATE.setdefault('badges', []).append(badge[0])
        log('drag: ring x =', round(ring.matrix_world.translation.x, 2), 'ball/ring:', st,
            '| last update', round(mon.last_tick_ms, 2), 'ms | badge', badge[0], '|', badge[1],
            '| hot parts', len(mon.hot), '| redraws so far', PROBE['draws'])
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
    PROBE['on'] = False
    kinds = {}
    for _, ids in PROBE['deps']:
        kinds[ids] = kinds.get(ids, 0) + 1
    log('dependency-graph updates during the drag:', len(PROBE['deps']), '| viewport redraws:', PROBE['draws'])
    for ids, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        log('   ', n, 'x', ids)
    log('badge during the drag:', ' > '.join(STATE.get('badges', [])))
    if len(set(states)) < 2:
        log('note: the pair stayed in one state for the whole drag')
    shot('gui_4_after_drag')
    return 0.3


def step_fix():
    bpy.data.objects['Pin'].location.z = 115
    bpy.data.objects['Head'].location = (300, 200, 60)
    bpy.data.objects['Ring'].location = (128, 110, 150)
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
