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


def sidebar():
    """The sidebar region if it is open and lies on top of the viewport, else None."""
    region = view_region()
    for r in view_area().regions:
        if r.type == 'UI' and r.width > 1 and region.x < r.x < region.x + region.width:
            return r
    return None


def ui_scale():
    return bpy.context.preferences.system.ui_scale


def sidebar_free():
    """What the add-on has measured: how many pixels of the sidebar are empty at its bottom."""
    return bc.overlay._state.get('sidebar', {}).get(view_area().as_pointer())


def colour_mask(box, colour):
    r, g, b = box[:, :, 0].astype(int), box[:, :, 1].astype(int), box[:, :, 2].astype(int)
    if colour == 'red':
        return (r > 200) & (g < 90) & (b < 90)
    if colour == 'amber':
        return (r > 200) & (g > 130) & (g < 220) & (b < 80)
    if colour == 'magenta':
        return (r > 180) & (g < 110) & (b > 150)
    if colour == 'reddish':                       # a part shaded as colliding
        return (r > 150) & (r > g + 60) & (r > b + 60)
    return (g > 170) & (r < 120) & (b < 150)      # green


def badge_box(colour, edge):
    """Bounding box of the pixels of one colour in the bottom right corner of
    the part of the viewport that ends at ``edge`` (pixels from the region's
    left): (count, distance of the rightmost one from that edge, of the
    lowest one from the bottom), or None without a virtual display to read."""
    px = gui_common.screen_pixels()
    if px is None:
        return None
    w = win()
    region = view_region()
    # screen rows run downwards, window pixels upwards
    x0 = w.x + region.x
    y0 = px.shape[0] - 1 - (w.y + region.y)
    edge = int(round(edge))
    m = colour_mask(px[y0 - 150:y0 + 1, x0 + edge - 520:x0 + edge], colour)
    if not m.any():
        return 0, None, None
    ys, xs = np.nonzero(m)
    return int(m.sum()), int(520 - 1 - xs.max()), int(150 - ys.max())


def check_badge(colour, where, edge):
    got = badge_box(colour, edge)
    if got is None:
        log('(no virtual display to read: the badge position is not checked)')
        return
    n, from_right, from_bottom = got
    log(f'badge ({where}): {n} {colour} pixels in the corner, {from_right} px from its right edge, '
        f'{from_bottom} px from its bottom')
    assert n > 800, f'no {colour} badge in the bottom right corner ({where}): {n} px'
    assert from_right <= 45 and from_bottom <= 45, (where, from_right, from_bottom)


def pixels_around(point, colour, size=14):
    """How many pixels of a colour the screen shows around a point of the
    scene (None without a virtual display)."""
    px = gui_common.screen_pixels()
    if px is None:
        return None
    w = win()
    region = view_region()
    rv3d = view_area().spaces.active.region_3d
    v = rv3d.perspective_matrix @ Vector((point[0], point[1], point[2], 1.0))
    x = w.x + region.x + int(round((v.x / v.w * 0.5 + 0.5) * region.width))
    y = px.shape[0] - 1 - (w.y + region.y + int(round((v.y / v.w * 0.5 + 0.5) * region.height)))
    return int(colour_mask(px[y - size:y + size + 1, x - size:x + size + 1], colour).sum())


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
    st = win().scene.bucket_builder
    assert not st.detect_collisions and not st.monitor_volume and monitor.get(win().scene) is None, \
        'a new scene must start with both switches off'
    st.detect_collisions = True
    st.monitor_volume = True
    for region in view_area().regions:
        if region.type == 'UI':
            try:
                region.active_panel_category = 'Bucket'
            except Exception as ex:
                log('could not switch sidebar tab:', ex)
    log('monitoring enabled; waiting for the add-on timer')
    return 1.5


def set_tab(name):
    for region in view_area().regions:
        if region.type == 'UI':
            region.active_panel_category = name


