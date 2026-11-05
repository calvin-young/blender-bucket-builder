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
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(ROOT, "lib")]
import capture as C  # noqa: E402
from capture import frames, smoother, orbit  # noqa: E402
import parts as P  # noqa: E402

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
    C.only_panels(importlib.import_module(BB + ".ui"),
                  ["BUCKETBUILDER_PT_main", "BUCKETBUILDER_PT_pack"], "BUCKETBUILDER_PT")
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
    while job['running'] and time.perf_counter() - t0 < 600:
        yield                                        # (the edit keeps a second or two of these)
        yield 0.25
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


def seg_patterns():
    """Many copies of one curved part: the packer's patterns nest them (layout from
    cache/pack/chips.3mf, packed by the CLI; make.sh pack)."""
    sys.path.insert(0, os.path.join(ROOT, "..", "bucket_builder"))
    from pack import meshio
    sc = bpy.context.scene
    for objs in S["kind"].values():                 # (removed, not hidden: hidden parts are still checked)
        for o in objs:
            bpy.data.objects.remove(o)
    S["kind"] = {}
    st().detect_collisions = False
    items = meshio.read(os.path.join(CACHE, "chips.3mf")).items
    m = items[0].mesh
    me = bpy.data.meshes.new("chip")
    me.from_pydata(m.verts.tolist(), [], m.tris.tolist())
    me.update()
    shift = Matrix.Translation((-0.5 * VOL[0], -0.5 * VOL[1], 0.0))
    chips = []
    for k, it in enumerate(items):
        o = bpy.data.objects.new("chip-%03d" % (k + 1), me)
        o.color = (0.93, 0.66, 0.28, 1.0) if k % 2 else (0.88, 0.58, 0.22, 1.0)
        sc.collection.objects.link(o)
        B = shift @ Matrix(np.asarray(it.matrix).tolist())
        A = Matrix.Translation((-450 + 64 * (k % 15), -300 + 64 * (k // 15), 520.0))
        o.matrix_world = A
        chips.append((o, A, B))
    POSE_CHIPS.apply()
    yield 1.0
    yield from hold(frames(0.8))
    order = sorted(chips, key=lambda c: c[2].translation.z)
    n, each = frames(4.5), frames(0.8)
    begin = {c[0]: int((n - each) * k / len(order)) for k, c in enumerate(order)}
    p0 = C.Pose.current()
    for i in range(n):
        p0.lerp(POSE_CHIPS2, smoother((i + 1) / n)).apply()
        for o, A, B in chips:
            t = smoother(min(1.0, max(0.0, (i - begin[o]) / each)))
            loc = A.translation.lerp(B.translation, t)
            q = A.to_quaternion().slerp(B.to_quaternion(), t)
            o.matrix_world = Matrix.Translation(loc) @ q.to_matrix().to_4x4()
        yield
    st().detect_collisions = True
    yield 1.0
    p0 = C.Pose.current()
    yield from camera(orbit(p0.target, p0.dist * 0.8, 70, 60), frames(3.5))


POSE_CHIPS = orbit((0, 0, 220), 1500, 64, 20)
POSE_CHIPS2 = orbit((0, 0, 50), 700, 62, 32)


def seg_end():
    s = st()
    s.detect_collisions = True
    yield 1.0
    p0 = C.Pose.current()
    n = frames(4.0)
    for i in range(n):
        t = (i + 1) / n
        orbit(p0.target, lerp_d(p0.dist, 700, t), lerp_d(74, 56, t), 10 + 50 * smoother(t)).apply()
        yield


def lerp_d(a, b, t):
    return a + (b - a) * smoother(t)


SEGMENTS = [
    ("a_pile", seg_pile),
    ("b_settings", seg_settings),
    ("c_packing", seg_packing),
    ("c2_fill", seg_fill),
    ("d_result", seg_result),
    ("e_report", seg_report),
    ("f_density", seg_density),
    ("g_end", seg_end),
    ("h_patterns", seg_patterns),
]

if __name__ == "__main__":
    C.Reel(OUT, SEGMENTS, setup=[setup, setup_scene]).start(set(C.args()) or None)
