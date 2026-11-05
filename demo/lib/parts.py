# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic mechanical parts (gears, bolts, nuts, pulleys...) as closed
triangle meshes in mm, plus `write_bucket()` which lays a mix of them out
unpacked in a 3MF for Bucket Builder's packer.  Plain numpy, no Blender.

Most shapes are "radial solids": stacked rings around the z axis whose radius
may vary with angle (gear teeth, hex flats, knurls), optionally with a bore.
"""
import math

import numpy as np

M = 96          # points per ring (divisible by 6 and by most tooth counts used)


def _theta(m=M):
    return np.linspace(0.0, 2.0 * math.pi, m, endpoint=False)


def radial_solid(outer, inner=None, m=M):
    """outer/inner: lists of (z, r) with r a scalar or an array of m radii,
    from bottom to top.  inner (a bore) must start and end at the same z as
    outer.  Returns (verts, tris), outward-facing."""
    th = _theta(m)
    c, s = np.cos(th), np.sin(th)
    V, F = [], []

    def ring(z, r):
        r = np.broadcast_to(np.asarray(r, float), (m,))
        i0 = len(V)
        V.extend(np.c_[r * c, r * s, np.full(m, z)])
        return np.arange(i0, i0 + m)

    def strip(a, b):                      # quads between two rings, a below / inside b
        for i in range(m):
            j = (i + 1) % m
            F.append((a[i], a[j], b[j]))
            F.append((a[i], b[j], b[i]))

    O = [ring(z, r) for z, r in outer]
    for a, b in zip(O, O[1:]):
        strip(a, b)
    if inner is None:
        for k, sign in ((0, -1), (-1, 1)):
            ctr = len(V)
            V.append((0.0, 0.0, outer[k][0]))
            R = O[k]
            for i in range(m):
                j = (i + 1) % m
                F.append((ctr, R[i], R[j]) if sign > 0 else (ctr, R[j], R[i]))
    else:
        Ii = [ring(z, r) for z, r in inner]
        for a, b in zip(Ii, Ii[1:]):
            strip(b, a)                  # bore faces point inwards (towards the axis)
        strip(Ii[0], O[0])               # bottom annulus
        strip(O[-1], Ii[-1])             # top annulus
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    # flat annuli above were wound for an upward-facing top; fix the global orientation
    vol = np.einsum('ij,ij->i', V[F[:, 0]], np.cross(V[F[:, 1]], V[F[:, 2]])).sum()
    if vol < 0:
        F = F[:, ::-1]
    return V, F


# ------------------------------------------------------------------ profiles

def gear_r(teeth, module, m=M):
    """Radius per angle of a spur gear outline (trapezoidal teeth, rounded a little)."""
    th = _theta(m)
    r_tip = module * (teeth + 2) / 2.0
    r_root = module * (teeth - 2.5) / 2.0
    ph = (th * teeth / (2 * math.pi)) % 1.0            # 0..1 within one tooth pitch
    # tooth: flat top over 30 % of the pitch, flanks over 20 % each, root 30 %
    up = np.clip((ph - 0.05) / 0.2, 0, 1)
    down = np.clip((0.75 - ph) / 0.2, 0, 1)
    k = np.minimum(up, down)
    k = 0.5 - 0.5 * np.cos(math.pi * k)
    return r_root + (r_tip - r_root) * k


def hex_r(across_flats, m=M):
    th = _theta(m)
    a = (th + math.pi / 6) % (math.pi / 3) - math.pi / 6
    return (across_flats / 2.0) / np.cos(a)


def knurl_r(r, n, depth, m=M):
    th = _theta(m)
    return r - depth * (0.5 + 0.5 * np.cos(th * n))


# ------------------------------------------------------------------ parts

def spur_gear(teeth=24, module=2.0, width=8.0, bore=5.0, hub=None):
    g = gear_r(teeth, module)
    outer = [(0.0, g), (width, g)]
    if hub:
        hr, hh = hub
        outer += [(width, hr), (width + hh, hr)]
    top = outer[-1][0]
    return radial_solid(outer, [(0.0, bore), (top, bore)])


def hex_bolt(d=8.0, length=40.0, head_af=13.0, head_h=5.5, pitch=1.25):
    """Head at the bottom, threaded shank up, chamfered tip."""
    outer = [(0.0, hex_r(head_af) * 0.97), (0.4, hex_r(head_af)), (head_h - 0.4, hex_r(head_af)),
             (head_h, hex_r(head_af) * 0.97), (head_h, d / 2 + 0.6), (head_h + 0.6, d / 2)]
    z = head_h + 0.6 + 2.0
    outer.append((z, d / 2))
    rmin = d / 2 - 0.55 * pitch
    while z + pitch < head_h + length - 1.0:
        outer.append((z + pitch / 2, rmin))
        outer.append((z + pitch, d / 2))
        z += pitch
    outer += [(head_h + length - 0.8, d / 2), (head_h + length, d / 2 - 0.8)]
    return radial_solid(outer)


def hex_nut(d=8.0, af=13.0, h=6.5):
    hx = hex_r(af)
    outer = [(0.0, np.minimum(hx, af * 0.55)), (0.6, hx), (h - 0.6, hx), (h, np.minimum(hx, af * 0.55))]
    return radial_solid(outer, [(0.0, d / 2 - 0.4), (h, d / 2 - 0.4)])


def washer(d=8.0, od=16.0, t=1.6):
    return radial_solid([(0.0, od / 2), (t, od / 2)], [(0.0, d / 2 + 0.2), (t, d / 2 + 0.2)])


def pulley(od=40.0, w=14.0, bore=6.0, groove=5.0):
    r = od / 2
    outer = [(0.0, r), (1.5, r), (w / 2, r - groove), (w - 1.5, r), (w, r)]
    return radial_solid(outer, [(0.0, bore / 2), (w, bore / 2)])


def knob(d=30.0, h=16.0, bore=6.0, n=18):
    kr = knurl_r(d / 2, n, 1.2)
    outer = [(0.0, kr * 0.95), (1.0, kr), (h - 3.0, kr), (h, kr * 0.85), (h, d * 0.18), (h + 6.0, d * 0.18)]
    return radial_solid(outer, [(0.0, bore / 2), (h + 6.0, bore / 2)])


def standoff(af=8.0, length=25.0, bore=3.0):
    hx = hex_r(af)
    return radial_solid([(0.0, hx), (length, hx)], [(0.0, bore / 2), (length, bore / 2)])


def bearing_housing(od=52.0, h=18.0, bore=30.0, flange=70.0, ft=5.0):
    outer = [(0.0, flange / 2), (ft, flange / 2), (ft, od / 2), (h, od / 2)]
    return radial_solid(outer, [(0.0, bore / 2 - 3.0), (2.0, bore / 2 - 3.0), (2.0, bore / 2), (h, bore / 2)])


def sprocket(teeth=18, pitch=8.0, width=5.0, bore=8.0):
    th = _theta()
    r_tip = pitch / (2 * math.sin(math.pi / teeth)) + 2.0
    ph = (th * teeth / (2 * math.pi)) % 1.0
    r = r_tip - 3.2 * np.sqrt(np.clip(1 - ((ph - 0.5) / 0.32) ** 2, 0, 1))
    return radial_solid([(0.0, r), (width, r)], [(0.0, bore / 2), (width, bore / 2)])


def hollow_sphere(r_out=40.0, r_in=33.0, segs=96, rings=48):
    """Same part as the Crosscut demo's sphere (outer + reversed inner shell)."""
    def uv(r, flip):
        V = [(0, 0, -r)]
        for i in range(1, rings):
            phi = -math.pi / 2 + math.pi * i / rings
            for j in range(segs):
                t = 2 * math.pi * j / segs
                V.append((r * math.cos(phi) * math.cos(t), r * math.cos(phi) * math.sin(t), r * math.sin(phi)))
        V.append((0, 0, r))
        F = []
        top = len(V) - 1
        for j in range(segs):
            F.append((0, 1 + (j + 1) % segs, 1 + j))
        for i in range(rings - 2):
            a, b = 1 + i * segs, 1 + (i + 1) * segs
            for j in range(segs):
                k = (j + 1) % segs
                F += [(a + j, a + k, b + k), (a + j, b + k, b + j)]
        a = 1 + (rings - 2) * segs
        for j in range(segs):
            F.append((a + j, a + (j + 1) % segs, top))
        F = np.asarray(F, np.int64)
        return np.asarray(V, float), (F[:, ::-1] if flip else F)
    V1, F1 = uv(r_out, False)
    V2, F2 = uv(r_in, True)
    return np.vstack([V1, V2]), np.vstack([F1, F2 + len(V1)])


