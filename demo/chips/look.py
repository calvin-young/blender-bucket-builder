# SPDX-License-Identifier: GPL-3.0-or-later
"""The look of the chip reel: a can, chips that look like chips, a table and lights, for a viewport
in Material Preview. Nothing here is rendered: every frame is a screenshot of the viewport."""
import math
import os

import bpy
import numpy as np

STUDIO = os.path.join(os.path.dirname(os.path.dirname(bpy.app.binary_path)), "")
R_OUT, H_CAN = 72.0, 445.0


def _nodes(mat):
    mat.use_nodes = True
    t = mat.node_tree
    t.nodes.clear()
    out = t.nodes.new("ShaderNodeOutputMaterial")
    b = t.nodes.new("ShaderNodeBsdfPrincipled")
    t.links.new(b.outputs[0], out.inputs[0])
    return t, b


def chip_material():
    """A golden chip, darker where it toasted, with bubbles and salt. Object color: the chip's own
    hue, its alpha how far the chip has gone from food to part (0 chip, 1 its color)."""
    mat = bpy.data.materials.new("chip")
    t, b = _nodes(mat)
    L = t.links.new
    co = t.nodes.new("ShaderNodeTexCoord")
    info = t.nodes.new("ShaderNodeObjectInfo")
    # toast: large soft noise
    n1 = t.nodes.new("ShaderNodeTexNoise")
    n1.inputs["Scale"].default_value = 0.055
    n1.inputs["Detail"].default_value = 6.0
    n1.inputs["Roughness"].default_value = 0.55
    L(co.outputs["Object"], n1.inputs["Vector"])
    ramp = t.nodes.new("ShaderNodeValToRGB")
    e = ramp.color_ramp.elements
    e[0].position, e[0].color = 0.36, (0.80, 0.40, 0.06, 1)
    e[1].position, e[1].color = 0.68, (0.95, 0.62, 0.13, 1)
    x = ramp.color_ramp.elements.new(0.46)
    x.color = (0.90, 0.52, 0.09, 1)
    L(n1.outputs["Fac"], ramp.inputs[0])
    # browned spots
    n2 = t.nodes.new("ShaderNodeTexVoronoi")
    n2.inputs["Scale"].default_value = 0.11
    L(co.outputs["Object"], n2.inputs["Vector"])
    r2 = t.nodes.new("ShaderNodeValToRGB")
    r2.color_ramp.elements[0].position, r2.color_ramp.elements[0].color = 0.05, (0.75, 0.75, 0.75, 1)
    r2.color_ramp.elements[1].position, r2.color_ramp.elements[1].color = 0.2, (1, 1, 1, 1)
    L(n2.outputs["Distance"], r2.inputs[0])
    m1 = t.nodes.new("ShaderNodeMixRGB")
    m1.blend_type = "MULTIPLY"
    m1.inputs[0].default_value = 1.0
    L(ramp.outputs[0], m1.inputs[1])
    L(r2.outputs[0], m1.inputs[2])
    # salt: tiny pale grains
    n3 = t.nodes.new("ShaderNodeTexVoronoi")
    n3.inputs["Scale"].default_value = 0.75
    L(co.outputs["Object"], n3.inputs["Vector"])
    r3 = t.nodes.new("ShaderNodeValToRGB")
    r3.color_ramp.elements[0].position, r3.color_ramp.elements[0].color = 0.08, (1, 1, 1, 1)
    r3.color_ramp.elements[1].position, r3.color_ramp.elements[1].color = 0.13, (0, 0, 0, 1)
    L(n3.outputs["Distance"], r3.inputs[0])
    m2 = t.nodes.new("ShaderNodeMixRGB")
    m2.inputs[2].default_value = (1.0, 0.97, 0.88, 1)
    L(r3.outputs[0], m2.inputs[0])
    L(m1.outputs[0], m2.inputs[1])
    # the part's own color
    m3 = t.nodes.new("ShaderNodeMixRGB")
    L(info.outputs["Alpha"], m3.inputs[0])
    L(m2.outputs[0], m3.inputs[1])
    L(info.outputs["Color"], m3.inputs[2])
    L(m3.outputs[0], b.inputs["Base Color"])
    # bubbles in the surface
    n4 = t.nodes.new("ShaderNodeTexNoise")
    n4.inputs["Scale"].default_value = 0.28
    n4.inputs["Detail"].default_value = 5.0
    L(co.outputs["Object"], n4.inputs["Vector"])
    bump = t.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.65
    bump.inputs["Distance"].default_value = 1.2
    L(n4.outputs["Fac"], bump.inputs["Height"])
    inv = t.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    L(info.outputs["Alpha"], inv.inputs[1])
    mul = t.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    L(n4.outputs["Fac"], mul.inputs[0])
    L(inv.outputs[0], mul.inputs[1])
    L(mul.outputs[0], bump.inputs["Height"])
    L(bump.outputs[0], b.inputs["Normal"])
    b.inputs["Roughness"].default_value = 0.46
    b.inputs["Specular IOR Level"].default_value = 0.55
    return mat


