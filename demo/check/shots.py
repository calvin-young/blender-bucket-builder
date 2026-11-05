# SPDX-License-Identifier: GPL-3.0-or-later
"""Bucket Builder reel 1: the live build check (volume, collisions, clearance,
wall gap, the problem list, the Build Report), played live in Blender.

    demo/check/make.sh capture [segment ...]
"""
import importlib
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(ROOT, "lib")]
import capture as C  # noqa: E402
from capture import smooth, smoother, ease_out, orbit  # noqa: E402
import parts as P  # noqa: E402

OUT = os.path.join(ROOT, "cache", "check", "frames")
BB = "bl_ext.user_default.bucket_builder"
S = {}
GREY = (0.78, 0.79, 0.81)
STRETCH = 1.4                      # every move and pause, so the reel runs about 1:30


def frames(seconds):
    return C.frames(seconds * STRETCH)


# the build: (name, maker, rotation in degrees, centre x, y on the floor of the 380 x 284 volume)
BUILD = [
    ("gear-01", lambda: P.spur_gear(48, 2.0, 10, 8, hub=(16, 10)), (0, 0, 0), -120, -82),
    ("housing-01", P.bearing_housing, (0, 0, 0), -25, -88),
    ("housing-02", P.bearing_housing, (0, 0, 0), 58, -88),
    ("pulley-01", P.pulley, (0, 0, 0), 135, -88),
    ("gear-02", lambda: P.spur_gear(32, 1.5, 8, 5, hub=(10, 6)), (0, 0, 0), -150, 2),
    ("gear-03", lambda: P.spur_gear(32, 1.5, 8, 5, hub=(10, 6)), (0, 0, 0), -80, 2),
    ("sprocket-01", P.sprocket, (0, 0, 0), -15, 2),
    ("gear-06", lambda: P.spur_gear(24, 2.0, 8, 5), (0, 0, 0), 45, 2),
    ("knob-01", P.knob, (0, 0, 0), 100, 2),
    ("knob-02", P.knob, (0, 0, 0), 148, 2),
    ("bolt-01", lambda: P.hex_bolt(10, 70, 16, 6.5, 1.5), (0, 90, 0), -138, 62),
    ("bolt-02", lambda: P.hex_bolt(10, 70, 16, 6.5, 1.5), (0, 90, 0), -138, 94),
    ("bolt-03", lambda: P.hex_bolt(8, 45, 13, 5.5, 1.25), (0, 90, 90), -76, 80),
    ("bolt-04", lambda: P.hex_bolt(8, 45, 13, 5.5, 1.25), (0, 90, 90), -50, 80),
    ("pulley-02", P.pulley, (0, 0, 0), 8, 80),
    ("gear-04", lambda: P.spur_gear(16, 1.5, 7, 4), (0, 0, 0), 62, 62),
    ("gear-05", lambda: P.spur_gear(16, 1.5, 7, 4), (0, 0, 0), 62, 100),
    ("nut-01", P.hex_nut, (0, 0, 0), 104, 58),
    ("nut-02", P.hex_nut, (0, 0, 0), 104, 82),
    ("nut-03", P.hex_nut, (0, 0, 0), 104, 106),
    ("standoff-01", P.standoff, (0, 90, 0), 145, 60),
    ("standoff-02", P.standoff, (0, 90, 0), 145, 84),
    ("washer-01", P.washer, (0, 0, 0), 140, 110),
    ("washer-02", P.washer, (0, 0, 0), 164, 110),
]


def st():
    return bpy.context.scene.bucket_builder