# The intruding part of the section demo: a gear with a hub.
def demo_gear():
    return spur_gear(teeth=20, module=2.5, width=10.0, bore=6.0, hub=(11.0, 8.0))


# ------------------------------------------------------------------ the bucket mix

# (kind, maker, copies, colour)  colours are sRGB, one hue per family
MIX = [
    ('gear-l', lambda: spur_gear(48, 2.0, 10, 8, hub=(16, 10)), 5, (0.27, 0.53, 0.82)),
    ('gear-m', lambda: spur_gear(32, 1.5, 8, 5, hub=(10, 6)), 10, (0.36, 0.62, 0.88)),
    ('gear-s', lambda: spur_gear(16, 1.5, 7, 4), 22, (0.46, 0.70, 0.93)),
    ('sprocket', lambda: sprocket(), 8, (0.30, 0.69, 0.66)),
    ('bolt-l', lambda: hex_bolt(10, 70, 16, 6.5, 1.5), 12, (0.85, 0.62, 0.22)),
    ('bolt-m', lambda: hex_bolt(8, 45, 13, 5.5, 1.25), 26, (0.90, 0.70, 0.30)),
    ('bolt-s', lambda: hex_bolt(6, 25, 10, 4.0, 1.0), 30, (0.94, 0.78, 0.40)),
    ('nut', lambda: hex_nut(8, 13, 6.5), 36, (0.75, 0.75, 0.78)),
    ('nut-l', lambda: hex_nut(10, 16, 8), 20, (0.66, 0.66, 0.70)),
    ('washer', lambda: washer(10, 20, 2.0), 24, (0.58, 0.60, 0.64)),
    ('pulley', lambda: pulley(), 10, (0.84, 0.40, 0.33)),
    ('knob', lambda: knob(), 12, (0.55, 0.42, 0.75)),
    ('standoff', lambda: standoff(), 24, (0.40, 0.72, 0.42)),
    ('housing', lambda: bearing_housing(), 6, (0.88, 0.50, 0.30)),
]


