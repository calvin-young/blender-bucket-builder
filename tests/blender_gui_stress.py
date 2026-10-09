"""A build-sized scene in a real Blender window.

Dozens of dense parts fill the build volume; one of them is moved, rotated and
scaled with simulated mouse input, exactly as a user would with G, R and S.
The script prints what each step of the interaction cost the add-on.

    blender --enable-event-simulate --python this_file -- <output dir> [parts] [triangles per part] [storm]

(or through tests/gui_on_xvfb.sh on a machine without a display).

With "storm" as the last argument the scenario is an import into a full
build instead: the parts are packed around the middle of the volume, a large
new part appears on top of them and is dragged out of the way.
"""

import importlib
import os
import sys
import time
import traceback

import bpy
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gui_common  # noqa: E402
import meshes  # noqa: E402

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor = bc.monitor
core = importlib.import_module(PKG + ".core")

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUT = ARGS[0] if ARGS else '/tmp'
N_PARTS = int(ARGS[1]) if len(ARGS) > 1 else 60
TRIS = int(ARGS[2]) if len(ARGS) > 2 else 20000
STORM = len(ARGS) > 3 and ARGS[3] == 'storm'
SPACING = 46.0
NAMES = {core.OK: 'ok', core.CLEAR: 'near', core.COLLIDE: 'COLLIDE'}
S = {'t0': time.perf_counter(), 'phase': {}, 'mouse': [0, 0]}


def log(*a):
    print(f'[{time.perf_counter() - S["t0"]:7.2f}s] ' + ' '.join(str(x) for x in a), flush=True)


def win():
    return bpy.context.window_manager.windows[0]


def area():
    return next(a for a in win().screen.areas if a.type == 'VIEW_3D')


def region():
    return next(r for r in area().regions if r.type == 'WINDOW')


def mon():
    return monitor.get(win().scene)


def shot(name):
    path = os.path.join(OUT, name + '.png')
    gui_common.screenshot(win(), path)
    log('screenshot', path)


def new_mesh(name, verts, tris):
    """Create mesh data from arrays without Python loops."""
    me = bpy.data.meshes.new(name)
    nt = len(tris)
    me.vertices.add(len(verts))
    me.loops.add(nt * 3)
    me.polygons.add(nt)
    me.vertices.foreach_set('co', np.ascontiguousarray(verts, dtype=np.float32).ravel())
    me.loops.foreach_set('vertex_index', np.ascontiguousarray(tris, dtype=np.int32).ravel())
    me.polygons.foreach_set('loop_start', np.arange(0, nt * 3, 3, dtype=np.int32))
    me.update(calc_edges=True)
    return me


