# SPDX-License-Identifier: GPL-3.0-or-later
"""How much surface two parts have in close contact (#43).

A gap of 1 mm between two spheres is a speck; between two parallel plates
it is a large face that may fuse.  The owner judges a compromise by that
area, so a pair closer than the clearance is described by how close and
over how much of each surface.

The area is the summed area of the triangles of one part whose distance to
the other part is under `within` (a triangle counts whole when any of it
is that close, so the figure is an upper bound, close to the truth on fine
meshes).  NumPy only: triangles are matched through a grid of cells about
the size of `within`, and distances are exact triangle to triangle.
"""

import numpy as np

from ._core import tritri

CHUNK = 400_000          # triangle pairs per vectorized distance call


def _world(mesh, M):
    M = np.asarray(M, float)
    V = np.asarray(mesh.verts, float) @ M[:3, :3].T + M[:3, 3]
    return V[np.asarray(mesh.tris)]                       # (n, 3 corners, 3)


def _near(Ta, Tb, within):
    """Indices of a's triangles whose box comes within `within` of b's box, and of b's toward a."""
    la, ha = Ta.min(axis=1), Ta.max(axis=1)
    lb, hb = Tb.min(axis=1), Tb.max(axis=1)
    A_lo, A_hi = la.min(axis=0) - within, ha.max(axis=0) + within
    B_lo, B_hi = lb.min(axis=0) - within, hb.max(axis=0) + within
    ia = np.flatnonzero(np.all((ha >= B_lo) & (la <= B_hi), axis=1))
    ib = np.flatnonzero(np.all((hb >= A_lo) & (lb <= A_hi), axis=1))
    return ia, ib


def _cells(lo, hi, origin, cell, dims):
    """(item, cell key) for every cell each box [lo, hi] touches."""
    c0 = np.floor((lo - origin) / cell).astype(np.int64)
    c1 = np.floor((hi - origin) / cell).astype(np.int64)
    n = c1 - c0 + 1
    cnt = n.prod(axis=1)
    item = np.repeat(np.arange(len(lo)), cnt)
    k = np.arange(len(item)) - np.repeat(np.concatenate([[0], np.cumsum(cnt)[:-1]]), cnt)
    nn = n[item]
    z = k % nn[:, 2]
    y = (k // nn[:, 2]) % nn[:, 1]
    x = k // (nn[:, 2] * nn[:, 1])
    c = c0[item] + np.stack([x, y, z], axis=1)
    return item, (c[:, 0] * dims[1] + c[:, 1]) * dims[2] + c[:, 2]


def _min_dist(Ta, Tb, within):
    """For each triangle of Ta, its smallest distance to Tb (inf beyond `within`).
    Both are put in a grid of fixed cells (about the typical triangle, at
    least twice `within`); a long triangle simply touches more cells."""
    out = np.full(len(Ta), np.inf)
    if len(Ta) == 0 or len(Tb) == 0:
        return out
    edges = np.linalg.norm(Tb[:, 1] - Tb[:, 0], axis=1)
    cell = max(2.0 * within, float(np.median(edges)), 1e-3)
    origin = np.minimum(Ta.min(axis=(0, 1)), Tb.min(axis=(0, 1))) - 2 * cell - within
    top = np.maximum(Ta.max(axis=(0, 1)), Tb.max(axis=(0, 1))) + 2 * cell + within
    dims = np.floor((top - origin) / cell).astype(np.int64) + 2
    ib, kb = _cells(Tb.min(axis=1) - within, Tb.max(axis=1) + within, origin, cell, dims)
    ia, ka = _cells(Ta.min(axis=1), Ta.max(axis=1), origin, cell, dims)
    order = np.argsort(kb, kind='stable')
    kb, ib = kb[order], ib[order]
    s = np.searchsorted(kb, ka, 'left')
    e = np.searchsorted(kb, ka, 'right')
    n = e - s
    pa = np.repeat(ia, n)
    if len(pa) == 0:
        return out
    pb = ib[np.repeat(s - np.concatenate([[0], np.cumsum(n)[:-1]]), n) + np.arange(len(pa))]
    pair = np.unique(pa.astype(np.int64) * len(Tb) + pb)            # (a pair met in several cells: once)
    pa, pb = pair // len(Tb), pair % len(Tb)
    # boxes apart by more than `within` cannot be closer
    gap = np.maximum(Ta[pa].min(axis=1) - Tb[pb].max(axis=1), Tb[pb].min(axis=1) - Ta[pa].max(axis=1)).max(axis=1)
    keep = gap <= within
    pa, pb = pa[keep], pb[keep]
    for k in range(0, len(pa), CHUNK):
        a, b = pa[k:k + CHUNK], pb[k:k + CHUNK]
        P = tritri.to_soa(Ta[a])
        Q = tritri.to_soa(Tb[b])
        hit, _seg = tritri.tri_tri_intersect(P, Q, 1e-9)
        d2, _p, _q = tritri.tri_tri_distance(P, Q)
        d = np.where(hit, 0.0, np.sqrt(np.maximum(d2, 0.0)))
        np.minimum.at(out, a, d)
    out[out > within] = np.inf
    return out


def _areas(T):
    return 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)


