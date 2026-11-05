# SPDX-License-Identifier: GPL-3.0-or-later
"""Bucket Builder reel 2: the packer, played live in Blender.

    demo/pack/make.sh pack        # the density-limit comparison layouts (CLI, ~2 min)
    demo/pack/make.sh capture [segment ...]
"""
import importlib
import os
import re
import sys
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(ROOT, "lib")]
import capture as C  # noqa: E402
from capture import frames, smoother, orbit  # noqa: E402
import parts as P  # noqa: E402
sys.path.insert(0, HERE)
import inputs  # noqa: E402

CACHE = os.path.join(ROOT, "cache", "pack")
OUT = os.path.join(CACHE, "frames")
BB = "bl_ext.user_default.bucket_builder"
SCALE = 0.6                          # of parts.MIX: 147 parts (as cache/pack/unpacked.3mf)
VOL = (380.0, 284.0, 380.0)
S = {}


def st():
    return bpy.context.scene.bucket_builder


def mon():
    return importlib.import_module(BB + ".monitor").get(bpy.context.scene)


def hold(n):
    for _ in range(n):
        yield


def camera(pose, n):
    p0 = C.Pose.current()
    for i in range(n):
        p0.lerp(pose, smoother((i + 1) / n)).apply()
        yield


def read_layout(path):
    """{kind: [4x4 world matrices]} of a packed 3MF from the CLI (volume corner at 0)."""
    sys.path.insert(0, os.path.join(ROOT, "..", "bucket_builder"))
    from pack import meshio
    kinds = [k for k, _mk, _n, _c in P.MIX]
    shift = Matrix.Translation((-0.5 * VOL[0], -0.5 * VOL[1], 0.0))
    out = {}
    for it in meshio.read(path).items:
        kind = kinds[int(it.name.split("#")[0].split("-")[1]) - 1]
        out.setdefault(kind, []).append(shift @ Matrix(np.asarray(it.matrix).tolist()))
    return out


# ------------------------------------------------------------------ setup

def setup():
    bpy.ops.preferences.addon_enable(module=BB)
    C.single_viewport(ui_scale=1.1)
    return 0.5


def setup_scene():
    C.configure_viewport()
    ui = importlib.import_module(BB + ".ui")
    ui.BUCKETBUILDER_PT_pack.bl_label = "Pack (In Development)"     # staging: the add-on is unchanged
    C.only_panels(ui, ["BUCKETBUILDER_PT_main", "BUCKETBUILDER_PT_pack"], "BUCKETBUILDER_PT")
    C.sidebar_first("Bucket")
    sc = bpy.context.scene
    C.set_units_mm(sc)
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    sp = C.space()
    sp.shading.color_type = 'OBJECT'
    sp.overlay.grid_scale = 10.0
    # every part, in rows in front of the volume (the -Y side), not packed
    S["kind"] = {}
    x, y, row = -420.0, -175.0, 0.0
    for kind, make, n, col in P.MIX:
        V, F = make()
        me = bpy.data.meshes.new(kind)
        me.from_pydata(V.tolist(), [], F.tolist())
        me.update()
        lo, hi = V.min(0), V.max(0)
        for c in range(int(round(n * SCALE))):
            if x + hi[0] - lo[0] > 420.0:
                x, y, row = -420.0, y - row - 12.0, 0.0
            o = bpy.data.objects.new(f"{kind}-{c + 1:02d}", me)
            o.location = (x - lo[0], y - hi[1], -lo[2])
            j = (hash(o.name) % 1000) / 1000.0 - 0.5
            o.color = (*[min(1.0, v * (1 + 0.16 * j)) for v in col], 1.0)
            sc.collection.objects.link(o)
            S["kind"].setdefault(kind, []).append(o)
            x += hi[0] - lo[0] + 12.0
            row = max(row, hi[1] - lo[1])
    s = st()
    s.volume_align = 'CENTER_XY'
    s.monitor_volume = True
    s.detect_collisions = False
    s.report_density = False
    s.pack_mode = 'FREE'
    s.pack_gap = 3.0
    s.pack_wall_gap = 5.0
    s.pack_quality = 50
    s.pack_effort = 'QUICK'
    s.pack_preset = 'OVERNIGHT'
    s.use_clearance = False                    # (collisions and the volume only: the packer keeps its own gap)
    sp.show_region_ui = True
    POSE_PILE.apply()
    bpy.context.view_layer.update()
    _remember_start()
    return 1.5


