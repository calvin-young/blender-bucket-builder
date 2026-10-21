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
from bpy.props import FloatProperty
from bpy.types import Operator
from mathutils import Matrix

from . import props

HELPER_KEY = "bucket_builder_helper"     # custom property that marks the snap wireframe
HELPER_NAME = "Build Volume (snap)"
MAX_NORMALS = 400        # hull face directions tried for the smallest box


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


def _extremes(points, n=600):
    """The points that are furthest out along ``n`` directions spread over
    the sphere: a few hundred points whose hull is close to the hull of all
    of them."""
    k = np.arange(n) + 0.5
    phi = np.arccos(1.0 - 2.0 * k / n)
    th = math.pi * (1.0 + 5.0 ** 0.5) * k
    d = np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)
    pts = points
    if len(pts) > 200000:
        pts = pts[np.random.default_rng(0).choice(len(pts), 200000, replace=False)]
    pr = pts @ d.T
    idx = np.unique(np.concatenate([pr.argmax(axis=0), pr.argmin(axis=0)]))
    return pts[idx]


def _hull(points):
    """Convex hull of a part: (vertices (k, 3), outward face normals (f, 3),
    face areas (f,)).  Made from the part's extreme points (see
    ``_extremes``), incrementally: what it is for is choosing an
    orientation, and the final box is measured on all points."""
    pts = _extremes(np.asarray(points, dtype=np.float64))
    span = float(np.ptp(pts, axis=0).max()) if len(pts) else 0.0
    eps = 1e-9 * max(span, 1e-30)
    if len(pts) < 4 or span <= 0.0:
        raise ValueError("the part is flat or empty")
    # a first tetrahedron of points far apart
    i0 = int(np.argmin(pts[:, 0]))
    i1 = int(np.argmax(np.linalg.norm(pts - pts[i0], axis=1)))
    line = pts[i1] - pts[i0]
    i2 = int(np.argmax(np.linalg.norm(np.cross(pts - pts[i0], line), axis=1)))
    nrm0 = np.cross(line, pts[i2] - pts[i0])
    i3 = int(np.argmax(np.abs((pts - pts[i0]) @ nrm0)))
    if abs(float((pts[i3] - pts[i0]) @ nrm0)) <= eps * float(np.linalg.norm(nrm0)):
        raise ValueError("the part is flat")
    inside = pts[[i0, i1, i2, i3]].mean(axis=0)
    faces = []

    def add(a, b, c):
        n = np.cross(pts[b] - pts[a], pts[c] - pts[a])
        if np.dot(n, pts[a] - inside) < 0.0:
            b, c = c, b
            n = -n
        ln = float(np.linalg.norm(n))
        faces.append((a, b, c, n / ln if ln > 0 else n, float(np.dot(n / ln if ln > 0 else n, pts[a]))))

    for f in ((i0, i1, i2), (i0, i1, i3), (i0, i2, i3), (i1, i2, i3)):
        add(*f)
    for p in range(len(pts)):
        if p in (i0, i1, i2, i3):
            continue
        q = pts[p]
        seen = [f for f in faces if float(np.dot(f[3], q)) - f[4] > eps]
        if not seen:
            continue
        edges = {}
        for a, b, c, _, _ in seen:
            for e in ((a, b), (b, c), (c, a)):
                if (e[1], e[0]) in edges:
                    del edges[(e[1], e[0])]
                else:
                    edges[e] = True
        ids = {id(f) for f in seen}
        faces[:] = [f for f in faces if id(f) not in ids]
        for a, b in edges:
            add(a, b, p)
    nrm = np.array([f[3] for f in faces])
    tri = np.array([[f[0], f[1], f[2]] for f in faces])
    area = 0.5 * np.linalg.norm(np.cross(pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]]), axis=1)
    used = np.unique(tri)
    return pts[used], nrm, area


def _directions(nrm, area, limit):
    """Distinct directions among face normals (to about a degree), the ones
    with the most area first."""
    key = np.round(nrm * 60.0).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    tot = np.bincount(inv, weights=area)
    vec = np.stack([np.bincount(inv, weights=area * nrm[:, c]) for c in range(3)], axis=1)
    vec /= np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-30)
    order = np.argsort(-tot)[:limit]
    return vec[order], tot[order]


def _basis(n):
    """Two unit vectors that make (u, v, n) a right-handed frame."""
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(a, n)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def _min_rect(p2):
    """The angle at which a 2-D point set has the smallest bounding rectangle,
    and that area.  (The best angle is that of an edge of the convex hull;
    a fine search over a quarter turn finds it closely enough.)"""
    def areas(angles):
        c, s = np.cos(angles), np.sin(angles)
        x = p2[:, :1] * c + p2[:, 1:] * s
        y = -p2[:, :1] * s + p2[:, 1:] * c
        return (x.max(axis=0) - x.min(axis=0)) * (y.max(axis=0) - y.min(axis=0))
    a = np.linspace(0.0, 0.5 * math.pi, 91)
    best = a[np.argmin(areas(a))]
    a = best + np.linspace(-math.radians(1.0), math.radians(1.0), 81)
    ar = areas(a)
    k = int(np.argmin(ar))
    return float(a[k]), float(ar[k])


def _frame(n, hv):
    """Rows (u, v, n): n, and in its plane the directions of the smallest
    rectangle around the points ``hv``."""
    u, v = _basis(n)
    ang, _ = _min_rect(np.stack([hv @ u, hv @ v], axis=1))
    c, s = math.cos(ang), math.sin(ang)
    return np.array([c * u + s * v, -s * u + c * v, n])


def rotation_min_height(points):
    """A rotation (3, 3) that makes the part as low as possible: a face of its
    hull on the floor, and its footprint turned to the smallest rectangle."""
    hv, nrm, area = _hull(points)
    dirs, _ = _directions(nrm, area, 100000)
    proj = hv @ dirs.T
    width = proj.max(axis=0) - proj.min(axis=0)
    n = dirs[int(np.argmin(width))]
    F = _frame(n, hv)
    F[2] = -n                     # (that face down: its outward normal points to the floor)
    F[1] = np.cross(F[2], F[0])   # keep it a rotation
    return F


def rotation_min_volume(points):
    """A rotation that makes the part's bounding box smallest; of its three
    sides the shortest is put upright (lowest too)."""
    hv, nrm, area = _hull(points)
    dirs, _ = _directions(nrm, area, MAX_NORMALS)
    best = None
    for n in dirs:
        F = _frame(n, hv)
        p = hv @ F.T
        ext = p.max(axis=0) - p.min(axis=0)
        vol = float(ext.prod())
        if best is None or vol < best[0] - 1e-12 * vol:
            best = (vol, F, ext)
    _, F, ext = best
    order = np.argsort(-ext)      # longest along X, shortest along Z
    F = F[order]
    if np.linalg.det(F) < 0.0:
        F[1] = -F[1]
    return F


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
        floor = float(volume_box(context)[0][2])

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
    if not st.snap_to_volume:
        if obj is not None:
            me = obj.data
            bpy.data.objects.remove(obj)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
        return
    lo, hi = volume_box(context or bpy.context)
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
           BUCKETBUILDER_OT_minimize_height, BUCKETBUILDER_OT_tilt_off_axis)

_keymaps = []


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
        kmi = km.keymap_items.new(BUCKETBUILDER_OT_drop_to_floor.bl_idname, 'DOWN_ARROW', 'PRESS',
                                  ctrl=True)
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
