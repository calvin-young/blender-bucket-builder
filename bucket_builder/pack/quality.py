# SPDX-License-Identifier: GPL-3.0-or-later
"""Print-quality rules about large flat faces (docs/AUTOPACK.md §5.2).

The owner's rules for MJF builds (2026-10-21): large flat faces of two parts
parallel and close to each other invite defects ("elephant skin"), and so
do large flat faces parallel to the side walls of the bucket; a good layout
avoids both unless it has to.  These are soft goals: they never cost a part
in the bucket, they are weighed against height in the score.

A part's large flat faces are found once per orientation: its triangles
grouped by normal (within about 3 degrees) and plane (within 0.5 mm),
groups of at least `min_area` kept, each with its normal, plane offset,
area and the rectangle it covers in its own plane.  Placed, a face moves
with the part's translation.

* Facing pair: two faces of different parts with opposite normals, the
  second in front of the first by less than `near`, their rectangles
  overlapping: the overlap counts, more the closer they are.
* Wall face: a face whose normal is within 10 degrees of the x or y axis:
  its area counts.
"""

import math

import numpy as np

MIN_AREA = 2500.0           # mm^2: a flat face this large counts (50 x 50 mm)
NEAR = 10.0                 # mm: facing faces closer than this count
WALL_COS = math.cos(math.radians(10.0))
FACING_COS = -math.cos(math.radians(5.0))


class Faces:
    """Large flat faces of a part in one orientation, in the posed part's
    frame: n (k, 3) unit normals, d (k,) plane offsets (n . x = d), area
    (k,), u, v (k, 3) in-plane axes, box (k, 4) as (u0, u1, v0, v1)."""
    __slots__ = ('n', 'd', 'area', 'u', 'v', 'box')

    def __len__(self):
        return len(self.area)


def _basis(n):
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def flat_faces(verts, tris, A, min_area=MIN_AREA):
    """Faces of the mesh posed by the 3 x 3 map A."""
    V = np.asarray(verts, float) @ np.asarray(A, float).T
    T = V[np.asarray(tris)]
    cr = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ar = 0.5 * np.linalg.norm(cr, axis=1)
    f = Faces()
    ok = ar > 1e-9
    if not ok.any() or ar.sum() < min_area:
        f.n = f.u = f.v = np.zeros((0, 3))
        f.d = f.area = np.zeros(0)
        f.box = np.zeros((0, 4))
        return f
    T, cr, ar = T[ok], cr[ok], ar[ok]
    nrm = cr / (2.0 * ar[:, None])
    qn = np.rint(nrm * 20.0).astype(np.int64)                    # (about 3 degrees)
    off = np.einsum('ij,ij->i', nrm, T[:, 0])
    qd = np.rint(off / 0.5).astype(np.int64)
    key = np.column_stack([qn, qd])
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    tot = np.bincount(inv, weights=ar, minlength=len(uniq))
    big = np.nonzero(tot >= min_area)[0]
    ns, ds, areas, us, vs, boxes = [], [], [], [], [], []
    for g in big:
        m = inv == g
        n = (nrm[m] * ar[m, None]).sum(0)
        n /= np.linalg.norm(n)
        P = T[m].reshape(-1, 3)
        u, v = _basis(n)
        pu, pv = P @ u, P @ v
        ns.append(n)
        ds.append(float(np.average(off[m], weights=ar[m])))
        areas.append(float(tot[g]))
        us.append(u)
        vs.append(v)
        boxes.append((pu.min(), pu.max(), pv.min(), pv.max()))
    f.n = np.array(ns).reshape(-1, 3)
    f.d = np.array(ds)
    f.area = np.array(areas)
    f.u = np.array(us).reshape(-1, 3)
    f.v = np.array(vs).reshape(-1, 3)
    f.box = np.array(boxes).reshape(-1, 4)
    return f


def turned(f, R):
    """Faces f of a part turned by the rotation R."""
    g = Faces()
    g.n, g.u, g.v = f.n @ R.T, f.u @ R.T, f.v @ R.T
    g.d, g.area, g.box = f.d.copy(), f.area.copy(), f.box.copy()
    return g


def assess(placed, near=NEAR, pairs_out=None):
    """placed: [(part id (an integer), Faces, translation (3,))].  Returns
    (facing pairs, facing area, wall faces, wall area); facing area is the
    overlap weighted by closeness (1 at contact, 0 at `near`).  With a list
    as `pairs_out`, each facing pair is appended to it as (part id of the
    first face, part id of the second, the first face's normal, how far
    apart, overlap)."""
    rows = []
    for pid, f, t in placed:
        t = np.asarray(t, float)
        for j in range(len(f)):
            rows.append((pid, f.n[j], f.d[j] + f.n[j] @ t, f.area[j], f.u[j], f.v[j],
                         f.box[j] + np.array([f.u[j] @ t, f.u[j] @ t, f.v[j] @ t, f.v[j] @ t])))
    if not rows:
        return 0, 0.0, 0, 0.0
    pid = np.array([r[0] for r in rows])
    n = np.array([r[1] for r in rows])
    d = np.array([r[2] for r in rows])
    area = np.array([r[3] for r in rows])
    u = np.array([r[4] for r in rows])
    v = np.array([r[5] for r in rows])
    box = np.array([r[6] for r in rows])
    wall = (np.abs(n[:, 0]) > WALL_COS) | (np.abs(n[:, 1]) > WALL_COS)
    n_wall, a_wall = int(wall.sum()), float(area[wall].sum())
    pairs, a_pair = 0, 0.0
    # candidates: faces of different parts with opposite normals; the
    # rest is a few vector operations per face
    for a in range(len(rows)):
        cos = n @ n[a]
        s = -d - d[a]                                   # (how far face b's plane is in front of a's)
        c = np.nonzero((cos < FACING_COS) & (pid != pid[a]) & (s > -1e-6) & (s < near))[0]
        c = c[c > a] if len(c) else c                   # (each pair once: faces of b see a the same way)
        for b in c:
            # b's rectangle in a's plane: its four corners projected
            U = np.array([box[b, 0], box[b, 1]])
            Vv = np.array([box[b, 2], box[b, 3]])
            corners = np.array([U[i] * u[b] + Vv[j] * v[b] for i in (0, 1) for j in (0, 1)])
            cu, cv = corners @ u[a], corners @ v[a]
            ou = min(box[a, 1], cu.max()) - max(box[a, 0], cu.min())
            ov = min(box[a, 3], cv.max()) - max(box[a, 2], cv.min())
            if ou > 0 and ov > 0:
                pairs += 1
                a_pair += ou * ov * (1.0 - max(0.0, s[b]) / near)
                if pairs_out is not None:
                    pairs_out.append((int(pid[a]), int(pid[b]), n[a].copy(), float(s[b]), float(ou * ov)))
    return pairs, a_pair, n_wall, a_wall