POSE_PILE = orbit((40, -180, 40), 1150, 58, 12)
POSE_PACK = orbit((40, -90, 60), 1000, 60, 18)
POSE_DONE = orbit((20, 0, 40), 720, 58, 28)
POSE_REP = orbit((20, 0, 40), 760, 60, 34)


# ------------------------------------------------------------------ segments

def seg_pile():
    POSE_PILE.apply()
    yield


def seg_settings():
    s = st()
    yield from hold(frames(0.6))
    for q, mode in ((50, 'Z'), (50, 'FREE')):
        s.pack_mode = mode
        yield from hold(frames(0.8))
    yield from camera(POSE_PACK, frames(1.6))


def seg_packing():
    """The real packer runs (its progress shows top right); then the fill is replayed:
    every part flies from the pile to the place the packer gave it, bottom layer first."""
    with C.override():
        r = bpy.ops.bucketbuilder.pack('INVOKE_DEFAULT', selected_only=False)
    C.log("pack", r)
    job = importlib.import_module(BB + ".packing").JOB
    t0 = time.perf_counter()
    yield 0.3
    # film it only until the parts start to move: what follows is the replay of the final layout
    objs = [o for objs in S["kind"].values() for o in objs]
    filming = True
    while job['running'] and time.perf_counter() - t0 < 600:
        if filming and any((o.matrix_world.translation - START[o].translation).length > 0.01 for o in objs):
            filming = False
        if filming:
            yield
        yield 0.1
    S["pack_seconds"] = time.perf_counter() - t0
    S["pack_msg"] = st().pack_last
    C.log("packed:", S["pack_msg"], f"{S['pack_seconds']:.0f} s")
    _write_facts()
    S["packing_frames"] = 0


def seg_fill():
    objs = [o for objs in S["kind"].values() for o in objs]
    final = {o: o.matrix_world.copy() for o in objs}
    for o in objs:
        o.matrix_world = START[o]
    order = sorted(objs, key=lambda o: final[o].translation.z)
    n = frames(4.5)
    each = frames(0.9)
    begin = {o: int((n - each) * k / len(order)) for k, o in enumerate(order)}
    p0 = C.Pose.current()
    for i in range(n):
        p0.lerp(POSE_DONE, smoother((i + 1) / n)).apply()
        for o in objs:
            t = smoother(min(1.0, max(0.0, (i - begin[o]) / each)))
            A, B = START[o], final[o]
            loc = A.translation.lerp(B.translation, t)
            loc.z += 140.0 * 4 * t * (1 - t)
            q = A.to_quaternion().slerp(B.to_quaternion(), t)
            o.matrix_world = Matrix.Translation(loc) @ q.to_matrix().to_4x4()
        yield
    for o in objs:
        o.matrix_world = final[o]
    yield


START = {}


def _remember_start():
    for objs in S["kind"].values():
        for o in objs:
            START[o] = o.matrix_world.copy()


def _write_facts():
    import json
    m = re.search(r"Packed (\d+) of (\d+) parts in ([\d.]+) s: height ([\d.]+) mm, density ([\d.]+)",
                  S.get("pack_msg", ""))
    facts = dict(zip(("placed", "total", "seconds", "height", "density"), m.groups())) if m else {}
    with open(os.path.join(CACHE, "facts.json"), "w") as f:
        json.dump(facts, f)


def seg_result():
    s = st()
    s.detect_collisions = True
    yield 0.5
    p0 = C.Pose.current()
    n = frames(3.4)
    for i in range(n):
        t = (i + 1) / n
        orbit(p0.target, p0.dist, 58, 28 + 40 * smoother(t)).apply()
        yield


def seg_report():
    C.only_panels(importlib.import_module(BB + ".ui"),
                  ["BUCKETBUILDER_PT_main", "BUCKETBUILDER_PT_report"], "BUCKETBUILDER_PT")
    st().report_density = True
    yield 1.0
    p0 = C.Pose.current()
    yield from camera(orbit(p0.target, 760, 62, 80), frames(5.0))


