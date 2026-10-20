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


def prism(poly, depth, name='prism'):
    """A convex polygon in the x-z plane (counter-clockwise or not),
    extruded along y from 0 to depth."""
    P = np.asarray(poly, float)
    n = len(P)
    v = [(x, 0.0, z) for x, z in P] + [(x, depth, z) for x, z in P]
    f = []
    for i in range(1, n - 1):
        f += [(0, i + 1, i), (n, n + i, n + i + 1)]
    for i in range(n):
        j = (i + 1) % n
        f += [(i, j, n + j), (i, n + j, n + i)]
    v = np.asarray(v)
    f = np.asarray(f)
    c = v.mean(0)                                     # (convex: outward is away from the centre)
    T = v[f]
    nrm = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    inward = ((T.mean(1) - c) * nrm).sum(1) < 0
    f[inward] = f[inward][:, ::-1]
    return _mesh(v, f, name)


def box_at(lo, hi, name='box'):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return transformed(box(*(hi - lo), name=name), t=(lo + hi) / 2)


def wedge(length=60.0, height=25.0, width=30.0, name='wedge'):
    """A door stop: a right-angled triangle extruded (pairs of them, one
    turned over, make a block)."""
    return prism([(0, 0), (length, 0), (0, height)], width, name)


def gusset(leg=50.0, thick=6.0, name='gusset'):
    """A right-angled triangular plate."""
    return transformed(prism([(0, 0), (leg, 0), (0, leg)], thick, name), rot('x', 90))


def trapezoid(name='trapezoid'):
    return prism([(0, 0), (60, 0), (45, 25), (15, 25)], 30, name)


def half_disc(r=30.0, thick=6.0, name='half disc'):
    pts = [(r * math.cos(a), r * math.sin(a)) for a in np.linspace(0, math.pi, 25)]
    return transformed(prism(pts, thick, name), rot('x', 90))


def tee(name='tee'):
    """A flat T, 60 x 50 mm, 6 mm thick."""
    return merge([box_at((0, 0, 0), (60, 12, 6)), box_at((24, 12, 0), (36, 50, 6))], name)


def l_plate(name='L plate'):
    """A flat L, 50 x 50 mm, arms 12 mm wide, 6 mm thick."""
    return merge([box_at((0, 0, 0), (50, 12, 6)), box_at((0, 12, 0), (12, 50, 6))], name)


def angle(name='angle'):
    """A length of angle: two 30 mm legs, 4 mm thick, 60 mm long."""
    return merge([box_at((0, 0, 0), (30, 60, 4)), box_at((0, 0, 4), (4, 60, 30))], name)


def peg(head=16.0, shank=5.0, length=30.0, name='peg'):
    """A knob: a 32 mm disc, 4 mm thick, on a 10 mm stem."""
    return merge([cylinder(head, 4.0, 32),
                  transformed(cylinder(shank, length, 24), t=(0, 0, 2.0 + length / 2))], name)


def _ear_clip(P):
    """Triangles of a simple polygon (n x 2, counter-clockwise), by ear
    clipping: indices into P."""
    idx = list(range(len(P)))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        n = len(idx)
        for j in range(n):
            i0, i1, i2 = idx[j - 1], idx[j], idx[(j + 1) % n]
            a, b, c = P[i0], P[i1], P[i2]
            if cross(a, b, c) <= 1e-12:
                continue                                  # (reflex or flat)
            inside = False
            for k in idx:
                if k in (i0, i1, i2):
                    continue
                q = P[k]
                if cross(a, b, q) >= -1e-12 and cross(b, c, q) >= -1e-12 and cross(c, a, q) >= -1e-12:
                    inside = True
                    break
            if not inside:
                tris.append((i0, i1, i2))
                idx.pop(j)
                break
        else:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def extrude(poly, depth, name='extrusion'):
    """Any simple polygon in the x-y plane extruded along z from 0 to depth."""
    P = np.asarray(poly, float)
    area = 0.5 * float(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]))
    if area < 0:
        P = P[::-1]
    n = len(P)
    v = np.concatenate([np.c_[P, np.zeros(n)], np.c_[P, np.full(n, depth)]])
    f = []
    for a, b, c in _ear_clip(P):
        f += [(a, c, b), (n + a, n + b, n + c)]
    for i in range(n):
        j = (i + 1) % n
        f += [(i, j, n + j), (i, n + j, n + i)]
    return _mesh(v, f, name)


def lathe(profile, segs=48, name='lathe'):
    """A closed profile [(r, z)] (counter-clockwise in the r-z plane) turned
    about the z axis; points with r = 0 lie on the axis."""
    Q = np.asarray(profile, float)
    ang = np.linspace(0, 2 * np.pi, segs, endpoint=False)
    v, rings = [], []
    for r, z in Q:
        if r <= 1e-12:
            rings.append([len(v)] * segs)
            v.append((0.0, 0.0, z))
        else:
            rings.append(list(range(len(v), len(v) + segs)))
            v += [(r * math.cos(a), r * math.sin(a), z) for a in ang]
    f = []
    m = len(Q)
    for p in range(m):
        A, B = rings[p], rings[(p + 1) % m]
        for i in range(segs):
            j = (i + 1) % segs
            for t in ((A[i], B[i], B[j]), (A[i], B[j], A[j])):
                if len(set(t)) == 3:
                    f.append(t)
    V, F = np.asarray(v, float), np.asarray(f, np.int64)
    if np.einsum('ij,ij->i', V[F[:, 0]], np.cross(V[F[:, 1]], V[F[:, 2]])).sum() < 0:
        F = F[:, ::-1]                                    # (outward, whichever way the profile ran)
    return _mesh(V, F, name)


