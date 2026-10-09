"""Off-screen rendering test of the viewport overlay (background Blender with a GPU context).

Compiles the add-on's shaders on the real GPU module, draws the overlay over a
simple shaded view of the scene into an off-screen buffer and saves PNGs, so the
drawing code is exercised and its output can be looked at.

Run with: blender --background --python this_file -- <output directory>
"""

import importlib
import math
import os
import struct
import sys
import time
import zlib

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor, overlay, props = bc.monitor, bc.overlay, bc.props

OUT = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else '/tmp'
W, H = 1400, 900


def write_png(path, rgba):
    h, w, _ = rgba.shape
    raw = b''.join(b'\x00' + rgba[y].tobytes() for y in range(h - 1, -1, -1))

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b'IDAT', zlib.compress(raw, 6)))
        f.write(chunk(b'IEND', b''))


def perspective(fov_y, aspect, near, far):
    f = 1.0 / math.tan(fov_y / 2.0)
    return Matrix(((f / aspect, 0, 0, 0), (0, f, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
                   (0, 0, -1, 0)))


def ortho(l, r, b, t, n, f):
    return Matrix(((2 / (r - l), 0, 0, -(r + l) / (r - l)), (0, 2 / (t - b), 0, -(t + b) / (t - b)),
                   (0, 0, -2 / (f - n), -(f + n) / (f - n)), (0, 0, 0, 1)))


def look_at(eye, target, up=(0, 0, 1)):
    eye = Vector(eye)
    fwd = (Vector(target) - eye).normalized()
    right = fwd.cross(Vector(up)).normalized()
    upv = right.cross(fwd)
    rot = Matrix((right, upv, -fwd)).to_4x4()
    return rot @ Matrix.Translation(-eye)


def scene_batch(shader):
    """Flat-shaded triangles of every mesh object, lit from a fixed direction."""
    dg = bpy.context.evaluated_depsgraph_get()
    pos, col = [], []
    light = np.array([0.35, -0.5, 0.8])
    light /= np.linalg.norm(light)
    palette = [(0.62, 0.66, 0.72), (0.72, 0.68, 0.60), (0.60, 0.70, 0.64), (0.70, 0.62, 0.68)]
    k = 0
    for ob in bpy.context.view_layer.objects:
        if ob.type != 'MESH' or not ob.visible_get():
            continue
        data = monitor.read_mesh(ob.evaluated_get(dg))
        if data is None:
            continue
        co, tri = data
        M = np.array(ob.matrix_world)
        wv = co @ M[:3, :3].T + M[:3, 3]
        P = wv[tri]
        n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
        shade = 0.35 + 0.65 * np.abs(n @ light)
        base = np.array(palette[k % len(palette)])
        k += 1
        c = np.concatenate([shade[:, None] * base[None, :], np.ones((len(P), 1))], axis=1)
        pos.append(P.reshape(-1, 3))
        col.append(np.repeat(c, 3, axis=0))
    pos = np.ascontiguousarray(np.concatenate(pos), dtype=np.float32)
    col = np.ascontiguousarray(np.concatenate(col), dtype=np.float32)
    return batch_for_shader(shader, 'TRIS', {"pos": pos, "color": col})


def render(mon, st, name, eye, target, is_ortho=False, ortho_scale=200.0, up=(0, 0, 1)):
    p = props.prefs()
    aspect = W / H
    view = look_at(eye, target, up)
    dist = (Vector(eye) - Vector(target)).length
    if is_ortho:
        s = ortho_scale * 0.5
        proj = ortho(-s * aspect, s * aspect, -s, s, 1.0, 5000.0)
    else:
        proj = perspective(math.radians(40.0), aspect, 1.0, 5000.0)
    offscreen = gpu.types.GPUOffScreen(W, H)
    smooth = gpu.shader.from_builtin('SMOOTH_COLOR')
    t0 = time.perf_counter()
    with offscreen.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.16, 0.17, 0.19, 1.0), depth=1.0)
        gpu.state.viewport_set(0, 0, W, H)
        with gpu.matrix.push_pop():
            gpu.matrix.load_matrix(view)
            gpu.matrix.load_projection_matrix(proj)
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.depth_mask_set(True)
            gpu.state.blend_set('NONE')
            sb = scene_batch(smooth)
            sb.draw(smooth)
            drawn = overlay.draw_scene(mon, st, p, proj @ view, proj, dist, (W, H), 1.0, srgb_target=False)
        with gpu.matrix.push_pop():
            gpu.matrix.load_matrix(Matrix.Identity(4))
            gpu.matrix.load_projection_matrix(ortho(0, W, 0, H, -1, 1))
            overlay.draw_hud(mon, st, p, proj @ view, W, H, float(W), 0.0, 1.0, srgb_target=False)
        buf = fb.read_color(0, 0, W, H, 4, 0, 'UBYTE')
    ms = (time.perf_counter() - t0) * 1000
    offscreen.free()
    img = np.array(buf.to_list(), dtype=np.uint8).reshape(H, W, 4)
    img[:, :, 3] = 255
    path = os.path.join(OUT, name + '.png')
    write_png(path, img)
    red, amber, magenta = colours(img)
    print(f'  {name}: saved {path}  ({ms:.0f} ms)  red px {red}  amber px {amber}  magenta px {magenta}'
          f'  triangles: shading {drawn[0]}, hatching {drawn[1]}')
    LAST.update(img=img, matrix=proj @ view, drawn=drawn)
    return red, amber, magenta