def _tween_to(layout, n, lift=60.0):
    """Every part from where it is to its place in `layout`, with a little hop."""
    moves = []
    for kind, objs in S["kind"].items():
        for o, M in zip(objs, layout.get(kind, [])):
            moves.append((o, o.matrix_world.copy(), M))
    for i in range(n):
        t = smoother((i + 1) / n)
        for o, A, B in moves:
            loc = A.translation.lerp(B.translation, t)
            loc.z += lift * 4 * t * (1 - t)
            q = A.to_quaternion().slerp(B.to_quaternion(), t)
            o.matrix_world = Matrix.Translation(loc) @ q.to_matrix().to_4x4()
        yield


def seg_density():
    """The density limit: no limit packs a low, dense brick; the 12 % default builds
    higher so the heat in each layer stays even."""
    s = st()
    s.detect_collisions = False                     # (off while everything flies; on again below)
    dense = read_layout(os.path.join(CACHE, "d0.3mf"))
    d12 = read_layout(os.path.join(CACHE, "d12.3mf"))
    yield from camera(orbit((20, 0, 50), 780, 76, 12), frames(1.0))
    s.pack_max_density = 0.0
    yield from _tween_to(dense, frames(1.6))
    yield 1.0
    yield from hold(frames(3.0))
    s.pack_max_density = 12.0
    yield from _tween_to(d12, frames(1.8))
    yield 1.0
    yield from hold(frames(3.4))


def settled(limit=30.0):
    """Wait for the monitor to read everything and report a clean build (it tints all it hasn't
    cleared), without capturing."""
    t0 = time.perf_counter()
    yield 0.6
    while time.perf_counter() - t0 < limit:
        m = mon()
        if m is not None and not m.problems() and not m.status().get("busy"):
            yield 0.6
            return
        yield 0.25
    C.log("not settled after", limit)


def _everything(hide):
    for objs in S["kind"].values():
        for o in objs:
            o.hide_set(hide)


def _cells_mesh(name, grid, h, origin, color):
    """A voxel grid as a mesh: only the faces between a full cell and an empty one."""
    g = np.pad(grid.astype(bool), 1)
    V, F, index = [], [], {}

    def v(p):
        return index.setdefault(p, (V.append(p), len(V) - 1)[1])

    dirs = [((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
            ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
            ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
            ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
            ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
            ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)))]
    for i, j, k in np.argwhere(g):
        for d, q in dirs:
            if not g[i + d[0], j + d[1], k + d[2]]:
                F.append([v((i + a, j + b, k + c)) for a, b, c in q])
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple((np.array(p) - 1) * h + np.array(origin)) for p in V], [], F)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.color = (*color, 1.0)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def seg_voxels():
    """How it searches, 1: a part as voxels, with its bore left empty, then grown by the gap."""
    sys.path.insert(0, os.path.join(ROOT, "..", "bucket_builder"))
    from pack import voxel
    st().monitor_volume = False
    st().detect_collisions = False
    st().report_density = False
    sp = C.space()
    sp.show_region_ui = False
    _everything(True)
    V, F = P.bearing_housing()
    V = V - 0.5 * (V.min(0) + V.max(0))
    h = 2.0
    pg = voxel.voxelize(V, F.astype(np.int32), np.eye(3), h, 3.0)
    me = bpy.data.meshes.new("demo-housing")
    me.from_pydata(V.tolist(), [], F.tolist())
    me.update()
    solid_mesh = bpy.data.objects.new("demo-housing", me)
    solid_mesh.color = (0.88, 0.50, 0.30, 1.0)
    bpy.context.scene.collection.objects.link(solid_mesh)
    org = np.asarray(pg.origin, float)
    solid = _cells_mesh("demo-solid", pg.solid, h, org, (0.88, 0.50, 0.30))
    halo = _cells_mesh("demo-halo", pg.kernel, h, org, (0.95, 0.72, 0.25))
    solid.hide_set(True)
    halo.hide_set(True)
    C.log("voxels", pg.solid.shape, pg.kernel.shape, "origin", pg.origin)
    PV = orbit((0, 0, 0), 210, 58, 28)
    PV.apply()
    S["demo"] = (solid_mesh, solid, halo)
    yield from hold(frames(1.0))
    solid_mesh.hide_set(True)
    solid.hide_set(False)
    n = frames(3.4)
    p = C.Pose.current()
    q = orbit((0, 0, 0), 190, 44, 56)
    for i in range(n):
        p.lerp(q, smoother((i + 1) / n)).apply()
        yield
    # grown by the gap, seen in the wire over the solid
    halo.hide_set(False)
    halo.display_type = 'WIRE'
    yield from camera(orbit((0, 0, 0), 180, 50, 76), frames(3.0))
    yield from hold(frames(0.6))


