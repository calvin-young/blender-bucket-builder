# SPDX-License-Identifier: GPL-3.0-or-later
"""Greedy placement, the fast tier of the solvers (docs/AUTOPACK.md §8.2).

Parts go in by tier, then largest first; copies of a part one after the
other.  For every allowed orientation the coarse grid gives all feasible
offsets at once; the lowest few are refined at full resolution (moved down,
then back, then left, a cell at a time, while they fit) and scored:

    score = height of the part's bottom (mm)
            + layer_weight * sum over layers of max(0, density - layer_cap)^2

subject to the 3D density limit, which is a lower bound on the packed
height: with V placed, a new part of volume v must leave the build at least
(V + v) / (area * max_density) high.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import metrics, voxel
from .bucket import Bucket
from .problem import Placement, Result, orientations


class _Pose:
    """A part in one orientation, ready to place."""
    __slots__ = ('A', 'grid', 'profile', 'volume', 'z_lo', 'z_hi', 'key', 'same_as')


def _open(mesh):
    _, openness = metrics.mesh_stats(mesh)
    return openness > metrics.OPEN_TOL


class Greedy:
    def __init__(self, parts, settings, threads=None):
        self.parts = parts
        self.s = settings
        self._poses = {}            # (part index, orientation index) -> _Pose
        self._orients = {}
        self._volume = {}
        self._close = {}
        self._sigs = {}
        n = metrics.default_workers() if threads is None else threads
        self._pool = ThreadPoolExecutor(max_workers=n) if n > 1 else None

    # ------------------------------------------------------------ poses
    def _part_volume(self, i):
        if i not in self._volume:
            vol, openness = metrics.mesh_stats(self.parts[i].mesh)
            self._volume[i] = abs(vol)
            self._close[i] = self.parts[i].close_cells
            if self._close[i] is None:
                self._close[i] = 2 if openness > metrics.OPEN_TOL else 0
        return self._volume[i]

    def orients(self, i):
        if i not in self._orients:
            self._orients[i] = orientations(self.parts[i], self.s.z_step)
        return self._orients[i]

    def pose(self, i, k):
        key = (i, k)
        p = self._poses.get(key)
        if p is not None:
            return p
        part = self.parts[i]
        A = self.orients(i)[k]
        s = self.s
        vol = self._part_volume(i) * abs(np.linalg.det(A))
        g = voxel.voxelize(part.mesh.verts, part.mesh.tris, A, s.h, s.gap, self._close[i])
        # exact volume per band of h, from the grid's cell 0
        M = np.eye(4)
        M[:3, :3] = A
        M[:3, 3] = -g.origin
        nz = g.shape[2]
        r = metrics.measure_parts([('p', part.mesh, M)], [0, 0, 0], [1, 1, nz * s.h], band=s.h,
                                  nbands=nz)[0]
        p = _Pose()
        p.A = A
        p.grid = g
        p.profile = np.maximum(np.diff(r.bounds), 0.0)
        p.volume = vol
        p.z_lo = float(r.lo[2])            # exact extent, from cell 0's corner
        p.z_hi = float(r.hi[2])
        p.key = ('pose', id(self), i, k)
        # an orientation whose grids are those of an earlier one (a symmetric
        # part turned onto itself) is not searched again
        p.same_as = None
        sig = (g.shape, g.solid.tobytes(), tuple(np.round(g.origin, 9)))
        seen = self._sigs.setdefault(i, {})
        if sig in seen:
            p.same_as = seen[sig]
        else:
            seen[sig] = k
        self._poses[key] = p
        return p

    # ------------------------------------------------------------ one part
    def _top(self, bucket, p, b):
        return (b[2] - bucket.M) * bucket.h + p.z_hi

    def _bottom(self, bucket, p, b):
        return (b[2] - bucket.M) * bucket.h + p.z_lo

    def _need(self, bucket, p):
        """Lowest top this part may have (the density limit), mm above the floor."""
        if not self.s.max_density:
            return -np.inf
        req = (bucket.volume + p.volume) / (bucket.area * self.s.max_density)
        return req if bucket.height < req else -np.inf

    def _layer_penalty(self, bucket, p, b):
        s = self.s
        z0 = int(b[2] - bucket.M)
        prof = p.profile
        lo, hi = max(z0, 0), min(z0 + len(prof), len(bucket.profile))
        if hi <= lo:
            return 0.0
        cap = s.layer_cap * bucket.area * bucket.h
        before = bucket.profile[lo:hi]
        after = before + prof[lo - z0:hi - z0]
        norm = bucket.area * bucket.h
        ex_a = np.maximum(after - cap, 0.0) / norm
        ex_b = np.maximum(before - cap, 0.0) / norm
        return float((ex_a * ex_a).sum() - (ex_b * ex_b).sum())

    def _score(self, bucket, p, b):
        return self._bottom(bucket, p, b) + self.s.layer_weight * self._layer_penalty(bucket, p, b)

    def _ok(self, bucket, p, b, need):
        return (bucket.in_range(p.grid, b) and self._top(bucket, p, b) >= need - 1e-9
                and bucket.fits(p.grid, b))

    def _refine(self, bucket, p, b, need):
        """Move down, back, left while the part still fits (b must fit)."""
        b = np.array(b, dtype=np.int64)
        limit = 6 * bucket.f + 8
        g = p.grid
        for _ in range(4):
            moved = False
            for ax in (2, 1, 0):
                room = int(b[ax] + g.s_lo[ax] - bucket.ilo[ax])         # cells to the wall
                if ax == 2 and need > -np.inf:
                    room = min(room, int(np.floor((self._top(bucket, p, b) - need) / bucket.h + 1e-9)))
                n = bucket.slide(g, b, ax, min(limit, room))
                if n > 0:
                    b[ax] -= n
                    moved = True
            if not moved:
                break
        return b

    def _phases(self, f):
        if self.s.phases > 1:
            half = f // 2
            return [(x, y, z) for x in (0, half) for y in (0, half) for z in (0, half)]
        return [(0, 0, 0)]

    def _coarse(self, bucket, p):
        return [(ph,) + tuple(bucket.coarse_feasible(p.grid, p.key, ph)) for ph in self._phases(bucket.f)]

    def _best_for_pose(self, bucket, p, need, coarse=None):
        s = self.s
        f = bucket.f
        best = None
        for phase, mask, c0 in (coarse if coarse is not None else self._coarse(bucket, p)):
            if mask is None or not mask.any():
                continue
            # the lowest coarse layers that hold candidates (the density
            # limit wants the top at least `need` high)
            cz_top = ((np.arange(mask.shape[2]) + c0[2]) * f + phase[2] - bucket.M) * bucket.h + p.z_hi
            zs = np.nonzero(mask.any(axis=(0, 1)))[0]
            if not zs.size:
                continue
            ok_z = zs[cz_top[zs] >= need - (f + 1) * bucket.h]
            zs = ok_z if ok_z.size else zs[-1:]
            got = []
            n = 0
            for z in zs:
                xy = np.argwhere(mask[:, :, z])
                got.append(np.column_stack([xy, np.full(len(xy), z)]))
                n += len(xy)
                if n >= s.candidates:
                    break
            idx = np.concatenate(got)
            b = (idx + c0) * f + np.array(phase)
            key = b[:, 2] * 1e6 + b[:, 1] * 1e3 + b[:, 0]
            k = min(s.candidates, len(key))
            pick = np.argpartition(key, k - 1)[:k]
            for j in pick[np.argsort(key[pick])]:
                cand = b[j]
                if not self._ok(bucket, p, cand, need):
                    # the density limit may hold it up: lift it until it is met
                    lifted = cand.copy()
                    while self._top(bucket, p, lifted) < need - 1e-9:
                        lifted[2] += 1
                    if not (bucket.in_range(p.grid, lifted) and bucket.fits(p.grid, lifted)):
                        continue
                    cand = lifted
                cand = self._refine(bucket, p, cand, need)
                sc = self._score(bucket, p, cand)
                if best is None or sc < best[0]:
                    best = (sc, cand)
        return best

    def place_one(self, bucket, i):
        """Best placement of part i over its orientations, or None.  The
        coarse searches of the orientations run in threads (the FFTs release
        the GIL); refinement is sequential."""
        why = 'too large for the volume'
        todo = []
        for k in range(len(self.orients(i))):
            p = self.pose(i, k)
            if p.same_as is not None:
                continue
            g = p.grid
            if np.any(g.s_hi - g.s_lo > bucket.ihi - bucket.ilo + 1):
                continue                                    # does not fit the empty volume this way
            need = self._need(bucket, p)
            if why == 'too large for the volume':
                why = 'no room'
            if need > -np.inf and why == 'no room':
                full = bucket.vhi[2] - bucket.vlo[2]
                why = ('over the density limit even at full height' if need > full + 1e-9
                       else 'no room within the density limit')
            todo.append((k, p, need))
        if not todo:
            return None, why
        for _, p, _ in todo:                                 # (the bucket's spectra, once, before the threads)
            bucket.spectrum(bucket._dtype(int(p.grid.coarse(bucket.f).sum())))
        if self._pool is not None and len(todo) > 1:
            coarse = list(self._pool.map(lambda t: self._coarse(bucket, t[1]), todo))
        else:
            coarse = [self._coarse(bucket, t[1]) for t in todo]
        best = None
        for (k, p, need), co in zip(todo, coarse):
            r = self._best_for_pose(bucket, p, need, co)
            if r is not None and (best is None or r[0] < best[0]):
                best = (r[0], k, r[1])
        return best, why

    # ------------------------------------------------------------ the run
    def order(self):
        """(part index, copy) in the order they go in."""
        keys = []
        for i, part in enumerate(self.parts):
            V = np.asarray(part.mesh.verts) @ np.asarray(part.base, float).T
            size = float(np.prod(V.max(0) - V.min(0))) if len(V) else 0.0
            keys.append((part.tier, -size, i))
        out = []
        for _, _, i in sorted(keys):
            out += [(i, c) for c in range(self.parts[i].quantity)]
        return out

    def run(self, bucket=None):
        s = self.s
        t0 = time.perf_counter()
        if bucket is None:
            close = max([2 if (p.close_cells is None) else p.close_cells for p in self.parts] + [0])
            margin = voxel.gap_cells(s.gap, s.h) + close + 2
            bucket = Bucket(s.vlo, s.vhi, s.h, s.wall_gap, margin, s.coarse)
        self.bucket = bucket
        res = Result()
        for i, c in self.order():
            if s.time_limit is not None and time.perf_counter() - t0 > s.time_limit:
                res.unplaced.append((i, c, 'time limit'))
                continue
            best, why = self.place_one(bucket, i)
            if best is None:
                res.unplaced.append((i, c, why))
                continue
            _, k, b = best
            p = self.pose(i, k)
            t = bucket.translation(p.grid, b)
            M = np.eye(4)
            M[:3, :3] = p.A
            M[:3, 3] = t
            bucket.place(p.grid, b, p.profile, p.volume, self._top(bucket, p, b))
            res.placements.append(Placement(i, c, M, k))
        res.seconds = time.perf_counter() - t0
        res.volume = bucket.volume
        res.height = bucket.height
        res.density = bucket.volume / (bucket.area * bucket.height) if bucket.height > 0 else None
        return res


def solve(parts, settings, threads=None):
    g = Greedy(parts, settings, threads)
    try:
        return g.run()
    finally:
        if g._pool is not None:
            g._pool.shutdown()
