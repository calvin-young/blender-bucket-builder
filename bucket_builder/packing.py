# SPDX-License-Identifier: GPL-3.0-or-later
"""Pack (experimental): the packer of `pack/` on the objects of the scene
(docs/AUTOPACK.md, Stages 2 and 3).

The operator reads the parts on the main thread and runs a `pack.job.Job`
in a separate process (`pack/worker.py`; a thread if Blender's own Python
cannot be found): a first layout, then better ones for the time set.  The
parts move as the layout fills and improves; at the end the best layout is
applied as one undo step.  Esc stops it; the best layout so far is kept (or
what the first one has placed by then).
"""

import math
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
from .pack import job as J, metrics, safe, tempdir, worker
from .pack.meshio import Mesh
from .pack.problem import Part, Settings, group_key

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
    """Arrange the parts in the build volume automatically (experimental). Parts
    marked Locked stay where they are and the others are packed around them"""
    bl_idname = "bucketbuilder.pack"
    bl_label = "Pack Parts"
    bl_options = {'UNDO'}          # (no redo panel: redoing would pack again, without progress)

    selected_only: BoolProperty(name="Selected Only", default=True,
                                description="Pack the selected parts (otherwise every visible part)")
    improve: BoolProperty(name="Improve Current Layout", default=False,
                          description="Start from where the parts are now: keep those that fit, place "
                                      "the others, then look for a better layout")

    _thread = None
    _timer = None
    _job = None

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.scene is not None

    def execute(self, context):
        """Without a window (scripts, tests): pack in one go."""
        if not self._prepare(context):
            return {'CANCELLED'}
        try:
            self._result = self._job.run()
        finally:
            self._job.close()
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
            def work():
                try:
                    self._result = self._job.run()
                except Exception as ex:          # reported by the modal handler
                    self._error = ex

            self._thread = threading.Thread(target=work, name='bucket_builder_pack', daemon=True)
            self._thread.start()
        self._applied = 0
        self._shown = (0, 0.0)                   # (version of the layout shown, when)
        self._stop_t = None
        JOB.update(running=True, done=0, total=sum(p.quantity for p in self._parts), eta=None,
                   elapsed=0.0, stopping=False, phase='greedy', height=0.0, first_height=None,
                   budget=self._settings.time_budget, time_left=None)
        _pause_pairs(context.scene, True)
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        self._status(context)
        return {'RUNNING_MODAL'}

    def _start_process(self, exe):
        tempdir.sweep_stale()                  # (what an earlier run left if Blender was killed)
        self._dir = tempdir.new_job_folder()
        worker.write_job(self._dir, self._parts, self._settings, self._lock, self._start)
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
        start = {}
        any_essential = any(o.bucket_builder_pack_essential for o in move)
        for o in move:
            key_mesh = (o.data.as_pointer(), bool(o.modifiers)) if not o.modifiers else ('obj', o.as_pointer())
            if key_mesh not in cache:
                v, t = _mesh_arrays(o, dg)
                cache[key_mesh] = Mesh(v, t, o.data.name)
            mw = np.array(o.matrix_world, dtype=np.float64)
            L = mw[:3, :3] * k
            mode = st.pack_mode if o.bucket_builder_pack_mode == 'DEFAULT' else o.bucket_builder_pack_mode
            tier = 1 if (o.bucket_builder_pack_essential or not any_essential) else 2
            gkey = (key_mesh, group_key(L, _MODE[mode]), tier)
            if gkey not in groups:
                groups[gkey] = (Part(o.name, cache[key_mesh], 0, _MODE[mode], tier=tier, base=L.copy()), [])
            groups[gkey][0].quantity += 1
            groups[gkey][1].append(o)
            M = mw.copy()
            M[:3, :] *= k                           # (mm)
            start[(list(groups).index(gkey), len(groups[gkey][1]) - 1)] = M
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
                     max_density=(st.pack_max_density / 100.0) or None,
                     time_budget=float(st.pack_time), preset=st.pack_preset.lower(),
                     spread_z=not st.pack_keep_low)
        self._settings = s
        self._start = start if self.improve else None
        self._job = J.Job(self._parts, s, lock, start=self._start)
        self._result = None
        self._error = None
        self._t0 = time.perf_counter()
        self._lock = lock
        return True

    def _status(self, context):
        done, total = JOB['done'], JOB['total']
        el = time.perf_counter() - self._t0
        JOB['elapsed'] = el
        if JOB['phase'] == 'greedy':
            JOB['eta'] = (el / done * (total - done)) if done >= 2 else None
        else:
            JOB['eta'] = JOB['time_left']
        left = "" if JOB['eta'] is None else ", about %s left" % _duration(JOB['eta'])
        if JOB['stopping']:
            text = "Stopping..."
        elif JOB['phase'] == 'greedy':
            text = "Packing: %d of %d parts, %s%s   (Esc to stop)" % (done, total, _duration(el), left)
        else:
            text = "Improving the layout: %.1f mm high, %s%s   (Esc to stop, the best layout stays)" % (
                JOB['height'], _duration(el), left)
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass
        _redraw_views()

    def _end(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        if self._job is not None:
            self._job.close()
        if getattr(self, '_proc', None) is not None and self._proc.poll() is None:
            self._proc.kill()
        if getattr(self, '_dir', None):
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None
        JOB.update(running=False, stopping=False)
        _pause_pairs(context.scene, False)
        _redraw_views()

    def _poll_process(self):
        """Take what the worker has reported.  Returns True when it is done."""
        r = worker.read_progress(self._dir)
        alive = self._proc.poll() is None
        if r is not None:
            JOB['done'] = r['done']
            self._take_state(r.get('phase', 'greedy'), r.get('height', 0.0), r.get('budget'),
                             r.get('seconds', 0.0))
            self._result = _result_from(r)
            self._version = r.get('version', 0)
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
                self._job.cancel = True
            self._status(context)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}          # (the user goes on working meanwhile)
        if self._proc is not None:
            done = self._poll_process()
            if not done and self._stop_t is not None and time.perf_counter() - self._stop_t > 5.0:
                self._proc.kill()                # (it did not stop at the next part: stop it now)
            self._show_progress(context)
        else:
            jb = self._job
            JOB['done'] = jb.progress[0]
            self._take_state(jb.phase, jb._best_height() if jb.best else 0.0, jb.budget, jb.elapsed)
            done = not self._thread.is_alive()
        if not done:
            self._status(context)
            return {'PASS_THROUGH'}
        self._end(context)
        if self._error is not None and self._result is None:
            self.report({'ERROR'}, "Packing failed: %s" % safe.tidy(str(self._error)))
            return {'CANCELLED'}
        self._apply(context)
        return {'FINISHED'}

    def _take_state(self, phase, height, budget, seconds):
        if phase != 'greedy' and JOB['first_height'] is None and height:
            JOB['first_height'] = height
        JOB['phase'] = phase
        JOB['height'] = height
        JOB['budget'] = budget
        JOB['time_left'] = None if budget is None else max(0.0, budget - seconds)

    def _show_progress(self, context):
        """Move the parts into place as the packer goes: while the first
        layout fills, the new ones; when a better layout turns up, all of
        them (at most twice a second).  The final layout is applied, and made
        one undo step, at the end."""
        res = self._result
        if res is None:
            return
        k = self._k
        v = getattr(self, '_version', 0)
        if v != self._shown[0]:
            if time.perf_counter() - self._shown[1] < 0.5:
                return
            self._shown = (v, time.perf_counter())
            self._applied = 0
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
        first = JOB.get('first_height')
        if first and first > rep.height + 0.05:
            msg += " (first layout %.1f mm)" % first
        if missing:
            why = sorted({u[2] for u in res.unplaced}) or ['stopped']
            msg += "; %d left beside the volume (%s)" % (missing, ", ".join(why))
        self.report({'WARNING'} if missing else {'INFO'}, msg)
        props.settings(context.scene).pack_last = msg
        monitor.tag_redraw_all()

    def cancel(self, context):
        if self._job is not None:
            self._job.cancel = True
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
        TIMING['fraction'] = 0.0
        TIMING['eta'] = None
        TIMING['done_at'] = None
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        proc = TIMING['proc']
        TIMING['fraction'] = _last_progress(self._out.name, TIMING.get('fraction', 0.0))
        el = time.perf_counter() - TIMING['t0']
        f = TIMING['fraction']
        TIMING['eta'] = el / f * (1.0 - f) if f > 0.02 else None
        try:
            context.workspace.status_text_set("Measuring the packer's speed: %d %%, %s   (it runs in the background)"
                                              % (round(100 * f), _duration(el)))
        except Exception:
            pass
        _redraw_views()
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
            text = ''.join(line for line in f if not line.startswith('@@'))
        os.unlink(self._out.name)
        from . import ops
        text = "Bucket Builder %s packer speed\n\n%s" % (ops.addon_version(), text)
        txt = bpy.data.texts.get("Bucket Builder speed") or bpy.data.texts.new("Bucket Builder speed")
        txt.clear()
        txt.write(text)
        context.window_manager.clipboard = text
        TIMING['done_at'] = time.perf_counter()
        _redraw_views()
        bpy.app.timers.register(_redraw_views, first_interval=DONE_SHOWN + 0.5)    # (to take the badge away)

        def popup(menu, context):
            menu.layout.label(text="The table is on the clipboard: paste it into the chat.", icon='COPYDOWN')
            menu.layout.label(text="It is also in the text \"Bucket Builder speed\".", icon='TEXT')

        context.window_manager.popup_menu(popup, title="Packer speed measured", icon='CHECKMARK')
        self.report({'INFO'}, "Packer speed measured: copied to the clipboard (and the text "
                              "\"Bucket Builder speed\")")
        return {'FINISHED'}


