# SPDX-License-Identifier: GPL-3.0-or-later
"""Tools for arranging parts by hand (3D Viewport > Sidebar > Bucket > Arrange).

    Drop to Floor           each selected part straight down onto the floor of
                            the build volume (Ctrl+Down Arrow)
    Minimize Box Volume     turn each part so its bounding box is smallest
    Minimize Height         turn each part so it is as low as it can be, lying
                            on a face of its convex hull
    Tilt Off Axis           turn each part so its large flat faces are not
                            parallel to a wall or to the layers
    Snap to Build Volume    a switch: a wireframe of the volume that Blender's
                            own snapping can use

These only change object transforms, never meshes, and do not depend on the
live check: they could move to an add-on of their own.  Every selected part
is treated on its own.  The geometry is what the viewport shows (after
modifiers), in world space.
"""

import math

import bpy
import numpy as np
from bpy.props import FloatProperty, FloatVectorProperty, StringProperty
from bpy.types import Operator
from mathutils import Matrix, Vector

from . import props

HELPER_KEY = "bucket_builder_helper"     # custom property that marks the snap wireframe
HELPER_NAME = "Build Volume (snap)"


# ---------------------------------------------------------------------------
# geometry of a part
# ---------------------------------------------------------------------------

def _world_mesh(obj, depsgraph, triangles=False):
    """(vertices (n, 3) float64 in world space, triangles (m, 3) or None)."""
    ob = obj.evaluated_get(depsgraph)
    me = ob.to_mesh()
    try:
        n = len(me.vertices)
        co = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get('co', co)
        co = co.reshape(n, 3)
        tris = None
        if triangles:
            me.calc_loop_triangles()
            m = len(me.loop_triangles)
            tris = np.empty(m * 3, dtype=np.int64)
            me.loop_triangles.foreach_get('vertices', tris)
            tris = tris.reshape(m, 3)
    finally:
        ob.to_mesh_clear()
    M = np.array(obj.matrix_world, dtype=np.float64)
    return co @ M[:3, :3].T + M[:3, 3], tris


from .pack.minbox import (_extremes, _hull, _directions, _basis, _min_rect, _frame,  # noqa: F401
                          rotation_min_height, rotation_min_volume)


def flat_faces(points, tris, share=0.04, most=6):
    """The large flat faces of a part: (directions (k, 3), areas (k,)), from
    its triangles grouped by normal (to about a degree).  A group counts if it
    holds at least ``share`` of the surface."""
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    cr = np.cross(b - a, c - a)
    ar = 0.5 * np.linalg.norm(cr, axis=1)
    ok = ar > 1e-12 * max(float(ar.max(initial=0.0)), 1e-30)
    nrm = cr[ok] / (2.0 * ar[ok, None])
    dirs, tot = _directions(nrm, ar[ok], most)
    keep = tot >= share * float(ar.sum())
    return dirs[keep], tot[keep]


def _rotations(rng, n):
    """``n`` rotations: small turns about X and Y (the tilt people make by
    hand) and random ones, as (n, 3, 3)."""
    out = []
    for ax in np.radians(np.arange(-40, 41, 5)):
        for ay in np.radians(np.arange(-40, 41, 5)):
            out.append(_rot_y(ay) @ _rot_x(ax))
    q = rng.normal(size=(n, 4))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    R = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                  np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                  np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], 1)
    return np.concatenate([np.array(out), R])


def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def off_axis_angles(R, dirs):
    """For rotations R (m, 3, 3) and directions (k, 3): the angle of every
    turned direction to the nearest axis, in radians, (m, k)."""
    t = np.abs(np.einsum('mij,kj->mki', R, dirs))
    return np.arccos(np.clip(t.max(axis=2), -1.0, 1.0))


