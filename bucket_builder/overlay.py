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

A viewport in local view shows only the problems among the parts it shows
(``only``); the badge always speaks for the whole build.  Parts that are
checked although they are hidden get no shading and no hatching (the user
hid them to see past them), but the curve, the closest-distance line and the
marker of a problem with them are drawn.
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
MAX_LABELS = 12      # with more distances than this, only those of the parts being edited

# styles understood by the hatch fragment shader
STYLE_CROSS, STYLE_DIAG, STYLE_BACK, STYLE_SOLID = 0.0, 1.0, 2.0, 3.0

# The constants of the hatch shader are packed into 128 bytes, the size every
# graphics backend passes directly:
#   u_a = (hatch spacing, code, fill, k1)      code = style + 4 * clip mode + 16 * sRGB target
#   u_b = (clip box low corner, k2)            clip mode 0: draw everything,
#   u_c = (clip box high corner, k3)                     1: only inside the box, 2: only outside
# k1..k3 pull the surface slightly towards the viewer so that it does not
# z-fight with the mesh it lies on: z' = (z + k1 * w) * k2 + k3.
_VERT = """
void main()
{
  v_pos = pos;
  vec4 p = u_viewProj * vec4(pos, 1.0);
  p.z = (p.z + u_a.w * p.w) * u_b.w + u_c.w;
  gl_Position = p;
}
"""

_FRAG = """
void main()
{
  float code = u_a.y;
  float srgb = floor(code / 16.0);
  code -= 16.0 * srgb;
  float mode = floor(code / 4.0);
  float style = code - 4.0 * mode;
  if (mode > 0.5) {
    bool inside = all(greaterThanEqual(v_pos, u_b.xyz)) && all(lessThanEqual(v_pos, u_c.xyz));
    if (inside == (mode > 1.5)) {
      discard;
    }
  }
  /* The spacing is a whole number of pixels and so is the line width: with
   * anything else the lines come out unevenly thick and the pattern shimmers.
   * (Pixel centres are at halves, so both sums below are whole numbers.) */
  float s = u_a.x;
  float w = max(1.0, floor(0.145 * s + 0.5));
  float d1 = mod(floor(gl_FragCoord.x) + floor(gl_FragCoord.y), s);
  float d2 = mod(floor(gl_FragCoord.x) - floor(gl_FragCoord.y) + 16384.0 * s, s);
  float l1 = (d1 < w - 0.5) ? 1.0 : 0.0;
  float l2 = (d2 < w - 0.5) ? 1.0 : 0.0;
  float cov = 1.0;
  if (style < 0.5) {
    cov = max(l1, l2);
  }
  else if (style < 1.5) {
    cov = l1;
  }
  else if (style < 2.5) {
    cov = l2;
  }
  float alpha = u_color.a * max(cov, u_a.z);
  if (alpha < 0.004) {
    discard;
  }
  vec3 c = u_color.rgb;
  if (srgb > 0.5) {
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
  p.z = (p.z + u_bias.x * p.w) * u_bias.y + u_bias.z;      /* k1, k2, k3 as above */
  gl_Position = p;
}
"""