def link(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(V.tolist(), [], F.tolist())
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def world_box(ob):
    pts = np.array([tuple(ob.matrix_world @ Vector(c)) for c in ob.bound_box])
    return pts.min(0), pts.max(0)


def put(ob, x, y):
    """Centre the part's footprint on (x, y), standing on z = 0."""
    bpy.context.view_layer.update()
    lo, hi = world_box(ob)
    ob.location += Vector((x - 0.5 * (lo[0] + hi[0]), y - 0.5 * (lo[1] + hi[1]), -lo[2]))
    bpy.context.view_layer.update()


# ------------------------------------------------------------------ setup

def setup():
    bpy.ops.preferences.addon_enable(module=BB)
    C.single_viewport(ui_scale=1.1)
    return 0.5


def setup_scene():
    C.configure_viewport()
    C.only_panels(importlib.import_module(BB + ".ui"),
                  ["BUCKETBUILDER_PT_main", "BUCKETBUILDER_PT_problems", "BUCKETBUILDER_PT_collisions",
                   "BUCKETBUILDER_PT_volume", "BUCKETBUILDER_PT_report"], "BUCKETBUILDER_PT")
    C.sidebar_first("Bucket")
    sc = bpy.context.scene
    C.set_units_mm(sc)
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    sp = C.space()
    sp.shading.color_type = 'SINGLE'
    sp.shading.single_color = GREY
    sp.overlay.grid_scale = 10.0
    objs = []
    for name, make, rot, x, y in BUILD:
        V, F = make()
        ob = link(name, V, F)
        ob.rotation_euler = [math.radians(a) for a in rot]
        put(ob, x, y)
        objs.append(ob)
    for ob in objs:
        ob.select_set(False)
    s = st()
    s.detect_collisions = False
    s.monitor_volume = False
    s.volume_align = 'CENTER_XY'
    s.use_clearance = False
    s.clearance_mm = 3.0
    s.use_wall_clearance = False
    s.wall_clearance_mm = 5.0
    s.report_density = False
    sp.show_region_ui = True
    POSE_A.apply()
    return 1.0


POSE_A = orbit((10, 0, 0), 560, 54, 22)           # the whole build, sidebar on the right
POSE_OUT = orbit((120, -60, 10), 330, 58, 30)     # the pulley going through the +X wall
POSE_COL = orbit((-140, 22, 5), 215, 50, 8)       # bolt into gear
POSE_WAL = orbit((160, 0, 5), 170, 50, 40)        # knob close to the wall
POSE_REP = orbit((10, 0, 0), 600, 56, 22)


def ob(name):
    return bpy.data.objects[name]


def mon():
    return importlib.import_module(BB + ".monitor").get(bpy.context.scene)


def problems():
    m = mon()
    return m.problems() if m is not None else []


def slide(o, to, n, ease=smoother, pose=None, each=None):
    """Move a part to `to` over n frames (camera from where it is to `pose`)."""
    a = o.location.copy()
    b = Vector(to)
    p0 = C.Pose.current()
    for i in range(n):
        t = (i + 1) / n
        o.location = a.lerp(b, ease(t))
        if pose is not None:
            p0.lerp(pose, smoother(t)).apply()
        yield


def camera(pose, n):
    p0 = C.Pose.current()
    for i in range(n):
        p0.lerp(pose, smoother((i + 1) / n)).apply()
        yield


def hold(n):
    for _ in range(n):
        yield


def settle():
    """Let the monitor finish (it checks in timers between redraws)."""
    yield 0.4


def seg_intro():
    POSE_A.apply()
    yield


def seg_volume():
    yield from hold(frames(0.7))
    st().monitor_volume = True
    yield from settle()
    yield from camera(orbit((10, 0, 0), 540, 55, 26), frames(3.3))


def seg_outside():
    p = ob("pulley-01")
    home = p.location.copy()
    S["pulley_home"] = home
    yield from slide(p, home + Vector((78, 0, 0)), frames(2.4), pose=POSE_OUT)
    yield from hold(frames(1.6))
    yield from slide(p, home, frames(1.5))
    yield from hold(frames(0.9))


def seg_collisions_on():
    yield from camera(POSE_A, frames(1.0))
    st().detect_collisions = True
    yield from settle()
    yield from hold(frames(2.0))


def seg_collision():
    b = ob("bolt-01")
    S["bolt_home"] = b.location.copy()
    to = b.location + Vector((-6, -44, 0))
    yield from camera(POSE_COL, frames(1.3))
    yield from slide(b, to, frames(3.2), ease=smooth)
    yield from hold(frames(2.0))


def seg_hero():
    """A close-up of the collision for the title card: not in the timeline."""
    sp = C.space()
    keep = C.Pose.current(), sp.show_region_ui, st().show_hud, sp.overlay.show_text, sp.overlay.show_floor
    sp.show_region_ui = False
    st().show_hud = False
    sp.overlay.show_text = False
    sp.overlay.show_floor = False
    sp.overlay.show_axis_x = sp.overlay.show_axis_y = False
    orbit((-183, 30, 10), 118, 52, 18).apply()
    yield 0.5
    yield
    pose, sp.show_region_ui, st().show_hud, sp.overlay.show_text, sp.overlay.show_floor = keep
    sp.overlay.show_axis_x = sp.overlay.show_axis_y = True
    pose.apply()


def seg_clearance():
    st().use_clearance = True
    n = ob("nut-01")
    S["nut_home"] = n.location.copy()
    k = ob("knob-01")
    # stop the nut 1.8 mm short of the knob's bounding box (the clearance is 3 mm), on its
    # centre line so that's the real gap
    lo_k, hi_k = world_box(k)
    lo_n, hi_n = world_box(n)
    dx = 0.5 * (lo_k[0] + hi_k[0] - lo_n[0] - hi_n[0])
    dy = (hi_k[1] + 1.8) - lo_n[1]
    # nearly straight down: the nut comes in from behind the taller knob, which hid the gap from lower
    gap_y = hi_k[1] + 0.9
    yield from camera(orbit((lo_k[0] + 15.0, gap_y + 2.0, 6.0), 72, 16, 8), frames(1.3))
    yield from slide(n, n.location + Vector((dx, dy, 0)), frames(2.6), ease=ease_out)
    yield from hold(frames(2.0))


def seg_wallgap():
    st().use_wall_clearance = True
    k = ob("knob-02")
    S["knob_home"] = k.location.copy()
    yield from camera(POSE_WAL, frames(1.2))
    lo, hi = world_box(k)
    dx = (190.0 - 2.5) - hi[0]
    yield from slide(k, k.location + Vector((dx, 0, 0)), frames(2.2), ease=ease_out)
    yield from hold(frames(1.6))


def _goto_problem(direction=1):
    bpy.context.preferences.view.smooth_view = 0
    before = C.Pose.current()
    with C.override():
        bpy.ops.bucketbuilder.step_problem(direction=direction)
    after = C.Pose.current()
    before.apply()
    # a little further out than Blender frames it, and from our angle
    return orbit(after.target, max(after.dist * 1.6, 160), 34, 20)


def _row(pr):
    """The arguments a row button of the problem list passes."""
    a, b = pr["names"]
    return {"kind": pr["key"][0], "a": a or "", "b": b or ""}


def seg_problems():
    yield from camera(POSE_A, frames(1.2))
    yield from hold(frames(0.6))
    for _ in range(3):
        pose = _goto_problem(1)
        yield from camera(pose, frames(1.1))
        yield from hold(frames(1.3))
    # the magnifier on the collision: its parts on their own
    col = next((p for p in problems() if p["kind"] == "COLLIDE"), None)
    if col is not None:
        with C.override():
            bpy.ops.bucketbuilder.isolate_problem(**_row(col))
        yield from camera(orbit(POSE_COL.target, 190, 52, 20), frames(0.8))
        yield from hold(frames(1.6))
        with C.override():
            bpy.ops.bucketbuilder.isolate_problem(**_row(col))
        yield from hold(frames(0.4))


def seg_ignore():
    yield from camera(POSE_WAL, frames(1.0))
    wall = next((p for p in problems() if p["kind"] == "WALL"), None)
    yield from hold(frames(0.6))
    if wall is not None:
        with C.override():
            bpy.ops.bucketbuilder.ignore_problem(**_row(wall))
        S["ignored"] = wall
    yield from settle()
    yield from hold(frames(2.4))


def seg_fix():
    for o in bpy.context.selected_objects:
        o.select_set(False)
    yield from camera(POSE_A, frames(1.0))
    b, n, k = ob("bolt-01"), ob("nut-01"), ob("knob-02")
    a = [o.location.copy() for o in (b, n, k)]
    to = [S["bolt_home"], S["nut_home"], S["knob_home"]]
    m = frames(1.6)
    for i in range(m):
        t = smoother((i + 1) / m)
        for o, p, q in zip((b, n, k), a, to):
            o.location = p.lerp(q, t)
        yield
    with C.override():
        bpy.ops.bucketbuilder.count_all_problems()
    yield from settle()
    yield from hold(frames(2.2))


def seg_report():
    C.only_panels(importlib.import_module(BB + ".ui"),
                  ["BUCKETBUILDER_PT_main", "BUCKETBUILDER_PT_report"], "BUCKETBUILDER_PT")
    st().report_density = True
    yield from settle()
    yield 1.0
    yield from camera(POSE_REP, frames(4.5))


SEGMENTS = [
    ("a_intro", seg_intro),
    ("b_volume", seg_volume),
    ("c_outside", seg_outside),
    ("d_collisions_on", seg_collisions_on),
    ("e_collision", seg_collision),
    ("z_hero", seg_hero),
    ("f_clearance", seg_clearance),
    ("g_wallgap", seg_wallgap),
    ("h_problems", seg_problems),
    ("i_ignore", seg_ignore),
    ("j_fix", seg_fix),
    ("k_report", seg_report),
]

if __name__ == "__main__":
    C.Reel(OUT, SEGMENTS, setup=[setup, setup_scene]).start(set(C.args()) or None)
