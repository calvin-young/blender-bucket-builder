"""Undo, redo and delete in a real Blender window while the monitor runs.

    blender --enable-event-simulate --python this_file -- <output directory>

This is the situation that crashed Blender for the owner (Windows, 5.2.0):
timers run at the start of a pass of Blender's main loop, the dependency
graph is refreshed at its end, and in between an operator has freed objects
the graph still lists.  The add-on's timer used to walk the graph's instances
right there.

Here the real undo, redo and delete operators are triggered with simulated
key presses, with geometry-node instances in the scene and the search for
instances made due every time, so that the add-on's own timer comes by in
exactly that gap, over and over.  Blender has to survive, and the monitor has
to end up describing the scene as it is.
"""

import importlib
import os
import sys
import time
import traceback

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_common  # noqa: E402

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor = bc.monitor

OUT = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else '/tmp'
ROUNDS = 14
STATE = {'t0': time.perf_counter(), 'errors': [], 'ticks': 0, 'late': 0, 'in_tick': False,
         'round': 0, 'undone': 0, 'redone': 0, 'deleted': 0}


def log(*a):
    print(f'[{time.perf_counter() - STATE["t0"]:6.2f}s] ' + ' '.join(str(x) for x in a), flush=True)


def win():
    return bpy.context.window_manager.windows[0]


def view_area():
    return next(a for a in win().screen.areas if a.type == 'VIEW_3D')


def view_region():
    return next(r for r in view_area().regions if r.type == 'WINDOW')


def override():
    return bpy.context.temp_override(window=win(), area=view_area(), region=view_region())


# -- probes: was the timer's slice ever run while the graph was out of date,
#    and did anything go wrong in it?
_tick = monitor._tick_scene


def _watched_tick(scene, view_layer):
    STATE['ticks'] += 1
    STATE['in_tick'] = True
    try:
        return _tick(scene, view_layer)
    except Exception as ex:
        STATE['errors'].append(repr(ex))
        raise
    finally:
        STATE['in_tick'] = False


def _probe_deps(scene, depsgraph):
    # an evaluation inside the timer's slice: the graph was behind the data
    if STATE['in_tick']:
        STATE['late'] += 1


monitor._tick_scene = _watched_tick
bpy.app.handlers.depsgraph_update_post.append(_probe_deps)


def scan_due():
    mon = monitor.get(win().scene)
    if mon is not None:
        mon.need_scan = True
        mon.last_scan = 0.0
        mon.last_hot = 0.0


def key(kind, **mods):
    region = view_region()
    x = region.x + region.width // 2
    y = region.y + region.height // 2
    w = win()
    w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    w.event_simulate(type=kind, value='PRESS', x=x, y=y, **mods)
    w.event_simulate(type=kind, value='RELEASE', x=x, y=y, **mods)


def step_setup():
    monitor.SCAN_SECONDS = 0.0
    with override():
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete()
        # instances that geometry nodes do not realize, as in the owner's scene
        ng = bpy.data.node_groups.new('Instances', 'GeometryNodeTree')
        ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        nin = ng.nodes.new('NodeGroupInput')
        nout = ng.nodes.new('NodeGroupOutput')
        inst = ng.nodes.new('GeometryNodeInstanceOnPoints')
        cube = ng.nodes.new('GeometryNodeMeshCube')
        join = ng.nodes.new('GeometryNodeJoinGeometry')
        ng.links.new(nin.outputs[0], inst.inputs['Points'])
        ng.links.new(cube.outputs['Mesh'], inst.inputs['Instance'])
        ng.links.new(nin.outputs[0], join.inputs[0])
        ng.links.new(inst.outputs['Instances'], join.inputs[0])
        ng.links.new(join.outputs[0], nout.inputs[0])
        bpy.ops.mesh.primitive_plane_add(size=60, location=(60, 60, 40))
        bpy.context.active_object.name = 'Instancer'
        bpy.context.active_object.modifiers.new('GN', 'NODES').node_group = ng
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=25, location=(150, 100, 60))
        bpy.context.active_object.name = 'Ball'
        bpy.ops.mesh.primitive_torus_add(major_radius=30, minor_radius=10, location=(185, 100, 66))
        bpy.context.active_object.name = 'Ring'
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.ed.undo_push(message='scene built')
    space = view_area().spaces.active
    rv3d = space.region_3d
    rv3d.view_perspective = 'PERSP'
    rv3d.view_location = (180, 130, 60)
    rv3d.view_rotation = Vector((0.62, -0.62, 0.48)).normalized().to_track_quat('Z', 'Y')
    rv3d.view_distance = 760
    space.clip_end = 20000.0
    st = win().scene.bucket_builder
    st.detect_collisions = True
    st.monitor_volume = True
    log('scene built, monitoring on')
    return 1.5


