# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic shapes and buckets for the packer (docs/AUTOPACK.md §15).  In
the add-on, so the speed measurement can run on the owner's machine.

    python3 benchmarks/synthetic.py --list
    python3 benchmarks/synthetic.py --write out_dir      # each bucket as a 3MF, unpacked

The 3MF files hold the parts side by side (not packed), one build item per
copy, so the same buckets can be packed in commercial packers for a baseline.
Every shape is a closed mesh in millimetres; none of them is anybody's
real part.
"""

import math
import os

import numpy as np

from .meshio import Mesh, write_3mf
from .problem import Part


# ------------------------------------------------------------------ shapes

def _mesh(v, f, name):
    return Mesh(np.asarray(v, float), np.asarray(f, np.int32), name)


def box(sx, sy, sz, name='box'):
    x, y, z = sx / 2, sy / 2, sz / 2
    v = [[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z],
         [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]]
    f = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
         [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return _mesh(v, f, name)


def sphere(r, segs=48, rings=24, name='sphere', center=(0, 0, 0)):
    v = [[0, 0, r]]
    for i in range(1, rings):
        th = math.pi * i / rings
        for j in range(segs):
            ph = 2 * math.pi * j / segs
            v.append([r * math.sin(th) * math.cos(ph), r * math.sin(th) * math.sin(ph), r * math.cos(th)])
    v.append([0, 0, -r])
    f = []
    for j in range(segs):
        f.append([0, 1 + j, 1 + (j + 1) % segs])
    for i in range(rings - 2):
        a, b = 1 + i * segs, 1 + (i + 1) * segs
        for j in range(segs):
            j2 = (j + 1) % segs
            f += [[a + j, b + j, b + j2], [a + j, b + j2, a + j2]]
    last = len(v) - 1
    a = 1 + (rings - 2) * segs
    for j in range(segs):
        f.append([last, a + (j + 1) % segs, a + j])
    return _mesh(np.asarray(v) + center, f, name)


def cylinder(r, length, segs=24, name='cylinder'):
    """Along z, centred on the origin."""
    ang = np.linspace(0, 2 * np.pi, segs, endpoint=False)
    bot = np.stack([r * np.cos(ang), r * np.sin(ang), np.full(segs, -length / 2)], 1)
    top = bot + [0, 0, length]
    v = np.concatenate([bot, top, [[0, 0, -length / 2], [0, 0, length / 2]]])
    cb, ct = 2 * segs, 2 * segs + 1
    f = []
    for i in range(segs):
        j = (i + 1) % segs
        f += [[i, j, segs + j], [i, segs + j, segs + i], [cb, j, i], [ct, segs + i, segs + j]]
    return _mesh(v, f, name)


def merge(meshes, name):
    vs, fs, n = [], [], 0
    for m in meshes:
        vs.append(m.verts)
        fs.append(m.tris + n)
        n += len(m.verts)
    return _mesh(np.concatenate(vs), np.concatenate(fs), name)


def transformed(m, A=None, t=(0, 0, 0)):
    A = np.eye(3) if A is None else np.asarray(A, float)
    return _mesh(m.verts @ A.T + t, m.tris, m.name)


def rot(axis, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if axis == 'x':
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == 'y':
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def figurine(scale=1.0, name='figurine'):
    """A head and body (two spheres) holding a spear (a thin rod through the body)."""
    body = sphere(9 * scale, 32, 16, center=(0, 0, 0))
    head = sphere(5 * scale, 24, 12, center=(0, 0, 13 * scale))
    spear = transformed(cylinder(0.8 * scale, 60 * scale, 12), rot('y', 25), (8 * scale, 0, 6 * scale))
    return merge([body, head, spear], name)


def pringle(radius=30.0, curve=40.0, thick=2.0, rings=16, segs=48, name='pringle'):
    """A saddle-shaped chip, `thick` thick: z = (x^2 - y^2) / (2 curve)."""
    pts = [(0.0, 0.0)]
    for i in range(1, rings + 1):
        rr = radius * i / rings
        for j in range(segs):
            a = 2 * math.pi * j / segs
            pts.append((rr * math.cos(a), rr * math.sin(a)))
    pts = np.array(pts)
    z = (pts[:, 0] ** 2 - pts[:, 1] ** 2) / (2 * curve)
    n = len(pts)
    top = np.column_stack([pts, z + thick / 2])
    bot = np.column_stack([pts, z - thick / 2])
    v = np.concatenate([top, bot])
    f = []
    for j in range(segs):
        j2 = (j + 1) % segs
        f.append([0, 1 + j, 1 + j2])
        f.append([n, n + 1 + j2, n + 1 + j])
    for i in range(rings - 1):
        a, b = 1 + i * segs, 1 + (i + 1) * segs
        for j in range(segs):
            j2 = (j + 1) % segs
            f += [[a + j, b + j, b + j2], [a + j, b + j2, a + j2]]
            f += [[n + a + j, n + b + j2, n + b + j], [n + a + j, n + a + j2, n + b + j2]]
    a = 1 + (rings - 1) * segs
    for j in range(segs):
        j2 = (j + 1) % segs
        f += [[a + j, n + a + j, n + a + j2], [a + j, n + a + j2, a + j2]]
    return _mesh(v, f, name)


def cup(R, r, H, floor, segs=48, name='cup', square=False):
    """A cup standing on z = 0, open at the top: a solid with an open cavity."""
    ang = np.linspace(0, 2 * np.pi, segs, endpoint=False) + (math.pi / 4 if square else 0.0)
    k = math.sqrt(2) if square else 1.0
    c, s = np.cos(ang) * k, np.sin(ang) * k
    v = np.concatenate([
        np.stack([R * c, R * s, np.zeros(segs)], 1),
        np.stack([R * c, R * s, np.full(segs, H)], 1),
        np.stack([r * c, r * s, np.full(segs, H)], 1),
        np.stack([r * c, r * s, np.full(segs, floor)], 1),
        [[0, 0, 0], [0, 0, floor]]])
    cb, cf = 4 * segs, 4 * segs + 1
    f = []
    for i in range(segs):
        j = (i + 1) % segs
        ob, ot, it, ib = i, segs + i, 2 * segs + i, 3 * segs + i
        ob2, ot2, it2, ib2 = j, segs + j, 2 * segs + j, 3 * segs + j
        f += [[ob, ob2, ot2], [ob, ot2, ot], [ot, ot2, it2], [ot, it2, it],
              [it, it2, ib2], [it, ib2, ib], [ib, ib2, cf], [ob2, ob, cb]]
    return _mesh(v, f, name)


def socket(name='socket'):
    """A prosthetic-socket-like cup: 80 mm across, 140 mm high, 4 mm walls."""
    return cup(40.0, 36.0, 140.0, 4.0, 64, name)


def insole(name='insole'):
    """A flat elongated plate, 90 x 30 x 4 mm, rounded in plan."""
    pts = []
    for j in range(40):
        a = 2 * math.pi * j / 40
        pts.append((45 * math.cos(a), 15 * math.sin(a)))
    pts = np.array(pts)
    n = len(pts)
    v = np.concatenate([np.column_stack([pts, np.full(n, -2.0)]), np.column_stack([pts, np.full(n, 2.0)]),
                        [[0, 0, -2.0], [0, 0, 2.0]]])
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [[i, j, n + j], [i, n + j, n + i], [2 * n, j, i], [2 * n + 1, n + i, n + j]]
    return _mesh(v, f, name)


def keycap(name='keycap'):
    """An 18 x 18 x 9 mm square shell, open at the bottom (turned cup)."""
    m = cup(9.0, 7.5, 9.0, 1.5, 4, name, square=True)
    return transformed(m, rot('x', 180), (0, 0, 9.0))


def big_keycap(name='big keycap'):
    """A 34 x 34 x 22 mm square shell, 1.5 mm walls, open at the bottom."""
    m = cup(17.0, 15.5, 22.0, 1.5, 4, name, square=True)
    return transformed(m, rot('x', 180), (0, 0, 22.0))


def ring(name='ring'):
    R, rr, segs, sides = 22.0, 5.0, 48, 16
    v, f = [], []
    for i in range(segs):
        a = 2 * math.pi * i / segs
        for j in range(sides):
            b = 2 * math.pi * j / sides
            v.append([(R + rr * math.cos(b)) * math.cos(a), (R + rr * math.cos(b)) * math.sin(a), rr * math.sin(b)])
    for i in range(segs):
        for j in range(sides):
            a, b = i * sides + j, ((i + 1) % segs) * sides + j
            c, d = ((i + 1) % segs) * sides + (j + 1) % sides, i * sides + (j + 1) % sides
            f += [[a, b, c], [a, c, d]]
    return _mesh(v, f, name)


def bracket(name='bracket'):
    """An L-shaped bracket, 60 x 40 x 40 mm, 6 mm thick."""
    a = transformed(box(60, 40, 6), t=(0, 0, 3))
    b = transformed(box(6, 40, 40), t=(-27, 0, 20))
    return merge([a, b], name)


# ------------------------------------------------------------------ buckets

def buckets():
    """name -> (description, [Part], settings): settings are keyword
    arguments for pack.problem.Settings (the gap the owner would use)."""
    return {
        'bureau-day': ('mixed service-bureau day: blocks, balls, brackets, figurines, chips, keycaps', [
            Part('block', box(40, 40, 40, 'block'), 6, 'z'),
            Part('ball', sphere(20, name='ball'), 8, 'fixed'),
            Part('bracket', bracket(), 10, 'free'),
            Part('figurine', figurine(), 10, 'z'),
            Part('pringle', pringle(), 12, 'free'),
            Part('keycap', keycap(), 30, 'z'),
            Part('ring', ring(), 6, 'free'),
        ], {'gap': 3.0}),
        'pringles': ('150 saddle-shaped chips, free rotation', [
            Part('pringle', pringle(), 150, 'free'),
        ], {'gap': 2.0}),
        'figurines': ('80 figurines with spears, turned about z', [
            Part('figurine', figurine(), 80, 'z'),
        ], {'gap': 1.0}),
        'sockets': ('6 sockets upright and 24 insoles, which can go inside them', [
            Part('socket', socket(), 6, 'fixed'),
            Part('insole', insole(), 24, 'free'),
        ], {'gap': 3.0}),
        'keycaps': ('81 large keycaps, free rotation: flat they need two layers, standing one', [
            Part('keycap', big_keycap(), 81, 'free'),
        ], {'gap': 3.0}),
        'giant': ('a 200 mm square housing, open at the top, and 30 keycaps (which may go inside)', [
            Part('housing', cup(100.0, 94.0, 180.0, 6.0, 4, 'housing', square=True), 1, 'fixed'),
            Part('keycap', keycap(), 30, 'z'),
        ], {'gap': 3.0}),
    }


def write_unpacked(path, parts):
    """The parts side by side in rows (not packed), one build item per copy."""
    items = []
    x = y = 0.0
    row = 0.0
    for p in parts:
        V = p.mesh.verts
        lo, hi = V.min(0), V.max(0)
        for c in range(p.quantity):
            if x + (hi[0] - lo[0]) > 2000:
                x = 0.0
                y += row + 10
                row = 0.0
            M = np.eye(4)
            M[:3, 3] = [x - lo[0], y - lo[1], -lo[2]]
            items.append((p.mesh, M, '%s-%d' % (p.name, c + 1)))
            x += hi[0] - lo[0] + 10
            row = max(row, hi[1] - lo[1])
    write_3mf(path, items)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--write', metavar='DIR')
    a = ap.parse_args()
    for name, (desc, parts, _) in buckets().items():
        n = sum(p.quantity for p in parts)
        print('%-11s %3d parts  %s' % (name, n, desc))
        if a.write:
            os.makedirs(a.write, exist_ok=True)
            write_unpacked(os.path.join(a.write, name + '.3mf'), parts)