LAST = {}


def colours(img):
    """Pixels in the collision, warning and out-of-volume colours."""
    red = int(((img[:, :, 0] > 200) & (img[:, :, 1] < 90) & (img[:, :, 2] < 90)).sum())
    amber = int(((img[:, :, 0] > 200) & (img[:, :, 1] > 130) & (img[:, :, 1] < 220) & (img[:, :, 2] < 80)).sum())
    magenta = int(((img[:, :, 0] > 180) & (img[:, :, 1] < 110) & (img[:, :, 2] > 150)).sum())
    return red, amber, magenta


def green(img):
    return int(((img[:, :, 1] > 170) & (img[:, :, 0] < 110) & (img[:, :, 2] < 140)).sum())


def corner(img):
    """The bottom right corner of the last picture, where the badge is (row 0
    is the bottom row)."""
    return img[:150, W - 420:]


def around(point, size=9):
    """The pixels of the last picture around a point of the scene."""
    v = LAST['matrix'] @ Vector((point[0], point[1], point[2], 1.0))
    x = int(round((v.x / v.w * 0.5 + 0.5) * W))
    y = int(round((v.y / v.w * 0.5 + 0.5) * H))
    assert size <= x < W - size and size <= y < H - size, (point, x, y)
    return LAST['img'][y - size:y + size + 1, x - size:x + size + 1]


def settle():
    sc = bpy.context.scene
    mon = monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    mon.settle(sc, vl.depsgraph, vl)
    return mon


