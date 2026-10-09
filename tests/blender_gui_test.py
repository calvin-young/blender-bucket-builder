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
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common  # noqa: E402

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


def shot(name):
    path = os.path.join(OUT, name + '.png')
    gui_common.screenshot(win(), path)
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


def override():
    return bpy.context.temp_override(window=win(), area=view_area(), region=view_region())


def free_corner():
    """Where the viewport's free bottom right corner is, in pixels of the
    viewport region, worked out from where the regions lie on the screen."""
    area = view_area()
    region = view_region()
    right = region.width
    for r in area.regions:
        if r.type == 'UI' and r.width > 1 and region.x < r.x < region.x + region.width:
            right = min(right, r.x - region.x)
    return right, 0


def badge_box(colour):
    """Bounding box of the pixels of one colour in the free bottom right corner
    of the viewport: (count, distance of the rightmost one from the corner's
    right edge, of the lowest one from its bottom edge), or None without a
    virtual display to read."""
    px = gui_common.screen_pixels()
    if px is None:
        return None
    w = win()
    region = view_region()
    right, bottom = free_corner()
    # screen rows run downwards, window pixels upwards
    x0 = w.x + region.x
    y0 = px.shape[0] - 1 - (w.y + region.y)
    box = px[y0 - bottom - 150:y0 - bottom + 1, x0 + right - 420:x0 + right].astype(int)
    r, g, b = box[:, :, 0], box[:, :, 1], box[:, :, 2]
    if colour == 'red':
        m = (r > 200) & (g < 90) & (b < 90)
    elif colour == 'amber':
        m = (r > 200) & (g > 130) & (g < 220) & (b < 80)
    else:
        m = (g > 170) & (r < 120) & (b < 150)
    if not m.any():
        return 0, None, None
    ys, xs = np.nonzero(m)
    return int(m.sum()), int(420 - 1 - xs.max()), int(150 - ys.max())


def check_badge(colour, where):
    got = badge_box(colour)
    if got is None:
        log('(no virtual display to read: the badge position is not checked)')
        return
    n, from_right, from_bottom = got
    log(f'badge ({where}): {n} {colour} pixels in the corner, {from_right} px from its right edge, '
        f'{from_bottom} px from its bottom')
    assert n > 800, f'no {colour} badge in the bottom right corner ({where}): {n} px'
    assert from_right <= 45 and from_bottom <= 45, (where, from_right, from_bottom)


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
    text = bc.overlay.badge_text(s)
    assert text[:2] == ('FAIL', 'Collision Detected'), text
    w = win()
    log('window at', w.x, w.y, 'size', w.width, w.height)
    for r in view_area().regions:
        log('  region', r.type, r.alignment, 'at', r.x, r.y, 'size', r.width, r.height)
    with override():
        got = bc.overlay.free_corner(bpy.context)
    assert tuple(got) == free_corner(), (got, free_corner())
    assert got[0] < view_region().width - 100, 'the sidebar was expected to cover part of the viewport'
    shot('gui_1_overview')
    check_badge('red', 'beside the open sidebar')
    view_area().spaces.active.show_region_ui = False
    return 0.6


def step_sidebar_closed():
    with override():
        got = bc.overlay.free_corner(bpy.context)
    assert tuple(got) == free_corner() == (view_region().width, 0), (got, free_corner())
    shot('gui_1b_sidebar_closed')
    check_badge('red', 'in the corner of the viewport')
    view_area().spaces.active.show_region_ui = True
    return 0.5


def step_closeup():
    look((112, 110, 64), (0.35, -0.8, 0.5), 200)
    return 0.5


def step_closeup_shot():
    shot('gui_2_collision_closeup')
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    return 0.5


def step_view_idle():
    # Turning the view is something the user does that the dependency graph
    # does not report; the overlay notices it by the view matrix, and the
    # background work then makes room.  A redraw alone is not the user.
    PROBE['user'] = monitor._pace['user']
    view_area().tag_redraw()
    return 0.5


def step_view_turn():
    assert monitor._pace['user'] == PROBE['user'], 'a mere redraw was taken for the user turning the view'
    look((180, 130, 60), (0.60, -0.64, 0.48), 760)
    return 0.5


def step_view_seen():
    assert monitor._pace['user'] > PROBE['user'], 'turning the view went unnoticed'
    log('turning the view is noticed (and a redraw alone is not taken for it)')
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
    assert bc.overlay.badge_text(s)[:2] == ('OK', 'Build OK'), bc.overlay.badge_text(s)
    shot('gui_5_clean')
    check_badge('green', 'clean build')
    return 0.2


def step_wall():
    st = win().scene.bucket_builder
    st.use_wall_clearance = True                      # 5 mm by default
    bpy.data.objects['Cone'].location.x = 25.0        # base radius 22: 3 mm from the x = 0 wall
    look((60, 160, 40), (-0.2, -0.75, 0.62), 420)
    return 1.5


def step_wall_shot():
    mon = monitor.get(win().scene)
    s = mon.status()
    log('status with a part 3 mm from a side wall:', s, '| badge', bc.overlay.badge_text(s)[:2])
    assert s['near_wall'] == 1 and s['collisions'] == 0 and s['partly_out'] == 0, s
    assert bc.overlay.badge_text(s)[:2] == ('WARN', 'Wall Gap Warning'), bc.overlay.badge_text(s)
    shot('gui_6_wall_clearance')
    check_badge('amber', 'wall gap warning')
    return 0.2