def write_bucket(path, with_demo=True, scale=1.6):
    """All parts side by side (unpacked), item names '<kind>-<n>'."""
    import sys
    import os
    sys.path.insert(0, os.path.expanduser('~/dev/blender-bucket-builder/bucket_builder'))
    from pack.meshio import Mesh, write_3mf
    items, x, y, row = [], 0.0, 0.0, 0.0
    makers = [(k, mk, int(round(n * scale)), c) for k, mk, n, c in MIX]
    if with_demo:
        makers = [('sphere', hollow_sphere, 1, None), ('demo-gear', demo_gear, 1, None)] + makers
    for kind, make, n, _ in makers:
        V, F = make()
        mesh = Mesh(V, F.astype(np.int32), kind)
        lo, hi = V.min(0), V.max(0)
        for c in range(n):
            if x + hi[0] - lo[0] > 2000:
                x, y, row = 0.0, y + row + 10, 0.0
            T = np.eye(4)
            T[:3, 3] = [x - lo[0], y - lo[1], -lo[2]]
            items.append((mesh, T, '%s-%d' % (kind, c + 1)))
            x += hi[0] - lo[0] + 10
            row = max(row, hi[1] - lo[1])
    write_3mf(path, items)
    return len(items)


if __name__ == '__main__':
    import sys
    print(write_bucket(sys.argv[1]), 'items')