def rotation_off_axis(dirs, area, target):
    """A rotation that brings every large flat face at least ``target``
    radians away from the nearest axis (so from the layers and from the
    walls), the larger faces counting more; of the rotations that do that
    best, the smallest turn."""
    R = _rotations(np.random.default_rng(1), 6000)
    ang = off_axis_angles(R, dirs)
    w = area / area.sum()
    score = (np.minimum(ang, target) / target) @ w
    good = np.flatnonzero(score >= score.max() - 1e-3)
    turn = np.arccos(np.clip((np.trace(R[good], axis1=1, axis2=2) - 1.0) * 0.5, -1.0, 1.0))
    k = good[int(np.argmin(turn))]
    return R[k], float(np.degrees(ang[k].min()))


def _turn(obj, R, points):
    """Turn an object by the world rotation R about the center of its box."""
    c = 0.5 * (points.min(axis=0) + points.max(axis=0))
    T = Matrix.Translation(c)
    obj.matrix_world = T @ Matrix(R.tolist()).to_4x4() @ T.inverted() @ obj.matrix_world


# ---------------------------------------------------------------------------
# the build volume
# ---------------------------------------------------------------------------

def _mm_per_unit(context):
    from . import monitor
    mon = monitor.get(context.scene)
    if mon is None:
        mon = monitor.Monitor(context.scene)
        mon.mm_per_unit, _ = mon._resolve_units(context.scene, props.settings(context.scene))
    return mon.mm_per_unit


def volume_box(context):
    """The build volume as (lo, hi) in scene units."""
    from . import monitor
    scene = context.scene
    st = props.settings(scene)
    mon = monitor.get(scene)
    if mon is None:
        mon = monitor.Monitor(scene)
        mon.mm_per_unit, _ = mon._resolve_units(scene, st)
    return mon.volume_box(st)


def _parts(context):
    return [o for o in context.selected_objects if o.type == 'MESH' and not is_helper(o)]


def is_helper(obj):
    try:
        return bool(obj.get(HELPER_KEY))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

class _PartsOperator:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(
            o.type == 'MESH' and not is_helper(o) for o in context.selected_objects)

    def run(self, context, fn):
        dg = context.evaluated_depsgraph_get()
        done, failed = 0, []
        for obj in _parts(context):
            try:
                fn(obj, dg)
                done += 1
            except Exception as ex:
                failed.append(f"{obj.name}: {ex}")
            dg = context.evaluated_depsgraph_get()   # (the next part sees the moves so far)
        for line in failed[:5]:
            self.report({'WARNING'}, line)
        return done


class BUCKETBUILDER_OT_drop_to_floor(_PartsOperator, Operator):
    """Move each selected part straight down until it rests on the floor of the build volume"""
    bl_idname = "bucketbuilder.drop_to_floor"
    bl_label = "Drop to Floor"

    def execute(self, context):
        from .pack.problem import FLOOR_INSET
        # 0.001 mm above the floor: the checker passes contact, but placing keeps off it (the owner, 2026-11-19)
        floor = float(volume_box(context)[0][2]) + FLOOR_INSET / _mm_per_unit(context)

        def drop(obj, dg):
            pts, _ = _world_mesh(obj, dg)
            if len(pts):
                obj.matrix_world = Matrix.Translation((0.0, 0.0, floor - float(pts[:, 2].min()))) \
                    @ obj.matrix_world

        n = self.run(context, drop)
        self.report({'INFO'}, f"{n} part(s) dropped to the floor")
        return {'FINISHED'}


class BUCKETBUILDER_OT_minimize_box(_PartsOperator, Operator):
    """Turn each selected part so that its bounding box is as small as it can be (the shortest side upright)"""
    bl_idname = "bucketbuilder.minimize_box"
    bl_label = "Minimize Box Volume"

    def execute(self, context):
        def turn(obj, dg):
            pts, _ = _world_mesh(obj, dg)
            _turn(obj, rotation_min_volume(pts), pts)

        n = self.run(context, turn)
        self.report({'INFO'}, f"{n} part(s) turned to their smallest box")
        return {'FINISHED'}


