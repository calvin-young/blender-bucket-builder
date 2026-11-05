# SPDX-License-Identifier: GPL-3.0-or-later
"""The chip reel, played live in Blender. See chips/make.sh."""
import importlib
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(ROOT, "lib")]
import capture as C  # noqa: E402
from capture import frames, orbit, smoother  # noqa: E402
import chips as CH  # noqa: E402
import look as L  # noqa: E402

CACHE = os.path.join(ROOT, "cache", "chips")
OUT = os.path.join(CACHE, "frames")
BB = "bl_ext.user_default.bucket_builder"
VOL = (380.0, 284.0, 380.0)
HOME = Vector((-430.0, -40.0, 0.0))     # where the can stands
FLOOR_Z = 8.0                           # of the first chip's middle, above the can's foot + half a chip
S = {}
C.DRAWS = int(os.environ.get("DRAWS", "6"))


def st():
    return bpy.context.scene.bucket_builder


def setup():
    bpy.ops.preferences.addon_enable(module=BB)
    C.single_viewport(ui_scale=1.1)
    return 0.5


def setup_full():
    with C.override():
        bpy.ops.screen.screen_full_area(use_hide_panels=True)
    return 0.5


def setup_scene():
    C.configure_viewport()
    sp = C.space()
    sp.show_region_header = False
    sp.clip_start, sp.clip_end = 5.0, 30000.0
    sc = bpy.context.scene
    C.set_units_mm(sc)
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    L.world()
    L.lights()
    L.table()
    d = S["data"] = np.load(os.path.join(CACHE, "data.npz"))
    body, lid = L.can(os.path.join(CACHE, "label.png"))
    S["can"], S["lid"] = body, lid
    mat = L.chip_material()
    obs = []
    for k, m in enumerate(CH.chip_meshes()):
        me = bpy.data.meshes.new("chip-%03d" % (k + 1))
        me.from_pydata(np.asarray(m.verts).tolist(), [], np.asarray(m.tris).tolist())
        me.update()
        for p in me.polygons:
            p.use_smooth = True
        me.materials.append(mat)
        o = bpy.data.objects.new(me.name, me)
        sc.collection.objects.link(o)
        import colorsys
        o.color = (*colorsys.hsv_to_rgb((k * 0.61803) % 1.0, 0.88, 0.85), 0.0)
        obs.append(o)
    S["chips"] = obs
    S["can_low"] = -float(min(np.asarray(m.verts)[:, 2].min() for m in CH.chip_meshes(rings=6, segs=24)))
    L.viewport(sp, sc)
    s = st()
    s.monitor_volume = False
    s.detect_collisions = False
    s.report_density = False
    s.show_hud = False
    s.volume_align = "CENTER_XY"
    place_can(Matrix.Translation(HOME))
    S["lid"].matrix_world = lid_seat(Matrix.Translation(HOME))
    bpy.context.view_layer.update()
    return 2.0


def place_can(M):
    S["can"].matrix_world = M
    z = S["data"]["can_z"]
    for k, o in enumerate(S["chips"]):
        o.matrix_world = M @ Matrix.Translation((0, 0, FLOOR_Z + S["can_low"] + float(z[k])))


def hold(n):
    for _ in range(n):
        yield


def camera(pose, n):
    p0 = C.Pose.current()
    for i in range(n):
        p0.lerp(pose, smoother((i + 1) / n)).apply()
        yield


POSE_LABEL = orbit(HOME + Vector((0, 0, 275)), 300, 88, 2)
POSE_LABEL2 = orbit(HOME + Vector((0, 0, 265)), 400, 85, -4)
POSE_START = orbit((0, 0, 150), 1100, 66, 20)        # as the packing reel's last shot ends (pack/shots.py)
POSE_CAN = orbit(HOME + Vector((60, 0, 230)), 920, 80, 0)
POSE_TOP = orbit(HOME + Vector((10, 0, 340)), 640, 64, -6)
POSE_WIDE = orbit(Vector((-170, -20, 170)), 1330, 68, 8)
POSE_HAND = orbit(Vector((-40, 0, 80)), 850, 58, 22)
POSE_POUR = orbit(Vector((-240, -420, 190)), 1650, 70, 4)
POSE_PILE = orbit(Vector((-40, -500, 50)), 1000, 56, 10)
POSE_BAD = orbit(Vector((-10, -130, 170)), 1150, 72, 14)
POSE_GOOD = orbit(Vector((-10, -60, 60)), 880, 58, 18)
POSE_CLOSE = orbit(Vector((-30, -60, 70)), 330, 72, 38)
POSE_CLOSE2 = orbit(Vector((90, -60, 70)), 300, 76, 62)
POSE_END = orbit(Vector((0, 0, 70)), 820, 56, 28)
BOX = Vector((-VOL[0] / 2, -VOL[1] / 2, 0))