def seg_exact():
    """How it searches, the last step: back on the real meshes."""
    for o in S.pop("demo"):
        bpy.data.objects.remove(o)
    _everything(False)
    st().monitor_volume = True
    st().detect_collisions = True
    sp = C.space()
    sp.show_region_ui = True
    orbit((20, 0, 40), 720, 58, 28).apply()
    yield 1.0
    p0 = C.Pose.current()
    n = frames(4.0)
    for i in range(n):
        t = (i + 1) / n
        orbit(p0.target, lerp_d(p0.dist, 620, t), 58, 28 + 25 * smoother(t)).apply()
        yield


def seg_even():
    """Even layers, with the same parts, height and density: kept low, then staggered."""
    s = st()
    s.detect_collisions = False
    keep = read_layout(os.path.join(CACHE, "keep.3mf"))
    d12 = read_layout(os.path.join(CACHE, "d12.3mf"))
    yield from camera(orbit((20, 0, 40), 700, 78, 8), frames(0.8))
    yield from _tween_to(keep, frames(1.6))
    yield 1.0
    yield from hold(frames(3.4))
    yield from _tween_to(d12, frames(1.8))
    yield 1.0
    yield from hold(frames(3.4))


def _ridges(name):
    """The 24 ridges of a dial layout, as objects (added or moved)."""
    sys.path.insert(0, os.path.join(ROOT, "..", "bucket_builder"))
    from pack import meshio
    shift = Matrix.Translation((-0.5 * VOL[0], -0.5 * VOL[1], 0.0))
    items = meshio.read(os.path.join(CACHE, name + ".3mf")).items
    return items, [shift @ Matrix(np.asarray(it.matrix).tolist()) for it in items]


def seg_dial():
    """Part Quality: the dial at 0 % and 100 %, on parts where it shows."""
    s = st()
    for objs in S["kind"].values():                 # (removed, not hidden: hidden parts are still checked)
        for o in objs:
            bpy.data.objects.remove(o)
    S["kind"] = {}
    C.only_panels(importlib.import_module(BB + ".ui"),
                  ["BUCKETBUILDER_PT_main", "BUCKETBUILDER_PT_pack"], "BUCKETBUILDER_PT")
    s.report_density = False
    s.detect_collisions = False
    items, M0 = _ridges("dial0")
    _, M1 = _ridges("dial100")
    m = items[0].mesh
    me = bpy.data.meshes.new("ridge")
    me.from_pydata(m.verts.tolist(), [], m.tris.tolist())
    me.update()
    ridges = []
    for k, M in enumerate(M0):
        o = bpy.data.objects.new("ridge-%02d" % (k + 1), me)
        o.matrix_world = M
        o.color = _base_color(M)
        bpy.context.scene.collection.objects.link(o)
        ridges.append(o)
    s.pack_quality = 0
    orbit((0, 0, 20), 640, 52, 20).apply()
    yield 1.0
    yield from hold(frames(3.4))
    s.pack_quality = 100
    n = frames(2.0)
    A = [o.matrix_world.copy() for o in ridges]
    for i in range(n):
        t = smoother((i + 1) / n)
        for o, a, b in zip(ridges, A, M1):
            loc = a.translation.lerp(b.translation, t)
            loc.z += 70.0 * 4 * t * (1 - t)
            o.matrix_world = Matrix.Translation(loc) @ a.to_quaternion().slerp(b.to_quaternion(), t).to_matrix().to_4x4()
        yield
    for o, b in zip(ridges, M1):
        o.matrix_world = b
        o.color = _base_color(b)
    yield 1.0
    yield from hold(frames(3.4))
    for o in ridges:
        bpy.data.objects.remove(o)
    bpy.data.meshes.remove(me)


def _base_color(M):
    """Amber when the flat base (the ridge's local -Z) faces up."""
    up = (M.to_3x3() @ Vector((0, 0, -1))).z > 0.9
    return (0.95, 0.62, 0.15, 1.0) if up else (0.62, 0.66, 0.72, 1.0)


def _hue(k):
    """A colour of its own for each mesh."""
    import colorsys
    return (*colorsys.hsv_to_rgb((k * 0.61803) % 1.0, 0.62, 0.9), 1.0)