# ---------------------------------------------------------------------------
# isolating the parts of a problem (Blender's local view), and hidden parts
# ---------------------------------------------------------------------------

def in_local_view():
    space = view_area().spaces.active
    if space.local_view is None:
        return None
    return sorted(o.name for o in win().view_layer.objects if o.local_view_get(space))


def step_problems_again():
    bpy.data.objects['Ring'].location = (128, 110, 66)       # into the ball again
    bpy.data.objects['Pin'].location.z = 103                 # 3 mm above the block
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    return 1.5


def step_isolate():
    mon = monitor.get(win().scene)
    st = win().scene.bucket_builder
    probs = mon.problems()
    kinds = [p['kind'] for p in probs]
    log('problems:', [(p['kind'], p['a'], p['b']) for p in probs])
    assert kinds == ['COLLIDE', 'CLEAR', 'WALL'], kinds
    with override():
        assert bpy.ops.bucketbuilder.focus_problem(index=0) == {'FINISHED'}
    assert in_local_view() is None, 'going to a problem must not isolate by itself'
    with override():
        assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'}
    assert st.isolate and in_local_view() == ['Ball', 'Ring'], (st.isolate, in_local_view())
    log('isolated:', in_local_view())
    return 0.8


def step_isolate_shot():
    shot('gui_7_isolated_collision')
    st = win().scene.bucket_builder
    with override():
        assert bpy.ops.bucketbuilder.step_problem(direction=1) == {'FINISHED'}
    assert st.problem_index == 1 and in_local_view() == ['Block', 'Pin'], (st.problem_index, in_local_view())
    log('next problem, isolated:', in_local_view())
    return 0.8


def step_isolate_next_shot():
    shot('gui_8_isolated_clearance')
    st = win().scene.bucket_builder
    with override():
        assert bpy.ops.bucketbuilder.step_problem(direction=1) == {'FINISHED'}
    assert in_local_view() == ['Cone'], in_local_view()          # the part that is close to a wall
    with override():
        assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'}
    assert not st.isolate and in_local_view() is None, (st.isolate, in_local_view())
    log('isolation off: the whole build shows again')
    with override():
        assert bpy.ops.bucketbuilder.step_problem(direction=1) == {'FINISHED'}
    assert in_local_view() is None, 'going to a problem isolated it although Isolate is off'
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    return 0.5


def step_hide():
    ring = bpy.data.objects['Ring']
    with override():
        bpy.ops.object.select_all(action='DESELECT')
    ring.hide_set(True)
    look((112, 110, 64), (0.35, -0.8, 0.5), 200)
    return 1.0


def step_hidden_shot():
    mon = monitor.get(win().scene)
    s = mon.status()
    text = bc.overlay.badge_text(s)
    log('with the ring hidden:', {k: s[k] for k in ('objects', 'hidden', 'hidden_problems', 'collisions')},
        '| badge', text)
    assert s['objects'] == 6 and s['hidden'] == 1 and s['collisions'] == 1 and s['hidden_problems'] == 1, s
    assert '1 problem with hidden parts' in text[2], text
    shot('gui_9_hidden_part')
    # the isolate switch with a part that cannot be shown: the visible one alone
    st = win().scene.bucket_builder
    with override():
        assert bpy.ops.bucketbuilder.focus_problem(index=0) == {'FINISHED'}
        assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'}
    assert in_local_view() == ['Ball'], in_local_view()
    return 0.8


def step_hidden_isolated_shot():
    shot('gui_10_hidden_part_isolated')
    st = win().scene.bucket_builder
    with override():
        assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'}
    assert not st.isolate and in_local_view() is None
    st.ignore_hidden = True
    return 1.0


def step_ignore_hidden():
    mon = monitor.get(win().scene)
    s = mon.status()
    log('with Ignore Hidden Parts:', {k: s[k] for k in ('objects', 'hidden', 'collisions')})
    assert s['objects'] == 5 and s['hidden'] == 0 and s['collisions'] == 0, s
    win().scene.bucket_builder.ignore_hidden = False
    bpy.data.objects['Ring'].hide_set(False)
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    return 1.0


def step_final():
    mon = monitor.get(win().scene)
    s = mon.status()
    assert s['objects'] == 6 and s['hidden'] == 0 and s['collisions'] == 1, s
    assert not mon.error, mon.error
    shot('gui_11_final')
    return 0.2


STEPS = [step_setup, step_enable, step_check_initial, step_sidebar_closed, step_closeup, step_closeup_shot,
         step_view_idle, step_view_turn, step_view_seen, step_drag_begin, step_drag_grab]
for i, dx in enumerate([-30, -30, -30, -30, 30, 40, 40, 40, 40, 40, 40]):
    STEPS += make_drag_step(dx, 'gui_3_mid_drag' if i == 2 else None)
STEPS += [step_drag_confirm, step_after_drag, step_fix, step_clean_shot, step_wall, step_wall_shot,
          step_problems_again, step_isolate, step_isolate_shot, step_isolate_next_shot,
          step_hide, step_hidden_shot, step_hidden_isolated_shot, step_ignore_hidden, step_final]


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