def tapered_cup(r_top=30.0, r_bottom=22.0, height=40.0, wall=2.0, name='tapered cup'):
    """A cup that is wider at the top: copies nest inside each other."""
    return lathe([(0, 0), (r_bottom, 0), (r_top, height), (r_top - wall, height),
                  (r_bottom - wall, wall), (0, wall)], 48, name)


def dome(r=30.0, wall=2.5, name='dome'):
    """A hemispherical shell, open at the bottom."""
    out = [(r * math.cos(a), r * math.sin(a)) for a in np.linspace(0, math.pi / 2, 12)]
    inn = [((r - wall) * math.cos(a), (r - wall) * math.sin(a)) for a in np.linspace(math.pi / 2, 0, 12)]
    return lathe(out + inn, 48, name)


def hex_nut(across=20.0, hole=6.0, thick=8.0, name='hex nut'):
    R = across / math.sqrt(3.0)
    return lathe([(hole, 0), (R, 0), (R, thick), (hole, thick)], 6, name)


def star(points=6, r_out=25.0, r_in=11.0, thick=6.0, name='star'):
    P = [((r_out if i % 2 == 0 else r_in) * math.cos(math.pi * i / points),
          (r_out if i % 2 == 0 else r_in) * math.sin(math.pi * i / points)) for i in range(2 * points)]
    return extrude(P, thick, name)


def c_clip(R=20.0, r=14.0, opening=70.0, thick=8.0, name='C clip'):
    a0 = math.radians(opening / 2)
    angs = np.linspace(a0, 2 * math.pi - a0, 28)
    P = [(R * math.cos(a), R * math.sin(a)) for a in angs] + [(r * math.cos(a), r * math.sin(a)) for a in angs[::-1]]
    return extrude(P, thick, name)


def hook(name='hook'):
    """A flat J, 6 mm thick."""
    P = [(0, 0), (8, 0), (8, 50), (22, 50), (22, 38), (30, 38), (30, 58), (0, 58)]
    return extrude(P, 6.0, name)


def rod(length=200.0, r=4.0, name='rod'):
    return cylinder(r, length, 16, name)


def plate(sx=150.0, sy=100.0, t=3.0, name='plate'):
    return box(sx, sy, t, name)


def varied(make, n, seed, **ranges):
    """n meshes from make(**kw) with each keyword drawn from its (lo, hi)
    range: a family of parts that are alike but not the same."""
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        kw = {key: float(rng.uniform(lo, hi)) for key, (lo, hi) in ranges.items()}
        out.append(make(**kw))
    return out


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


def library():
    """More buckets, for experiments (not in the speed measurement): shapes
    chosen for how they pack.  name -> (description, [Part], settings)."""
    chips = varied(lambda radius, curve, thick: pringle(radius, curve, thick), 50, 5,
                   radius=(27, 33), curve=(36, 44), thick=(1.8, 2.4))
    knobs = varied(lambda head, shank, length: peg(head, shank, length), 40, 7,
                   head=(14, 18), shank=(4, 6), length=(25, 35))
    caps = [transformed(big_keycap(), np.eye(3) * f) for f in (0.6, 0.75, 1.0, 1.2)]
    P = lambda name, m, q, mode='free': Part(name, m, q, mode)
    return {
        'varied-chips': ('50 chips, each a little different (radius, bend, thickness)',
                         [P('chip-%02d' % k, m, 1) for k, m in enumerate(chips)], {'gap': 2.0}),
        'door-stops': ('150 wedges: pairs, one turned over, make blocks', [P('wedge', wedge(), 150)], {'gap': 2.0}),
        'gussets': ('300 triangular plates: pairs make squares', [P('gusset', gusset(), 300)], {'gap': 2.0}),
        'tees': ('200 flat tees: they interlock turned', [P('tee', tee(), 200)], {'gap': 2.0}),
        'knobs': ('150 knobs (a disc on a stem): heads beside stems', [P('knob', peg(), 150)], {'gap': 2.0}),
        'varied-knobs': ('40 knobs, each a little different', [P('knob-%02d' % k, m, 1) for k, m in enumerate(knobs)],
                         {'gap': 2.0}),
        'cups': ('60 tapered cups: they nest inside each other', [P('cup', tapered_cup(), 60)], {'gap': 2.0}),
        'domes': ('60 hemispherical shells', [P('dome', dome(), 60)], {'gap': 2.0}),
        'nuts': ('600 hex nuts: many small parts', [P('nut', hex_nut(), 600)], {'gap': 1.5}),
        'stars': ('200 six-pointed stars', [P('star', star(), 200)], {'gap': 2.0}),
        'clips': ('200 C clips', [P('clip', c_clip(), 200)], {'gap': 2.0}),
        'hooks': ('200 flat J hooks', [P('hook', hook(), 200)], {'gap': 2.0}),
        'angles': ('100 lengths of angle and 100 flat L plates: they nest along a diagonal',
                   [P('angle', angle(), 100), P('L plate', l_plate(), 100)], {'gap': 2.0}),
        'keycap-sizes': ('four sizes of keycap, 20 of each', [P('keycap-%d' % k, m, 20) for k, m in enumerate(caps)],
                         {'gap': 2.0}),
        'workshop': ('a mixed day: brackets, keycaps, knobs, cups, chips, hooks, nuts, plates, rods', [
            P('bracket', bracket(), 12), P('keycap', keycap(), 40, 'z'), P('knob', peg(), 30),
            P('cup', tapered_cup(), 6), P('chip', pringle(), 40), P('hook', hook(), 20),
            P('nut', hex_nut(), 24), P('plate', plate(), 4, 'z'), P('rod', rod(), 10)], {'gap': 2.0}),
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