def _split(T, size, cap=64):
    """Each triangle cut into k x k smaller ones (k by its longest edge over `size`, at most `cap`)."""
    e = np.linalg.norm(T[:, [1, 2, 0]] - T, axis=2).max(axis=1)
    k = np.clip(np.ceil(e / size).astype(np.int64), 1, cap)
    out = [T[k == 1]]
    for kk in np.unique(k[k > 1]):
        S = T[k == kk]
        a, b, c = S[:, 0], S[:, 1], S[:, 2]
        u = (b - a) / kk
        v = (c - a) / kk
        tris = []
        for i in range(kk):
            for j in range(kk - i):
                p = a + i * u + j * v
                tris.append(np.stack([p, p + u, p + v], axis=1))
                if i + j < kk - 1:
                    tris.append(np.stack([p + u, p + u + v, p + v], axis=1))
        out.append(np.concatenate(tris))
    return np.concatenate(out)


def _close_area(Ta, Tb, within):
    """Area of Ta within `within` of Tb, and the smallest distance."""
    d = _min_dist(Ta, Tb, within)
    near = np.isfinite(d)
    if not near.any():
        return 0.0, float('inf')
    low = float(d[near].min())
    fine = _split(Ta[near], max(within, 0.05) / 4.0)        # (triangles counted whole: cut them small first)
    df = _min_dist(fine, Tb, within)
    return float(_areas(fine)[np.isfinite(df)].sum()), low


def contact(mesh_a, Ma, mesh_b, Mb, within):
    """(smallest distance, area of a within `within` of b, area of b within it of a), mm and mm^2."""
    Ta, Tb = _world(mesh_a, Ma), _world(mesh_b, Mb)
    ia, ib = _near(Ta, Tb, within)
    if len(ia) == 0 or len(ib) == 0:
        return float('inf'), 0.0, 0.0
    area_a, low_a = _close_area(Ta[ia], Tb[ib], within)
    area_b, low_b = _close_area(Tb[ib], Ta[ia], within)
    return min(low_a, low_b), area_a, area_b


# The owner's rule (2026-11-18): faces closer than 0.5 mm may fuse ("touching");
# a point, edge or rounded approach can go to about 0.1 mm without fusing.
FACE_GAP = 0.5           # mm
POINT_GAP = 0.1          # mm
FACE_SHARE = 0.6         # area within FACE_GAP over area within 2 x FACE_GAP: faces facing keep
                         # most of it (a flat face is close all over), a rounded approach little


def kind(mesh_a, Ma, mesh_b, Mb, closest):
    """'touching' (may fuse) or 'close' for a pair `closest` mm apart.  Faces
    facing each other are close over about the same area at FACE_GAP as at
    twice that; a sphere, an edge or a corner is close over a patch that
    grows with the distance.  So the share of one in the other tells them
    apart, whatever the size of the parts."""
    if closest < POINT_GAP:
        return 'touching'
    if closest >= FACE_GAP:
        return 'close'
    _l, a1, b1 = contact(mesh_a, Ma, mesh_b, Mb, FACE_GAP)
    _l, a2, b2 = contact(mesh_a, Ma, mesh_b, Mb, 2 * FACE_GAP)
    share = max(a1, b1) / max(a2, b2, 1e-9)
    return 'touching' if share > FACE_SHARE else 'close'
