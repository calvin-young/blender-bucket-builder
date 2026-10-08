# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport drawing: hatched problem regions, intersection curves, clearance
gaps, the build volume and the pass / fail badge.

Nothing is added to the scene.  Geometry is drawn straight from arrays the
collision world hands over, through two draw handlers:

* POST_VIEW  (3-D)  build volume, hatched regions, curves, gap lines
* POST_PIXEL (2-D)  problem markers, distance labels, status badge

Problem geometry is split into a *static* set (parts nobody is touching; its
GPU buffers are rebuilt only when it changes) and a *hot* set (anything
involving a part that is being moved; rebuilt every update, but small).
"""

import math
import time

import blf
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from . import monitor, props
from .core import COLLIDE, PARTIAL, OUTSIDE

_handles = []
_state = {}          # lazily created GPU resources and per-scene caches

MAX_MARKERS = 200

# styles understood by the hatch fragment shader
STYLE_CROSS, STYLE_DIAG, STYLE_BACK, STYLE_SOLID = 0.0, 1.0, 2.0, 3.0

_VERT = """
void main()
{
  v_pos = pos;
  vec4 p = u_viewProj * vec4(pos, 1.0);
  /* Pull the surface slightly towards the viewer so it does not z-fight with
   * the mesh it lies on: u_bias = (perspective?, winmat[2][2], relative, absolute). */
  if (u_bias.x > 0.5) {
    p.z = (p.z + u_bias.y * u_bias.z * p.w) / (1.0 - u_bias.z);
  }
  else {
    p.z += u_bias.y * u_bias.w;
  }
  gl_Position = p;
}
"""

# u_params = (hatch spacing, line width, clip mode, style)
# clip mode 0: draw everything, 1: only inside the clip box, 2: only outside it
_FRAG = """
void main()
{
  if (u_params.z > 0.5) {
    bool inside = all(greaterThanEqual(v_pos, u_clipLo.xyz)) && all(lessThanEqual(v_pos, u_clipHi.xyz));
    if (inside == (u_params.z > 1.5)) {
      discard;
    }
  }
  float s = u_params.x;
  float w = u_params.y;
  float d1 = mod(gl_FragCoord.x + gl_FragCoord.y, s);
  float d2 = mod(gl_FragCoord.x - gl_FragCoord.y + 16384.0, s);
  float l1 = (d1 <= w) ? 1.0 : 0.0;
  float l2 = (d2 <= w) ? 1.0 : 0.0;
  float cov = 1.0;
  if (u_params.w < 0.5) {
    cov = max(l1, l2);
  }
  else if (u_params.w < 1.5) {
    cov = l1;
  }
  else if (u_params.w < 2.5) {
    cov = l2;
  }
  float alpha = u_color.a * max(cov, u_extra.x);
  if (alpha < 0.004) {
    discard;
  }
  vec3 c = u_color.rgb;
  if (u_extra.y > 0.5) {
    /* The viewport overlay buffer is sRGB encoded by the hardware on write, so
     * hand over linear values (this is what Blender's built-in shaders do). */
    vec3 lin_lo = c / 12.92;
    vec3 lin_hi = pow((c + vec3(0.055)) / 1.055, vec3(2.4));
    c = mix(lin_lo, lin_hi, step(vec3(0.04045), c));
  }
  fragColor = vec4(c, alpha);
}
"""


# Whole-part shading: the mesh is uploaded once per unique part and redrawn
# with the object's current matrix, so nothing is rebuilt while a part moves.
_TINT_VERT = """
void main()
{
  vec4 p = u_mvp * vec4(pos, 1.0);
  if (u_bias.x > 0.5) {
    p.z = (p.z + u_bias.y * u_bias.z * p.w) / (1.0 - u_bias.z);
  }
  else {
    p.z += u_bias.y * u_bias.w;
  }
  gl_Position = p;
}
"""

_TINT_FRAG = """
void main()
{
  vec3 c = u_color.rgb;
  if (u_extra.y > 0.5) {
    vec3 lin_lo = c / 12.92;
    vec3 lin_hi = pow((c + vec3(0.055)) / 1.055, vec3(2.4));
    c = mix(lin_lo, lin_hi, step(vec3(0.04045), c));
  }
  fragColor = vec4(c, u_color.a);
}
"""


def _make_tint_shader():
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', "u_mvp")
    info.push_constant('VEC4', "u_color")
    info.push_constant('VEC4', "u_bias")
    info.push_constant('VEC4', "u_extra")
    info.vertex_in(0, 'VEC3', "pos")
    info.fragment_out(0, 'VEC4', "fragColor")
    info.vertex_source(_TINT_VERT)
    info.fragment_source(_TINT_FRAG)
    shader = gpu.shader.create_from_info(info)
    del info
    return shader


def _make_hatch_shader():
    iface = gpu.types.GPUStageInterfaceInfo("bucket_builder_iface")
    iface.smooth('VEC3', "v_pos")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', "u_viewProj")
    info.push_constant('VEC4', "u_color")
    info.push_constant('VEC4', "u_params")
    info.push_constant('VEC4', "u_bias")
    info.push_constant('VEC4', "u_extra")
    info.push_constant('VEC4', "u_clipLo")
    info.push_constant('VEC4', "u_clipHi")
    info.vertex_in(0, 'VEC3', "pos")
    info.vertex_out(iface)
    info.fragment_out(0, 'VEC4', "fragColor")
    info.vertex_source(_VERT)
    info.fragment_source(_FRAG)
    shader = gpu.shader.create_from_info(info)
    del iface, info
    return shader


def _shaders():
    sh = _state.get('shaders')
    if sh is None:
        sh = {}
        try:
            sh['hatch'] = _make_hatch_shader()
        except Exception as ex:      # unusual GPU / backend: fall back to flat tinting
            print("Bucket Builder: hatch shader unavailable, using flat tint:", ex)
            sh['hatch'] = None
        try:
            sh['tint'] = _make_tint_shader()
        except Exception as ex:      # colliding parts then get outline boxes
            print("Bucket Builder: part shading unavailable, using outlines:", ex)
            sh['tint'] = None
        sh['flat'] = gpu.shader.from_builtin('UNIFORM_COLOR')
        sh['line'] = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        _state['shaders'] = sh
    return sh


# ---------------------------------------------------------------------------
# geometry assembly
# ---------------------------------------------------------------------------

class _Group:
    """GPU batches for one set of problems."""

    def __init__(self):
        self.key = None
        self.tri = {}        # kind -> batch
        self.clipped = {}    # kind -> [(batch, box lo, box hi), ...] patches to trim
        self.line = {}       # kind -> batch
        self.ntri = 0
        self.empty = True


def _tri_array(parts):
    """parts: list of (tris (n, 3, 3) f32, offset (3,)) -> positions (3n, 3) f32."""
    if not parts:
        return None
    n = sum(p[0].shape[0] for p in parts)
    if n == 0:
        return None
    pos = np.empty((n * 3, 3), dtype=np.float32)
    i = 0
    for tris, off in parts:
        m = tris.shape[0] * 3
        if m == 0:
            continue
        np.add(tris.reshape(m, 3), off, out=pos[i:i + m], casting='unsafe')
        i += m
    return pos


def _line_array(parts):
    """parts: list of (segments (n,2,3), offset) -> (2n, 3) float32."""
    if not parts:
        return None
    n = sum(p[0].shape[0] for p in parts)
    if n == 0:
        return None
    pos = np.empty((n * 2, 3), dtype=np.float32)
    i = 0
    for segs, off in parts:
        m = segs.shape[0] * 2
        np.add(segs.reshape(m, 3), off, out=pos[i:i + m], casting='unsafe')
        i += m
    return pos


def _box_edges(lo, hi):
    c = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]],
                  [lo[0], hi[1], lo[2]], [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
                  [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]], dtype=np.float32)
    e = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7]
    return c[e].reshape(12, 2, 3)


def _box_faces(lo, hi):
    c = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]],
                  [lo[0], hi[1], lo[2]], [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
                  [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]], dtype=np.float32)
    f = [0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 5, 1, 0, 4, 5,
         1, 6, 2, 1, 5, 6, 2, 7, 3, 2, 6, 7, 3, 4, 0, 3, 7, 4]
    return np.ascontiguousarray(c[f])


def _build_group(mon, pairs, oobs, walls, vol, shaders):
    """Create the batches for the given problems."""
    w = mon.world
    g = _Group()
    tri_parts = {'collide': [], 'clear': [], 'outside': [], 'wall': []}
    clip_parts = {'collide': [], 'clear': []}
    line_parts = {'collide': [], 'clear': [], 'outside': [], 'wall': []}
    inner = w.inner_box()
    for (a, b), pr in pairs:
        off = w.O_T[a]
        kind = 'collide' if pr.state == COLLIDE else 'clear'
        # each patch travels with its own part; most are drawn as they are,
        # one made of long triangles is trimmed to a box around the contact
        for tris, own, clip in ((pr.tri_a, off, pr.clip_a), (pr.tri_b, w.O_T[b], pr.clip_b)):
            if tris is None or len(tris) == 0:
                continue
            if clip is None:
                tri_parts[kind].append((tris, own))
            else:
                clip_parts[kind].append((tris, own, clip[0] + off, clip[1] + off))
        if pr.segs is not None and len(pr.segs):
            line_parts['collide'].append((pr.segs, off))
        elif pr.pa is not None and pr.pb is not None and pr.dist > 0.0:
            seg = np.array([[pr.pa, pr.pb]], dtype=np.float64)
            line_parts[kind].append((seg, off))
    zero = np.zeros(3)
    for slot, r in oobs:
        if r.tris is not None and len(r.tris) and vol is not None:
            tri_parts['outside'].append((r.tris, zero))
        if r.state == OUTSIDE or r.band_only:
            line_parts['outside'].append((_box_edges(r.lo, r.hi), zero))
    for slot, r in walls:
        # drawn where it lies outside the inner box, i.e. inside the margin
        if r.tris is not None and len(r.tris) and inner is not None:
            tri_parts['wall'].append((r.tris, zero))
        if r.band_only:
            line_parts['wall'].append((_box_edges(r.lo, r.hi), zero))

    hatch = shaders['hatch']
    for kind, parts in tri_parts.items():
        pos = _tri_array(parts)
        if pos is None:
            continue
        g.tri[kind] = batch_for_shader(hatch if hatch is not None else shaders['flat'], 'TRIS',
                                       {"pos": pos})
        g.ntri += pos.shape[0] // 3
        g.empty = False
    for kind, parts in clip_parts.items():
        for tris, own, lo, hi in parts:
            pos = _tri_array([(tris, own)])
            batch = batch_for_shader(hatch if hatch is not None else shaders['flat'], 'TRIS',
                                     {"pos": pos})
            g.clipped.setdefault(kind, []).append((batch, lo, hi))
            g.ntri += pos.shape[0] // 3
            g.empty = False
    for kind, parts in line_parts.items():
        pos = _line_array(parts)
        if pos is None:
            continue
        g.line[kind] = batch_for_shader(shaders['line'], 'LINES', {"pos": pos})
        g.empty = False
    return g


def _groups(mon, st, shaders):
    """Static and hot batch groups for a monitor, rebuilt only when needed."""
    cache = _state.setdefault('groups', {})
    entry = cache.get(mon.scene_uid)
    if entry is None or entry.get('mon') is not mon:
        entry = {'mon': mon, 'static': _Group(), 'hot': _Group()}
        cache[mon.scene_uid] = entry
    w = mon.world
    hot_slots = mon.hot
    vol = w.volume
    stat_pairs, hot_pairs, stat_oob, hot_oob, stat_wall, hot_wall = [], [], [], [], [], []
    skey = [len(hot_slots), mon.cold_serial, w.wall_margin]
    for key, pr in w.viol.items():
        if key[0] in hot_slots or key[1] in hot_slots:
            hot_pairs.append((key, pr))
        else:
            stat_pairs.append((key, pr))
            skey.append(key)
            skey.append(pr.serial)
    for slot, r in w.oob.items():
        if slot in hot_slots:
            hot_oob.append((slot, r))
        else:
            stat_oob.append((slot, r))
            skey.append(slot)
            skey.append(r.serial)
    for slot, r in w.wall.items():
        if slot in hot_slots:
            hot_wall.append((slot, r))
        else:
            stat_wall.append((slot, r))
            skey.append(-1 - slot)
            skey.append(r.serial)
    skey = hash(tuple(skey))
    if entry['static'].key != skey:
        entry['static'] = _build_group(mon, stat_pairs, stat_oob, stat_wall, vol, shaders)
        entry['static'].key = skey
    hkey = (w.version, mon.move_serial, len(hot_pairs), len(hot_oob), len(hot_wall))
    if entry['hot'].key != hkey:
        entry['hot'] = _build_group(mon, hot_pairs, hot_oob, hot_wall, vol, shaders)
        entry['hot'].key = hkey
    return entry['static'], entry['hot']


# ---------------------------------------------------------------------------
# 3-D pass
# ---------------------------------------------------------------------------

def _rgba(color, alpha_scale=1.0):
    return (color[0], color[1], color[2], color[3] * alpha_scale)


def _draw_tris(shaders, batch, color, style, spacing, width, fill, vp, bias, xray, srgb,
               clip=None, outside=False):
    """Draw hatched triangles.  ``clip`` is an optional box (lo, hi): only the
    part inside it is drawn, or with ``outside`` only the part beyond it."""
    hatch = shaders['hatch']
    if hatch is None:
        sh = shaders['flat']
        sh.bind()
        if xray > 0.0:
            gpu.state.depth_test_set('NONE')
            sh.uniform_float("color", _rgba(color, 0.18 * xray))
            batch.draw(sh)
        gpu.state.depth_test_set('LESS_EQUAL')
        sh.uniform_float("color", _rgba(color, 0.4))
        batch.draw(sh)
        return
    hatch.bind()
    hatch.uniform_float("u_viewProj", vp)
    mode = 0.0 if clip is None else (2.0 if outside else 1.0)
    hatch.uniform_float("u_params", (spacing, width, mode, style))
    if clip is not None:
        hatch.uniform_float("u_clipLo", (clip[0][0], clip[0][1], clip[0][2], 0.0))
        hatch.uniform_float("u_clipHi", (clip[1][0], clip[1][1], clip[1][2], 0.0))
    if xray > 0.0:
        # hidden parts of the region: dimmer, drawn through everything
        gpu.state.depth_test_set('NONE')
        hatch.uniform_float("u_bias", (bias[0], bias[1], 0.0, 0.0))
        hatch.uniform_float("u_color", _rgba(color, xray))
        hatch.uniform_float("u_extra", (fill * 0.5, srgb, 0.0, 0.0))
        batch.draw(hatch)
    gpu.state.depth_test_set('LESS_EQUAL')
    hatch.uniform_float("u_bias", bias)
    hatch.uniform_float("u_color", color)
    hatch.uniform_float("u_extra", (fill, srgb, 0.0, 0.0))
    batch.draw(hatch)


def _draw_lines(shaders, batch, color, width, viewport, xray=True):
    sh = shaders['line']
    sh.bind()
    sh.uniform_float("viewportSize", viewport)
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    gpu.state.depth_test_set('NONE' if xray else 'LESS_EQUAL')
    batch.draw(sh)


def _colliding(mon):
    """Parts that collide with something, lightest first (cached per result version)."""
    cache = _state.setdefault('colliding', {})
    w = mon.world
    entry = cache.get(mon.scene_uid)
    if entry is None or entry[0] is not mon or entry[1] != w.version:
        slots = sorted(w.colliding_slots(), key=w.part_triangles)
        entry = (mon, w.version, slots)
        cache[mon.scene_uid] = entry
    return entry[2]


def _draw_tint(mon, p, shaders, persp_matrix, bias, srgb, viewport, ui, color):
    """Shade every colliding part so the parts at fault stand out.

    Parts are shaded lightest first until the triangle budget is used up; the
    rest get an outline box, so this pass cannot be what slows a viewport down.
    """
    slots = _colliding(mon)
    strength = p.tint_strength if p else 0.3
    if not slots or strength <= 0.0:
        return
    w = mon.world
    budget = int((p.tint_budget if p else 4.0) * 1e6)
    sh = shaders.get('tint')
    cache = _state.setdefault('tint', {})
    frame = _state['frame'] = _state.get('frame', 0) + 1
    boxed = []
    used = 0
    if sh is not None:
        P = np.array(persp_matrix, dtype=np.float64)
        sh.bind()
        sh.uniform_float("u_color", (color[0], color[1], color[2], 0.5 * strength))
        sh.uniform_float("u_bias", bias)
        sh.uniform_float("u_extra", (0.0, srgb, 0.0, 0.0))
        gpu.state.depth_test_set('LESS_EQUAL')
    for slot in slots:
        n = w.part_triangles(slot)
        if sh is None or used + n > budget:
            boxed.append(slot)
            continue
        used += n
        key, verts, tris = w.part_mesh(slot)
        entry = cache.get(key)
        if entry is None:
            batch = batch_for_shader(sh, 'TRIS', {"pos": verts}, indices=np.ascontiguousarray(tris))
            entry = cache[key] = [batch, n, frame]
        entry[2] = frame
        sh.uniform_float("u_mvp", Matrix((P @ w.part_matrix(slot)).tolist()))
        entry[0].draw(sh)
    if boxed:
        segs = np.concatenate([_box_edges(*w.part_bounds(s)) for s in boxed]).reshape(-1, 3)
        batch = batch_for_shader(shaders['line'], 'LINES', {"pos": np.ascontiguousarray(segs)})
        _draw_lines(shaders, batch, _rgba(color, 0.9), 1.5 * ui, viewport, xray=False)
    # forget meshes that have not been needed for a while once the cache is large
    if len(cache) > 8 and sum(e[1] for e in cache.values()) > 3 * budget:
        for key in sorted(cache, key=lambda k: cache[k][2])[:len(cache) // 2]:
            if cache[key][2] != frame:
                del cache[key]


def _volume_batches(mon, st, shaders):
    """(edges, faces, floor, margin edges or None) of the build volume."""
    lo, hi = mon.volume_box(st)
    margin = mon.margin_box(st)
    key = (tuple(lo), tuple(hi), None if margin is None else (tuple(margin[0]), tuple(margin[1])))
    cache = _state.setdefault('volume', {})
    entry = cache.get(mon.scene_uid)
    if entry is None or entry[0] != key:
        edges = batch_for_shader(shaders['line'], 'LINES',
                                 {"pos": np.ascontiguousarray(_box_edges(lo, hi).reshape(24, 3))})
        inner = None
        if margin is not None:
            inner = batch_for_shader(
                shaders['line'], 'LINES',
                {"pos": np.ascontiguousarray(_box_edges(margin[0], margin[1]).reshape(24, 3))})
        faces = batch_for_shader(shaders['flat'], 'TRIS', {"pos": _box_faces(lo, hi)})
        floor = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]],
                          [lo[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]]],
                         dtype=np.float32)
        floor_b = batch_for_shader(shaders['flat'], 'TRIS', {"pos": floor})
        entry = (key, edges, faces, floor_b, inner)
        cache[mon.scene_uid] = entry
    return entry[1], entry[2], entry[3], entry[4]


def draw_scene(mon, st, p, persp_matrix, window_matrix, view_distance, viewport, ui,
               srgb_target=True):
    """Draw the 3-D overlay for one monitor with explicit view matrices.

    Kept free of ``bpy.context`` so it can also render into an off-screen
    buffer (used by the automated tests).  ``srgb_target`` says whether the
    bound framebuffer is sRGB encoded, as the viewport's overlay buffer is.
    """
    srgb = 1.0 if srgb_target else 0.0
    shaders = _shaders()
    col_c = tuple(p.color_collision) if p else (1.0, 0.08, 0.05, 1.0)
    col_w = tuple(p.color_clearance) if p else (1.0, 0.72, 0.0, 1.0)
    col_o = tuple(p.color_outside) if p else (0.95, 0.1, 0.85, 1.0)
    col_v = tuple(p.color_volume) if p else (0.35, 0.75, 1.0, 1.0)
    spacing = (p.hatch_spacing if p else 9.0) * ui
    xray = p.xray if p else 0.45
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_mask_set(False)
        gpu.state.face_culling_set('NONE')

        w = mon.world
        counts = w.counts()
        if st.show_volume:
            edges, faces, floor, margin = _volume_batches(mon, st, shaders)
            bad = st.use_volume and (counts[2] or counts[3])
            vc = col_o if bad else col_v
            gpu.state.depth_test_set('LESS_EQUAL')
            # The viewport blends in linear light, where a little alpha goes a
            # long way on a dark background: keep the tint faint so the parts
            # and the problem colours stay readable.
            fill = p.volume_fill if p else 0.25
            if fill > 0.0:
                flat = shaders['flat']
                flat.bind()
                flat.uniform_float("color", (vc[0], vc[1], vc[2], 0.02 * fill))
                faces.draw(flat)
                flat.uniform_float("color", (vc[0], vc[1], vc[2], 0.05 * fill))
                floor.draw(flat)
            _draw_lines(shaders, edges, (vc[0], vc[1], vc[2], 0.25), 1.0 * ui, viewport, xray=True)
            _draw_lines(shaders, edges, (vc[0], vc[1], vc[2], 0.9), 1.6 * ui, viewport, xray=False)
            if margin is not None:
                # the limit parts should stay inside of: amber once something crosses it
                mc = col_w if counts[4] else col_v
                _draw_lines(shaders, margin, (mc[0], mc[1], mc[2], 0.16), 1.0 * ui, viewport,
                            xray=True)
                _draw_lines(shaders, margin, (mc[0], mc[1], mc[2], 0.5), 1.0 * ui, viewport,
                            xray=False)

        if st.show_overlay:
            persp = 1.0 if window_matrix[3][3] == 0.0 else 0.0
            # How far the overlay is pulled towards the viewer to sit on top of
            # the surface it marks.  With a very small clip start the depth
            # buffer is coarse at working distance, so the pull grows with it.
            rel = 0.0015
            if persp:
                a, b = window_matrix[2][2], window_matrix[2][3]
                near = abs(b / (a - 1.0)) if a != 1.0 else 0.0
                if near > 0.0:
                    rel = min(0.02, max(rel, 6.0 * view_distance / (near * 16777216.0)))
            bias = (persp, window_matrix[2][2], rel, 0.004 * max(view_distance, 1e-9))
            lw = 1.3 * ui
            if st.show_tint:
                _draw_tint(mon, p, shaders, persp_matrix,
                           (bias[0], bias[1], 0.4 * bias[2], 0.4 * bias[3]), srgb, viewport, ui,
                           col_c)
            groups = _groups(mon, st, shaders)
            for g in groups:
                if g.empty:
                    continue
                b = g.tri.get('clear')
                if b is not None:
                    _draw_tris(shaders, b, col_w, STYLE_DIAG, spacing, lw, 0.10,
                               persp_matrix, bias, xray, srgb)
                for b, lo, hi in g.clipped.get('clear', ()):
                    _draw_tris(shaders, b, col_w, STYLE_DIAG, spacing, lw, 0.10,
                               persp_matrix, bias, xray, srgb, (lo, hi))
                b = g.tri.get('wall')
                if b is not None:
                    # only what lies beyond the inner box, i.e. inside the margin
                    _draw_tris(shaders, b, col_w, STYLE_BACK, spacing, lw, 0.10,
                               persp_matrix, bias, xray, srgb, w.inner_box(), True)
                b = g.tri.get('outside')
                if b is not None:
                    # only what lies beyond the walls
                    _draw_tris(shaders, b, col_o, STYLE_BACK, spacing * 0.8, lw, 0.16,
                               persp_matrix, bias, xray, srgb, w.volume, True)
                b = g.tri.get('collide')
                if b is not None:
                    _draw_tris(shaders, b, col_c, STYLE_CROSS, spacing, lw, 0.16,
                               persp_matrix, bias, xray, srgb)
                for b, lo, hi in g.clipped.get('collide', ()):
                    _draw_tris(shaders, b, col_c, STYLE_CROSS, spacing, lw, 0.16,
                               persp_matrix, bias, xray, srgb, (lo, hi))
            for g in groups:
                if g.empty:
                    continue
                b = g.line.get('outside')
                if b is not None:
                    _draw_lines(shaders, b, _rgba(col_o, 0.8), 1.5 * ui, viewport)
                b = g.line.get('wall')
                if b is not None:
                    _draw_lines(shaders, b, _rgba(col_w, 0.8), 1.5 * ui, viewport)
                b = g.line.get('clear')
                if b is not None:
                    _draw_lines(shaders, b, col_w, 2.2 * ui, viewport)
                b = g.line.get('collide')
                if b is not None:
                    _draw_lines(shaders, b, col_c, 2.8 * ui, viewport)
    finally:
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set('NONE')


def draw_view():
    """POST_VIEW callback."""
    context = bpy.context
    scene = context.scene
    st = props.settings(scene)
    if st is None or not st.enabled:
        return
    mon = monitor.get(scene)
    rv3d = context.region_data
    if mon is None or rv3d is None:
        return
    t0 = time.perf_counter()
    try:
        draw_scene(mon, st, props.prefs(context), rv3d.perspective_matrix, rv3d.window_matrix,
                   rv3d.view_distance, gpu.state.viewport_get()[2:],
                   context.preferences.system.ui_scale)
    except Exception:
        import traceback
        traceback.print_exc()
    mon.draw_ms.append((time.perf_counter() - t0) * 1000.0)


# ---------------------------------------------------------------------------
# 2-D pass
# ---------------------------------------------------------------------------

def _circle(cx, cy, r, n=28):
    a = np.linspace(0.0, 2.0 * math.pi, n + 1)
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)


def _fan(cx, cy, r, n=40):
    ring = _circle(cx, cy, r, n)
    tri = np.empty((n, 3, 2), dtype=np.float32)
    tri[:, 0] = (cx, cy)
    tri[:, 1] = ring[:-1]
    tri[:, 2] = ring[1:]
    return tri.reshape(n * 3, 2)


def _ring_template(n=24, inner=0.74):
    """Triangles of a unit ring (outer radius 1), shape (6 n, 2)."""
    a = np.linspace(0.0, 2.0 * math.pi, n + 1)
    c = np.stack([np.cos(a), np.sin(a)], axis=1)
    o0, o1 = c[:-1], c[1:]
    i0, i1 = o0 * inner, o1 * inner
    tri = np.stack([o0, i0, i1, o0, i1, o1], axis=1)
    return tri.reshape(n * 6, 2).astype(np.float32)


def _rings(px, py, radius, inner=0.74):
    """Many rings at once: centres (k,), radius scalar or (k,) -> (k * T, 2)."""
    tpl = _state.get(('ring', inner))
    if tpl is None:
        tpl = _ring_template(24, inner)
        _state[('ring', inner)] = tpl
    cen = np.stack([px, py], axis=1).astype(np.float32)
    rad = np.broadcast_to(np.asarray(radius, dtype=np.float32), (cen.shape[0],))
    out = cen[:, None, :] + rad[:, None, None] * tpl[None, :, :]
    return np.ascontiguousarray(out.reshape(-1, 2))


def _stroke(points, width):
    """Thick polyline as triangles (with round joints)."""
    pts = np.asarray(points, dtype=np.float32)
    out = []
    for i in range(len(pts) - 1):
        a = pts[i]
        b = pts[i + 1]
        d = b - a
        ln = float(np.hypot(d[0], d[1])) or 1.0
        nrm = np.array([-d[1], d[0]], dtype=np.float32) / ln * (0.5 * width)
        out.append(np.array([a + nrm, a - nrm, b - nrm, a + nrm, b - nrm, b + nrm], dtype=np.float32))
    for q in pts:
        out.append(_fan(float(q[0]), float(q[1]), 0.5 * width, 12))
    return np.concatenate(out)


def _text(x, y, text, size, color, center=False):
    font = 0
    try:
        blf.size(font, size)
    except TypeError:               # pre-4.0 signature
        blf.size(font, int(size), 72)
    if center:
        tw, _ = blf.dimensions(font, text)
        x -= 0.5 * tw
    blf.color(font, color[0], color[1], color[2], color[3] if len(color) > 3 else 1.0)
    blf.position(font, x, y, 0)
    blf.draw(font, text)


def _text_shadow(on):
    try:
        if on:
            blf.enable(0, blf.SHADOW)
            blf.shadow(0, 3, 0.0, 0.0, 0.0, 0.9)
            blf.shadow_offset(0, 1, -1)
        else:
            blf.disable(0, blf.SHADOW)
    except Exception:
        pass


def badge_text(status):
    """(state, headline, detail lines) for the status badge and the panel.

    state is 'BUSY', 'OK', 'WARN' (pass, but with clearance warnings) or 'FAIL'.
    """
    nc = status['collisions']
    nout = status['partly_out'] + status['outside']
    ncl = status['clearance']
    nw = status['near_wall']
    lines = []
    if status['preparing']:
        done, total = status['preparing']
        return 'BUSY', "Preparing parts", [f"{done} of {total} read"]
    if nc or nout:
        if nc:
            lines.append(f"{nc} collision" + ("s" if nc != 1 else ""))
        if nout:
            lines.append(f"{nout} part" + ("s" if nout != 1 else "") + " outside the volume")
        if ncl:
            lines.append(f"{ncl} clearance warning" + ("s" if ncl != 1 else ""))
        if nw:
            lines.append(f"{nw} part" + ("s" if nw != 1 else "") + " close to a wall")
        if status['busy']:
            lines.append("still checking...")
        return 'FAIL', "Build has problems", lines
    if status['busy']:
        return 'BUSY', "Checking", [f"{status['pending']} pairs to go"] if status['pending'] else []
    n = status['objects']
    if n == 0:
        return 'BUSY', "No parts", ["no visible mesh objects"]
    # short lines: the same text has to fit the sidebar
    lines.append(f"{n} part" + ("s" if n != 1 else "") + ", no collisions")
    if status['volume']:
        lines.append("all inside the build volume")
    if ncl:
        lines.append(f"{ncl} clearance warning" + ("s" if ncl != 1 else ""))
    if nw:
        lines.append(f"{nw} part" + ("s" if nw != 1 else "") + " close to a wall")
    return ('WARN' if ncl or nw else 'OK'), "Build OK", lines


def _badge_shapes(state, cx, cy, r, flat):
    """Batches of the badge icon, cached: [(colour key, batch), ...]."""
    key = ('badge', state, round(cx), round(cy), round(r, 1))
    shapes = _state.get(key)
    if shapes is not None:
        return shapes
    for k in [k for k in _state if isinstance(k, tuple) and k and k[0] == 'badge']:
        del _state[k]

    def mk(verts):
        return batch_for_shader(flat, 'TRIS', {"pos": np.ascontiguousarray(verts, dtype=np.float32)})

    pen = 0.2 * r
    shapes = [('back', mk(_fan(cx, cy, 1.16 * r)))]
    shapes.append(('main', mk(_rings(np.array([cx]), np.array([cy]), r, 0.9))))
    if state in ('OK', 'WARN'):
        mark = [(cx - 0.45 * r, cy + 0.02 * r), (cx - 0.12 * r, cy - 0.32 * r),
                (cx + 0.48 * r, cy + 0.36 * r)]
        shapes.append(('main', mk(_stroke(mark, pen))))
    elif state == 'FAIL':
        k = 0.36 * r
        shapes.append(('main', mk(_stroke([(cx - k, cy - k), (cx + k, cy + k)], pen))))
        shapes.append(('main', mk(_stroke([(cx - k, cy + k), (cx + k, cy - k)], pen))))
    else:
        dots = np.concatenate([_fan(cx + i * 0.36 * r, cy, 0.11 * r, 14) for i in (-1, 0, 1)])
        shapes.append(('main', mk(dots)))
    if state == 'WARN':
        shapes.append(('warn', mk(_fan(cx + 0.78 * r, cy + 0.78 * r, 0.27 * r, 20))))
    _state[key] = shapes
    return shapes


def _text_width(text, size):
    try:
        blf.size(0, size)
    except TypeError:               # pre-4.0 signature
        blf.size(0, int(size), 72)
    return blf.dimensions(0, text)[0]


def _draw_badge(mon, st, p, shaders, ui, cx):
    """The pass / fail badge: a large icon with the verdict next to it,
    centred at the bottom of the viewport."""
    status = mon.status()
    state, head, lines = badge_text(status)
    if status['skipped']:
        lines = lines + [f"{status['skipped']} object(s) not checked"]
    lines = lines[:5]
    green = (0.25, 0.85, 0.35, 1.0)
    red = tuple(p.color_collision) if p else (1.0, 0.08, 0.05, 1.0)
    amber = tuple(p.color_clearance) if p else (1.0, 0.72, 0.0, 1.0)
    grey = (0.75, 0.75, 0.75, 1.0)
    color = {'OK': green, 'WARN': green, 'FAIL': red, 'BUSY': grey}[state]

    k = ui * (p.badge_scale if p else 1.0)
    r = 38.0 * k
    head_size = 26.0 * k
    body_size = 13.0 * k
    line_h = 17.0 * k
    gap = 16.0 * k
    block_h = head_size + (len(lines) * line_h + 3.0 * k if lines else 0.0)
    text_w = max([_text_width(head, head_size)] + [_text_width(t, body_size) for t in lines])
    left = cx - 0.5 * (2.0 * r + gap + text_w)
    icx = left + r
    cy = 22.0 * ui + max(1.16 * r, 0.5 * block_h)     # nothing may fall below the region

    flat = shaders['flat']
    flat.bind()
    gpu.state.blend_set('ALPHA')
    palette = {'back': (0.0, 0.0, 0.0, 0.55), 'main': color, 'warn': amber}
    for ckey, batch in _badge_shapes(state, icx, cy, r, flat):
        flat.uniform_float("color", palette[ckey])
        batch.draw(flat)
    if state == 'WARN':
        _text(icx + 0.78 * r, cy + 0.78 * r - 0.17 * r, "!", 0.46 * r, (0, 0, 0, 1), center=True)

    _text_shadow(True)
    tx = left + 2.0 * r + gap
    y = cy + 0.5 * block_h - 0.8 * head_size
    _text(tx, y, head, head_size, color)
    y -= 3.0 * k
    for line in lines:
        y -= line_h
        lc = amber if ('clearance' in line or 'wall' in line) else (0.92, 0.92, 0.92, 1.0)
        _text(tx, y, line, body_size, lc)
    _text_shadow(False)


def _draw_markers(mon, st, p, shaders, ui, persp_matrix, width, height):
    problems = mon.problems()
    if not problems:
        return
    n = min(len(problems), MAX_MARKERS)
    pts = np.ones((n, 4))
    for i in range(n):
        pts[i, :3] = problems[i]['center']
    clip = pts @ np.array(persp_matrix).T
    wv = clip[:, 3]
    ok = wv > 1e-9
    ndc = clip[:, :2] / np.where(ok, wv, 1.0)[:, None]
    px = (ndc[:, 0] * 0.5 + 0.5) * width
    py = (ndc[:, 1] * 0.5 + 0.5) * height
    red = tuple(p.color_collision) if p else (1.0, 0.08, 0.05, 1.0)
    amber = tuple(p.color_clearance) if p else (1.0, 0.72, 0.0, 1.0)
    mag = tuple(p.color_outside) if p else (0.95, 0.1, 0.85, 1.0)
    colors = {'COLLIDE': red, 'CLEAR': amber, 'WALL': amber, 'PARTIAL': mag, 'OUTSIDE': mag}
    flat = shaders['flat']
    gpu.state.blend_set('ALPHA')
    active = st.problem_index
    kinds = np.array([pr['kind'] for pr in problems[:n]])
    radius = np.full(n, 7.0 * ui, dtype=np.float32)
    if 0 <= active < n:
        radius[active] = 11.0 * ui
    flat.bind()
    for kind in ('WALL', 'CLEAR', 'OUTSIDE', 'PARTIAL', 'COLLIDE'):
        m = ok & (kinds == kind)
        if not m.any():
            continue
        flat.uniform_float("color", colors[kind])
        batch_for_shader(flat, 'TRIS', {"pos": _rings(px[m], py[m], radius[m])}).draw(flat)
    _text_shadow(True)
    for i in range(n):
        if not ok[i]:
            continue
        pr = problems[i]
        if pr['kind'] == 'CLEAR':
            label = f"{pr['dist_mm']:.2f} mm"
        elif pr['kind'] == 'WALL':
            label = f"{pr['dist_mm']:.2f} mm to wall"
        elif i == active:
            label = {'COLLIDE': "collision", 'PARTIAL': "outside volume",
                     'OUTSIDE': "outside volume"}[pr['kind']]
        else:
            continue
        _text(px[i] + 12.0 * ui, py[i] - 4.0 * ui, label, 12.0 * ui, colors[pr['kind']])
    _text_shadow(False)


def draw_hud(mon, st, p, persp_matrix, width, height, center_x, ui):
    """Draw markers, labels and the status badge (2-D, pixel coordinates)."""
    shaders = _shaders()
    try:
        if st.show_overlay and st.show_labels:
            _draw_markers(mon, st, p, shaders, ui, persp_matrix, width, height)
        if st.show_hud:
            _draw_badge(mon, st, p, shaders, ui, center_x)
    finally:
        gpu.state.blend_set('NONE')


def draw_pixel():
    """POST_PIXEL callback."""
    context = bpy.context
    scene = context.scene
    st = props.settings(scene)
    if st is None or not st.enabled:
        return
    mon = monitor.get(scene)
    rv3d = context.region_data
    if mon is None or rv3d is None:
        return
    region = context.region
    cx = region.width * 0.5
    try:
        # with overlapping side regions the usable middle is shifted
        if context.preferences.system.use_region_overlap and context.area is not None:
            for reg in context.area.regions:
                if reg.width <= 1:
                    continue
                if reg.type == 'TOOLS' and reg.alignment == 'LEFT':
                    cx += 0.5 * reg.width
                elif reg.type == 'UI' and reg.alignment == 'RIGHT':
                    cx -= 0.5 * reg.width
        draw_hud(mon, st, props.prefs(context), rv3d.perspective_matrix, region.width,
                 region.height, cx, context.preferences.system.ui_scale)
    except Exception:
        import traceback
        traceback.print_exc()


def register():
    if _handles:
        return
    sv = bpy.types.SpaceView3D
    _handles.append((sv.draw_handler_add(draw_view, (), 'WINDOW', 'POST_VIEW'), 'WINDOW'))
    _handles.append((sv.draw_handler_add(draw_pixel, (), 'WINDOW', 'POST_PIXEL'), 'WINDOW'))


def unregister():
    sv = bpy.types.SpaceView3D
    for handle, region in _handles:
        try:
            sv.draw_handler_remove(handle, region)
        except Exception:
            pass
    _handles.clear()
    _state.clear()