def step_check_initial():
    mon = monitor.get(win().scene)
    assert mon is not None, 'timer did not create the monitor'
    s = mon.status()
    log('status from the timer alone:', s)
    assert not s['busy'], 'background analysis did not finish'
    assert s['objects'] == 6 and s['collisions'] == 1 and s['clearance'] >= 1 and s['partly_out'] == 1, s
    text = bc.overlay.badge_text(s)
    assert text[:2] == ('FAIL', '1 Part Outside Build Volume') and '1 Collision Detected' in text[2], text
    w = win()
    log('window at', w.x, w.y, 'size', w.width, w.height)
    for r in view_area().regions:
        log('  region', r.type, r.alignment, 'at', r.x, r.y, 'size', r.width, r.height)
    # The sidebar is open on our own tab, whose panels fill it to the bottom:
    # the badge has to keep to the left of it.
    side = sidebar()
    assert side is not None and side.width > 100, 'the sidebar was expected to cover part of the viewport'
    free = sidebar_free()
    log('the sidebar is empty for', free, 'pixels at its bottom')
    assert free is not None and free < 60, ('the panels of the Bucket tab were expected to fill the sidebar', free)
    shot('gui_1_overview')
    check_badge('magenta', 'beside the sidebar, whose panels reach the bottom', side.x - view_region().x)
    set_tab('Item')                                   # a tab with one short panel
    return 1.0


def step_badge_far_right():
    # Now most of the sidebar is empty, and the viewport shows through it:
    # the badge goes all the way to the right, next to the column of tabs.
    side = sidebar()
    free = sidebar_free()
    log('with the Item tab the sidebar is empty for', free, 'pixels at its bottom')
    assert free is not None and free > 200, free
    shot('gui_1a_badge_far_right')
    check_badge('magenta', 'under the empty part of the sidebar',
                view_region().width - bc.overlay.TAB_STRIP * ui_scale())
    set_tab('Bucket')
    return 1.0


def step_badge_back():
    free = sidebar_free()
    assert free is not None and free < 60, free
    check_badge('magenta', 'beside the sidebar again', sidebar().x - view_region().x)
    view_area().spaces.active.show_region_ui = False
    return 0.8


def step_sidebar_closed():
    assert sidebar() is None
    shot('gui_1b_sidebar_closed')
    check_badge('magenta', 'in the corner of the viewport', view_region().width)
    view_area().spaces.active.show_region_ui = True
    return 0.8


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
    check_badge('green', 'clean build', sidebar().x - view_region().x)
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
    assert bc.overlay.badge_text(s)[:2] == ('WARN', '1 Part Within Wall Gap'), bc.overlay.badge_text(s)
    shot('gui_6_wall_clearance')
    check_badge('amber', 'wall gap warning', sidebar().x - view_region().x)
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


def row_args(kind):
    """What the buttons in the row of the first problem of a kind send."""
    mon = monitor.get(win().scene)
    pr = next(p for p in mon.problems() if p['kind'] == kind)
    return dict(kind=pr['key'][0], a=pr['names'][0], b=pr['names'][1])


def step_click_row():
    """A real click on a row of the list of problems: the row of the
    clearance warning, found on the screen by its amber tab."""
    px = gui_common.screen_pixels()
    side = sidebar()
    if px is None or side is None:
        log('(no virtual display to read: the click on a row is not tried)')
        STATE['clicked'] = None
        return 0.1
    w = win()
    x0 = w.x + side.x
    top = px.shape[0] - 1 - (w.y + side.y + side.height)
    box = px[top:top + side.height, x0:x0 + side.width].astype(int)
    r, g, b = box[:, :, 0], box[:, :, 1], box[:, :, 2]
    amber = (r > 110) & (g > 70) & (g < r - 25) & (b < 70)
    # The tab is a narrow upright strip near the left edge of the list (the
    # amber icons of the box above it are wider than they are narrow).
    run = 0
    y = col = None
    for yy in range(amber.shape[0]):
        xs = np.nonzero(amber[yy, :60])[0]
        narrow = xs.shape[0] >= 2 and xs[-1] - xs[0] <= 8
        run = run + 1 if narrow else 0
        if run >= 12:
            y, col = yy - 6, int(xs[0])            # inside the first amber row: the clearance warning
            break
    assert y is not None, 'no amber tab found in the list of problems'
    sx = x0 + col + 70
    sy = px.shape[0] - 1 - (top + y)
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        w.event_simulate(type=kind, value=value, x=sx - w.x, y=sy - w.y)
    STATE['clicked'] = (sx, sy)
    return 1.0