_TINT_FRAG = """
void main()
{
  vec3 c = u_color.rgb;
  if (u_bias.w > 0.5) {                                    /* sRGB target */
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
    info.push_constant('VEC4', "u_a")
    info.push_constant('VEC4', "u_b")
    info.push_constant('VEC4', "u_c")
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


def _build_group(mon, pairs, oobs, walls, vol, shaders, hidden):
    """Create the batches for the given problems.  ``hidden``: slots of parts
    that are checked although the user has hidden them; their surfaces are
    left alone."""
    w = mon.world
    g = _Group()
    tri_parts = {'collide': [], 'clear': [], 'outside': [], 'wall': []}
    clip_parts = {'collide': [], 'clear': []}
    # lines: 'collide' the curves along which parts cut through each other,
    # 'clear' the closest points of parts that are too close, 'inside' the
    # box around a part that lies inside another one
    line_parts = {'collide': [], 'clear': [], 'inside': [], 'outside': [], 'wall': []}
    inner = w.inner_box()
    for (a, b), pr in pairs:
        off = w.O_T[a]
        kind = 'collide' if pr.state == COLLIDE else 'clear'
        # each patch travels with its own part; most are drawn as they are,
        # one made of long triangles is trimmed to a box around the contact
        for slot, tris, own, clip in ((a, pr.tri_a, off, pr.clip_a), (b, pr.tri_b, w.O_T[b], pr.clip_b)):
            if tris is None or len(tris) == 0 or slot in hidden:
                continue
            if clip is None:
                tri_parts[kind].append((tris, own))
            else:
                clip_parts[kind].append((tris, own, clip[0] + off, clip[1] + off))
        if pr.segs is not None and len(pr.segs):
            # the intersection curve; for a part that lies inside another one,
            # its outline box, which is not an outline to switch off
            line_parts['inside' if pr.enclosed else 'collide'].append((pr.segs, off))
        elif pr.pa is not None and pr.pb is not None and pr.dist > 0.0:
            seg = np.array([[pr.pa, pr.pb]], dtype=np.float64)    # the closest points
            line_parts['inside' if pr.state == COLLIDE else 'clear'].append((seg, off))
    zero = np.zeros(3)
    for slot, r in oobs:
        shown = slot not in hidden
        if r.tris is not None and len(r.tris) and vol is not None and shown:
            tri_parts['outside'].append((r.tris, zero))
        if r.state == OUTSIDE or r.band_only or r.tris is None or not shown:
            line_parts['outside'].append((_box_edges(r.lo, r.hi), zero))
    for slot, r in walls:
        shown = slot not in hidden
        # drawn where it lies outside the inner box, i.e. inside the margin
        if r.tris is not None and len(r.tris) and inner is not None and shown:
            tri_parts['wall'].append((r.tris, zero))
        if r.band_only or not shown:
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


def _shown(a, b, only, hidden):
    """Whether a viewport in local view shows the problem between two parts:
    it does if both are in it, or if one is and the other is hidden everywhere
    (and checked all the same)."""
    if a in only:
        return b in only or b in hidden
    return b in only and a in hidden


def _groups(mon, shaders, only=None, view=0):
    """Static and hot batch groups for a monitor, rebuilt only when needed.
    ``only``: the slots a viewport in local view shows (``view`` tells such
    viewports apart); problems with any other part are left out."""
    cache = _state.setdefault('groups', {})
    ckey = (mon.scene_uid, view)
    entry = cache.get(ckey)
    if entry is None or entry.get('mon') is not mon:
        if len(cache) > 16:
            cache.clear()                 # viewports that went away
        entry = {'mon': mon, 'static': _Group(), 'hot': _Group()}
        cache[ckey] = entry
    w = mon.world
    hot_slots = mon.hot
    hidden = mon.hidden_slots()
    vol = w.volume
    stat_pairs, hot_pairs, stat_oob, hot_oob, stat_wall, hot_wall = [], [], [], [], [], []
    skey = [len(hot_slots), mon.cold_serial, w.wall_margin,
            0 if only is None else hash(frozenset(only))]
    for key, pr in w.viol.items():
        if only is not None and not _shown(key[0], key[1], only, hidden):
            continue
        if key[0] in hot_slots or key[1] in hot_slots:
            hot_pairs.append((key, pr))
        else:
            stat_pairs.append((key, pr))
            skey.append(key)
            skey.append(pr.serial)
    for slot, r in w.oob.items():
        if only is not None and slot not in only:
            continue
        if slot in hot_slots:
            hot_oob.append((slot, r))
        else:
            stat_oob.append((slot, r))
            skey.append(slot)
            skey.append(r.serial)
    for slot, r in w.wall.items():
        if only is not None and slot not in only:
            continue
        if slot in hot_slots:
            hot_wall.append((slot, r))
        else:
            stat_wall.append((slot, r))
            skey.append(-1 - slot)
            skey.append(r.serial)
    skey = hash(tuple(skey))
    if entry['static'].key != skey:
        entry['static'] = _build_group(mon, stat_pairs, stat_oob, stat_wall, vol, shaders, hidden)
        entry['static'].key = skey
    hkey = (w.version, mon.move_serial, len(hot_pairs), len(hot_oob), len(hot_wall), skey)
    if entry['hot'].key != hkey:
        entry['hot'] = _build_group(mon, hot_pairs, hot_oob, hot_wall, vol, shaders, hidden)
        entry['hot'].key = hkey
    return entry['static'], entry['hot']


# ---------------------------------------------------------------------------
# 3-D pass
# ---------------------------------------------------------------------------

def _rgba(color, alpha_scale=1.0):
    return (color[0], color[1], color[2], color[3] * alpha_scale)


def _draw_tris(shaders, batch, color, style, spacing, fill, vp, bias, xray, srgb,
               clip=None, outside=False):
    """Draw hatched triangles.  ``clip`` is an optional box (lo, hi): only the
    part inside it is drawn, or with ``outside`` only the part beyond it.
    ``bias`` is (k1, k2, k3), see the shader."""
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
    code = style + (0.0 if clip is None else (8.0 if outside else 4.0)) + 16.0 * srgb
    lo = clip[0] if clip is not None else (0.0, 0.0, 0.0)
    hi = clip[1] if clip is not None else (0.0, 0.0, 0.0)
    if xray > 0.0:
        # hidden parts of the region: dimmer, drawn through everything
        gpu.state.depth_test_set('NONE')
        hatch.uniform_float("u_color", _rgba(color, xray))
        hatch.uniform_float("u_a", (spacing, code, fill * 0.5, 0.0))
        hatch.uniform_float("u_b", (lo[0], lo[1], lo[2], 1.0))
        hatch.uniform_float("u_c", (hi[0], hi[1], hi[2], 0.0))
        batch.draw(hatch)
    gpu.state.depth_test_set('LESS_EQUAL')
    hatch.uniform_float("u_color", color)
    hatch.uniform_float("u_a", (spacing, code, fill, bias[0]))
    hatch.uniform_float("u_b", (lo[0], lo[1], lo[2], bias[1]))
    hatch.uniform_float("u_c", (hi[0], hi[1], hi[2], bias[2]))
    batch.draw(hatch)


def _draw_lines(shaders, batch, color, width, viewport, xray=True):
    sh = shaders['line']
    sh.bind()
    sh.uniform_float("viewportSize", viewport)
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    gpu.state.depth_test_set('NONE' if xray else 'LESS_EQUAL')
    batch.draw(sh)


def _colliding(mon, only=None, view=0):
    """The visible parts that collide with something, lightest first (cached
    per result version).  In local view: with something that is shown."""
    cache = _state.setdefault('colliding', {})
    w = mon.world
    ckey = (mon.scene_uid, view)
    stamp = (w.version, mon.move_serial if only is not None else 0,
             0 if only is None else hash(frozenset(only)), mon.cold_serial)
    entry = cache.get(ckey)
    if entry is None or entry[0] is not mon or entry[1] != stamp:
        hidden = mon.hidden_slots()
        slots = set()
        for key, pr in w.viol.items():
            if pr.state != COLLIDE or (only is not None
                                       and not _shown(key[0], key[1], only, hidden)):
                continue
            slots.update(key)
        slots = sorted((s for s in slots if s not in hidden), key=w.part_triangles)
        if len(cache) > 16:
            cache.clear()
        entry = (mon, stamp, slots)
        cache[ckey] = entry
    return entry[2]


def _draw_tint(mon, p, shaders, persp_matrix, bias, srgb, viewport, ui, color, only=None, view=0):
    """Shade every colliding part so the parts at fault stand out.

    Parts are shaded lightest first until the triangle budget is used up; the
    rest get an outline box, so this pass cannot be what slows a viewport down.
    Returns the number of triangles drawn.
    """
    slots = _colliding(mon, only, view)
    strength = p.tint_strength if p else 0.5
    if not slots or strength <= 0.0:
        return 0
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
        sh.uniform_float("u_bias", (bias[0], bias[1], bias[2], srgb))
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
    return used


# corners of each wall of a box, in the order x low, y low, z low, x high, y high, z high
_WALLS = ((0, 3, 7, 4), (0, 1, 5, 4), (0, 1, 2, 3), (1, 2, 6, 5), (3, 2, 6, 7), (4, 5, 6, 7))


def _box_corners(lo, hi):
    return np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]],
                     [lo[0], hi[1], lo[2]], [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
                     [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]], dtype=np.float32)


def _wall_batches(lo, hi, shaders):
    """For every wall of a box: (its outline as lines, its face as triangles)."""
    c = _box_corners(lo, hi)
    out = []
    for q in _WALLS:
        edge = c[[q[0], q[1], q[1], q[2], q[2], q[3], q[3], q[0]]]
        face = c[[q[0], q[1], q[2], q[0], q[2], q[3]]]
        out.append((batch_for_shader(shaders['line'], 'LINES', {"pos": np.ascontiguousarray(edge)}),
                    batch_for_shader(shaders['flat'], 'TRIS', {"pos": np.ascontiguousarray(face)})))
    return out


def _volume_batches(mon, st, shaders):
    """Batches of the build volume: its edges, its faces, its floor, the edges
    of the wall-gap box (or None), and both boxes wall by wall."""
    lo, hi = mon.volume_box(st)
    margin = mon.margin_box(st)
    key = (tuple(lo), tuple(hi), None if margin is None else (tuple(margin[0]), tuple(margin[1])))
    cache = _state.setdefault('volume', {})
    entry = cache.get(mon.scene_uid)
    if entry is None or entry['key'] != key:
        entry = {'key': key}
        entry['edges'] = batch_for_shader(
            shaders['line'], 'LINES', {"pos": np.ascontiguousarray(_box_edges(lo, hi).reshape(24, 3))})
        entry['faces'] = batch_for_shader(shaders['flat'], 'TRIS', {"pos": _box_faces(lo, hi)})
        floor = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]],
                          [lo[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]]],
                         dtype=np.float32)
        entry['floor'] = batch_for_shader(shaders['flat'], 'TRIS', {"pos": floor})
        entry['walls'] = _wall_batches(lo, hi, shaders)
        entry['inner'] = None
        entry['inner_walls'] = None
        if margin is not None:
            entry['inner'] = batch_for_shader(
                shaders['line'], 'LINES',
                {"pos": np.ascontiguousarray(_box_edges(margin[0], margin[1]).reshape(24, 3))})
            entry['inner_walls'] = _wall_batches(margin[0], margin[1], shaders)
        cache[mon.scene_uid] = entry
    return entry


def _draw_volume(mon, st, p, shaders, viewport, ui, col_v, col_o, col_w, only=None):
    """The build volume.  It keeps its own colour; only the walls something
    goes through turn to the out-of-volume colour, and only the sides of the
    wall-gap box that something is too close to turn to the warning colour."""
    v = _volume_batches(mon, st, shaders)
    out, near = mon.walls(only) if st.use_volume else ((False,) * 6, (False,) * 4)
    flat = shaders['flat']
    gpu.state.depth_test_set('LESS_EQUAL')
    # The viewport blends in linear light, where a little alpha goes a long
    # way on a dark background: keep the tint faint so the parts and the
    # problem colours stay readable.
    fill = p.volume_fill if p else 0.25
    if fill > 0.0:
        flat.bind()
        flat.uniform_float("color", (col_v[0], col_v[1], col_v[2], 0.02 * fill))
        v['faces'].draw(flat)
        flat.uniform_float("color", (col_v[0], col_v[1], col_v[2], 0.05 * fill))
        v['floor'].draw(flat)
    _draw_lines(shaders, v['edges'], (col_v[0], col_v[1], col_v[2], 0.25), 1.0 * ui, viewport, xray=True)
    _draw_lines(shaders, v['edges'], (col_v[0], col_v[1], col_v[2], 0.9), 1.6 * ui, viewport, xray=False)
    if any(out):
        if fill > 0.0:
            gpu.state.depth_test_set('LESS_EQUAL')
            flat.bind()
            flat.uniform_float("color", (col_o[0], col_o[1], col_o[2], 0.16 * fill))
            for i, (edge, face) in enumerate(v['walls']):
                if out[i]:
                    face.draw(flat)
        for i, (edge, face) in enumerate(v['walls']):
            if out[i]:
                _draw_lines(shaders, edge, (col_o[0], col_o[1], col_o[2], 0.45), 1.6 * ui, viewport, xray=True)
                _draw_lines(shaders, edge, (col_o[0], col_o[1], col_o[2], 1.0), 2.6 * ui, viewport, xray=False)
    if v['inner'] is not None:
        # the limit parts should stay inside of
        _draw_lines(shaders, v['inner'], (col_v[0], col_v[1], col_v[2], 0.16), 1.0 * ui, viewport, xray=True)
        _draw_lines(shaders, v['inner'], (col_v[0], col_v[1], col_v[2], 0.5), 1.0 * ui, viewport, xray=False)
        for i, k in enumerate((0, 1, 3, 4)):          # the four side walls
            if near[i]:
                edge = v['inner_walls'][k][0]
                _draw_lines(shaders, edge, (col_w[0], col_w[1], col_w[2], 0.35), 1.4 * ui, viewport, xray=True)
                _draw_lines(shaders, edge, (col_w[0], col_w[1], col_w[2], 1.0), 2.2 * ui, viewport, xray=False)


def draw_scene(mon, st, p, persp_matrix, window_matrix, view_distance, viewport, ui,
               srgb_target=True, only=None, view=0):
    """Draw the 3-D overlay for one monitor with explicit view matrices.

    Kept free of ``bpy.context`` so it can also render into an off-screen
    buffer (used by the automated tests).  ``srgb_target`` says whether the
    bound framebuffer is sRGB encoded, as the viewport's overlay buffer is.
    ``only``: the slots this viewport shows when it is in local view.
    Returns the number of triangles drawn (shading, hatching).
    """
    srgb = 1.0 if srgb_target else 0.0
    shaders = _shaders()
    col_c = tuple(p.color_collision) if p else (1.0, 0.08, 0.05, 1.0)
    col_w = tuple(p.color_clearance) if p else (1.0, 0.72, 0.0, 1.0)
    col_o = tuple(p.color_outside) if p else (0.95, 0.1, 0.85, 1.0)
    col_v = tuple(p.color_volume) if p else (0.35, 0.75, 1.0, 1.0)
    # whole pixels, whatever the interface scale (see the shader)
    spacing = float(max(2, round((p.hatch_pixels if p else 4) * ui)))
    xray = (p.xray if p.use_xray else 0.0) if p else 0.45
    drawn = [0, 0]
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_mask_set(False)
        gpu.state.face_culling_set('NONE')

        w = mon.world
        if st.show_volume:
            _draw_volume(mon, st, p, shaders, viewport, ui, col_v, col_o, col_w, only)

        if st.show_overlay:
            # How far the overlay is pulled towards the viewer to sit on top of
            # the surface it marks, as (k1, k2, k3) for z' = (z + k1 w) k2 + k3.
            # With a very small clip start the depth buffer is coarse at
            # working distance, so the pull grows with it.
            a = window_matrix[2][2]
            if window_matrix[3][3] == 0.0:              # perspective: a relative pull
                rel = 0.0015
                near = abs(window_matrix[2][3] / (a - 1.0)) if a != 1.0 else 0.0
                if near > 0.0:
                    rel = min(0.02, max(rel, 6.0 * view_distance / (near * 16777216.0)))
                bias = (a * rel, 1.0 / (1.0 - rel), 0.0)
                tint_bias = (a * 0.4 * rel, 1.0 / (1.0 - 0.4 * rel), 0.0)
            else:                                       # orthographic: an absolute one
                pull = 0.004 * max(view_distance, 1e-9)
                bias = (0.0, 1.0, a * pull)
                tint_bias = (0.0, 1.0, a * 0.4 * pull)
            if st.show_tint:
                drawn[0] = _draw_tint(mon, p, shaders, persp_matrix, tint_bias, srgb, viewport, ui,
                                      col_c, only, view)
            groups = _groups(mon, shaders, only, view)
            for g in groups:
                if g.empty:
                    continue
                drawn[1] += g.ntri
                b = g.tri.get('clear')
                if b is not None:
                    _draw_tris(shaders, b, col_w, STYLE_DIAG, spacing, 0.10,
                               persp_matrix, bias, xray, srgb)
                for b, lo, hi in g.clipped.get('clear', ()):
                    _draw_tris(shaders, b, col_w, STYLE_DIAG, spacing, 0.10,
                               persp_matrix, bias, xray, srgb, (lo, hi))
                b = g.tri.get('wall')
                if b is not None:
                    # only what lies beyond the inner box, i.e. inside the margin
                    _draw_tris(shaders, b, col_w, STYLE_BACK, spacing, 0.10,
                               persp_matrix, bias, xray, srgb, w.inner_box(), True)
                b = g.tri.get('outside')
                if b is not None:
                    # only what lies beyond the walls
                    _draw_tris(shaders, b, col_o, STYLE_BACK, spacing, 0.16,
                               persp_matrix, bias, xray, srgb, w.volume, True)
                b = g.tri.get('collide')
                if b is not None:
                    _draw_tris(shaders, b, col_c, STYLE_CROSS, spacing, 0.16,
                               persp_matrix, bias, xray, srgb)
                for b, lo, hi in g.clipped.get('collide', ()):
                    _draw_tris(shaders, b, col_c, STYLE_CROSS, spacing, 0.16,
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
                if b is not None and st.show_gap_lines:
                    _draw_lines(shaders, b, col_w, 2.2 * ui, viewport)
                b = g.line.get('inside')
                if b is not None:
                    _draw_lines(shaders, b, col_c, 2.2 * ui, viewport)
                b = g.line.get('collide')
                if b is not None and st.show_curves:
                    _draw_lines(shaders, b, col_c, 2.8 * ui, viewport)
    finally:
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set('NONE')
    return drawn


def _local_view(context, mon):
    """(slots shown, an id for the viewport) if the viewport being drawn is in
    local view, else (None, 0)."""
    space = context.space_data
    if space is None or getattr(space, 'local_view', None) is None:
        return None, 0
    w = mon.world
    shown = set()
    for obj in context.view_layer.objects:
        if obj is None or obj.type != 'MESH':
            continue
        try:
            if not obj.local_view_get(space):
                continue
        except Exception:
            continue
        slot = w.slot(monitor._uid(obj))
        if slot is not None:
            shown.add(slot)
    return shown, space.as_pointer()


def _draw_failed(mon, ex):
    """A draw callback must never raise into Blender.  What went wrong is
    noted where the sidebar shows it, and printed once, not on every redraw."""
    msg = f'drawing: {ex}'
    if mon.error != msg:
        mon.error = msg
        import traceback
        traceback.print_exc()


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
    rid = context.region.as_pointer()
    # A view that has been turned, moved or zoomed since it was last drawn:
    # the user is navigating.  The background work then makes room (the
    # dependency graph says nothing about this; the view matrix does).
    try:
        vm = rv3d.view_matrix
        view = tuple(vm[0]) + tuple(vm[1]) + tuple(vm[2])
    except Exception:
        view = None
    views = _state.setdefault('views', {})
    if views.get(rid) != view:
        if len(views) > 64:
            views.clear()
        views[rid] = view
        monitor.user_active()
    if mon.hot:
        # how often the viewport gets redrawn while something is being moved:
        # the frame rate the user sees, whoever it is that takes the time
        last = _state.setdefault('last_draw', {})
        prev = last.get(rid)
        if prev is not None and t0 - prev < 1.0:
            mon.frame_ms.append((t0 - prev) * 1000.0)
        last[rid] = t0
    elif 'last_draw' in _state:
        del _state['last_draw']
    try:
        only, view = _local_view(context, mon)
        mon.draw_tris = draw_scene(mon, st, props.prefs(context), rv3d.perspective_matrix,
                                   rv3d.window_matrix, rv3d.view_distance,
                                   gpu.state.viewport_get()[2:], context.preferences.system.ui_scale,
                                   only=only, view=view)
    except Exception as ex:
        _draw_failed(mon, ex)
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


def unchecked_lines(status):
    """Short lines about what is in the scene but is not being checked."""
    lines = []
    n = status.get('skipped', 0)
    if n:
        lines.append(f"{n} part" + ("s" if n != 1 else "") + " not checked (too large)")
    un = status.get('unchecked') or {}
    n = un.get('instances', 0)
    if n:
        lines.append(f"{n} part" + ("s have" if n != 1 else " has") + " unchecked instances")
    n = un.get('collections', 0)
    if n:
        lines.append(f"{n} collection instance" + ("s" if n != 1 else "") + " not checked")
    n = un.get('other', 0)
    if n:
        lines.append(f"{n} object" + ("s" if n != 1 else "") + " not checked (not a mesh)")
    return lines


def _count(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def badge_rows(status):
    """(state, headline, [(line, tone), ...]) for the status badge.

    state is 'BUSY', 'OK', 'WARN' (no collision, but something to look at) or
    'FAIL'.  A problem that is known is shown at once, even while other parts
    are still being read: it will not go away by waiting.  tone is '' for a
    plain line, 'warn' for one about a warning and 'dim' for one about work
    in progress.
    """
    nc = status['collisions']
    nout = status['partly_out'] + status['outside']
    ncl = status['clearance']
    nw = status['near_wall']
    gaps = [(line, 'warn') for line in unchecked_lines(status)]
    hidden = status.get('hidden', 0)
    with_hidden = status.get('hidden_problems', 0)
    rows = []
    if nc or nout:
        if nc:
            rows.append((_count(nc, "collision"), ''))
        if nout:
            rows.append((_count(nout, "part") + " outside the volume", ''))
        if ncl:
            rows.append((_count(ncl, "clearance warning"), 'warn'))
        if nw:
            rows.append((_count(nw, "part") + " close to a wall", 'warn'))
        if with_hidden:
            rows.append((_count(with_hidden, "problem") + " with hidden parts", ''))
        rows += gaps
        if status['preparing']:
            done, total = status['preparing']
            rows.append((f"still preparing parts ({done} of {total})", 'dim'))
        elif status['busy']:
            rows.append(("still checking...", 'dim'))
        return 'FAIL', ("Collision Detected" if nc else "Outside Build Volume"), rows
    if status['preparing']:
        done, total = status['preparing']
        return 'BUSY', "Preparing Parts", [(f"{done} of {total} ready", '')]
    if status['busy']:
        return 'BUSY', "Checking", [(f"{status['pending']} pairs to go", '')] if status['pending'] else []
    n = status['objects']
    if n == 0:
        return 'BUSY', "No Parts", [("no mesh objects to check", '')] + gaps
    # short lines: the same text has to fit the sidebar
    rows.append((_count(n, "part") + (f" ({hidden} hidden)" if hidden else "") + ", no collisions", ''))
    if status['volume']:
        rows.append(("all inside the build volume", ''))
    if ncl:
        rows.append((_count(ncl, "clearance warning"), 'warn'))
    if nw:
        rows.append((_count(nw, "part") + " close to a wall", 'warn'))
    if with_hidden:
        rows.append((_count(with_hidden, "warning") + " with hidden parts", 'warn'))
    rows += gaps
    if status['refining'] and status['clearance_on']:
        # collisions are settled; some gaps are still being measured exactly
        rows.append(("measuring clearances...", 'dim'))
    if ncl:
        return 'WARN', "Clearance Warning", rows
    if nw:
        return 'WARN', "Wall Gap Warning", rows
    if gaps:
        return 'WARN', "Not Everything Checked", rows
    return 'OK', "Build OK", rows


def badge_text(status):
    """(state, headline, detail lines): ``badge_rows`` as plain text, for the
    sidebar."""
    state, head, rows = badge_rows(status)
    return state, head, [line for line, _ in rows]


def _badge_shapes(state, cx, cy, r, flat):
    """Batches of the badge icon, cached: [(colour key, batch), ...].

    A ring with a tick (pass), a cross (fail) or three dots (busy); a
    triangle with an exclamation mark for warnings."""
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
    if state == 'WARN':
        # corners on a circle a little smaller than the ring of the other
        # states, and lowered so that the triangle looks centred
        q = 0.98 * r
        top = (cx, cy + q - 0.06 * r)
        left = (cx - 0.866 * q, cy - 0.5 * q - 0.06 * r)
        right = (cx + 0.866 * q, cy - 0.5 * q - 0.06 * r)
        shapes.append(('main', mk(_stroke([top, left, right, top], 0.9 * pen))))
        shapes.append(('main', mk(_stroke([(cx, cy + 0.36 * r), (cx, cy - 0.06 * r)], 0.95 * pen))))
        shapes.append(('main', mk(_fan(cx, cy - 0.34 * r, 0.115 * r, 16))))
    else:
        shapes.append(('main', mk(_rings(np.array([cx]), np.array([cy]), r, 0.9))))
        if state == 'OK':
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
    _state[key] = shapes
    return shapes


def _text_width(text, size):
    try:
        blf.size(0, size)
    except TypeError:               # pre-4.0 signature
        blf.size(0, int(size), 72)
    return blf.dimensions(0, text)[0]


def _draw_badge(mon, st, p, shaders, ui, right, bottom):
    """The pass / fail badge in the bottom right corner of the viewport: a
    large icon, and to its left the verdict and what is behind it, set flush
    right.  ``right`` and ``bottom`` are the edges of the free part of the
    region."""
    status = mon.status()
    state, head, rows = badge_rows(status)
    green = (0.25, 0.85, 0.35, 1.0)
    red = tuple(p.color_collision) if p else (1.0, 0.08, 0.05, 1.0)
    amber = tuple(p.color_clearance) if p else (1.0, 0.72, 0.0, 1.0)
    blue = tuple(p.color_volume) if p else (0.35, 0.75, 1.0, 1.0)
    grey = (0.75, 0.75, 0.75, 1.0)
    white = (0.92, 0.92, 0.92, 1.0)
    color = {'OK': green, 'WARN': amber, 'FAIL': red, 'BUSY': grey}[state]
    tones = {'': white, 'warn': amber, 'dim': grey}
    rows = [(line, tones[tone]) for line, tone in rows[:6]]
    printer = status.get('printer')
    if printer:
        rows.append((printer, (blue[0], blue[1], blue[2], 1.0)))      # which printer this is for

    k = ui * (p.badge_scale if p else 1.0)
    r = 34.0 * k
    head_size = 24.0 * k
    body_size = 13.0 * k
    line_h = 17.0 * k
    gap = 14.0 * k
    margin = 20.0 * ui
    block_h = head_size + (len(rows) * line_h + 3.0 * k if rows else 0.0)
    icx = right - margin - 1.16 * r
    cy = bottom + margin + max(1.16 * r, 0.5 * block_h)       # nothing may fall below the region

    flat = shaders['flat']
    flat.bind()
    gpu.state.blend_set('ALPHA')
    palette = {'back': (0.0, 0.0, 0.0, 0.55), 'main': color}
    for ckey, batch in _badge_shapes(state, icx, cy, r, flat):
        flat.uniform_float("color", palette[ckey])
        batch.draw(flat)

    _text_shadow(True)
    edge = icx - 1.16 * r - gap                               # the text ends here
    y = cy + 0.5 * block_h - 0.8 * head_size
    _text(edge - _text_width(head, head_size), y, head, head_size, color)
    y -= 3.0 * k
    for line, lc in rows:
        y -= line_h
        _text(edge - _text_width(line, body_size), y, line, body_size, lc)
    _text_shadow(False)


_MARK_COLLISION = ('COLLIDE', 'PARTIAL', 'OUTSIDE')
_MARK_CLEARANCE = ('CLEAR', 'WALL')


def _draw_markers(mon, st, p, shaders, ui, persp_matrix, width, height, only=None):
    problems = mon.problems()
    if not problems:
        return
    kinds_on = ((_MARK_COLLISION if st.show_markers_collision else ())
                + (_MARK_CLEARANCE if st.show_markers_clearance else ()))
    if not kinds_on:
        return
    n = min(len(problems), MAX_MARKERS)
    pts = np.ones((n, 4))
    for i in range(n):
        pts[i, :3] = problems[i]['center']
    clip = pts @ np.array(persp_matrix).T
    wv = clip[:, 3]
    ok = wv > 1e-9
    w = mon.world
    if only is not None:
        # a viewport in local view: only what is among the parts it shows
        hidden = mon.hidden_slots()
        for i in range(n):
            if not ok[i]:
                continue
            slots = [w.slot(u) for u in problems[i]['key'][1:]]
            if len(slots) == 2:
                ok[i] = _shown(slots[0], slots[1], only, hidden)
            else:
                ok[i] = slots[0] in only
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
        if kind not in kinds_on:
            continue
        m = ok & (kinds == kind)
        if not m.any():
            continue
        flat.uniform_float("color", colors[kind])
        batch_for_shader(flat, 'TRIS', {"pos": _rings(px[m], py[m], radius[m])}).draw(flat)
    # Distances are worth reading for the parts in hand; in a crowded build a
    # label on every near pair is only clutter (the rings and the list remain).
    measured = [i for i in range(n) if ok[i] and problems[i]['kind'] in _MARK_CLEARANCE]
    if len(measured) > MAX_LABELS:
        hot = mon.hot
        measured = [i for i in measured
                    if i == active or any(w.slot(u) in hot for u in problems[i]['key'][1:])]
    measured = set(measured)
    _text_shadow(True)
    for i in range(n):
        pr = problems[i]
        if not ok[i] or pr['kind'] not in kinds_on:
            continue
        if pr['kind'] == 'CLEAR':
            if i not in measured:
                continue
            label = f"{pr['dist_mm']:.2f} mm"
        elif pr['kind'] == 'WALL':
            if i not in measured:
                continue
            label = f"{pr['dist_mm']:.2f} mm to wall"
        elif i == active:
            label = {'COLLIDE': "collision", 'PARTIAL': "outside volume",
                     'OUTSIDE': "outside volume"}[pr['kind']]
        else:
            continue
        _text(px[i] + 12.0 * ui, py[i] - 4.0 * ui, label, 12.0 * ui, colors[pr['kind']])
    _text_shadow(False)


def draw_hud(mon, st, p, persp_matrix, width, height, right, bottom, ui, only=None):
    """Draw markers, labels and the status badge (2-D, pixel coordinates).
    ``right`` and ``bottom``: the edges of the part of the region that
    nothing else covers, where the badge sits."""
    shaders = _shaders()
    try:
        if st.show_overlay:
            _draw_markers(mon, st, p, shaders, ui, persp_matrix, width, height, only)
        if st.show_hud:
            _draw_badge(mon, st, p, shaders, ui, right, bottom)
    finally:
        gpu.state.blend_set('NONE')


def free_corner(context):
    """(right, bottom) of the part of the viewport region that the sidebar and
    the asset shelf do not cover.  With overlapping regions they lie on top of
    the viewport; without, the viewport ends where they begin."""
    region = context.region
    right = float(region.width)
    bottom = 0.0
    try:
        if context.preferences.system.use_region_overlap and context.area is not None:
            for reg in context.area.regions:
                if reg.width <= 1 or reg.height <= 1:
                    continue
                if reg.type == 'UI' and reg.alignment == 'RIGHT':
                    right -= reg.width
                elif reg.type == 'TOOLS' and reg.alignment == 'RIGHT':
                    right -= reg.width
                elif reg.type in {'ASSET_SHELF', 'ASSET_SHELF_HEADER'} and reg.alignment == 'BOTTOM':
                    bottom += reg.height
    except Exception:
        pass
    return right, bottom


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
    try:
        right, bottom = free_corner(context)
        only, _ = _local_view(context, mon)
        draw_hud(mon, st, props.prefs(context), rv3d.perspective_matrix, region.width,
                 region.height, right, bottom, context.preferences.system.ui_scale, only)
    except Exception as ex:
        _draw_failed(mon, ex)


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
