"""Blender's mathutils.bvhtree.BVHTree against the add-on's engine.

Two things are measured for a part being dragged into a neighbour:

* BVHTree.overlap() has no transform argument, so both trees must be in world
  space and the moving part's tree has to be rebuilt on every step.
* The add-on keeps one tree per part and applies the translation at query time.

It also serves as an independent correctness check: Blender's own
triangle/triangle overlap must find the same intersecting pairs.

Run with: blender --background --python this_file
"""

import importlib
import sys
import time

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("build_check"))
bc = importlib.import_module(PKG)
monitor = bc.monitor
core = importlib.import_module(PKG + ".core")


def settle():
    sc = bpy.context.scene
    mon = monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    for _ in range(20000):
        mon.tick(sc, vl.depsgraph, vl, live=False)
        if not mon.busy:
            break
    return mon


def world_arrays(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    co, tri = monitor.read_mesh(obj.evaluated_get(dg))
    M = np.array(obj.matrix_world)
    return co @ M[:3, :3].T + M[:3, 3], tri


def tree(obj):
    v, t = world_arrays(obj)
    return BVHTree.FromPolygons(v.tolist(), t.tolist(), all_triangles=True)


def main():
    print('Blender', bpy.app.version_string)
    rows = []
    for segs in (64, 128, 256, 512):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=segs, ring_count=segs // 2, radius=30, location=(100, 100, 100))
        a = bpy.context.active_object
        bpy.ops.mesh.primitive_torus_add(major_radius=30, minor_radius=10, major_segments=segs,
                                         minor_segments=segs // 4, location=(170, 100, 104),
                                         rotation=(0.4, 0.2, 0.1))
        b = bpy.context.active_object
        bpy.context.view_layer.update()
        st = bpy.context.scene.build_check
        st.enabled = False
        st.enabled = True
        mon = settle()
        ntri = mon.objs[a.session_uid].ntri + mon.objs[b.session_uid].ntri

        tree_a = tree(a)
        xs = np.linspace(170.0, 140.0, 16)

        # --- BVHTree: rebuild the moving tree and query, every step
        t_build = t_query = 0.0
        counts_bvh = []
        for x in xs:
            b.location.x = float(x)
            bpy.context.view_layer.update()
            t0 = time.perf_counter()
            tb = tree(b)
            t1 = time.perf_counter()
            pairs = tree_a.overlap(tb)
            t2 = time.perf_counter()
            t_build += t1 - t0
            t_query += t2 - t1
            counts_bvh.append(len(pairs))

        # --- the add-on: the handler runs inside view_layer.update()
        st.use_clearance = False
        mon = settle()
        b.location.x = 171.0
        bpy.context.view_layer.update()
        counts_bc = []
        t_bc = 0.0
        for x in xs:
            b.location.x = float(x)
            t0 = time.perf_counter()
            bpy.context.view_layer.update()
            t_bc += time.perf_counter() - t0
            w = mon.world
            sa, sb = w.slot(a.session_uid), w.slot(b.session_uid)
            pr = w.pairs.get((sa, sb) if sa < sb else (sb, sa))
            counts_bc.append(0 if pr is None or pr.state != core.COLLIDE else pr.nseg)
        # Blender's own cost of the update without the add-on
        st.enabled = False
        t_plain = 0.0
        for x in xs:
            b.location.x = float(x) + 0.001
            t0 = time.perf_counter()
            bpy.context.view_layer.update()
            t_plain += time.perf_counter() - t0
        st.use_clearance = True

        n = len(xs)
        same = sum(1 for p, q in zip(counts_bvh, counts_bc) if p == q)
        rows.append((ntri, t_build / n * 1000, t_query / n * 1000, (t_bc - t_plain) / n * 1000,
                     same, n, max(counts_bvh)))
        diff = [(p, q) for p, q in zip(counts_bvh, counts_bc) if p != q]
        print(f'  {ntri:8d} triangles: BVHTree rebuild {t_build / n * 1000:8.2f} ms + overlap '
              f'{t_query / n * 1000:7.2f} ms per step | add-on {(t_bc - t_plain) / n * 1000:6.2f} ms per step'
              f' | intersecting-pair counts equal in {same}/{n} steps (max {max(counts_bvh)})'
              + (f' differences: {diff[:4]}' if diff else ''))
    print('BVHTREE COMPARE DONE')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('BVHTREE COMPARE FAILED')
    sys.exit(1)