def seg_can():
    """The chips: stacked in a can, poured into a pile (simulated once by make.sh pour)."""
    s = st()
    s.monitor_volume = False
    s.detect_collisions = False
    C.space().show_region_ui = False
    sc = bpy.context.scene
    d = np.load(os.path.join(CACHE, "pour.npz"))
    Fr, Ca = d["chips"], d["can"]
    R, H = float(d["R"]), float(d["H"])
    chips = []
    for k, m in enumerate(inputs.chip_meshes()):
        me = bpy.data.meshes.new("chip-%03d" % (k + 1))
        me.from_pydata(m.verts.tolist(), [], m.tris.tolist())
        me.update()
        for p in me.polygons:
            p.use_smooth = True
        o = bpy.data.objects.new(me.name, me)
        o.color = _hue(k)
        sc.collection.objects.link(o)
        chips.append(o)
    S["chips"] = chips
    # the can: a plain open cylinder with a floor and a band
    n, v, f = 64, [], []
    for i in range(n):
        a = 2 * np.pi * i / n
        for z, r in ((0, R), (H, R), (H, R - 1.0), (1.0, R - 1.0)):
            v.append((r * np.cos(a), r * np.sin(a), z))
    for i in range(n):
        j = (i + 1) % n
        for c in range(4):
            e = (c + 1) % 4
            f.append((4 * i + c, 4 * j + c, 4 * j + e, 4 * i + e))
    me = bpy.data.meshes.new("can")
    me.from_pydata(v, [], f + [tuple(4 * i for i in reversed(range(n)))] + [tuple(4 * i + 3 for i in range(n))])
    me.update()
    for p in me.polygons:
        p.use_smooth = True
    can = bpy.data.objects.new("can", me)
    can.color = (0.72, 0.13, 0.13, 1.0)
    sc.collection.objects.link(can)
    S["can"] = can
    p0 = orbit((-180, -480, 220), 1500, 68, 14)
    p1 = orbit((-120, -500, 110), 1350, 58, 8)
    p0.apply()
    for k, o in enumerate(chips):
        o.matrix_world = Matrix(Fr[0, k].tolist())
    can.matrix_world = Matrix(Ca[0].tolist())
    yield 0.5
    yield from hold(frames(0.7))
    T = len(Fr)
    for fr in range(T):
        for k, o in enumerate(chips):
            o.matrix_world = Matrix(Fr[fr, k].tolist())
        can.matrix_world = Matrix(Ca[fr].tolist())
        p0.lerp(p1, smoother((fr + 1) / T)).apply()
        yield
    yield from hold(frames(0.6))


def seg_chips():
    """The packer's layout for those chips (fam.3mf, from the CLI), replayed like the first."""
    sys.path.insert(0, os.path.join(ROOT, "..", "bucket_builder"))
    from pack import meshio
    shift = Matrix.Translation((-0.5 * VOL[0], -0.5 * VOL[1], 0.0))
    chips = S["chips"]
    moves = []
    for it in meshio.read(os.path.join(CACHE, "fam.3mf")).items:
        k = int(it.name.split("#")[0].split("-")[1]) - 1
        o = chips[k]
        moves.append((o, o.matrix_world.copy(), shift @ Matrix(np.asarray(it.matrix).tolist())))
    moves.sort(key=lambda c: c[2].translation.z)
    bpy.data.objects.remove(S.pop("can"))
    n, each = frames(5.0), frames(1.0)
    begin = {c[0]: int((n - each) * k / len(moves)) for k, c in enumerate(moves)}
    p0 = C.Pose.current()
    p1 = orbit((0, 0, 100), 860, 62, 28)
    for i in range(n):
        p0.lerp(p1, smoother((i + 1) / n)).apply()
        for o, A, B in moves:
            t = smoother(min(1.0, max(0.0, (i - begin[o]) / each)))
            loc = A.translation.lerp(B.translation, t)
            loc.z += 140.0 * 4 * t * (1 - t)
            o.matrix_world = Matrix.Translation(loc) @ A.to_quaternion().slerp(B.to_quaternion(), t).to_matrix().to_4x4()
        yield
    st().monitor_volume = True
    st().detect_collisions = True
    C.space().show_region_ui = True
    yield from settled()
    p0 = C.Pose.current()
    yield from camera(orbit(p0.target, p0.dist * 0.85, 70, 62), frames(3.6))