def step_check_start():
    mon = monitor.get(win().scene)
    assert mon is not None and not mon.status()['busy'], 'the monitor did not get going'
    assert mon.status()['objects'] == 3 and mon.unchecked == {'instances': 1}, (
        mon.status()['objects'], mon.unchecked)
    log('at the start:', mon.status()['objects'], 'parts,', mon.unchecked)
    return 0.1


def step_add():
    i = STATE['round']
    with override():
        bpy.ops.mesh.primitive_monkey_add(size=40 + 3 * i, location=(100 + 20 * (i % 5), 200, 60 + 15 * (i % 3)))
        bpy.ops.ed.undo_push(message=f'monkey {i}')
    scan_due()
    return 0.12


def step_undo():
    n = len(bpy.data.objects)
    STATE['before_undo'] = n
    scan_due()
    key('Z', ctrl=True)                      # the real undo, through the event system
    return 0.15


def step_after_undo():
    STATE['undone'] += len(bpy.data.objects) < STATE['before_undo']
    scan_due()
    return 0.08


def step_redo():
    STATE['before_redo'] = len(bpy.data.objects)
    scan_due()
    key('Z', ctrl=True, shift=True)
    return 0.15


def step_after_redo():
    STATE['redone'] += len(bpy.data.objects) > STATE['before_redo']
    scan_due()
    STATE['round'] += 1
    return 0.08


def step_delete():
    # every third round the newest monkey is deleted with the Delete key
    if STATE['round'] % 3:
        return 0.02
    newest = max((o for o in bpy.data.objects if o.name.startswith('Suzanne')),
                 key=lambda o: o.session_uid, default=None)
    if newest is None:
        return 0.02
    with override():
        bpy.ops.object.select_all(action='DESELECT')
        newest.select_set(True)
        win().view_layer.objects.active = newest
    STATE['before_delete'] = len(bpy.data.objects)
    scan_due()
    key('DEL')
    return 0.15


def step_after_delete():
    if 'before_delete' in STATE:
        STATE['deleted'] += len(bpy.data.objects) < STATE.pop('before_delete')
    scan_due()
    return 0.08


def step_finish():
    return 1.5


def step_verdict():
    mon = monitor.get(win().scene)
    s = mon.status()
    meshes = [o for o in win().view_layer.objects if o.type == 'MESH' and o.visible_get()]
    log(f'{ROUNDS} rounds: undo took effect {STATE["undone"]} times, redo {STATE["redone"]}, '
        f'delete {STATE["deleted"]}')
    log(f'the timer ran {STATE["ticks"]} slices; in {STATE["late"]} of them the dependency graph '
        f'was behind the data and had to be brought up to date first')
    log('at the end:', s['objects'], 'parts in the monitor,', len(meshes), 'visible meshes in the scene,',
        'errors:', STATE['errors'] or 'none', '| monitor error:', repr(mon.error))
    assert STATE['undone'] >= ROUNDS // 2 and STATE['redone'] >= ROUNDS // 2, 'undo / redo did not happen'
    assert STATE['deleted'] >= 2, 'delete did not happen'
    assert STATE['late'] >= 5, 'the timer never came by while the graph was out of date: nothing was tested'
    assert not STATE['errors'] and not mon.error, (STATE['errors'], mon.error)
    assert not s['busy'] and s['objects'] == len(meshes), (s, len(meshes))
    assert mon.unchecked == {'instances': 1}, mon.unchecked
    path = os.path.join(OUT, 'undo_final.png')
    gui_common.screenshot(win(), path)
    log('screenshot', path)
    return 0.1


STEPS = [step_setup, step_check_start]
for _ in range(ROUNDS):
    STEPS += [step_add, step_undo, step_after_undo, step_redo, step_after_redo, step_delete,
              step_after_delete]
STEPS += [step_finish, step_verdict]


def runner():
    if not STEPS:
        log('GUI UNDO TEST OK')
        os._exit(0)
    step = STEPS.pop(0)
    try:
        return step() or 0.1
    except Exception:
        traceback.print_exc()
        print('GUI UNDO TEST FAILED', flush=True)
        os._exit(1)


bpy.app.timers.register(runner, first_interval=1.0)