def warm(seconds):
    """Keep drawing, uncaptured, while the viewport compiles its shaders and settles."""
    import time
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        yield 0.1


def hand_matrix(k):
    return Matrix.Translation(BOX) @ Matrix(S["data"]["hand"][k].tolist())


def in_can(k, base=None):
    return (base or S["can"].matrix_world) @ Matrix.Translation((0, 0, FLOOR_Z + S["can_low"] + float(S["data"]["can_z"][k])))


def layout(name):
    """{chip: world matrix} of a packer's result."""
    from pack import meshio
    out = {}
    for it in meshio.read(os.path.join(CACHE, name + ".3mf")).items:
        k = int(it.name.split("#")[0].split("-")[1]) - 1
        out[k] = Matrix.Translation(BOX) @ Matrix(np.asarray(it.matrix).tolist())
    return out


def frame_box():
    """The printer's build volume as thin glowing bars (the add-on draws its own at the end)."""
    x, y, z = VOL
    c = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
    v, f = [], []
    t = 0.9
    for a, b in ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)):
        p, q = np.array(c[a], float), np.array(c[b], float)
        ax = np.argmax(np.abs(q - p))
        d = [np.eye(3)[i] * t for i in range(3) if i != ax]
        n = len(v)
        for e in (p, q):
            for s1, s2 in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                v.append(tuple(e + s1 * d[0] + s2 * d[1]))
        for i in range(4):
            j = (i + 1) % 4
            f.append((n + i, n + j, n + 4 + j, n + 4 + i))
    me = bpy.data.meshes.new("volume")
    me.from_pydata(v, [], f)
    o = bpy.data.objects.new("volume", me)
    bpy.context.scene.collection.objects.link(o)
    o.location = BOX
    m = bpy.data.materials.new("volume")
    m.use_nodes = True
    t_ = m.node_tree
    t_.nodes.clear()
    out = t_.nodes.new("ShaderNodeOutputMaterial")
    em = t_.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0.25, 0.62, 1.0, 1)
    em.inputs["Strength"].default_value = 4.0
    t_.links.new(em.outputs[0], out.inputs[0])
    me.materials.append(m)
    S["volume"] = o
    return o


def wobble(t, seed, amount=1.0):
    """A hand's unsteadiness at time t (seconds): (offset, rotation), small and slow."""
    def n(k, *freqs):
        return sum(math.sin(2 * math.pi * q * t + 2.1 * (seed + k) + q) for q in freqs) / len(freqs)
    off = Vector((3.2 * n(1, 0.7, 1.4, 3.1), 3.2 * n(2, 0.9, 1.7, 2.7), 2.4 * n(3, 0.8, 2.3))) * amount
    rot = Matrix.Rotation(math.radians(1.1) * amount * n(4, 0.6, 1.9), 4, 'X') @ Matrix.Rotation(math.radians(1.1) * amount * n(5, 0.8, 2.1), 4, 'Y')
    return off, rot


def can_at(pos, amount, t, seed=0, lean=None):
    off, rot = wobble(t, seed, amount)
    M = Matrix.Translation(pos + off) @ rot
    return M @ lean if lean is not None else M


def lid_seat(can_matrix):
    return can_matrix @ Matrix.Translation((0, 0, L.H_CAN))


