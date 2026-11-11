# SPDX-License-Identifier: GPL-3.0-or-later
"""Jiggle: small moves that make a layout valid (the owner's word, 2026-10-13).

A layout packed by hand, or one with a few parts too close, is mended by
moving the parts that break a rule a little at a time, never by taking
them out and packing them again somewhere else.  Every move is measured on
the meshes by the exact engine, as the referee does.

Each part has a penalty: for every pair it is in, the gap it lacks (a
collision counts as the gap, plus how far their boxes overlap), and how
far it reaches past a side wall, the floor or the top.  The worst part
tries steps in the 26 grid directions and straight away from its nearest
problem; the step that lowers its penalty most is kept.  Steps start at
the gap (at least a millimeter) and halve when nothing helps, down to
MIN_STEP; a part stuck at the smallest step lets the other part of its
worst pair move instead.  It stops when nothing is left to mend, or at the
deadline, keeping the best layout seen.
"""

import time

import numpy as np

from ._core import World, COLLIDE

MIN_STEP = 0.0001        # mm (fine enough to settle a part between two neighbors at the gap)
TOL = 1e-6               # mm: less than this past a wall is rounding, not a problem
MARGIN = 0.0001          # mm beyond the gap a moved part is held to (the referee allows 1e-6 of the volume below it)
FAR = 20.0               # mm of penalty: a part this far wrong is for packing again, not for nudging
DIRS = np.array([(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
                 if (x, y, z) != (0, 0, 0)], float)
DIRS /= np.linalg.norm(DIRS, axis=1)[:, None]


class Jiggler:
    def __init__(self, items, vlo, vhi, gap, wall_gap, locked=()):
        """items: [(mesh, 4 x 4)] that may move; locked: [(mesh, 4 x 4)] that stay."""
        self.vlo = np.asarray(vlo, float)
        self.vhi = np.asarray(vhi, float)
        # A pair (or wall) counts as broken under the referee's threshold, a hair below
        # the gap; a part the jiggle moves is held to the gap plus MARGIN, so what it
        # calls mended the referee does too, while pairs left alone keep their gap.
        self.gap = float(gap)
        self.wall = float(wall_gap)
        self.moved = set()
        self.meshes = [m for m, _ in items] + [m for m, _ in locked]
        self.M = [np.array(M, float) for _, M in items] + [np.array(M, float) for _, M in locked]
        self.n = len(items)
        size = float(np.max(self.vhi - self.vlo))
        self.w = World()
        self.w.set_scale(size, 0.5)
        self.floor = max(0.0, self.gap - 1e-6 * size) if self.gap > 0 else 0.0
        self.w.set_thresholds(0.0, self.gap + MARGIN if self.gap > 0 else 0.0)
        keys = {}
        for uid, (m, M) in enumerate(zip(self.meshes, self.M)):
            k = keys.get(id(m))
            if k is None:
                k = keys[id(m)] = 'm%d' % len(keys)
                self.w.add_geom(k, np.asarray(m.verts, np.float64), np.asarray(m.tris))
            self.w.add_object(uid, k, M)
        self._box = [self._extent(j) for j in range(len(self.M))]
        self._settle()
        self.moves = 0
        self.travel = np.zeros(self.n)

    # ------------------------------------------------------------ measuring

    def _settle(self):
        while self.w.step(budget=0.05):
            pass

    def _extent(self, j):
        V = np.asarray(self.meshes[j].verts, float) @ self.M[j][:3, :3].T + self.M[j][:3, 3]
        return V.min(axis=0), V.max(axis=0)

    def _wall_penalty(self, j):
        lo, hi = self._box[j]
        w = self.wall + (MARGIN if j in self.moved and self.wall > 0 else 0.0)
        lo_ok = self.vlo + np.array([w, w, 0.0])
        hi_ok = self.vhi - np.array([w, w, 0.0])
        over = np.r_[lo_ok - lo, hi - hi_ok]
        return float(over[over > TOL].sum())

    def _overlap(self, a, b):
        (la, ha), (lb, hb) = self._box[a], self._box[b]
        return float(np.maximum(np.minimum(ha, hb) - np.maximum(la, lb), 0).min())

    def problems(self, j):
        """[(other uid, penalty, direction away from it)] for part j."""
        s = self.w.slot(j)
        out = []
        for key, pr in self.w.viol.items():
            if s not in key:
                continue
            o = self.w.uid(key[1] if key[0] == s else key[0])
            if o is None:
                continue
            if pr.state == COLLIDE:
                pen = self.gap + 1.0 + self._overlap(j, o)
                d = (self._box[j][0] + self._box[j][1] - self._box[o][0] - self._box[o][1]) / 2
            else:
                dist = float(pr.dist)
                if dist >= self.floor and j not in self.moved and o not in self.moved:
                    continue                  # (at the gap, and left alone: fine)
                pen = max(0.0, self.gap + MARGIN - dist)
                d = None
                if pr.pa is not None and pr.pb is not None:
                    pa, pb = np.asarray(pr.pa, float), np.asarray(pr.pb, float)
                    d = (pa - pb) if key[0] == s else (pb - pa)
                if d is None or not np.any(d):
                    d = (self._box[j][0] + self._box[j][1] - self._box[o][0] - self._box[o][1]) / 2
            nd = np.linalg.norm(d)
            out.append((o, pen, d / nd if nd > 0 else None))
        return out

    def penalty(self, j):
        return sum(p for _, p, _ in self.problems(j)) + self._wall_penalty(j)

    def total(self):
        pairs = 0.0
        for key, pr in self.w.viol.items():
            a, b = self.w.uid(key[0]), self.w.uid(key[1])
            if pr.state == COLLIDE:
                pairs += self.gap + 1.0 + (self._overlap(a, b) if a is not None and b is not None else 0.0)
            elif float(pr.dist) < self.floor or a in self.moved or b in self.moved:
                pairs += max(0.0, self.gap + MARGIN - float(pr.dist))
        return pairs + sum(self._wall_penalty(j) for j in range(self.n))

    # ------------------------------------------------------------ moving

    def _set(self, j, t):
        self.M[j][:3, 3] = t
        self.w.set_matrix(j, self.M[j], hot=False)
        self._box[j] = self._extent(j)
        self._settle()

    def _try(self, j, step):
        """The best step for part j: (penalty after, translation), or None.
        The directions away from its problems first; the 26 grid directions
        only if none of those helps."""
        t0 = self.M[j][:3, 3].copy()
        was = j in self.moved
        self.moved.add(j)                    # (a part that moves is held to the gap plus MARGIN)
        base = self.penalty(j)
        first = [d for _, _, d in self.problems(j) if d is not None]
        lo, hi = self._box[j]
        floor = self.vlo + np.array([self.wall, self.wall, 0.0])
        ceil = self.vhi - np.array([self.wall, self.wall, 0.0])
        for ax in range(3):                  # (back inside, past a wall)
            if lo[ax] < floor[ax]:
                first.append(np.eye(3)[ax])
            if hi[ax] > ceil[ax]:
                first.append(-np.eye(3)[ax])
        if len(first) > 1:
            first.append(np.mean(first, axis=0))
        best = None
        for group in (first, list(DIRS)):
            for d in group:
                nd = np.linalg.norm(d)
                if nd == 0:
                    continue
                t = t0 + step * d / nd
                self._set(j, t)
                p = self.penalty(j)
                if p < base - 1e-9 and (best is None or p < best[0]):
                    best = (p, t)
                    if p == 0.0:
                        break
            if best is not None:
                break
        self._set(j, t0)
        if best is None and not was:
            self.moved.discard(j)
        return best

    def run(self, seconds=30.0, deadline=None, on_step=None):
        """Mend the layout; returns the new matrices of the parts that may move."""
        if deadline is None:
            deadline = time.perf_counter() + seconds
        step = {j: max(self.gap, 1.0) for j in range(self.n)}
        stuck = set()
        best_total, best_M = self.total(), [M.copy() for M in self.M[:self.n]]
        while time.perf_counter() < deadline:
            pens = [(self.penalty(j), j) for j in range(self.n)]
            bad = sorted((p, j) for p, j in pens if TOL < p < FAR and j not in stuck)
            if not bad:
                if not stuck:
                    break
                stuck.clear()                 # (others have moved since: try again)
                step = {j: max(self.gap, 1.0) for j in range(self.n)}
                if not any(TOL < p < FAR for p, _ in pens):
                    break
                continue
            _, j = bad[-1]
            got = self._try(j, step[j])
            if got is not None:
                self.travel[j] += np.linalg.norm(got[1] - self.M[j][:3, 3])
                self._set(j, got[1])
                self.moves += 1
                step[j] = min(step[j] * 1.5, 4 * max(self.gap, 1.0))
                tot = self.total()
                if tot < best_total:
                    best_total, best_M = tot, [M.copy() for M in self.M[:self.n]]
                if on_step is not None:
                    on_step(self)
            elif step[j] > MIN_STEP:
                step[j] *= 0.5
            else:
                stuck.add(j)
        for j in range(self.n):             # (back to the best layout seen)
            if not np.array_equal(self.M[j], best_M[j]):
                self.M[j] = best_M[j]
                self.w.set_matrix(j, self.M[j], hot=False)
                self._box[j] = self._extent(j)
        self._settle()
        return [M.copy() for M in self.M[:self.n]]


def mend(items, vlo, vhi, gap, wall_gap, locked=(), deadline=None, seconds=30.0, on_step=None):
    """Jiggler runs until the layout passes the referee or time is up: a run
    whose result a fresh referee still faults is followed by another from
    where it ended (a fresh world settles every pair from scratch, where the
    jiggler's world worked through many small moves).  Returns (matrices,
    last Jiggler, total moves)."""
    from . import referee
    if deadline is None:
        deadline = time.perf_counter() + seconds
    cur = [(m, np.asarray(M, float)) for m, M in items]
    moves = 0
    jg = None
    for _ in range(4):
        jg = Jiggler(cur, vlo, vhi, gap, wall_gap, locked)
        Ms = jg.run(deadline=deadline, on_step=on_step)
        moves += jg.moves
        cur = [(m, M) for (m, _), M in zip(cur, Ms)]
        bad = referee.offenders([('l', m, M) for m, M in locked] + [('p', m, M) for m, M in cur],
                                vlo, vhi, gap, wall_gap, len(locked))
        if not bad or jg.moves == 0 or time.perf_counter() >= deadline:
            break
    jg.moves = moves
    return [M for _, M in cur], jg, moves


def jiggle(items, vlo, vhi, gap, wall_gap, locked=(), seconds=30.0):
    """New matrices for items [(mesh, 4 x 4)] with small moves that remove
    collisions, gaps under `gap` and parts past the walls; and the last
    Jiggler (its moves are the total)."""
    Ms, jg, _n = mend(items, vlo, vhi, gap, wall_gap, locked, seconds=seconds)
    return Ms, jg


class Stopped(Exception):
    pass


def fix(parts, start, s, locked=(), tighten=False, deadline=None, on_step=None, stop=None):
    """Jiggle a whole start layout ({(part, copy): 4 x 4}, mm) with settings s;
    with `tighten`, every part then moves down, back and left to the exact
    gap (pack.compact).  Nothing is taken out.  Returns (keys, matrices,
    Jiggler); `stop()` true ends it early, keeping what was done."""
    from . import compact
    keys = sorted(start)
    items = [(parts[q[0]].mesh, np.asarray(start[q], float)) for q in keys]
    if deadline is None:
        deadline = time.perf_counter() + (s.time_budget or 60.0)
    def step(j):
        if on_step is not None:
            on_step(j)
        if stop is not None and stop():
            raise Stopped()
    try:
        Ms, jg, _n = mend(items, s.vlo, s.vhi, s.gap, s.wall_gap, locked, deadline=deadline, on_step=step)
    except Stopped:
        jg = Jiggler(items, s.vlo, s.vhi, s.gap, s.wall_gap, locked)   # (stopped: as it was)
        Ms = [np.asarray(M, float) for _, M in items]
    if tighten and not (stop is not None and stop()):
        Ms, _c = compact.compact(list(zip([m for m, _ in items], Ms)), s.vlo, s.vhi, s.gap, s.wall_gap, locked,
                                 seconds=max(5.0, deadline - time.perf_counter()))
        Ms = [np.asarray(M, float) for M in Ms]
    return keys, Ms, jg