DONE_SHOWN = 60.0      # seconds the "measured" badge stays in the viewport


def _last_progress(path, old):
    """The last "@@progress f" line the timing run has written so far."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - 4096))
            tail = f.read().decode('utf-8', 'replace')
        lines = [ln for ln in tail.splitlines() if ln.startswith('@@progress')]
        return float(lines[-1].split()[1]) if lines else old
    except (OSError, ValueError, IndexError):
        return old


def _redraw_views():
    """Ask every 3D viewport of every window to redraw (for the badge)."""
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# the badge at the top of the viewport while the packer (or its speed
# measurement) works in the background, and for a minute after a measurement
# ---------------------------------------------------------------------------

ACCENT = (0.33, 0.62, 1.0, 1.0)
DONE = (0.3, 0.86, 0.4, 1.0)


def badge_state(now=None):
    """(title, detail, fraction or None, colour) of what to show, or None."""
    now = time.perf_counter() if now is None else now
    if JOB['running']:
        done, total = JOB['done'], max(JOB['total'], 1)
        if JOB['stopping']:
            return ("Stopping the packer...", "the parts placed so far stay", done / total, ACCENT)
        left = "" if JOB['eta'] is None else "about %s left, " % _duration(JOB['eta'])
        if JOB.get('phase', 'greedy') != 'greedy':
            b = JOB.get('budget') or 0.0
            frac = 1.0 - (JOB['time_left'] or 0.0) / b if b > 0 else None
            return ("Improving the layout: %.1f mm" % JOB['height'], left + "Esc keeps the best", frac, ACCENT)
        return ("Packing: %d of %d parts" % (done, total), left + "Esc to stop", done / total, ACCENT)
    if TIMING.get('proc') is not None:
        f = TIMING.get('fraction', 0.0)
        left = "" if TIMING.get('eta') is None else "about %s left, " % _duration(TIMING['eta'])
        return ("Measuring packer speed: %d %%" % round(100 * f), left + "runs in the background", f, ACCENT)
    t = TIMING.get('done_at')
    if t is not None and now - t < DONE_SHOWN:
        return ("Packer speed measured", "copied to the clipboard: paste it into the chat", 1.0, DONE)
    return None


def _wedge(cx, cy, r, frac, n=48):
    """Triangles of a pie slice from 12 o'clock, clockwise."""
    k = max(1, int(round(n * frac)))
    a = math.pi / 2 - np.linspace(0.0, 2 * math.pi * frac, k + 1)
    ring = np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)
    tri = np.empty((k, 3, 2), dtype=np.float32)
    tri[:, 0] = (cx, cy)
    tri[:, 1] = ring[:-1]
    tri[:, 2] = ring[1:]
    return tri.reshape(-1, 2)


