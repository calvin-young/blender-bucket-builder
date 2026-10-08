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
            overlay.draw_scene(mon, st, p, proj @ view, proj, dist, (W, H), 1.0, srgb_target=False)
        with gpu.matrix.push_pop():
            gpu.matrix.load_matrix(Matrix.Identity(4))
            gpu.matrix.load_projection_matrix(ortho(0, W, 0, H, -1, 1))
            overlay.draw_hud(mon, st, p, proj @ view, W, H, W * 0.5, 1.0)
        buf = fb.read_color(0, 0, W, H, 4, 0, 'UBYTE')
    ms = (time.perf_counter() - t0) * 1000
    offscreen.free()
    img = np.array(buf.to_list(), dtype=np.uint8).reshape(H, W, 4)
    img[:, :, 3] = 255
    path = os.path.join(OUT, name + '.png')
    write_png(path, img)
    red = int(((img[:, :, 0] > 200) & (img[:, :, 1] < 90) & (img[:, :, 2] < 90)).sum())
    amber = int(((img[:, :, 0] > 200) & (img[:, :, 1] > 130) & (img[:, :, 1] < 220) & (img[:, :, 2] < 80)).sum())
    magenta = int(((img[:, :, 0] > 180) & (img[:, :, 1] < 110) & (img[:, :, 2] > 150)).sum())
    print(f'  {name}: saved {path}  ({ms:.0f} ms)  red px {red}  amber px {amber}  magenta px {magenta}')
    return red, amber, magenta


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


def main():
    print('Blender', bpy.app.version_string)
    gpu.init()
    print('GPU:', gpu.platform.vendor_get(), '|', gpu.platform.renderer_get(), '|',
          gpu.platform.version_get(), '| backend', gpu.platform.backend_type_get())

    sh = overlay._shaders()
    assert sh['hatch'] is not None, 'the hatch shader did not compile'
    print('  hatch shader compiled')

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
    st.enabled = True
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
    render(mon, st, 'collision_closeup', (150, -20, 150), (112, 110, 64))
    render(mon, st, 'clearance_closeup', (330, -10, 110), (230, 90, 68))
    render(mon, st, 'outside_closeup', (520, 60, 150), (350, 200, 60))
    render(mon, st, 'ortho_top', (190, 142, 900), (190, 142, 0), is_ortho=True, ortho_scale=420.0,
           up=(0, 1, 0))

    # whole-part shading of the colliding parts, and its fallback
    pr = props.prefs()
    st.show_tint = False
    r0, _, _ = render(mon, st, 'tint_off', (150, -20, 150), (112, 110, 64))
    st.show_tint = True
    r1, _, _ = render(mon, st, 'tint_on', (150, -20, 150), (112, 110, 64))
    assert len(overlay._state.get('tint', {})) == 2, 'one cached mesh per colliding part expected'
    pr.tint_budget = 0.0                       # no triangle budget: outline boxes instead
    r2, _, _ = render(mon, st, 'tint_boxes', (150, -20, 150), (112, 110, 64))
    pr.tint_budget = 4.0
    print(f'  red pixels: tint off {r0}, on {r1}, outline boxes {r2}')
    assert r2 > r0, 'outline boxes of the colliding parts are missing'

    # a clean build: the badge must turn green
    bpy.data.objects['Ring'].location.z = 150
    bpy.data.objects['Pin'].location.z = 115
    bpy.data.objects['Head'].location = (300, 200, 60)
    bpy.context.view_layer.update()
    mon = settle()
    print('  status after fixing:', mon.status())
    r, a, m = render(mon, st, 'clean', (520, -330, 330), (180, 130, 60))
    img_ok = mon.status()['collisions'] == 0 and mon.status()['partly_out'] == 0
    assert img_ok, mon.status()
    print('GPU TEST OK')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('GPU TEST FAILED')
    sys.exit(1)
