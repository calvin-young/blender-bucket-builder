# SPDX-License-Identifier: GPL-3.0-or-later
"""Pack (experimental): the greedy packer of `pack/` on the objects of the
scene (docs/AUTOPACK.md, Stage 2).

The operator reads the parts on the main thread, runs the packer in a
worker thread (NumPy releases the GIL in the heavy calls), shows progress in
the status bar, and moves the objects when it is done, as one undo step.
Esc stops it; what was placed by then is kept.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator

from . import monitor, props
from .pack import greedy, metrics, worker
from .pack.meshio import Mesh
from .pack.problem import Part, Settings

MODE_ITEMS = (
    ('FIXED', "Fixed", "Keep the part's rotation; only move it"),
    ('Z', "About Z", "Turn the part about the vertical axis"),
    ('FREE', "Free", "Any of the 24 axis-aligned orientations of its principal axes"),
)
OBJECT_MODE_ITEMS = (('DEFAULT', "Bucket Default", "Use the rotation set in the Pack panel"),) + MODE_ITEMS
_MODE = {'FIXED': 'fixed', 'Z': 'z', 'FREE': 'free'}


def _mesh_arrays(obj, depsgraph):
    """Vertices (object space) and triangles of the evaluated mesh."""
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
        v = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertices.foreach_get('co', v)
        t = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
        me.loop_triangles.foreach_get('vertices', t)
    finally:
        ev.to_mesh_clear()
    return v.reshape(-1, 3).astype(np.float64), t.reshape(-1, 3)


def mm_per_unit(scene, objects):
    mon = monitor.get(scene)
    if mon is not None:
        return mon.mm_per_unit
    st = props.settings(scene)
    us = scene.unit_settings
    scale = float(f"{us.scale_length:.7g}") if us.system != 'NONE' else 1.0
    if st.unit_mode == 'MM':
        return 1.0
    if st.unit_mode == 'SCENE' or (us.system != 'NONE' and abs(scale - 1.0) > 1e-9):
        return scale * 1000.0
    ext = [max(o.dimensions) for o in objects] or [10.0]
    return 1.0 if float(np.median(ext)) > 1.5 else 1000.0


def volume_mm(scene, k):
    """The build volume (lo, hi) in mm, as the monitor would place it."""
    st = props.settings(scene)
    size = np.array(st.volume_size, float)
    off = np.array(st.volume_offset, float)
    if st.volume_align == 'CORNER':
        lo = off
    elif st.volume_align == 'CENTER_XY':
        lo = off + np.array([-0.5 * size[0], -0.5 * size[1], 0.0])
    else:
        lo = off - 0.5 * size
    return lo, lo + size


def candidates(context, selected_only):
    """The mesh objects to pack and the locked ones."""
    objs = [o for o in (context.selected_objects if selected_only else context.scene.objects)
            if o.type == 'MESH' and not o.bucket_builder_ignore and o.visible_get()]
    move = [o for o in objs if not o.bucket_builder_pack_lock]
    locked = [o for o in context.scene.objects
              if o.type == 'MESH' and o.bucket_builder_pack_lock and not o.bucket_builder_ignore]
    return move, locked


class BUCKETBUILDER_OT_pack(Operator):
    """Arrange the parts in the build volume automatically (experimental: the
    first, greedy packer). Parts marked Locked stay where they are and the
    others are packed around them"""
    bl_idname = "bucketbuilder.pack"
    bl_label = "Pack Parts"
    bl_options = {'UNDO'}          # (no redo panel: redoing would pack again, without progress)

    selected_only: BoolProperty(name="Selected Only", default=True,
                                description="Pack the selected parts (otherwise every visible part)")

    _thread = None
    _timer = None
    _g = None

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.scene is not None

    def execute(self, context):
        """Without a window (scripts, tests): pack in one go."""
        if not self._prepare(context):
            return {'CANCELLED'}
        self._result = self._g.run(locked=self._lock)
        if self._g._pool is not None:
            self._g._pool.shutdown()
        self._apply(context)
        return {'FINISHED'}

    def invoke(self, context, event):
        if JOB['running']:
            self.report({'WARNING'}, "A packing run is already going on")
            return {'CANCELLED'}
        if not self._prepare(context):
            return {'CANCELLED'}
        self._proc = None
        exe = python_exe()
        if exe is not None:
            try:
                self._start_process(exe)
            except Exception as ex:                  # no separate Python: threads, as before
                print("Bucket Builder: packing in a separate process failed (%s); using a thread" % ex)
                self._proc = None
        if self._proc is None:
            lock = self._lock

            def work():
                try:
                    self._result = self._g.run(locked=lock)
                except Exception as ex:          # reported by the modal handler
                    self._error = ex

            self._thread = threading.Thread(target=work, name='bucket_builder_pack', daemon=True)
            self._thread.start()
        self._applied = 0
        self._stop_t = None
        JOB.update(running=True, done=0, total=sum(p.quantity for p in self._parts), eta=None,
                   elapsed=0.0, stopping=False)
        _pause_pairs(context.scene, True)
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        self._status(context)
        return {'RUNNING_MODAL'}

    def _start_process(self, exe):
        self._dir = tempfile.mkdtemp(prefix='bucket_builder_pack_')
        worker.write_job(self._dir, self._parts, self._settings, self._lock)
        addon = os.path.dirname(os.path.abspath(__file__))
        code = ("import sys; sys.path.insert(0, %r); from pack.worker import main; sys.exit(main(%r))"
                % (addon, self._dir))
        kw = {}
        if os.name == 'nt':
            kw['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | \
                getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        else:
            kw['preexec_fn'] = lambda: os.nice(5)
        self._proc = subprocess.Popen([exe, '-I', '-c', code], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, **kw)

    def _prepare(self, context):
        scene = context.scene
        st = props.settings(scene)
        move, locked = candidates(context, self.selected_only)
        if not move:
            self.report({'WARNING'}, "No parts to pack: select mesh objects (or untick Selected Only)")
            return False
        k = mm_per_unit(scene, move)
        vlo, vhi = volume_mm(scene, k)
        dg = context.evaluated_depsgraph_get()
        cache, groups = {}, {}
        for o in move:
            key_mesh = (o.data.as_pointer(), bool(o.modifiers)) if not o.modifiers else ('obj', o.as_pointer())
            if key_mesh not in cache:
                v, t = _mesh_arrays(o, dg)
                cache[key_mesh] = Mesh(v, t, o.data.name)
            mw = np.array(o.matrix_world, dtype=np.float64)
            L = mw[:3, :3] * k
            mode = st.pack_mode if o.bucket_builder_pack_mode == 'DEFAULT' else o.bucket_builder_pack_mode
            gkey = (key_mesh, np.round(L, 9).tobytes(), mode)
            if gkey not in groups:
                groups[gkey] = (Part(o.name, cache[key_mesh], 0, _MODE[mode], base=L.copy()), [])
            groups[gkey][0].quantity += 1
            groups[gkey][1].append(o)
        lock = []
        for o in locked:
            v, t = _mesh_arrays(o, dg)
            M = np.array(o.matrix_world, dtype=np.float64)
            M[:3, :] *= k
            lock.append((Mesh(v, t, o.name), M))
        self._parts = [g[0] for g in groups.values()]
        self._objects = [g[1] for g in groups.values()]
        self._k = k
        self._vlo, self._vhi = vlo, vhi
        s = Settings(vlo=vlo, vhi=vhi, gap=st.pack_gap, wall_gap=st.pack_wall_gap,
                     h=st.pack_cell, z_step=st.pack_z_step,
                     max_density=(st.pack_max_density / 100.0) or None)
        self._settings = s
        self._g = greedy.Greedy(self._parts, s)
        self._result = None
        self._error = None
        self._t0 = time.perf_counter()
        self._lock = lock
        return True

    def _status(self, context):
        done, total = JOB['done'], JOB['total']
        el = time.perf_counter() - self._t0
        JOB['elapsed'] = el
        JOB['eta'] = (el / done * (total - done)) if done >= 2 else None
        left = "" if JOB['eta'] is None else ", about %s left" % _duration(JOB['eta'])
        text = ("Stopping..." if JOB['stopping'] else
                "Packing: %d of %d parts, %s%s   (Esc to stop)" % (done, total, _duration(el), left))
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass
        for area in context.screen.areas if context.screen else ():
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def _end(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        if self._g is not None and self._g._pool is not None:
            self._g._pool.shutdown(wait=False)
        if getattr(self, '_proc', None) is not None and self._proc.poll() is None:
            self._proc.kill()
        if getattr(self, '_dir', None):
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None
        JOB.update(running=False, stopping=False)
        _pause_pairs(context.scene, False)

    def _poll_process(self):
        """Take what the worker has reported.  Returns True when it is done."""
        r = worker.read_progress(self._dir)
        alive = self._proc.poll() is None
        if r is not None:
            JOB['done'] = r['done']
            self._result = _result_from(r)
            if r.get('error'):
                self._error = r['error'].strip().splitlines()[-1]
        if not alive and (r is None or not r.get('finished')):
            err = self._proc.stderr.read().decode('utf-8', 'replace')[-500:] if self._proc.stderr else ''
            if not JOB['stopping']:
                self._error = self._error or ('the packing process ended unexpectedly. ' + err).strip()
        return not alive

    def modal(self, context, event):
        if ((event.type == 'ESC' and event.value == 'PRESS') or JOB.get('stop_request')) \
                and not JOB['stopping']:
            JOB['stop_request'] = False
            JOB['stopping'] = True
            self._stop_t = time.perf_counter()
            if self._proc is not None:
                open(os.path.join(self._dir, 'stop'), 'w').close()
            else:
                self._g.cancel = True
            self._status(context)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}          # (the user goes on working meanwhile)
        if self._proc is not None:
            done = self._poll_process()
            if not done and self._stop_t is not None and time.perf_counter() - self._stop_t > 5.0:
                self._proc.kill()                # (it did not stop at the next part: stop it now)
            self._show_progress(context)
        else:
            JOB['done'] = self._g.progress[0]
            done = not self._thread.is_alive()
        if not done:
            self._status(context)
            return {'PASS_THROUGH'}
        self._end(context)
        if self._error is not None and self._result is None:
            self.report({'ERROR'}, "Packing failed: %s" % self._error)
            return {'CANCELLED'}
        self._apply(context)
        return {'FINISHED'}

    def _show_progress(self, context):
        """Move the parts placed so far into place, so the build fills up as
        the packer goes (only the new ones; the final layout is applied, and
        made one undo step, at the end)."""
        res = self._result
        if res is None:
            return
        k = self._k
        for p in res.placements[self._applied:]:
            o = self._objects[p.part][p.copy]
            M = p.matrix.copy()
            M[:3, :] /= k
            o.matrix_world = _matrix(M)
        self._applied = len(res.placements)

    def _apply(self, context):
        res = self._result
        k = self._k
        placed = set()
        for p in res.placements:
            o = self._objects[p.part][p.copy]
            M = p.matrix.copy()
            M[:3, :] /= k                        # mm -> scene units
            o.matrix_world = _matrix(M)
            placed.add(o.as_pointer())
        # parts that did not fit go in a row beside the volume, out of the way
        x = self._vhi[0] + 20.0
        missing = 0
        for i, objs in enumerate(self._objects):
            for o in objs:
                if o.as_pointer() in placed:
                    continue
                missing += 1
                mw = np.array(o.matrix_world, dtype=np.float64)
                lo = np.min(np.array([mw @ np.array([*c, 1.0]) for c in o.bound_box])[:, :3], axis=0) * k
                hi = np.max(np.array([mw @ np.array([*c, 1.0]) for c in o.bound_box])[:, :3], axis=0) * k
                shift = np.array([x - lo[0], self._vlo[1] - lo[1], self._vlo[2] - lo[2]])
                mw[:3, 3] += shift / k
                o.matrix_world = _matrix(mw)
                x += (hi[0] - lo[0]) + 10.0
        context.view_layer.update()
        rep = metrics.measure(res.items(self._parts), self._vlo, self._vhi)
        total = sum(len(objs) for objs in self._objects)
        msg = "Packed %d of %d parts in %.1f s: height %.1f mm, density %.1f %%" % (
            len(res.placements), total, time.perf_counter() - self._t0,
            rep.height, 100.0 * (rep.density or 0.0))
        if missing:
            why = sorted({u[2] for u in res.unplaced}) or ['stopped']
            msg += "; %d left beside the volume (%s)" % (missing, ", ".join(why))
        self.report({'WARNING'} if missing else {'INFO'}, msg)
        props.settings(context.scene).pack_last = msg
        monitor.tag_redraw_all()

    def cancel(self, context):
        if self._g is not None:
            self._g.cancel = True
        self._end(context)


class BUCKETBUILDER_OT_pack_stop(Operator):
    """Stop the packing run (the parts placed so far stay where they are)"""
    bl_idname = "bucketbuilder.pack_stop"
    bl_label = "Stop Packing"

    def execute(self, context):
        JOB['stop_request'] = True
        return {'FINISHED'}


class BUCKETBUILDER_OT_pack_timing(Operator):
    """Measure how fast the packer is on this computer (a few minutes, in the
    background). The result goes to the clipboard and to a text called
    "Bucket Builder speed", to paste into a message"""
    bl_idname = "bucketbuilder.pack_timing"
    bl_label = "Measure Packer Speed"

    def invoke(self, context, event):
        if TIMING.get('proc') is not None:
            self.report({'WARNING'}, "Already measuring")
            return {'CANCELLED'}
        exe = python_exe()
        if exe is None:
            self.report({'ERROR'}, "Blender's own Python was not found")
            return {'CANCELLED'}
        addon = os.path.dirname(os.path.abspath(__file__))
        self._out = tempfile.NamedTemporaryFile(prefix='bucket_builder_speed_', suffix='.txt', delete=False)
        code = ("import sys; sys.path.insert(0, %r); from pack.timing import main; main(['--greedy'])" % addon)
        kw = {}
        if os.name == 'nt':
            kw['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        TIMING['proc'] = subprocess.Popen([exe, '-I', '-c', code], stdout=self._out,
                                          stderr=subprocess.STDOUT, **kw)
        TIMING['t0'] = time.perf_counter()
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        proc = TIMING['proc']
        try:
            context.workspace.status_text_set("Measuring the packer's speed: %s   (it runs in the background)"
                                              % _duration(time.perf_counter() - TIMING['t0']))
        except Exception:
            pass
        if proc.poll() is None:
            return {'PASS_THROUGH'}
        context.window_manager.event_timer_remove(self._timer)
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        TIMING['proc'] = None
        self._out.close()
        with open(self._out.name, encoding='utf-8', errors='replace') as f:
            text = f.read()
        os.unlink(self._out.name)
        from . import ops
        text = "Bucket Builder %s packer speed\n\n%s" % (ops.addon_version(), text)
        txt = bpy.data.texts.get("Bucket Builder speed") or bpy.data.texts.new("Bucket Builder speed")
        txt.clear()
        txt.write(text)
        context.window_manager.clipboard = text
        self.report({'INFO'}, "Packer speed measured: copied to the clipboard (and the text "
                              "\"Bucket Builder speed\")")
        return {'FINISHED'}


TIMING = {'proc': None, 't0': 0.0}


JOB = {'running': False, 'done': 0, 'total': 0, 'eta': None, 'elapsed': 0.0, 'stopping': False}


def _duration(s):
    s = int(round(s))
    return "%d s" % s if s < 90 else "%d min %02d s" % (s // 60, s % 60)


def _result_from(r):
    from .pack.problem import Placement, Result
    res = Result()
    res.placements = [Placement(int(i), int(c), np.array(m, float).reshape(4, 4), int(k))
                      for i, c, k, m in r['placements']]
    res.unplaced = [(int(i), int(c), why) for i, c, why in r['unplaced']]
    res.seconds = r.get('seconds', 0.0)
    return res


def python_exe():
    """Blender's own Python interpreter (it has NumPy), or None."""
    import glob
    import sys
    exe = sys.executable or ''
    if os.path.basename(exe).lower().startswith('python') and os.path.exists(exe):
        return exe
    base = os.path.dirname(bpy.app.binary_path)
    ver = "%d.%d" % bpy.app.version[:2]
    for pat in (os.path.join(base, ver, 'python', 'bin', 'python*'),
                os.path.join(base, '..', 'Resources', ver, 'python', 'bin', 'python*')):
        hits = sorted(h for h in glob.glob(pat) if os.path.isfile(h))
        if hits:
            return hits[0]
    return None


def _pause_pairs(scene, flag):
    """Collision checks wait while parts fly into place (the packer keeps
    them apart; the monitor looks again at the end)."""
    mon = monitor.get(scene)
    if mon is not None:
        mon.packing = flag
        mon.params_dirty = True


def _matrix(M):
    from mathutils import Matrix
    return Matrix([list(map(float, row)) for row in M])


CLASSES = (BUCKETBUILDER_OT_pack, BUCKETBUILDER_OT_pack_stop, BUCKETBUILDER_OT_pack_timing)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.bucket_builder_pack_mode = EnumProperty(
        name="Pack Rotation", items=OBJECT_MODE_ITEMS, default='DEFAULT',
        description="How the packer may turn this part")
    bpy.types.Object.bucket_builder_pack_lock = BoolProperty(
        name="Locked", default=False,
        description="The packer leaves this part where it is and packs the others around it")


def unregister():
    del bpy.types.Object.bucket_builder_pack_lock
    del bpy.types.Object.bucket_builder_pack_mode
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