def match_packing_reel():
    """The same place on screen as the packing reel's last frame, whose window has a title bar and
    header this one doesn't: move the view so that the build volume lands on the same pixels."""
    path = os.path.join(ROOT, "cache", "pack", "last_view.json")
    S["start"] = POSE_START
    if not os.path.exists(path):
        return
    want = json.load(open(path))
    from bpy_extras.view3d_utils import location_3d_to_region_2d as project
    a, r = C.view3d()
    pose = POSE_START
    for _ in range(4):
        pose.apply()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)     # (the view's matrix is only updated by a draw)
        yield 0.1
        rv = C.rv3d()
        c = project(r, rv, Vector(want["point"]))
        d = Vector(want["pixel"]) - Vector((c.x + r.x, c.y + r.y))
        right = rv.view_rotation @ Vector((1, 0, 0))
        up = rv.view_rotation @ Vector((0, 1, 0))
        per_mm = (project(r, rv, pose.target + right * 10.0) - project(r, rv, pose.target)).length / 10.0
        pose = C.Pose(pose.target - (right * d.x + up * d.y) / per_mm, pose.dist, pose.quat)
    C.log("view matched to the packing reel: off by", d.length, "px")
    S["start"] = pose


# ------------------------------------------------------------------ the can comes in, and packing it by hand

def seg_in():
    """Only the empty volume at first, in the same place as at the end of the reel before; then the can
    is carried in from off screen and set down."""
    S["volume"] = frame_box()
    yield from match_packing_reel()
    pose = S["start"]
    pose.apply()
    S["can"].matrix_world = Matrix.Translation(HOME + Vector((-1500, -300, 200)))
    place_can(S["can"].matrix_world)
    S["lid"].matrix_world = lid_seat(S["can"].matrix_world)
    yield from warm(45)
    yield from hold(frames(0.9))
    a, b = pose, POSE_WIDE
    n = frames(2.7)
    start = HOME + Vector((-1500, -300, 200))
    for i in range(n):
        u = (i + 1) / n
        p = smoother(u)
        a.lerp(b, smoother(min(1.0, u * 1.15))).apply()
        pos = start.lerp(HOME, p)
        pos.z = HOME.z + 200 * (1 - smoother(min(1.0, u / 0.92))) + 26 * math.sin(math.pi * min(1.0, u / 0.92))
        M = can_at(pos, 1.0 - smoother(max(0.0, (u - 0.8) / 0.2)), u * n / 30.0)
        lean = Matrix.Rotation(math.radians(9) * (1 - p) * (1 - p), 4, 'Y')
        M = M @ lean
        place_can(M)
        S["lid"].matrix_world = lid_seat(M)
        yield
    place_can(Matrix.Translation(HOME))
    S["lid"].matrix_world = lid_seat(Matrix.Translation(HOME))
    yield from hold(frames(0.5))


def seg_label():
    yield from camera(POSE_LABEL, frames(1.8))
    n = frames(2.2)
    a, b = POSE_LABEL, POSE_LABEL2
    for i in range(n):
        a.lerp(b, (i + 1) / n).apply()
        yield
    yield from camera(POSE_CAN, frames(1.6))


def seg_lid():
    """A hand steadies the can, another works the lid loose and sets it down beside it."""
    lid = S["lid"]
    yield from camera(POSE_TOP, frames(0.9))
    side = HOME + Vector((-185, -150, 11))
    n = frames(2.8)
    for i in range(n):
        t = i / n
        sec = i / 30.0
        # the can moves as the lid is worked, and is steady again once it is off
        strain = smoother(min(1.0, t / 0.12)) * (1 - smoother(max(0.0, (t - 0.42) / 0.2)))
        C_ = can_at(HOME, 0.9 * strain, sec, seed=7)
        place_can(C_)
        if t < 0.38:                                 # rocking it loose, one side, then the other
            u = t / 0.38
            rock = math.radians(5.0) * u * math.sin(2 * math.pi * 2.6 * sec)
            rock2 = math.radians(3.0) * u * math.sin(2 * math.pi * 1.9 * sec + 1.0)
            L_ = lid_seat(C_) @ Matrix.Translation((0, 0, 7.0 * u * u)) @ Matrix.Rotation(rock, 4, 'Y') @ Matrix.Rotation(rock2, 4, 'X')
            S["off"] = L_.translation.copy()
        else:                                        # off, carried across, and put down
            u = smoother((t - 0.38) / 0.62)
            pos = S["off"].lerp(side, u)
            pos.z += 70 * 4 * u * (1 - u)
            tilt = math.radians(16) * 4 * u * (1 - u)
            off, rot = wobble(sec, 3, 0.8 * (1 - u))
            L_ = Matrix.Translation(pos + off) @ rot @ Matrix.Rotation(tilt, 4, 'Y')
        lid.matrix_world = L_
        yield
    place_can(Matrix.Translation(HOME))
    lid.matrix_world = Matrix.Translation(side)
    yield from hold(frames(0.3))
    yield from camera(POSE_WIDE, frames(1.5))


