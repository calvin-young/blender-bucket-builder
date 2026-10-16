# SPDX-License-Identifier: GPL-3.0-or-later
"""Compaction to the exact gaps (docs/AUTOPACK.md §8.5).

A voxel layout keeps parts up to about two cells further apart than the gap
asks (each grid covers its part conservatively).  Here every part, lowest
first, moves down, then back, then left, for as long as it stays at least
the gap from every other part and inside the volume, measured on the
meshes themselves by the exact engine.

Each move is conservative advancement: with d the smallest distance from the
part to any other, moving it by d - gap in any direction cannot bring it
closer than the gap (a distance changes by at most the length of the move),
so it moves by that much, measures again, and stops when the step is
negligible.  The engine reports distances up to gap + REACH; parts further
away than that allow a step of REACH.

Every intermediate layout is valid, so the pass can stop at any time.
"""

import time

import numpy as np

from ._core import World, COLLIDE

REACH = 4.0           # mm beyond the gap the engine measures (the step when nothing is near)
TOL = 0.02            # mm: a smaller step is not worth taking
MAX_STEPS = 12        # per part and direction


class Compactor:
    def __init__(self, items, vlo, vhi, gap, wall_gap, locked=(), min_height=0.0, reach=REACH):
        """items: [(mesh, 4 x 4)] that may move; locked: [(mesh, 4 x 4)]
        that stay.  min_height: the build may not get lower than this (the
        density limit)."""
        self.vlo = np.asarray(vlo, float)
        self.vhi = np.asarray(vhi, float)
        self.gap = float(gap)
        self.wall = float(wall_gap)
        self.reach = float(reach)
        self.min_height = float(min_height)
        self.solves = 0
        self.M = [np.array(M, float) for _, M in items]
        self.meshes = [m for m, _ in items]
        self.n = len(items)
        w = self.w = World()
        size = float(np.max(self.vhi - self.vlo))
        w.set_scale(size, 0.5)
        w.set_thresholds(0.0, self.gap + self.reach)
        keys = {}
        for uid, (m, M) in enumerate(list(items) + list(locked)):
            k = keys.get(id(m))
            if k is None:
                k = keys[id(m)] = 'm%d' % len(keys)
                w.add_geom(k, np.asarray(m.verts, np.float64), np.asarray(m.tris))
            w.add_object(uid, k, np.asarray(M, float))
        self._settle()
        self.moves = 0
        self.travel = 0.0
        self.solves = 0

    def _settle(self):
        while self.w.step(budget=0.05):
            pass
        self.solves += 1

    def _extent(self, j):
        V = np.asarray(self.meshes[j].verts, float) @ self.M[j][:3, :3].T + self.M[j][:3, 3]
        return V.min(axis=0), V.max(axis=0)

    def nearest(self, j):
        """The smallest distance from part j to any other part, as far as it
        matters (gap + reach when none is nearer)."""
        s = self.w.slot(j)
        d = self.gap + self.reach
        for key, pr in self.w.viol.items():
            if s in key:
                dist = 0.0 if pr.state == COLLIDE else float(pr.dist)
                if pr.approx:
                    dist *= 0.99                 # (not proven to 1 %: take the low end)
                d = min(d, dist)
        return d

    def _room(self, j, ax, tops):
        """How far part j may go towards -ax before it leaves the volume (or,
        down, before the build gets lower than the density limit allows)."""
        lo, hi = self._extent(j)
        if ax == 2:
            r = lo[2] - self.vlo[2]
            others = max([t for q, t in enumerate(tops) if q != j] + [0.0])
            if others < self.min_height:
                r = min(r, hi[2] - self.vlo[2] - self.min_height)
            return max(0.0, r)
        return max(0.0, lo[ax] - (self.vlo[ax] + self.wall))

    def _place(self, j, ax, value):
        self.M[j][ax, 3] = value
        self.w.set_matrix(j, self.M[j])
        self._settle()

    def move(self, j, ax, tops, deadline=None):
        """Slide part j towards -ax as far as it goes; returns the distance.

        The engine's distances are what it found, which a closer pair it has
        not proven absent may undercut (by about 1 %, more where it flags
        them approximate), so each step stops a margin short, and a step
        after which the engine does find a pair under the gap is halved
        until it does not (or undone)."""
        went = 0.0
        margin = 0.01 * self.gap + 0.005
        for _ in range(MAX_STEPS):
            if deadline is not None and time.perf_counter() > deadline:
                break
            step = min(self.nearest(j) - self.gap - margin, self._room(j, ax, tops))
            if step < TOL:
                break
            x0 = self.M[j][ax, 3]
            self._place(j, ax, x0 - step)
            tries = 0
            while self.nearest(j) < self.gap and tries < 6:
                step *= 0.5
                self._place(j, ax, x0 - step)
                tries += 1
            if self.nearest(j) < self.gap:
                self._place(j, ax, x0)
                break
            went += step
            if ax == 2:
                tops[j] = self._extent(j)[1][2] - self.vlo[2]
        if went > 0:
            self.moves += 1
            self.travel += went
        return went

    def run(self, rounds=2, axes=(2, 1, 0), deadline=None):
        """Every part, lowest first, down, back, left; `rounds` times or
        until nothing moves.  Returns the new matrices."""
        tops = [self._extent(j)[1][2] - self.vlo[2] for j in range(self.n)]
        for _ in range(rounds):
            total = 0.0
            order = sorted(range(self.n), key=lambda j: (self._extent(j)[0][2], j))
            for j in order:
                for ax in axes:
                    if deadline is not None and time.perf_counter() > deadline:
                        return self.M
                    total += self.move(j, ax, tops, deadline)
            if total < TOL:
                break
        return self.M


def compact(items, vlo, vhi, gap, wall_gap, locked=(), min_height=0.0, seconds=None, rounds=2):
    """New matrices for items [(mesh, 4 x 4)], every part moved down, back and
    left to the exact gap (see Compactor)."""
    deadline = None if seconds is None else time.perf_counter() + seconds
    c = Compactor(items, vlo, vhi, gap, wall_gap, locked, min_height)
    return c.run(rounds, deadline=deadline), c