def main():
    print('Blender', bpy.app.version_string)
    gpu.init()
    print('GPU:', gpu.platform.vendor_get(), '|', gpu.platform.renderer_get(), '|',
          gpu.platform.version_get(), '| backend', gpu.platform.backend_type_get())

    sh = overlay._shaders()
    assert sh['hatch'] is not None, 'the hatch shader did not compile'
    assert sh['tint'] is not None, 'the shading shader did not compile'
    assert sh['sdf'] is not None, 'the shader of the icons did not compile'
    print('  shaders compiled: hatching, part shading, smooth icons')

    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    # a small build: two colliding parts, two parts too close, one part through a wall
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=28, location=(90, 110, 60))
    bpy.context.active_object.name = 'Ball'
    bpy.ops.mesh.primitive_torus_add(major_radius=30, minor_radius=11, major_segments=72, minor_segments=24,
                                     location=(128, 110, 66), rotation=(0.5, 0.3, 0.0))
    bpy.context.active_object.name = 'Ring'
    bpy.ops.mesh.primitive_cube_add(size=50, location=(230, 90, 40))
    bpy.context.active_object.name = 'Block'
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=14, depth=70, location=(230, 90, 103),
                                        rotation=(0.0, 0.0, 0.0))
    bpy.context.active_object.name = 'Pin'          # 3 mm above the block
    bpy.ops.mesh.primitive_monkey_add(size=60, location=(346, 200, 60), rotation=(0, 0, -0.6))
    mk = bpy.context.active_object
    mk.name = 'Head'                                # reaches through the x = 380 wall
    bpy.ops.object.modifier_add(type='BEVEL')       # a modifier stack, as on real parts
    bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=22, depth=60, location=(60, 240, 30))
    bpy.context.active_object.name = 'Cone'
    bpy.context.view_layer.update()

    sc = bpy.context.scene
    st = sc.bucket_builder
    st.detect_collisions = True
    st.monitor_volume = True
    mon = settle()
    s = mon.status()
    print('  status:', s)
    for pr in mon.problems():
        print('   problem:', pr['kind'], pr['a'], pr['b'], round(pr['dist_mm'], 3))
    assert s['collisions'] == 1, s
    assert s['clearance'] >= 1, s
    assert s['partly_out'] == 1, s

    r, a, m = render(mon, st, 'overview', (520, -330, 330), (180, 130, 60))
    assert r > 300, 'no collision hatch visible'
    assert a > 100, 'no clearance hatch visible'
    assert m > 300, 'no out-of-volume hatch visible'
    # the verdict sits in the bottom right corner: the most serious problem
    # is the part outside the volume, so in that colour, with the collision
    # in its own colour underneath
    cr, _, cm = colours(corner(LAST['img']))
    assert cm > 1500, f'no badge in the out-of-volume colour in the bottom right corner ({cm} px)'
    assert cr > 60, f'the line about the collision is not in the collision colour ({cr} px)'
    text = overlay.badge_text(mon.status())
    assert text[:2] == ('FAIL', '1 Part Outside Build Volume') and text[2][0] == '1 Collision Detected', text
    assert colours(LAST['img'][H // 2 - 60:H // 2 + 60, :300])[0] == 0, 'something red at the left edge'
    # Only the wall that the part goes through is marked: the middle of an
    # upright edge of the x = 380 wall is drawn in the out-of-volume colour,
    # that of an edge of the x = 0 wall in the volume's own.
    assert mon.walls()[0] == (False, False, False, True, False, False), mon.walls()
    hit = colours(around((380, 0, 190)))[2]
    calm = around((0, 0, 190))
    assert hit > 10, f'the wall that is exceeded is not marked ({hit} px)'
    assert colours(calm)[2] == 0, 'a wall that nothing goes through is marked'
    assert int(((calm[:, :, 2] > 150) & (calm[:, :, 0] < 140)).sum()) > 5, 'the edge of the volume is missing'
    render(mon, st, 'collision_closeup', (150, -20, 150), (112, 110, 64))
    render(mon, st, 'clearance_closeup', (330, -10, 110), (230, 90, 68))
    render(mon, st, 'outside_closeup', (520, 60, 150), (350, 200, 60))
    render(mon, st, 'ortho_top', (190, 142, 900), (190, 142, 0), is_ortho=True, ortho_scale=420.0,
           up=(0, 1, 0))

    # whole-part shading of the colliding parts, and its fallback
    pr = props.prefs()
    st.show_tint = False
    r0, _, _ = render(mon, st, 'tint_off', (150, -20, 150), (112, 110, 64))
    assert LAST['drawn'][0] == 0, LAST['drawn']
    st.show_tint = True
    r1, _, _ = render(mon, st, 'tint_on', (150, -20, 150), (112, 110, 64))
    assert len(overlay._state.get('tint', {})) == 2, 'one cached mesh per colliding part expected'
    assert LAST['drawn'][0] > 0, LAST['drawn']
    pr.tint_budget = 0.0                       # no triangle budget: outline boxes instead
    r2, _, _ = render(mon, st, 'tint_boxes', (150, -20, 150), (112, 110, 64))
    pr.tint_budget = 4.0
    print(f'  red pixels: tint off {r0}, on {r1}, outline boxes {r2}')
    assert r2 > r0, 'outline boxes of the colliding parts are missing'

    # the display switches
    both = LAST['drawn']
    st.show_curves = False
    st.show_markers_collision = False
    r3, _, _ = render(mon, st, 'no_outlines', (150, -20, 150), (112, 110, 64))
    st.show_curves = True
    st.show_markers_collision = True
    r4, _, _ = render(mon, st, 'outlines', (150, -20, 150), (112, 110, 64))
    assert r4 > r3, 'the intersection outline and the marker cannot be switched off'
    st.show_gap_lines = False
    st.show_markers_clearance = False
    st.show_labels_clearance = False
    _, a0, _ = render(mon, st, 'no_gap_lines', (330, -10, 110), (230, 90, 68))
    st.show_labels_clearance = True              # the distance without the ring: its own switch
    _, a_label, _ = render(mon, st, 'gap_label_only', (330, -10, 110), (230, 90, 68))
    st.show_labels_clearance = False
    st.show_markers_clearance = True             # and the ring without the distance
    _, a_ring, _ = render(mon, st, 'gap_ring_only', (330, -10, 110), (230, 90, 68))
    st.show_gap_lines = True
    st.show_labels_clearance = True
    _, a1, _ = render(mon, st, 'gap_lines', (330, -10, 110), (230, 90, 68))
    assert a_label > a0 and a_ring > a0, ('the marker and the distance are not separate switches',
                                          a0, a_label, a_ring)
    assert a1 > max(a_label, a_ring), 'the closest-distance line and the marker cannot be switched off'
    pr.use_xray = False
    render(mon, st, 'no_xray', (150, -20, 150), (112, 110, 64))
    pr.use_xray = True
    for px in (2, 4, 7, 10):
        pr.hatch_pixels = px
        render(mon, st, f'hatch_{px}px', (150, -20, 150), (112, 110, 64))
    pr.hatch_pixels = 4

    # A hidden part is still checked, but it is not shaded or hatched: the
    # user hid it to see past it.
    ring = bpy.data.objects['Ring']
    render(mon, st, 'ring_shown', (150, -20, 150), (112, 110, 64))
    shown = LAST['drawn']
    ring.hide_set(True)
    bpy.context.view_layer.update()
    mon = settle()
    s = mon.status()
    assert s['collisions'] == 1 and s['hidden'] == 1 and s['hidden_problems'] == 1, s
    r5, _, _ = render(mon, st, 'ring_hidden', (150, -20, 150), (112, 110, 64))
    assert 0 < LAST['drawn'][0] < shown[0] and 0 < LAST['drawn'][1] < shown[1], (LAST['drawn'], shown)
    assert r5 > 300, 'the collision with a hidden part is not shown'
    ring.hide_set(False)
    bpy.context.view_layer.update()
    mon = settle()

    # A problem that is ignored is not drawn and does not count; what is left
    # of it is a small grey ring where it is.
    ball_ring = next(q for q in mon.problems() if q['kind'] == 'COLLIDE')
    r_before, _, _ = render(mon, st, 'collision_counts', (150, -20, 150), (112, 110, 64))
    assert bpy.ops.bucketbuilder.ignore_problem(kind='P', a=ball_ring['names'][0],
                                                b=ball_ring['names'][1]) == {'FINISHED'}
    s = mon.status()
    assert s['collisions'] == 0 and s['ignored_problems'] == 1, s
    r_ignored, _, _ = render(mon, st, 'collision_ignored', (150, -20, 150), (112, 110, 64))
    assert LAST['drawn'] == [0, LAST['drawn'][1]] and r_ignored < 0.1 * r_before, (
        'an ignored collision is still drawn', LAST['drawn'], r_ignored, r_before)
    grey = around(ball_ring['center'], 8)
    assert int(((np.abs(grey[:, :, 0].astype(int) - grey[:, :, 1]) < 12) & (grey[:, :, 0] > 120)
                & (grey[:, :, 0] < 200)).sum()) > 12, 'no grey ring where the ignored problem is'
    assert bpy.ops.bucketbuilder.count_all_problems() == {'FINISHED'} and mon.status()['collisions'] == 1

    # a warning only: the badge is an amber triangle
    ring.location.z = 150
    bpy.data.objects['Head'].location = (300, 200, 60)
    bpy.context.view_layer.update()
    mon = settle()
    text = overlay.badge_text(mon.status())
    assert text[0] == 'WARN' and 'Clearance Warning' in text[1], text
    render(mon, st, 'warning', (520, -330, 330), (180, 130, 60))
    cr, ca, _ = colours(corner(LAST['img']))
    assert ca > 800 and cr == 0, f'no amber badge in the corner ({ca} amber, {cr} red px)'
    assert colours(around((380, 0, 190)))[2] == 0, 'a wall is still marked'
    # the wall gap: only the side of the inner box that a part is too close to
    st.use_wall_clearance = True
    bpy.data.objects['Cone'].location.x = 25.0        # base radius 22: 3 mm from the x = 0 wall
    bpy.context.view_layer.update()
    mon = settle()
    assert mon.walls()[1] == (True, False, False, False), mon.walls()
    render(mon, st, 'wall_gap', (520, -330, 330), (180, 130, 60))
    near = colours(around((5, 60, 0)))[1]             # bottom edge of the inner box, x low side
    assert near > 10, f'the side a part is too close to is not marked ({near} px)'
    assert colours(around((250, 5, 0)))[1] == 0, 'a side nothing is close to is marked'
    st.use_wall_clearance = False
    bpy.data.objects['Cone'].location.x = 60.0

    # a clean build: the badge must turn green
    bpy.data.objects['Pin'].location.z = 115
    bpy.context.view_layer.update()
    mon = settle()
    print('  status after fixing:', mon.status())
    r, a, m = render(mon, st, 'clean', (520, -330, 330), (180, 130, 60))
    text = overlay.badge_text(mon.status())
    assert text[:2] == ('OK', 'Build OK'), text
    assert r == 0 and a == 0 and m == 0, (r, a, m)
    cg = green(corner(LAST['img']))
    assert cg > 800, f'no green badge in the corner ({cg} px)'
    # The icon has smooth edges: between the green of the ring and the dark
    # disc behind it there are pixels of every shade in between.  (Made of
    # triangles, as it was, there were none: an edge was a staircase.)
    c = corner(LAST['img']).astype(int)
    ringish = (c[:, :, 1] > c[:, :, 0] + 25) & (c[:, :, 1] > c[:, :, 2] + 25)
    shades = np.unique(c[:, :, 1][ringish] // 8)
    assert len(shades) >= 14, f'the badge icon is not smooth ({len(shades)} shades of green)'
    print(f'  the badge icon is drawn with smooth edges ({len(shades)} shades along them)')
    print('GPU TEST OK')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('GPU TEST FAILED')
    sys.exit(1)
