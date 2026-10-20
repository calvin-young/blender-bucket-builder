# SPDX-License-Identifier: GPL-3.0-or-later
"""Families of similar parts (docs/AUTOPACK.md §8.4): different meshes that
are basically the same shape, such as 50 chips that each differ a little.

A pattern is found per mesh, so 50 one-off chips would each be placed on
their own, in whatever orientation suits it, and would not nest.  Here
parts that turn freely are compared in their principal frames: similar
size and volume, and a coarse voxel picture that mostly overlaps the first
member's under one of the 24 turns of the frame.  That turn lines the
member up with the first one, so "orientation k" means the same thing for
every member (`Family.matrix`), and a lattice found for the envelope of
the members (the union of their grids, pack.pattern.Envelope) holds any of
them in any of its places.
"""

import numpy as np

from .problem import cube_group, principal_frame

MIN_OVERLAP = 0.55          # intersection over union of the coarse pictures, at the best turn
MAX_RATIO = 1.5             # largest / smallest extent along each principal axis, and volume
CELLS = 16                  # cells along the longest side of the coarse picture
NEAREST = 4                 # members of a family a part is compared with (the most alike in size)


class Family:
    """Members (part indices), the first is the reference; `align[m]` is the
    linear map that puts member m in the reference's frame (its principal
    frame turned to line up with the reference's, through the member
    it matched)."""

    def __init__(self, ref, C):
        self.ref = ref
        self.members = [ref]
        self.align = {ref: C}

    def matrix(self, A_ref, m):
        """The orientation of member m that matches the reference's A_ref."""
        return A_ref @ np.linalg.inv(self.align[self.ref]) @ self.align[m]

    def key(self):
        return tuple(self.members)


def _canonical(part):
    base = np.asarray(part.base, float)
    V = np.asarray(part.mesh.verts, float) @ base.T
    P = principal_frame(V)
    return P @ base


def _picture(part, C, h):
    """Coarse solid picture of the part posed by C (boolean array, centred
    on its own centre of mass, in cells of h)."""
    from . import voxel
    g = voxel.voxelize(part.mesh.verts, part.mesh.tris, C, h, 0.0, align='min')
    return g.solid


def _turned(grid, G):
    """grid posed by the signed permutation G (new axis i = sign * old axis j)."""
    axes = [int(np.nonzero(G[i])[0][0]) for i in range(3)]
    out = np.transpose(grid, axes)
    for i in range(3):
        if G[i, axes[i]] < 0:
            out = np.flip(out, i)
    return out


def _overlap(a, b):
    """Intersection over union of two pictures, centres of mass together."""
    ca = np.rint(np.argwhere(a).mean(0)).astype(int)
    cb = np.rint(np.argwhere(b).mean(0)).astype(int)
    lo = np.maximum(ca, cb)
    hi = np.maximum(np.array(a.shape) - ca, np.array(b.shape) - cb)
    shape = tuple(lo + hi)
    A = np.zeros(shape, bool)
    B = np.zeros(shape, bool)
    da, db = lo - ca, lo - cb
    A[tuple(slice(d, d + n) for d, n in zip(da, a.shape))] = a
    B[tuple(slice(d, d + n) for d, n in zip(db, b.shape))] = b
    u = np.count_nonzero(A | B)
    return np.count_nonzero(A & B) / u if u else 0.0


def find(parts, volumes, eligible):
    """Families (of two or more parts) among the parts `eligible` (indices)
    that turn freely; volumes[i] is part i's volume.  Parts are taken
    largest first and join the first family whose reference they match."""
    cand = [i for i in eligible if parts[i].mode == 'free' and volumes[i] > 0]
    if len(cand) < 2:
        return []
    info = {}
    for i in cand:
        C = _canonical(parts[i])
        W = np.asarray(parts[i].mesh.verts, float) @ C.T
        info[i] = (C, W.max(0) - W.min(0))
    group = cube_group()
    fams = []
    pics = {}

    def picture(j, h):
        if (j, h) not in pics:
            pics[(j, h)] = _picture(parts[j], info[j][0], h)
        return pics[(j, h)]

    for i in sorted(cand, key=lambda i: (-volumes[i], i)):
        C, ext = info[i]
        best = None                                   # (overlap, family, member, turn)
        for F in fams:
            h = float(info[F.ref][1].max()) / CELLS
            near = []
            for j in F.members:                       # (any member: a chain of small differences is one family)
                Cj, ej = info[j]
                if max(volumes[i], volumes[j]) > MAX_RATIO * min(volumes[i], volumes[j]):
                    continue
                a, b = np.sort(ext), np.sort(ej)
                if np.any(np.maximum(a, b) > MAX_RATIO * np.minimum(a, b)):
                    continue
                near.append((float(np.abs(np.log(a / b)).sum() + abs(np.log(volumes[i] / volumes[j]))), j))
            for _, j in sorted(near)[:NEAREST]:
                pj, pi = picture(j, h), picture(i, h)
                for G in group:
                    o = _overlap(pj, _turned(pi, G))
                    if best is None or o > best[0] + 1e-12:
                        best = (o, F, j, G)
        if best is not None and best[0] >= MIN_OVERLAP:
            o, F, j, G = best
            F.members.append(i)
            F.align[i] = F.align[j] @ np.linalg.inv(info[j][0]) @ G @ C
        else:
            fams.append(Family(i, C))
    return [F for F in fams if len(F.members) >= 2]