def draw_badge():
    state = badge_state()
    if state is None:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        from . import overlay
        title, detail, frac, colour = state
        context = bpy.context
        region = context.region
        ui = context.preferences.system.ui_scale
        r = 20.0 * ui
        big, small = 17.0 * ui, 12.0 * ui
        tw = max(overlay._text_width(title, big), overlay._text_width(detail, small))
        w = 2 * r + 14 * ui + tw + 28 * ui
        h = 2 * r + 20 * ui
        x0 = 0.5 * (region.width - w)
        y0 = region.height - h - 46 * ui                 # (below the header and its tools)
        sh = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        quad = np.array([[x0, y0], [x0 + w, y0], [x0 + w, y0 + h],
                         [x0, y0], [x0 + w, y0 + h], [x0, y0 + h]], dtype=np.float32)
        sh.bind()
        sh.uniform_float("color", (0.08, 0.08, 0.08, 0.78))
        batch_for_shader(sh, 'TRIS', {"pos": quad}).draw(sh)
        sh.uniform_float("color", colour[:3] + (1.0,))
        edge = np.array([[x0, y0], [x0 + 3 * ui, y0], [x0 + 3 * ui, y0 + h],
                         [x0, y0], [x0 + 3 * ui, y0 + h], [x0, y0 + h]], dtype=np.float32)
        batch_for_shader(sh, 'TRIS', {"pos": edge}).draw(sh)
        cx, cy = x0 + 14 * ui + r, y0 + 0.5 * h
        sh.uniform_float("color", (0.35, 0.35, 0.35, 0.9))
        batch_for_shader(sh, 'TRIS', {"pos": overlay._fan(cx, cy, r)}).draw(sh)
        if frac is not None and frac > 0:
            sh.uniform_float("color", colour)
            batch_for_shader(sh, 'TRIS', {"pos": _wedge(cx, cy, r, min(frac, 1.0))}).draw(sh)
        tx = cx + r + 14 * ui
        overlay._text_shadow(True)
        overlay._text(tx, y0 + 0.5 * h + 3 * ui, title, big, (1.0, 1.0, 1.0, 1.0))
        overlay._text(tx, y0 + 0.5 * h - small - 4 * ui, detail, small, (0.85, 0.85, 0.85, 1.0))
        overlay._text_shadow(False)
        gpu.state.blend_set('NONE')
    except Exception as ex:                      # a badge must never break the viewport
        print("Bucket Builder: badge not drawn:", ex)