class BUCKETBUILDER_OT_minimize_height(_PartsOperator, Operator):
    """Turn each selected part so that it is as low as it can be, lying on a face of its outer shape"""
    bl_idname = "bucketbuilder.minimize_height"
    bl_label = "Minimize Height"

    def execute(self, context):
        def turn(obj, dg):
            pts, _ = _world_mesh(obj, dg)
            _turn(obj, rotation_min_height(pts), pts)

        n = self.run(context, turn)
        self.report({'INFO'}, f"{n} part(s) turned to their lowest")
        return {'FINISHED'}


class BUCKETBUILDER_OT_tilt_off_axis(_PartsOperator, Operator):
    """Turn each selected part so that its large flat faces are not parallel to the layers or to the walls, which avoids stair-stepping on them"""
    bl_idname = "bucketbuilder.tilt_off_axis"
    bl_label = "Tilt Off Axis"

    angle: FloatProperty(
        name="Angle", description="How far every large flat face should be from the nearest axis",
        default=math.radians(20.0), min=math.radians(1.0), max=math.radians(45.0), subtype='ANGLE')

    def execute(self, context):
        flat = []

        def turn(obj, dg):
            pts, tris = _world_mesh(obj, dg, triangles=True)
            dirs, area = flat_faces(pts, tris)
            if not len(dirs):
                flat.append(obj.name)
                return
            R, _ = rotation_off_axis(dirs, area, self.angle)
            _turn(obj, R, pts)

        n = self.run(context, turn) - len(flat)
        if flat:
            self.report({'INFO'}, f"No large flat faces: {', '.join(flat[:4])}"
                        + (" ..." if len(flat) > 4 else ""))
        self.report({'INFO'}, f"{n} part(s) tilted off axis")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# snapping to the build volume
# ---------------------------------------------------------------------------
# Blender snaps to real geometry only, and not to hidden objects.  So while
# the switch is on there is a wireframe object: the twelve edges of the
# volume, which cannot be selected (Blender still snaps to it), is never
# rendered, and is left out of every check.  The overlay draws the volume on
# top of it.  Switched off, the object goes.

_EDGES = ((0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4),
          (0, 4), (1, 5), (2, 6), (3, 7))


def align_face_down(obj, normal, point, depsgraph, floor):
    """Turn obj so the face with world normal `normal` faces straight down, about the point clicked,
    then drop it to the floor. The rotation is the shortest one, so the part turns no more than it
    must; an instanced part only changes its own transform (#50)."""
    n = Vector(normal).normalized()
    R = n.rotation_difference(Vector((0.0, 0.0, -1.0))).to_matrix().to_4x4()
    P = Matrix.Translation(Vector(point))
    obj.matrix_world = P @ R @ P.inverted() @ obj.matrix_world
    depsgraph.update()
    pts, _ = _world_mesh(obj, depsgraph)
    if len(pts):
        obj.matrix_world = Matrix.Translation((0.0, 0.0, floor - float(pts[:, 2].min()))) @ obj.matrix_world