def _lathe(name, profile, segs=128, uv=None, smooth=True, close_ends=()):
    """A solid of revolution around z from (r, z) points."""
    n = len(profile)
    v, f = [], []
    for i in range(segs):
        a = 2 * math.pi * i / segs
        for r, z in profile:
            v.append((r * math.cos(a), r * math.sin(a), z))
    for i in range(segs):
        j = (i + 1) % segs
        for k in range(n - 1):
            f.append((i * n + k, j * n + k, j * n + k + 1, i * n + k + 1))
    me = bpy.data.meshes.new(name)
    me.from_pydata(v, [], f)
    me.update()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    if uv is not None:
        lay = me.uv_layers.new(name="UV")
        for p in me.polygons:
            for li in p.loop_indices:
                vi = me.loops[li].vertex_index
                i, k = vi // n, vi % n
                # (the seam: a face that crosses it takes u from its own side)
                u = i / segs
                if p.vertices[0] // n == segs - 1 and i == 0:
                    u = 1.0
                lay.data[li].uv = (u, (profile[k][1] - uv[0]) / (uv[1] - uv[0]))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def can(label_png):
    """The body with its paper label, metal rims and base, foil inside. Returns (body, lid); the rest
    is parented to the body. Its foot is at the origin."""
    R, H = R_OUT, H_CAN
    # paper
    body = _lathe("can", [(R, 5.0), (R, H - 5.0)], uv=(5.0, H - 5.0))
    m = bpy.data.materials.new("label")
    t, b = _nodes(m)
    tex = t.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(label_png)
    tex.interpolation = "Cubic"
    t.links.new(tex.outputs[0], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.34
    b.inputs["Coat Weight"].default_value = 0.35
    b.inputs["Coat Roughness"].default_value = 0.12
    body.data.materials.append(m)
    # foil inside
    foil = _lathe("can foil", [(R - 1.6, H - 3.0), (R - 1.6, 4.0), (R - 12, 3.0), (0.01, 3.0)])
    m = bpy.data.materials.new("foil")
    t, b = _nodes(m)
    b.inputs["Base Color"].default_value = (0.82, 0.83, 0.85, 1)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.32
    foil.data.materials.append(m)
    # steel rims and base
    prof = [(0.01, 0.0), (R - 8, 0.0), (R - 5, 1.0), (R + 0.7, 1.0), (R + 1.5, 2.8), (R + 0.7, 5.2), (R - 0.5, 5.6)]
    base = _lathe("can base", prof)
    top = [(R - 0.5, H - 5.6), (R + 0.8, H - 5.2), (R + 1.8, H - 2.6), (R + 0.8, H - 0.3), (R - 0.8, H), (R - 1.9, H - 1.0), (R - 1.6, H - 3.0)]
    rim = _lathe("can rim", top)
    m = bpy.data.materials.new("steel")
    t, b = _nodes(m)
    b.inputs["Base Color"].default_value = (0.86, 0.87, 0.89, 1)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.24
    base.data.materials.append(m)
    rim.data.materials.append(m)
    for o in (foil, base, rim):
        o.parent = body
    # a lid of soft translucent plastic (its own object: it comes off)
    r = R + 3.4
    lp = [(0.01, 7.0), (r - 14, 7.0), (r - 11, 5.0), (r - 7, 5.0), (r - 3.5, 8.2), (r - 1.0, 7.4), (r, 4.5), (r, -9.0), (r - 1.4, -10.0), (r - 2.6, -9.0), (r - 2.6, 3.0), (0.01, 3.0)]
    lid = _lathe("can lid", lp)
    m = bpy.data.materials.new("lid")
    t, b = _nodes(m)
    b.inputs["Base Color"].default_value = (0.9, 0.9, 0.86, 1)
    b.inputs["Roughness"].default_value = 0.28
    b.inputs["Transmission Weight"].default_value = 0.45
    b.inputs["Subsurface Weight"].default_value = 0.5
    lid.data.materials.append(m)
    return body, lid


def table():
    """A dark studio floor that fades into the background."""
    v = [(-9000, -9000, 0), (9000, -9000, 0), (9000, 9000, 0), (-9000, 9000, 0)]
    me = bpy.data.meshes.new("table")
    me.from_pydata(v, [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new("table", me)
    bpy.context.scene.collection.objects.link(ob)
    m = bpy.data.materials.new("table")
    t, b = _nodes(m)
    co = t.nodes.new("ShaderNodeTexCoord")
    grad = t.nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "SPHERICAL"
    mp = t.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / 2200, 1 / 2200, 1)
    t.links.new(co.outputs["Object"], mp.inputs[0])
    t.links.new(mp.outputs[0], grad.inputs[0])
    r = t.nodes.new("ShaderNodeValToRGB")
    r.color_ramp.elements[0].position, r.color_ramp.elements[0].color = 0.0, BACK
    r.color_ramp.elements[1].position, r.color_ramp.elements[1].color = 0.55, FLOOR
    t.links.new(grad.outputs[0], r.inputs[0])
    t.links.new(r.outputs[0], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.5
    # (no shine far away, or the floor's edge shows against the background)
    fade = t.nodes.new("ShaderNodeMath")
    fade.operation = "MULTIPLY"
    fade.inputs[1].default_value = 0.22
    t.links.new(grad.outputs[0], fade.inputs[0])
    t.links.new(fade.outputs[0], b.inputs["Specular IOR Level"])
    ob.data.materials.append(m)
    return ob


BACK = (0.012, 0.018, 0.035, 1)
FLOOR = (0.035, 0.052, 0.09, 1)


def _light(name, kind, loc, target, energy, color, size=800.0):
    li = bpy.data.lights.new(name, kind)
    li.energy = energy
    li.color = color
    if kind == "AREA":
        li.shape = "DISK"
        li.size = size
    elif kind == "SPOT":
        li.spot_size = math.radians(40)
        li.spot_blend = 0.8
        li.shadow_soft_size = size
    ob = bpy.data.objects.new(name, li)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    d = np.array(target, float) - np.array(loc, float)
    from mathutils import Vector
    ob.rotation_euler = Vector(d.tolist()).to_track_quat("-Z", "Y").to_euler()
    return ob


def lights():
    _light("key", "AREA", (-900, -1300, 1500), (-100, -100, 120), 2.2e7, (1.0, 0.93, 0.84), 1400.0)
    _light("fill", "AREA", (1500, -500, 700), (0, -100, 120), 5e6, (0.72, 0.82, 1.0), 1800.0)
    _light("rim", "AREA", (300, 1500, 1000), (-100, 0, 150), 1.1e7, (0.95, 0.97, 1.0), 1000.0)


def world():
    w = bpy.data.worlds.new("studio")
    bpy.context.scene.world = w
    w.use_nodes = True
    t = w.node_tree
    t.nodes.clear()
    out = t.nodes.new("ShaderNodeOutputWorld")
    env = t.nodes.new("ShaderNodeTexEnvironment")
    path = os.path.join(bpy.utils.system_resource("DATAFILES"), "studiolights", "world", "studio.exr")
    env.image = bpy.data.images.load(path)
    bg = t.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.42
    t.links.new(env.outputs[0], bg.inputs["Color"])
    back = t.nodes.new("ShaderNodeBackground")
    back.inputs["Color"].default_value = BACK
    lp = t.nodes.new("ShaderNodeLightPath")
    mix = t.nodes.new("ShaderNodeMixShader")
    t.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
    t.links.new(bg.outputs[0], mix.inputs[1])
    t.links.new(back.outputs[0], mix.inputs[2])
    t.links.new(mix.outputs[0], out.inputs[0])


def viewport(sp, scene):
    sp.shading.type = "MATERIAL"
    sp.shading.use_scene_lights = True
    sp.shading.use_scene_world = True
    ov = sp.overlay
    ov.show_floor = False
    ov.show_axis_x = False
    ov.show_axis_y = False
    ov.show_axis_z = False
    ov.show_text = False
    ov.show_cursor = False
    ov.show_outline_selected = False
    ov.show_object_origins = False
    ov.show_relationship_lines = False
    ov.show_extras = False
    ov.show_stats = False
    e = scene.eevee
    e.taa_samples = 16
    e.use_shadows = True
    e.use_raytracing = True
    e.ray_tracing_method = "SCREEN"
    e.use_fast_gi = True
    scene.view_settings.look = "AgX - Punchy"
    scene.view_settings.exposure = 0.35