def _group_motion(group, t, rise):
    """Matrices of a group (bottom chip first) at t: 0 in the can, 1 laid down by hand. Up out of the can
    first; then over to the printer, turning on its side, opening up to the print gap as it lands."""
    d = S["data"]
    z = d["can_z"]
    f0 = group[0]
    M0 = in_can(f0)
    H0 = hand_matrix(f0)
    Q = H0.to_quaternion()
    up = smoother(min(1.0, t / 0.36))
    fly = smoother(max(0.0, (t - 0.36) / 0.64))
    start = M0.translation + Vector((0, 0, rise * up))
    end = H0.translation
    mid = start.lerp(end, fly)
    mid.z += 90 * 4 * fly * (1 - fly)
    R = Quaternion().slerp(Q, fly)
    if 0.0 < t < 1.0:                                # (carried by a hand, set down gently)
        off, rot = wobble(t * 3.5, group[0] % 7, 1.0 * math.sin(math.pi * t) ** 0.5)
        mid = mid + off
        R = rot.to_quaternion() @ R
    open_ = smoother(max(0.0, (t - 0.72) / 0.28))
    out = {}
    Rm = R.to_matrix()
    Qm = Q.to_matrix()
    for k in group:
        wc = float(z[k] - z[f0])
        Hk = hand_matrix(k)
        Hl = Qm.inverted() @ (Hk.translation - H0.translation)
        w = wc + (Hl.z - wc) * open_
        off = Vector((Hl.x * open_, Hl.y * open_, w))
        X = (Rm @ off) + mid
        M = Rm.to_4x4()
        M.translation = X
        out[k] = M
    return out


def _groups():
    d = S["data"]
    order = [int(i) for i in d["order"]]
    so = d["stack_of"]
    a = [k for k in order if so[k] == so[order[0]]]
    b = [k for k in order if so[k] != so[order[0]]]
    return a, b


def seg_hand():
    low, high = _groups()
    z = S["data"]["can_z"]
    # the top half first
    for group, secs in ((high, 3.4), (low, 3.8)):
        rise = L.H_CAN - (FLOOR_Z + S["can_low"] + float(z[group[0]])) + 40.0
        n = frames(secs)
        for i in range(n):
            for k, M in _group_motion(group, (i + 1) / n, rise).items():
                S["chips"][k].matrix_world = M
            yield
        yield from hold(frames(0.2))
    yield from camera(POSE_HAND, frames(2.0))
    yield from hold(frames(1.4))


def seg_back():
    """Back into the can, quickly, to start again."""
    low, high = _groups()
    z = S["data"]["can_z"]
    yield from warm(0.1)
    pose0, n = C.Pose.current(), frames(1.5)
    r = {id(low): L.H_CAN - (FLOOR_Z + S["can_low"] + float(z[low[0]])) + 40.0,
         id(high): L.H_CAN - (FLOOR_Z + S["can_low"] + float(z[high[0]])) + 40.0}
    for i in range(n):
        t = smoother((i + 1) / n)
        pose0.lerp(POSE_WIDE, t).apply()
        for group in (high, low):
            for k, M in _group_motion(group, 1 - t, r[id(group)]).items():
                S["chips"][k].matrix_world = M
        yield
    for k, o in enumerate(S["chips"]):
        o.matrix_world = in_can(k)


# ------------------------------------------------------------------ dumped out, every one its own color

def seg_dump():
    d = S["pour"] = np.load(os.path.join(CACHE, "pour.npz"))
    Fr, Ca, left = d["chips"], d["can"], d["left"]
    chips = S["chips"]
    can = S["can"]
    S["volume"].hide_viewport = True
    p0 = C.Pose.current()
    T = len(Fr)
    for fr in range(T):
        p0.lerp(POSE_POUR, smoother(min(1.0, (fr + 1) / 40))).lerp(POSE_PILE, smoother(max(0.0, (fr - T * 0.55) / (T * 0.45)))).apply()
        for k, o in enumerate(chips):
            o.matrix_world = Matrix(Fr[fr, k].tolist())
            a = min(1.0, max(0.0, (fr - left[k] + 4) / 10.0))
            o.color = (*o.color[:3], a)
        # (it goes back further than it came from, out of the shots to come)
        away = smoother(max(0.0, (fr - T + 50) / 50.0))
        can.matrix_world = Matrix.Translation(Vector((-900 * away, 1100 * away, 0))) @ Matrix(Ca[fr].tolist())
        yield
    S["volume"].hide_viewport = False
    yield from hold(frames(1.0))


