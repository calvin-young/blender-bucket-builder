# SPDX-License-Identifier: GPL-3.0-or-later
"""Pack (experimental): the greedy packer of `pack/` on the objects of the
scene (docs/AUTOPACK.md, Stage 2).

The operator reads the parts on the main thread, runs the packer in a
worker thread (NumPy releases the GIL in the heavy calls), shows progress in
the status bar, and moves the objects when it is done, as one undo step.
Esc stops it; what was placed by then is kept.
"""

import threading
import time

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator

from . import monitor, props
from .pack import greedy, metrics
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
        if not self._prepare(context):
            return {'CANCELLED'}
        lock = self._lock

        def work():
            try:
                self._result = self._g.run(locked=lock)
            except Exception as ex:          # reported by the modal handler
                self._error = ex

        self._thread = threading.Thread(target=work, name='bucket_builder_pack', daemon=True)
        self._thread.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        self._status(context)
        return {'RUNNING_MODAL'}

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
        done, total = self._g.progress
        text = "Packing: %d of %d parts, %.0f s   (Esc to stop)" % (done, total, time.perf_counter() - self._t0)
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass

    def _end(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        if self._g._pool is not None:
            self._g._pool.shutdown(wait=False)

    def modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._g.cancel = True
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}
        if self._thread.is_alive():
            self._status(context)
            return {'RUNNING_MODAL'}
        self._end(context)
        if self._error is not None:
            self.report({'ERROR'}, "Packing failed: %s" % self._error)
            return {'CANCELLED'}
        self._apply(context)
        return {'FINISHED'}

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
        msg = "Packed %d of %d parts in %.1f s: height %.1f mm, density %.1f %%" % (
            len(res.placements), len(res.placements) + len(res.unplaced), time.perf_counter() - self._t0,
            rep.height, 100.0 * (rep.density or 0.0))
        if missing:
            why = sorted({u[2] for u in res.unplaced})
            msg += "; %d left beside the volume (%s)" % (missing, ", ".join(why))
        self.report({'WARNING'} if missing else {'INFO'}, msg)
        props.settings(context.scene).pack_last = msg
        monitor.tag_redraw_all()

    def cancel(self, context):
        if self._g is not None:
            self._g.cancel = True
        self._end(context)


def _matrix(M):
    from mathutils import Matrix
    return Matrix([list(map(float, row)) for row in M])


CLASSES = (BUCKETBUILDER_OT_pack,)


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