def seg_hero():
    """A close-up of the nested chips for the title card: not in the timeline."""
    s = st()
    sp = C.space()
    keep = C.Pose.current(), sp.show_region_ui, s.monitor_volume, s.detect_collisions
    sp.show_region_ui = False
    s.show_hud = False
    s.monitor_volume = False
    s.detect_collisions = False
    sp.overlay.show_text = False
    sp.overlay.show_floor = False
    sp.overlay.show_axis_x = sp.overlay.show_axis_y = False
    orbit(*HERO_POSE).apply()
    yield 1.5
    yield
    pose, sp.show_region_ui, s.monitor_volume, s.detect_collisions = keep
    s.show_hud = True
    sp.overlay.show_text = True
    sp.overlay.show_floor = True
    sp.overlay.show_axis_x = sp.overlay.show_axis_y = True
    pose.apply()


HERO_POSE = ((-110, -60, 150), 400, 62, 30)


def seg_wedges():
    """Half the copies turned over: 300 wedges (wedges.3mf, from the CLI)."""
    sys.path.insert(0, os.path.join(ROOT, "..", "bucket_builder"))
    from pack import meshio
    for o in S.pop("chips"):
        bpy.data.objects.remove(o)
    shift = Matrix.Translation((-0.5 * VOL[0], -0.5 * VOL[1], 0.0))
    items = meshio.read(os.path.join(CACHE, "wedges.3mf")).items
    m = items[0].mesh
    me = bpy.data.meshes.new("wedge")
    me.from_pydata(m.verts.tolist(), [], m.tris.tolist())
    me.update()
    s = st()
    s.detect_collisions = False
    s.monitor_volume = False
    wedges = []
    colors, palette = {}, [(0.90, 0.60, 0.25, 1.0), (0.28, 0.62, 0.72, 1.0), (0.55, 0.72, 0.35, 1.0), (0.72, 0.45, 0.72, 1.0)]
    for k, it in enumerate(items):
        o = bpy.data.objects.new("wedge-%02d" % (k + 1), me)
        B = shift @ Matrix(np.asarray(it.matrix).tolist())
        # each orientation its own color: the turned-over partners show
        key = tuple(int(round(v)) for row in B.to_3x3() for v in row)
        o.color = colors.setdefault(key, palette[len(colors) % len(palette)])
        bpy.context.scene.collection.objects.link(o)
        A = Matrix.Translation((-760 + 80 * (k % 20), -560 + 80 * (k // 20), 480.0))
        o.matrix_world = A
        wedges.append((o, A, B))
    orbit((0, 0, 220), 1900, 62, 20).apply()
    yield 0.5
    order = sorted(wedges, key=lambda c: c[2].translation.z)
    n, each = frames(3.2), frames(0.8)
    begin = {c[0]: int((n - each) * k / len(order)) for k, c in enumerate(order)}
    p0 = C.Pose.current()
    p1 = orbit((0, 0, 80), 760, 62, 30)
    for i in range(n):
        p0.lerp(p1, smoother((i + 1) / n)).apply()
        for o, A, B in wedges:
            t = smoother(min(1.0, max(0.0, (i - begin[o]) / each)))
            loc = A.translation.lerp(B.translation, t)
            o.matrix_world = Matrix.Translation(loc) @ A.to_quaternion().slerp(B.to_quaternion(), t).to_matrix().to_4x4()
        yield
    for o, _A, B in wedges:
        o.matrix_world = B
    s.monitor_volume = True
    s.detect_collisions = True
    yield from settled()
    p0 = C.Pose.current()
    yield from camera(orbit(p0.target, p0.dist * 0.85, 70, 62), frames(2.6))


def lerp_d(a, b, t):
    return a + (b - a) * smoother(t)


SEGMENTS = [
    ("a_pile", seg_pile),
    ("b_settings", seg_settings),
    ("c_packing", seg_packing),
    ("c2_fill", seg_fill),
    ("d_result", seg_result),
    ("d2_voxels", seg_voxels),
    ("d3_exact", seg_exact),
    ("e_report", seg_report),
    ("f_density", seg_density),
    ("f2_even", seg_even),
    ("g_dial", seg_dial),
    ("h_can", seg_can),
    ("h2_chips", seg_chips),
    ("z_hero", seg_hero),
    ("i_wedges", seg_wedges),
]

if __name__ == "__main__":
    C.Reel(OUT, SEGMENTS, setup=[setup, setup_scene]).start(set(C.args()) or None)