def _tween(moves, n, each, lift):
    """Moves [(object, from, to)], one after another in the given order."""
    begin = {m[0]: int((n - each) * i / max(1, len(moves) - 1)) for i, m in enumerate(moves)}
    for i in range(n):
        for o, A, B in moves:
            t = smoother(min(1.0, max(0.0, (i - begin[o]) / each)))
            loc = A.translation.lerp(B.translation, t)
            loc.z += lift * 4 * t * (1 - t)
            o.matrix_world = Matrix.Translation(loc) @ A.to_quaternion().slerp(B.to_quaternion(), t).to_matrix().to_4x4()
        yield


def seg_bad():
    chips = S["chips"]
    S["pile"] = {k: o.matrix_world.copy() for k, o in enumerate(chips)}
    bad = S["bad"] = layout("bad")
    moves = sorted(((chips[k], S["pile"][k], bad[k]) for k in bad), key=lambda m: m[2].translation.z)
    p0 = C.Pose.current()
    n = frames(4.0)
    tw = _tween(moves, n, frames(0.7), 120.0)
    for i, _ in enumerate(tw):
        p0.lerp(POSE_BAD, smoother((i + 1) / n)).apply()
        yield
    yield from hold(frames(0.5))
    p0 = C.Pose.current()
    yield from camera(orbit(POSE_BAD.target, POSE_BAD.dist, 74, 30), frames(3.0))


def seg_undo():
    chips = S["chips"]
    moves = [(chips[k], S["bad"][k], S["pile"][k]) for k in S["bad"]]
    p0 = C.Pose.current()
    n = frames(1.5)
    for i, _ in enumerate(_tween(moves, n, n - 1, 30.0)):
        p0.lerp(POSE_PILE, smoother((i + 1) / n)).apply()
        yield


def seg_good():
    chips = S["chips"]
    good = S["good"] = layout("good")
    M = np.array([np.array(good[k].translation) for k in range(len(chips))])
    # stack by stack, from the far end of each
    ax = int(np.argmax(np.ptp(M, axis=0)))
    other = [a for a in (0, 1) if a != ax][0]
    side = M[:, other] > np.median(M[:, other])
    keys = sorted(good, key=lambda k: (bool(side[k]), M[k, ax]))
    moves = [(chips[k], S["pile"][k], good[k]) for k in keys]
    p0 = C.Pose.current()
    n = frames(5.5)
    for i, _ in enumerate(_tween(moves, n, frames(0.6), 150.0)):
        p0.lerp(POSE_GOOD, smoother((i + 1) / n)).apply()
        yield
    yield from hold(frames(0.8))


def seg_unique():
    """Close, to see that they are all different."""
    S["volume"].hide_viewport = True
    yield from camera(POSE_CLOSE, frames(1.8))
    n = frames(3.4)
    a = C.Pose.current()
    for i in range(n):
        a.lerp(POSE_CLOSE2, (i + 1) / n).apply()
        yield
    yield from hold(frames(0.8))


def seg_ok():
    """The add-on's own check of the result."""
    S["volume"].hide_viewport = True
    for name in ("can", "can foil", "can base", "can rim", "can lid", "table"):
        o = bpy.data.objects.get(name)
        if o is not None:
            o.bucket_builder_ignore = True
    s = st()
    s.use_clearance = False                     # (collisions and the volume; the exact gap was checked at packing)
    s.monitor_volume = True
    s.detect_collisions = True
    p0 = C.Pose.current()
    yield from warm(12.0)
    s.show_hud = True
    yield from camera(POSE_END, frames(1.8))
    yield from hold(frames(3.2))