class BUCKETBUILDER_OT_align_to_plane(Operator):
    """Click a face of a part: the part turns so that face lies flat on the build plate (facing down),
    then drops to the floor. Esc or right-click to stop"""
    bl_idname = "bucketbuilder.align_to_plane"
    bl_label = "Align to Plane"
    bl_options = {'REGISTER', 'UNDO'}

    # (for scripts and tests: a part, a world normal and a point on the face, with no clicking)
    part: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    normal: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    point: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def _floor(self, context):
        from .pack.problem import FLOOR_INSET
        return float(volume_box(context)[0][2]) + FLOOR_INSET / _mm_per_unit(context)

    def execute(self, context):
        obj = bpy.data.objects.get(self.part)
        if obj is None or Vector(self.normal).length < 1e-9:
            return {'CANCELLED'}
        align_face_down(obj, self.normal, self.point, context.evaluated_depsgraph_get(), self._floor(context))
        return {'FINISHED'}

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Align to Plane works in the 3D Viewport")
            return {'CANCELLED'}
        context.window.cursor_modal_set('EYEDROPPER')
        context.workspace.status_text_set("Align to Plane: click a face to lay it on the build plate   "
                                          "Esc / right-click: stop")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            from bpy_extras import view3d_utils
            region, rv3d = context.region, context.region_data
            if region is None or rv3d is None or region.type != 'WINDOW':
                return {'PASS_THROUGH'}
            co = (event.mouse_region_x, event.mouse_region_y)
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
            ray = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
            dg = context.evaluated_depsgraph_get()
            hit, loc, nrm, _i, obj, _M = context.scene.ray_cast(dg, origin, ray)
            if not hit or obj is None or obj.type != 'MESH' or is_helper(obj):
                self.report({'INFO'}, "Click a face of a part")
                return {'RUNNING_MODAL'}
            obj = obj.original
            align_face_down(obj, nrm, loc, dg, self._floor(context))
            self.part, self.normal, self.point = obj.name, tuple(nrm), tuple(loc)
            self._end(context)
            self.report({'INFO'}, f"{obj.name}: the face is on the build plate")
            return {'FINISHED'}
        return {'PASS_THROUGH'}


# ---------------------------------------------------------------------------
# snapping a turned part (#52)
# ---------------------------------------------------------------------------
# Blender snaps a part by the corners of its own bounding box, which turns with it: after Tilt Off
# Axis (rotation not applied, which instanced parts can't do) the box's corner lands on the wall and
# the part stops short of it. After a move that snapped, a part whose box corner sits on a plane of
# the snap wireframe is moved on until its real surface sits there instead.

_last_op = [None]


def snap_planes(scene, context=None):
    """The six planes of the snap wireframe as (lo, hi) in scene units, or None."""
    obj = find_helper(scene)
    if obj is None or len(obj.data.vertices) != 8:
        return None
    co = np.array([v.co[:] for v in obj.data.vertices])
    return co.min(axis=0), co.max(axis=0)


def settle_snapped(context, objs):
    """Move each turned part whose rotated box touches a snap plane so its true extent does.
    Returns how many were moved."""
    planes = snap_planes(context.scene, context)
    if planes is None:
        return 0
    lo, hi = planes
    size = float(np.max(hi - lo)) or 1.0
    tol = 1e-5 * size
    dg = context.evaluated_depsgraph_get()
    n = 0
    for obj in objs:
        M = np.array(obj.matrix_world)
        R = M[:3, :3] / np.maximum(np.linalg.norm(M[:3, :3], axis=0), 1e-12)
        if np.allclose(np.abs(R), np.round(np.abs(R)), atol=1e-6):
            continue                                     # (axis-aligned: Blender's snap is exact)
        box = np.array([obj.matrix_world @ Vector(c) for c in obj.bound_box])
        blo, bhi = box.min(axis=0), box.max(axis=0)
        pts, _ = _world_mesh(obj, dg)
        if not len(pts):
            continue
        tlo, thi = pts.min(axis=0), pts.max(axis=0)
        d = np.zeros(3)
        for a in range(3):
            if abs(blo[a] - lo[a]) < tol:
                d[a] = lo[a] - tlo[a]
            elif abs(bhi[a] - hi[a]) < tol:
                d[a] = hi[a] - thi[a]
        if np.any(np.abs(d) > tol):
            obj.matrix_world = Matrix.Translation(d.tolist()) @ obj.matrix_world
            n += 1
    return n