def step_click_row_check():
    if STATE.get('clicked') is None:
        return 0.1
    mon = monitor.get(win().scene)
    st = win().scene.bucket_builder
    at = mon.active_index(st)
    picked = sorted(o.name for o in win().view_layer.objects if o.select_get())
    log('clicked the row of the clearance warning at', STATE['clicked'], '-> problem', at, 'selected', picked)
    assert at == 1 and picked == ['Block', 'Pin'], (at, picked)
    assert in_local_view() is None
    shot('gui_6a_row_clicked')
    return 0.3


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
    # the button in the row of the collision
    with override():
        assert bpy.ops.bucketbuilder.isolate_problem(**row_args('COLLIDE')) == {'FINISHED'}
    assert st.isolate and st.problem_index == 0 and in_local_view() == ['Ball', 'Ring'], (
        st.isolate, st.problem_index, in_local_view())
    log('isolated with the button of its row:', in_local_view())
    assert st.local_view_mode == 'SHOWN', st.local_view_mode
    look((180, 110, 60), (0.45, -0.75, 0.48), 520)
    return 1.0


def local_count():
    """What the add-on says the viewport shows: (parts shown, parts checked)
    in local view, else None."""
    with override():
        return bc.overlay.local_count(bpy.context, monitor.get(win().scene))


def step_isolate_shot():
    shot('gui_7_isolated_collision')
    # The verdict is still the whole build's; a line under it says how little
    # of the build this viewport shows.
    mon = monitor.get(win().scene)
    local = local_count()
    text = bc.overlay.badge_text(mon.status(), local)
    log('in local view:', local, '| badge', text)
    assert local == (2, 6) and 'Local view: 2 of 6 parts shown' in text[2], (local, text)
    assert text[:2] == bc.overlay.badge_text(mon.status())[:2], 'local view changed the verdict'
    # Only the problems of the parts in view are drawn: the warning between
    # the block and the pin, neither of which is in view, is not.
    amber = pixels_around((230, 90, 66.5), 'amber', 22)
    log('amber pixels where the block and the pin are too close (both out of view):', amber)
    assert amber in (None, 0), amber
    # Now the ring is dragged into the block, which is not in view ...
    bpy.data.objects['Ring'].location = (200, 95, 52)
    return 1.2


def step_ghost_shot():
    mon = monitor.get(win().scene)
    block, ring = bpy.data.objects['Block'], bpy.data.objects['Ring']
    assert pair_state(block, ring) == 'COLLIDE', pair_state(block, ring)
    assert in_local_view() == ['Ball', 'Ring'], in_local_view()
    shot('gui_7a_isolated_new_collision')
    # ... and the block appears, shaded as a colliding part: a ghost that
    # says a new problem has come up outside the view.
    ghost = pixels_around((243, 77, 40), 'reddish', 12)
    log('reddish pixels where the block is (it is not in view):', ghost)
    assert ghost is None or ghost > 150, f'no ghost of the part that was run into ({ghost} px)'
    win().scene.bucket_builder.local_view_mode = 'ALL'
    return 1.0