def step_setup():
    w = win()
    with bpy.context.temp_override(window=w):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete()
    rng = np.random.default_rng(5)
    k = max(8, int(np.sqrt(TRIS / 2.0)))
    shapes = {
        'blob': meshes.blob(rng, 20.0, 2 * k, k, 0.25),
        'torus': meshes.torus(16.0, 6.0, 2 * k, k),
        'brick': meshes.grid_box((40.0, 30.0, 20.0), max(2, int(np.sqrt(TRIS / 12.0)))),
        'sphere': meshes.uv_sphere(18.0, 2 * k, k),
    }
    datas = {n: new_mesh(n, v, f) for n, (v, f) in shapes.items()}
    names = list(shapes)
    nx, ny = int(380 // SPACING), int(284 // SPACING)
    side = int(np.ceil(N_PARTS ** (1 / 3)))
    coll = w.scene.collection
    total = 0
    for i in range(N_PARTS):
        g = names[i % len(names)]
        ob = bpy.data.objects.new(f'{g}.{i:03d}', datas[g])
        M = np.eye(4)
        M[:3, :3] = meshes.rot(rng)
        if STORM:       # packed around the middle of the volume
            cell = np.array([i % side, (i // side) % side, i // (side * side)])
            M[:3, 3] = np.array([190.0, 142.0, 190.0]) + (cell - 0.5 * (side - 1)) * 48.0
        else:
            M[:3, 3] = ((i % nx + 0.5) * SPACING, ((i // nx) % ny + 0.5) * SPACING,
                        (i // (nx * ny) + 0.5) * SPACING)
        ob.matrix_world = Matrix(M.tolist())
        coll.objects.link(ob)
        total += len(shapes[g][1])
    S['total_tris'] = total
    if STORM:           # the part that will be "imported"; made now, linked later
        kb = max(8, int(np.sqrt(4 * TRIS / 2.0)))
        v, f = meshes.blob(rng, 75.0, 2 * kb, kb, 0.3)
        S['new_data'] = new_mesh('imported', v, f)
        S['new_tris'] = len(f)
        S['new_rot'] = meshes.rot(rng)
    space = area().spaces.active
    space.show_region_ui = True
    space.overlay.show_floor = False
    space.overlay.show_axis_x = False
    space.overlay.show_axis_y = False
    space.clip_start = 1.0
    space.clip_end = 20000.0
    space.lens = 50
    rv3d = space.region_3d
    rv3d.view_perspective = 'PERSP'
    layers = (N_PARTS - 1) // (nx * ny) + 1
    rv3d.view_location = (190.0, 142.0, 150.0 if STORM else 0.5 * layers * SPACING)
    rv3d.view_rotation = Vector((0.55, -0.7, 0.5)).normalized().to_track_quat('Z', 'Y')
    rv3d.view_distance = 760.0
    log(f'scene built: {N_PARTS} parts, {total / 1e6:.2f} M triangles, {len(shapes)} unique meshes')
    return 1.0


def step_enable():
    S['t_enable'] = time.perf_counter()
    win().scene.bucket_builder.enabled = True
    for r in area().regions:
        if r.type == 'UI':
            try:
                r.active_panel_category = 'Bucket'
            except Exception:
                pass
    return 0.3


def step_wait_ready():
    m = mon()
    if m is None or m.busy or m.status()['busy']:
        STEPS.insert(0, step_wait_ready)
        return 0.25
    st = m.world.stats()
    log(f'first analysis finished {time.perf_counter() - S["t_enable"]:.2f} s after switching on:',
        m.status())
    log(f'   {st["pairs"]} neighbouring pairs, {st["bytes"] / 1e6:.0f} MB cached')
    return 0.6


def step_overview():
    shot('stress_1_overview')
    return 0.2


def target():
    return bpy.data.objects[S['target']]


def neighbours():
    m = mon()
    w = m.world
    slot = w.slot(target().session_uid)
    worst, n = core.OK, 0
    for o in w.adj.get(slot, ()):
        pr = w.pairs.get((slot, o) if slot < o else (o, slot))
        if pr is not None and not pr.stale:
            worst = max(worst, pr.state)
        n += 1
    return n, NAMES[worst]


def to_window(co):
    r = region()
    p = location_3d_to_region_2d(r, area().spaces.active.region_3d, co)
    return int(r.x + p.x), int(r.y + p.y)


def begin(phase, key, offset=(0, 0)):
    def step():
        w = win()
        ob = target()
        with bpy.context.temp_override(window=w):
            bpy.ops.object.select_all(action='DESELECT')
            ob.select_set(True)
            w.view_layer.objects.active = ob
        x, y = to_window(ob.matrix_world.translation)
        S['mouse'] = [x + offset[0], y + offset[1]]
        w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=S['mouse'][0], y=S['mouse'][1])
        return 0.3

    def press():
        m = mon()
        m.live_ms.clear()
        m.draw_ms.clear()
        S['phase'][phase] = {'steps': [], 'versions': set(), 't': time.perf_counter()}
        w = win()
        w.event_simulate(type=key, value='PRESS', x=S['mouse'][0], y=S['mouse'][1])
        w.event_simulate(type=key, value='RELEASE', x=S['mouse'][0], y=S['mouse'][1])
        return 0.3
    return [step, press]


def move(phase, dx, dy, label=None):
    def step():
        S['mouse'][0] += dx
        S['mouse'][1] += dy
        win().event_simulate(type='MOUSEMOVE', value='NOTHING', x=S['mouse'][0], y=S['mouse'][1])
        return 0.25

    def record():
        m = mon()
        n, worst = neighbours()
        ph = S['phase'][phase]
        ph['steps'].append((n, worst, m.live_ms[-1] if m.live_ms else 0.0))
        ph['versions'].add(m.world.version)
        if label:
            shot(label)
        return 0.05
    return [step, record]


def finish(phase):
    def confirm():
        w = win()
        w.event_simulate(type='LEFTMOUSE', value='PRESS', x=S['mouse'][0], y=S['mouse'][1])
        w.event_simulate(type='LEFTMOUSE', value='RELEASE', x=S['mouse'][0], y=S['mouse'][1])
        return 1.2

    def summary():
        m = mon()
        ph = S['phase'][phase]
        live = np.array(m.live_ms) if m.live_ms else np.zeros(1)
        draw = np.array(m.draw_ms) if m.draw_ms else np.zeros(1)
        states = [s for _, s, _ in ph['steps']]
        nb = [n for n, _, _ in ph['steps']]
        log(f'{phase}: {len(ph["steps"])} steps, results changed on {len(ph["versions"])} of them; '
            f'neighbours tracked {min(nb)}-{max(nb)}; '
            f'steps colliding / near / clear: {states.count("COLLIDE")} / {states.count("near")} / '
            f'{states.count("ok")}')
        log(f'   live update per step: median {np.median(live):.1f} ms, p95 {np.percentile(live, 95):.1f}, '
            f'max {live.max():.1f}  ({len(live)} updates)')
        log(f'   overlay drawing per redraw: median {np.median(draw):.2f} ms, p95 '
            f'{np.percentile(draw, 95):.2f}, max {draw.max():.2f}  ({len(draw)} redraws)')
        ph['live'] = live
        ph['draw'] = draw
        assert len(ph['versions']) >= 3, f'{phase}: results did not follow the interaction'
        s = m.status()
        assert not s['busy'], f'{phase}: still busy a second after release: {s}'
        return 0.2
    return [confirm, summary]


def step_import():
    """A new part appears in the middle of the build, as after File > Import."""
    ob = bpy.data.objects.new('imported', S['new_data'])
    M = np.eye(4)
    M[:3, :3] = S['new_rot']
    M[:3, 3] = (190.0, 142.0, 190.0)
    ob.matrix_world = Matrix(M.tolist())
    win().scene.collection.objects.link(ob)
    S['target'] = ob.name
    S['t_import'] = time.perf_counter()
    S['draws_import'] = PREP['n']
    log(f'imported a part of {S["new_tris"] // 1000}k triangles into the middle of the build')
    return 0.05


def step_import_wait():
    m = mon()
    ob = target()
    if m.world.slot(ob.session_uid) is None or m.status()['busy']:
        STEPS.insert(0, step_import_wait)
        return 0.05
    n, worst = neighbours()
    w = m.world
    slot = w.slot(ob.session_uid)
    hits = sum(1 for o in w.adj[slot]
               if w.pairs[(slot, o) if slot < o else (o, slot)].state == core.COLLIDE)
    S['hits0'] = hits
    redraws = PREP['n'] - S['draws_import']
    log(f'verdict {time.perf_counter() - S["t_import"]:.2f} s after it appeared, {redraws} redraws later: '
        f'it collides with {hits} of {n} neighbouring parts; status {m.status()}')
    # The work is a fraction of a second.  Showing every step of it used to
    # take a redraw per slice, and the redraws were most of the wait.
    assert redraws <= 8, f'{redraws} redraws while one part was being checked'
    return 0.8


def step_import_shot():
    shot('storm_1_imported')
    return 0.2


def step_storm_end():
    m = mon()
    w = m.world
    slot = w.slot(target().session_uid)
    hits = sum(1 for o in w.adj.get(slot, ())
               if w.pairs[(slot, o) if slot < o else (o, slot)].state == core.COLLIDE)
    log(f'after the drag it collides with {hits} parts (was {S["hits0"]}); status {m.status()}')
    assert hits < S['hits0'], 'dragging the part out did not reduce the collisions'
    shot('storm_3_dragged_out')
    return 0.1


def step_pick():
    nx, ny = int(380 // SPACING), int(284 // SPACING)
    i = min(N_PARTS - 1, nx * (ny // 2) + nx // 2)        # a part in the middle of the first layer
    S['target'] = bpy.data.objects[i].name
    for ob in bpy.data.objects:
        if ob.name.endswith(f'.{i:03d}'):
            S['target'] = ob.name
    log('interacting with', S['target'])
    return 0.1


def step_end():
    m = mon()
    log('final status:', m.status())
    shot('stress_4_final')
    return 0.1


# what part of the overlay's time is spent preparing data (CPU, the same on any
# machine) as opposed to handing it to the graphics driver
PREP = {'t': 0.0, 'n': 0, 'tris': 0}
_groups_orig = bc.overlay._groups


def _groups_timed(*args, **kw):
    t = time.perf_counter()
    r = _groups_orig(*args, **kw)
    PREP['t'] += time.perf_counter() - t
    PREP['n'] += 1
    PREP['tris'] = max(PREP['tris'], r[0].ntri + r[1].ntri)
    return r


bc.overlay._groups = _groups_timed


def step_prep_report():
    assert PREP['n'] > 20, 'the overlay was hardly ever drawn'
    assert not mon().error, mon().error            # (a failed draw is noted there)
    log(f'overlay data preparation: {PREP["t"] / PREP["n"] * 1000:.2f} ms per redraw on average '
        f'over {PREP["n"]} redraws; at most {PREP["tris"] // 1000}k region triangles on screen')
    return 0.05


STEPS = [step_setup, step_enable, step_wait_ready, step_overview]
if STORM:
    STEPS += [step_import, step_import_wait, step_import_shot]
    STEPS += begin('drag out (G)', 'G')
    for i in range(44):
        STEPS += move('drag out (G)', 16, 2 if i % 2 else -2, 'storm_2_mid_drag' if i == 6 else None)
    STEPS += finish('drag out (G)')
    STEPS += [step_prep_report, step_storm_end]
else:
    STEPS += [step_pick]
    STEPS += begin('move (G)', 'G')
    for i in range(36):
        dx = 14 if i < 12 else (-14 if i < 30 else 14)
        STEPS += move('move (G)', dx, 3 if i % 2 else -3, 'stress_2_mid_drag' if i == 8 else None)
    STEPS += finish('move (G)')
    STEPS += begin('rotate (R)', 'R', offset=(120, 0))
    for i in range(20):
        STEPS += move('rotate (R)', 0, 14, 'stress_3_mid_rotate' if i == 10 else None)
    STEPS += finish('rotate (R)')
    STEPS += begin('scale (S)', 'S', offset=(120, 0))
    for i in range(14):
        STEPS += move('scale (S)', 6 if i < 9 else -8, 0)
    STEPS += finish('scale (S)')
    STEPS += [step_prep_report, step_end]


def runner():
    if not STEPS:
        log('STRESS TEST OK')
        os._exit(0)
    step = STEPS.pop(0)
    try:
        return step() or 0.2
    except Exception:
        traceback.print_exc()
        print('STRESS TEST FAILED', flush=True)
        try:
            shot('stress_failure')
        except Exception:
            pass
        os._exit(1)


bpy.app.timers.register(runner, first_interval=1.0)