def _watch_snaps():
    """Timer: after a move that snapped, settle the turned parts it moved."""
    try:
        ctx = bpy.context
        scene = ctx.scene
        st = props.settings(scene) if scene is not None else None
        wm = ctx.window_manager
        ops = wm.operators if wm is not None else ()
        last = ops[-1] if len(ops) else None
        key = last.as_pointer() if last is not None else None
        if key != _last_op[0]:
            _last_op[0] = key
            if (st is not None and st.snap_to_volume and last is not None
                    and last.bl_idname == 'TRANSFORM_OT_translate' and last.properties.snap):
                objs = [o for o in ctx.selected_objects if o.type == 'MESH' and not is_helper(o)]
                if 0 < len(objs) <= 16:
                    settle_snapped(ctx, objs)
    except Exception:                                    # noqa: BLE001  (never break the UI for this)
        pass
    return 0.25


def find_helper(scene):
    for obj in scene.objects:
        if is_helper(obj):
            return obj
    return None


_syncing = [False]


def sync_snap(scene, context=None):
    """Make the snap wireframe what the switch and the volume say it should be."""
    if _syncing[0]:
        return                    # (making it changes the scene, which comes back here)
    _syncing[0] = True
    try:
        _sync_snap(scene, context)
    finally:
        _syncing[0] = False


def _sync_snap(scene, context):
    st = props.settings(scene)
    if st is None:
        return
    obj = find_helper(scene)
    if not (st.snap_to_volume and st.monitor_volume):
        # (with the build volume's monitor off, no wireframe: the owner, 2026-11-24)
        if obj is not None:
            me = obj.data
            bpy.data.objects.remove(obj)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
        return
    lo, hi = volume_box(context or bpy.context)
    from .core.world import INSIDE_MARGIN
    m = 2.0 * INSIDE_MARGIN / _mm_per_unit(context or bpy.context)    # (snapping lands just inside the
    from .pack.problem import FLOOR_INSET
    lo, hi = lo + np.array([m, m, FLOOR_INSET / _mm_per_unit(context or bpy.context)]), hi - m   # side walls and the top;
                                                                                                   # 0.001 mm above the floor)
    if st.use_volume and st.use_wall_clearance and st.wall_clearance_mm > 0.0:
        # with a wall gap, parts snap to it, not to the walls (the owner, 2026-11-23)
        g = st.wall_clearance_mm / _mm_per_unit(context or bpy.context)
        lo, hi = lo + np.array([g, g, 0.0]), hi - np.array([g, g, 0.0])
    pts = [(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]
    if obj is None:
        me = bpy.data.meshes.new(HELPER_NAME)
        me.from_pydata(pts, _EDGES, [])
        obj = bpy.data.objects.new(HELPER_NAME, me)
        obj[HELPER_KEY] = True
        obj.display_type = 'WIRE'
        obj.hide_render = True
        obj.hide_select = True
        scene.collection.objects.link(obj)
        return
    me = obj.data
    now = [tuple(v.co) for v in me.vertices]
    if len(now) != 8 or any(max(abs(a - b) for a, b in zip(p, q)) > 1e-9 for p, q in zip(now, pts)):
        me.clear_geometry()
        me.from_pydata(pts, _EDGES, [])
        me.update()
    if obj.matrix_world != Matrix.Identity(4):
        obj.matrix_world = Matrix.Identity(4)


CLASSES = (BUCKETBUILDER_OT_drop_to_floor, BUCKETBUILDER_OT_minimize_box,
           BUCKETBUILDER_OT_minimize_height, BUCKETBUILDER_OT_tilt_off_axis, BUCKETBUILDER_OT_align_to_plane)

_keymaps = []


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.timers.is_registered(_watch_snaps):
        bpy.app.timers.register(_watch_snaps, first_interval=1.0, persistent=True)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
        kmi = km.keymap_items.new(BUCKETBUILDER_OT_drop_to_floor.bl_idname, 'DOWN_ARROW', 'PRESS',
                                  ctrl=True)
        _keymaps.append((km, kmi))


def unregister():
    if bpy.app.timers.is_registered(_watch_snaps):
        bpy.app.timers.unregister(_watch_snaps)
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