def step_all_shot():
    # The other way: a view in local view shows every problem of the build.
    amber = pixels_around((230, 90, 66.5), 'amber', 22)
    log('with "Whole Build": amber pixels where the block and the pin are too close:', amber)
    assert amber is None or amber > 20, amber
    shot('gui_7b_isolated_whole_build')
    st = win().scene.bucket_builder
    st.local_view_mode = 'SHOWN'
    bpy.data.objects['Ring'].location = (128, 110, 66)
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    return 1.2


def step_isolate_next():
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
    # the button of the row that is isolated switches it off again
    with override():
        assert bpy.ops.bucketbuilder.isolate_problem(**row_args('WALL')) == {'FINISHED'}
    assert not st.isolate and in_local_view() is None, (st.isolate, in_local_view())
    assert local_count() is None, local_count()
    log('isolation off: the whole build shows again')
    with override():
        assert bpy.ops.bucketbuilder.step_problem(direction=1) == {'FINISHED'}
    assert in_local_view() is None, 'going to a problem isolated it although Isolate is off'
    # ... and the switch above the list does the same for whatever is current
    with override():
        assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'}
    assert st.isolate and in_local_view() == ['Ball', 'Ring'], (st.problem_index, in_local_view())
    with override():
        assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'}
    assert not st.isolate and in_local_view() is None
    look((230, 90, 70), (0.62, -0.62, 0.3), 260)
    return 0.8


def step_ignore():
    # A problem that is ignored: its hatching goes, a small grey ring stays,
    # and the verdict no longer counts it but says that something is ignored.
    before = pixels_around((230, 90, 66.5), 'amber', 30)
    assert before is None or before > 20, before
    with override():
        assert bpy.ops.bucketbuilder.ignore_problem(**row_args('CLEAR')) == {'FINISHED'}
    STATE['amber_before'] = before
    return 1.0


def step_ignore_shot():
    mon = monitor.get(win().scene)
    s = mon.status()
    text = bc.overlay.badge_text(s)
    log('with the clearance warning ignored:', text)
    assert s['clearance'] == 0 and s['ignored_problems'] == 1 and '1 problem ignored' in text[2], text
    after = pixels_around((230, 90, 66.5), 'amber', 30)
    log('amber pixels at the warning before and after ignoring it:', STATE['amber_before'], after)
    assert after in (None, 0), after
    shot('gui_8a_problem_ignored')
    bpy.data.objects['Pin'].location.z = 102.0           # the gap changes: 2 mm now
    return 1.2


def step_ignore_over():
    mon = monitor.get(win().scene)
    s = mon.status()
    assert s['clearance'] == 1 and s['ignored_problems'] == 0, ('a problem that changes counts again', s)
    log('the part was moved: the warning counts again')
    bpy.data.objects['Pin'].location.z = 103.0
    look((180, 130, 60), (0.62, -0.62, 0.48), 760)
    return 1.0


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
    # the button that is there for what comes next: it says so, and does nothing else
    with override():
        assert bpy.ops.bucketbuilder.export_3mf() == {'CANCELLED'}
    return 0.8


def step_export():
    shot('gui_12_export_coming')
    return 0.2


STEPS = [step_setup, step_enable, step_check_initial, step_badge_far_right, step_badge_back,
         step_sidebar_closed, step_closeup, step_closeup_shot,
         step_view_idle, step_view_turn, step_view_seen, step_drag_begin, step_drag_grab]
for i, dx in enumerate([-30, -30, -30, -30, 30, 40, 40, 40, 40, 40, 40]):
    STEPS += make_drag_step(dx, 'gui_3_mid_drag' if i == 2 else None)
STEPS += [step_drag_confirm, step_after_drag, step_fix, step_clean_shot, step_wall, step_wall_shot,
          step_problems_again, step_click_row, step_click_row_check, step_isolate, step_isolate_shot, step_ghost_shot, step_all_shot,
          step_isolate_next, step_isolate_next_shot, step_ignore, step_ignore_shot, step_ignore_over,
          step_hide, step_hidden_shot, step_hidden_isolated_shot, step_ignore_hidden, step_final,
          step_export]


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
