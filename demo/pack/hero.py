# SPDX-License-Identifier: GPL-3.0-or-later
"""The picture on the packing reel's title card: a full bucket, zoomed out, from the packer's own layout."""
import colorsys
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(ROOT, "lib"), os.path.join(ROOT, "cache", "pack", "addon", "v1.5.1", "bucket_builder")]
import capture as C  # noqa: E402
from capture import orbit  # noqa: E402
from pack import meshio  # noqa: E402

CACHE = os.path.join(ROOT, "cache", "pack")
VOL = (380.0, 284.0, 380.0)
BB = "bl_ext.user_default.bucket_builder"


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
    sp.overlay.show_floor = False
    sp.overlay.show_axis_x = sp.overlay.show_axis_y = False
    sp.overlay.show_text = False
    sp.shading.light = "MATCAP" if False else "STUDIO"
    sp.shading.show_cavity = True
    sp.shading.background_type = "VIEWPORT"
    sp.shading.background_color = (0.012, 0.013, 0.018)
    sc = bpy.context.scene
    C.set_units_mm(sc)
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    shift = Matrix.Translation((-VOL[0] / 2, -VOL[1] / 2, 0))
    meshes, n = {}, 0
    for it in meshio.read(os.path.join(CACHE, "hero.3mf")).items:
        k = id(it.mesh)
        if k not in meshes:
            me = bpy.data.meshes.new(it.name.split("#")[0])
            me.from_pydata(np.asarray(it.mesh.verts).tolist(), [], np.asarray(it.mesh.tris).tolist())
            me.update()
            for p in me.polygons:
                p.use_smooth = True
            hue = (len(meshes) * 0.137 + 0.55) % 1.0
            meshes[k] = (me, colorsys.hsv_to_rgb(hue, 0.5, 0.82))
        me, col = meshes[k]
        o = bpy.data.objects.new("%s-%d" % (me.name, n), me)
        sc.collection.objects.link(o)
        o.matrix_world = shift @ Matrix(np.asarray(it.matrix).tolist())
        j = 1 + 0.14 * ((n * 37 % 100) / 100 - 0.5)
        o.color = (*[min(1.0, c * j) for c in col], 1.0)
        n += 1
    s = bpy.context.scene.bucket_builder
    s.volume_align = "CENTER_XY"
    s.monitor_volume = True
    s.detect_collisions = False
    s.show_hud = False
    return 3.0


def seg():
    # (the card darkens its left side: the bucket goes on the right)
    orbit((-175, 0, 175), 860, 68, 28).apply()
    yield 3.0
    yield


if __name__ == "__main__":
    class Reel(C.Reel):
        pass
    r = C.Reel(os.path.join(CACHE, "hero_frames"), [("h", seg)], setup=[setup, setup_full, setup_scene])
    r.start(None)