_badge_handle = []


TIMING = {'proc': None, 't0': 0.0, 'fraction': 0.0, 'eta': None, 'done_at': None}


JOB = {'running': False, 'done': 0, 'total': 0, 'eta': None, 'elapsed': 0.0, 'stopping': False,
       'phase': 'greedy', 'height': 0.0, 'first_height': None, 'budget': None, 'time_left': None}


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
    _badge_handle.append(bpy.types.SpaceView3D.draw_handler_add(draw_badge, (), 'WINDOW', 'POST_PIXEL'))
    bpy.types.Object.bucket_builder_pack_mode = EnumProperty(
        name="Pack Rotation", items=OBJECT_MODE_ITEMS, default='DEFAULT',
        description="How the packer may turn this part")
    bpy.types.Object.bucket_builder_pack_lock = BoolProperty(
        name="Locked", default=False,
        description="The packer leaves this part where it is and packs the others around it")
    bpy.types.Object.bucket_builder_pack_essential = BoolProperty(
        name="Essential", default=False,
        description="When some parts are marked essential, the packer places all of them first, "
                    "and the others only where there is room left")
    tempdir.sweep_stale()                      # (job folders left by a crashed session)


def unregister():
    while _badge_handle:
        bpy.types.SpaceView3D.draw_handler_remove(_badge_handle.pop(), 'WINDOW')
    del bpy.types.Object.bucket_builder_pack_essential
    del bpy.types.Object.bucket_builder_pack_lock
    del bpy.types.Object.bucket_builder_pack_mode
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
