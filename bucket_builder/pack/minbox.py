# SPDX-License-Identifier: GPL-3.0-or-later
"""Smallest boxes: the turn that gives a part its smallest bounding box, or its lowest height,
from its own convex hull. NumPy only, so the packer can use it as well as the Arrange tools
(moved here from arrange.py, 2026-11-24, for the packer's box pass).

Bullet's hull (`bmesh.ops.convex_hull`) is missing from some builds, so the hull is our own:
incremental, on about 1200 extreme points along a Fibonacci sphere of directions.
"""

import math

import numpy as np

MAX_NORMALS = 400        # hull face directions tried for the smallest box


def _extremes(points, n=600):
    """The points that are furthest out along ``n`` directions spread over
    the sphere: a few hundred points whose hull is close to the hull of all
    of them."""
    k = np.arange(n) + 0.5
    phi = np.arccos(1.0 - 2.0 * k / n)
    th = math.pi * (1.0 + 5.0 ** 0.5) * k
    d = np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)
    pts = points
    if len(pts) > 200000:
        pts = pts[np.random.default_rng(0).choice(len(pts), 200000, replace=False)]
    pr = pts @ d.T
    idx = np.unique(np.concatenate([pr.argmax(axis=0), pr.argmin(axis=0)]))
    return pts[idx]


def _hull(points):
    """Convex hull of a part: (vertices (k, 3), outward face normals (f, 3),
    face areas (f,)).  Made from the part's extreme points (see
    ``_extremes``), incrementally: what it is for is choosing an
    orientation, and the final box is measured on all points."""
    pts = _extremes(np.asarray(points, dtype=np.float64))
    span = float(np.ptp(pts, axis=0).max()) if len(pts) else 0.0
    eps = 1e-9 * max(span, 1e-30)
    if len(pts) < 4 or span <= 0.0:
        raise ValueError("the part is flat or empty")
    # a first tetrahedron of points far apart
    i0 = int(np.argmin(pts[:, 0]))
    i1 = int(np.argmax(np.linalg.norm(pts - pts[i0], axis=1)))
    line = pts[i1] - pts[i0]
    i2 = int(np.argmax(np.linalg.norm(np.cross(pts - pts[i0], line), axis=1)))
    nrm0 = np.cross(line, pts[i2] - pts[i0])
    i3 = int(np.argmax(np.abs((pts - pts[i0]) @ nrm0)))
    if abs(float((pts[i3] - pts[i0]) @ nrm0)) <= eps * float(np.linalg.norm(nrm0)):
        raise ValueError("the part is flat")
    inside = pts[[i0, i1, i2, i3]].mean(axis=0)
    faces = []

    def add(a, b, c):
        n = np.cross(pts[b] - pts[a], pts[c] - pts[a])
        if np.dot(n, pts[a] - inside) < 0.0:
            b, c = c, b
            n = -n
        ln = float(np.linalg.norm(n))
        faces.append((a, b, c, n / ln if ln > 0 else n, float(np.dot(n / ln if ln > 0 else n, pts[a]))))

    for f in ((i0, i1, i2), (i0, i1, i3), (i0, i2, i3), (i1, i2, i3)):
        add(*f)
    for p in range(len(pts)):
        if p in (i0, i1, i2, i3):
            continue
        q = pts[p]
        seen = [f for f in faces if float(np.dot(f[3], q)) - f[4] > eps]
        if not seen:
            continue
        edges = {}
        for a, b, c, _, _ in seen:
            for e in ((a, b), (b, c), (c, a)):
                if (e[1], e[0]) in edges:
                    del edges[(e[1], e[0])]
                else:
                    edges[e] = True
        ids = {id(f) for f in seen}
        faces[:] = [f for f in faces if id(f) not in ids]
        for a, b in edges:
            add(a, b, p)
    nrm = np.array([f[3] for f in faces])
    tri = np.array([[f[0], f[1], f[2]] for f in faces])
    area = 0.5 * np.linalg.norm(np.cross(pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]]), axis=1)
    used = np.unique(tri)
    return pts[used], nrm, area


def _directions(nrm, area, limit):
    """Distinct directions among face normals (to about a degree), the ones
    with the most area first."""
    key = np.round(nrm * 60.0).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    tot = np.bincount(inv, weights=area)
    vec = np.stack([np.bincount(inv, weights=area * nrm[:, c]) for c in range(3)], axis=1)
    vec /= np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-30)
    order = np.argsort(-tot)[:limit]
    return vec[order], tot[order]


def _basis(n):
    """Two unit vectors that make (u, v, n) a right-handed frame."""
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(a, n)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def _min_rect(p2):
    """The angle at which a 2-D point set has the smallest bounding rectangle,
    and that area.  (The best angle is that of an edge of the convex hull;
    a fine search over a quarter turn finds it closely enough.)"""
    def areas(angles):
        c, s = np.cos(angles), np.sin(angles)
        x = p2[:, :1] * c + p2[:, 1:] * s
        y = -p2[:, :1] * s + p2[:, 1:] * c
        return (x.max(axis=0) - x.min(axis=0)) * (y.max(axis=0) - y.min(axis=0))
    a = np.linspace(0.0, 0.5 * math.pi, 91)
    best = a[np.argmin(areas(a))]
    a = best + np.linspace(-math.radians(1.0), math.radians(1.0), 81)
    ar = areas(a)
    k = int(np.argmin(ar))
    return float(a[k]), float(ar[k])


def _frame(n, hv):
    """Rows (u, v, n): n, and in its plane the directions of the smallest
    rectangle around the points ``hv``."""
    u, v = _basis(n)
    ang, _ = _min_rect(np.stack([hv @ u, hv @ v], axis=1))
    c, s = math.cos(ang), math.sin(ang)
    return np.array([c * u + s * v, -s * u + c * v, n])


def rotation_min_height(points):
    """A rotation (3, 3) that makes the part as low as possible: a face of its
    hull on the floor, and its footprint turned to the smallest rectangle."""
    hv, nrm, area = _hull(points)
    dirs, _ = _directions(nrm, area, 100000)
    proj = hv @ dirs.T
    width = proj.max(axis=0) - proj.min(axis=0)
    n = dirs[int(np.argmin(width))]
    F = _frame(n, hv)
    F[2] = -n                     # (that face down: its outward normal points to the floor)
    F[1] = np.cross(F[2], F[0])   # keep it a rotation
    return F


def rotation_min_volume(points):
    """A rotation that makes the part's bounding box smallest; of its three
    sides the shortest is put upright (lowest too)."""
    hv, nrm, area = _hull(points)
    dirs, _ = _directions(nrm, area, MAX_NORMALS)
    best = None
    for n in dirs:
        F = _frame(n, hv)
        p = hv @ F.T
        ext = p.max(axis=0) - p.min(axis=0)
        vol = float(ext.prod())
        if best is None or vol < best[0] - 1e-12 * vol:
            best = (vol, F, ext)
    _, F, ext = best
    order = np.argsort(-ext)      # longest along X, shortest along Z
    F = F[order]
    if np.linalg.det(F) < 0.0:
        F[1] = -F[1]
    return F