def seg_wedges():
    """On to another kind of nesting, in one continuous shot: the chips go, 300 wedges come in."""
    from pack import meshio
    s = st()
    s.show_hud = False
    s.detect_collisions = False
    s.monitor_volume = False
    S["volume"].hide_viewport = False
    chips = S["chips"]
    items = meshio.read(os.path.join(ROOT, "cache", "pack", "wedges.3mf")).items
    m = items[0].mesh
    me = bpy.data.meshes.new("wedge")
    me.from_pydata(m.verts.tolist(), [], m.tris.tolist())
    me.update()
    me.materials.append(bpy.data.materials["chip"])
    wedges, colors = [], {}
    palette = [(0.90, 0.50, 0.12, 1.0), (0.12, 0.55, 0.78, 1.0)]
    for k, it in enumerate(items):
        o = bpy.data.objects.new("wedge-%03d" % (k + 1), me)
        B = Matrix.Translation(BOX) @ Matrix(np.asarray(it.matrix).tolist())
        key = tuple(int(round(v)) for row in B.to_3x3() for v in row)   # each orientation its own color
        o.color = colors.setdefault(key, palette[len(colors) % len(palette)])
        bpy.context.scene.collection.objects.link(o)
        A = Matrix.Translation((-760 + 80 * (k % 20), 500 + 80 * (k // 20), 1500.0))
        o.matrix_world = A
        o.hide_viewport = True                  # (until it comes in: up there it would cast a shadow)
        wedges.append((o, A, B))
    # the chips lift out, stack by stack, as the wedges start to come in
    leave = sorted(range(len(chips)), key=lambda k: -chips[k].matrix_world.translation.x)
    A_ch = {k: chips[k].matrix_world.copy() for k in leave}
    B_ch = {k: Matrix.Translation(A_ch[k].translation + Vector((-200, 900, 1300))) @ A_ch[k].to_3x3().to_4x4() for k in leave}
    order = sorted(wedges, key=lambda c: c[2].translation.z)
    n, each = frames(4.2), frames(0.9)
    begin = {c[0]: int(frames(0.9) + (n - frames(0.9) - each) * j / len(order)) for j, c in enumerate(order)}
    go = {k: int(frames(1.1) * j / len(leave)) for j, k in enumerate(leave)}
    p0 = C.Pose.current()
    p1 = orbit((0, 0, 110), 860, 66, 26)
    for i in range(n):
        p0.lerp(p1, smoother((i + 1) / n)).apply()
        for k in leave:
            t = smoother(min(1.0, max(0.0, (i - go[k]) / frames(0.9))))
            chips[k].matrix_world = Matrix.Translation(A_ch[k].translation.lerp(B_ch[k].translation, t)) @ A_ch[k].to_3x3().to_4x4()
        for o, A, B in wedges:
            if i >= begin[o]:
                o.hide_viewport = False
            t = smoother(min(1.0, max(0.0, (i - begin[o]) / each)))
            loc = A.translation.lerp(B.translation, t)
            o.matrix_world = Matrix.Translation(loc) @ A.to_quaternion().slerp(B.to_quaternion(), t).to_matrix().to_4x4()
        yield
    for o, _A, B in wedges:
        o.hide_viewport = False
        o.matrix_world = B
    for o in chips:
        o.hide_viewport = True
    S["volume"].hide_viewport = True
    s.monitor_volume = True
    s.detect_collisions = True
    yield from warm(6.0)
    s.show_hud = True
    p0 = C.Pose.current()
    yield from camera(orbit(p0.target, p0.dist * 0.85, 70, 62), frames(2.6))
    yield from hold(frames(0.4))


def seg_hero():
    """A still for the title card: not in the timeline."""
    st().show_hud = False
    st().monitor_volume = False
    st().detect_collisions = False
    back = C.Pose.current()
    orbit(Vector((-70, -60, 62)), 300, 74, 42).apply()
    yield from warm(3.0)
    yield
    back.apply()
    st().monitor_volume = True
    st().detect_collisions = True
    yield from warm(1.0)


SEGMENTS = [
    ("t_in", seg_in),
    ("a_label", seg_label),
    ("b_lid", seg_lid),
    ("c_hand", seg_hand),
    ("d_back", seg_back),
    ("e_dump", seg_dump),
    ("f_bad", seg_bad),
    ("g_undo", seg_undo),
    ("h_good", seg_good),
    ("i_unique", seg_unique),
    ("j_ok", seg_ok),
    ("z_hero", seg_hero),
    ("k_wedges", seg_wedges),
]

if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "..", "bucket_builder"))
    C.Reel(OUT, SEGMENTS, setup=[setup, setup_full, setup_scene]).start(set(C.args()) or None)
