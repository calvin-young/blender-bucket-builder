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

Two clearances (``aim`` given): ``gap`` is then the least any pair may
have, the hard rule, and every pair is wanted ``aim`` apart.  What a pair
lacks of the aim is a cost of its own, the soft one, which never counts
against the hard one: a step is kept if it lowers a part's hard penalty,
or leaves that as it is and lowers the soft cost.  So collisions and gaps
under the least go first, by the smallest moves, and after that parts
drift apart toward the aim where there is room, the tightest gaps
first (the cost grows faster than the shortfall).
"""

import time

import numpy as np

from ._core import World, COLLIDE

MIN_STEP = 0.0001        # mm (fine enough to settle a part between two neighbors at the gap)
TOL = 1e-6               # mm: less than this past a wall is rounding, not a problem
MARGIN = 0.0001          # mm beyond the gap a moved part is held to (the referee allows 1e-6 of the volume below it)
FAR = 20.0               # mm of penalty: a part this far wrong is for packing again, not for nudging
SOFT_STEP = 0.02         # mm: smaller steps are not worth taking for the aim alone
SOFT_GAIN = 1e-4         # of soft cost: a step must win this much to be kept for the aim alone
DIRS = np.array([(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
                 if (x, y, z) != (0, 0, 0)], float)
DIRS /= np.linalg.norm(DIRS, axis=1)[:, None]


class Jiggler:
    def __init__(self, items, vlo, vhi, gap, wall_gap, locked=(), aim=None, reach=None):
        """items: [(mesh, 4 x 4)] that may move; locked: [(mesh, 4 x 4)] that stay.
        gap: the least any pair may have; aim: what every pair should have
        (None or not above gap: one clearance, as before); reach: no part
        ends up farther than this from where it started (mm, None: no limit)."""
        self.vlo = np.asarray(vlo, float)
        self.vhi = np.asarray(vhi, float)
        # A pair (or wall) counts as broken under the referee's threshold, a hair below
        # the gap; a part the jiggle moves is held to the gap plus MARGIN, so what it
        # calls mended the referee does too, while pairs left alone keep their gap.
        self.gap = float(gap)
        self.wall = float(wall_gap)
        self.aim = float(aim) if aim is not None and float(aim) > self.gap else None
        self.reach = None if reach is None else float(reach)
        self.moved = set()
        self.meshes = [m for m, _ in items] + [m for m, _ in locked]
        self.M = [np.array(M, float) for _, M in items] + [np.array(M, float) for _, M in locked]
        self.n = len(items)
        size = float(np.max(self.vhi - self.vlo))
        self.w = World()
        self.w.set_scale(size, 0.5)
        self.floor = max(0.0, self.gap - 1e-6 * size) if self.gap > 0 else 0.0
        # (the world reports every pair under its threshold: with an aim, every pair short of it)
        self.w.set_thresholds(0.0, (self.aim or self.gap) + MARGIN if (self.aim or self.gap) > 0 else 0.0)
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
        self.start = [M[:3, 3].copy() for M in self.M[:self.n]]

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

    def _short(self, dist):
        """The soft cost of a gap: what it lacks of the aim, growing faster
        than the shortfall, so the tightest gaps are worth the most."""
        if self.aim is None or dist >= self.aim:
            return 0.0
        x = self.aim - max(dist, 0.0)
        return x * (1.0 + x / (self.aim - self.gap))

    def problems(self, j, soft=False):
        """[(other uid, penalty, direction away from it)] for part j; with
        `soft`, [(other, penalty, direction, soft cost)] and also the pairs
        that only fall short of the aim."""
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
                    pen = 0.0                 # (at the gap, and left alone: fine)
                else:
                    pen = max(0.0, self.gap + MARGIN - dist)
                if pen == 0.0 and not (soft and self._short(dist) > 0.0):
                    continue
                d = None
                if pr.pa is not None and pr.pb is not None:
                    pa, pb = np.asarray(pr.pa, float), np.asarray(pr.pb, float)
                    d = (pa - pb) if key[0] == s else (pb - pa)
                if d is None or not np.any(d):
                    d = (self._box[j][0] + self._box[j][1] - self._box[o][0] - self._box[o][1]) / 2
            nd = np.linalg.norm(d)
            d = d / nd if nd > 0 else None
            if soft:
                out.append((o, pen, d, self._short(0.0 if pr.state == COLLIDE else float(pr.dist))))
            else:
                out.append((o, pen, d))
        return out

    def penalty(self, j):
        """What part j lacks of the hard rules, mm (0: it keeps them all)."""
        return sum(p for _, p, _ in self.problems(j)) + self._wall_penalty(j)

    def cost(self, j):
        """(hard penalty, soft cost) of part j."""
        pr = self.problems(j, soft=True)
        return sum(p[1] for p in pr) + self._wall_penalty(j), sum(p[3] for p in pr)

    def soft_total(self):
        """The soft cost of the whole layout (every pair once)."""
        if self.aim is None:
            return 0.0
        return sum(self._short(0.0 if pr.state == COLLIDE else float(pr.dist)) for pr in self.w.viol.values())

    def gaps(self):
        """(collisions, pairs under the least, pairs short of the aim, smallest gap) as this world sees them."""
        col = under = short = 0
        low = float('inf')
        for pr in self.w.viol.values():
            if pr.state == COLLIDE:
                col += 1
                low = 0.0
                continue
            d = float(pr.dist)
            low = min(low, d)
            if d < self.floor:
                under += 1
            elif self.aim is not None and d < self.aim:
                short += 1
        return col, under, short, low

    @staticmethod
    def _better(new, old):
        """A step is worth keeping: less hard penalty, or the same and less soft cost."""
        if new[0] < old[0] - 1e-9:
            return True
        return new[0] <= old[0] + 1e-9 and new[1] < old[1] - SOFT_GAIN

    def total(self):
        pairs = 0.0
        for key, pr in self.w.viol.items():
            a, b = self.w.uid(key[0]), self.w.uid(key[1])
            if pr.state == COLLIDE:
                pairs += self.gap + 1.0 + (self._overlap(a, b) if a is not None and b is not None else 0.0)
            elif float(pr.dist) < self.floor or ((a in self.moved or b in self.moved)
                                                 and float(pr.dist) < self.gap + MARGIN):
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
        base = self.cost(j)
        first = [p[2] for p in self.problems(j, soft=True) if p[2] is not None]
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
                if self.reach is not None and np.linalg.norm(t - self.start[j]) > self.reach:
                    continue
                self._set(j, t)
                p = self.cost(j)
                if self._better(p, base) and (best is None or p < best[0]):
                    best = (p, t)
                    if p == (0.0, 0.0):
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
        best_key, best_M = (self.total(), self.soft_total()), [M.copy() for M in self.M[:self.n]]
        round_key = best_key
        while time.perf_counter() < deadline:
            costs = [(self.cost(j), j) for j in range(self.n)]
            # (a part with a hard problem goes down to the finest step; one that only
            # wants more room stops at SOFT_STEP)
            bad = sorted((c, j) for c, j in costs if j not in stuck
                         and (TOL < c[0] < FAR or (c[0] <= TOL and c[1] > SOFT_GAIN)))
            if not bad:
                if not stuck:
                    break
                now = (self.total(), self.soft_total())
                if not self._better(now, round_key):
                    break                     # (a whole round of every part won nothing)
                round_key = now
                stuck.clear()                 # (others have moved since: try again)
                step = {j: max(self.gap, 1.0) for j in range(self.n)}
                continue
            (hard, _soft), j = bad[-1]
            got = self._try(j, step[j])
            if got is not None:
                self.travel[j] += np.linalg.norm(got[1] - self.M[j][:3, 3])
                self._set(j, got[1])
                self.moves += 1
                step[j] = min(step[j] * 1.5, 4 * max(self.gap, 1.0))
                key = (self.total(), self.soft_total())
                if self._better(key, best_key):
                    best_key, best_M = key, [M.copy() for M in self.M[:self.n]]
                if on_step is not None:
                    on_step(self)
            elif step[j] > (MIN_STEP if hard > TOL else SOFT_STEP):
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


def mend(items, vlo, vhi, gap, wall_gap, locked=(), deadline=None, seconds=30.0, on_step=None, aim=None,
         reach=None):
    """Jiggler runs until the layout passes the referee or time is up: a run
    whose result a fresh referee still faults is followed by another from
    where it ended (a fresh world settles every pair from scratch, where the
    jiggler's world worked through many small moves).  Returns (matrices,
    last Jiggler, total moves)."""
    from . import referee
    if deadline is None:
        deadline = time.perf_counter() + seconds
    cur = [(m, np.asarray(M, float)) for m, M in items]
    first = [np.asarray(M, float)[:3, 3].copy() for _, M in items]
    moves = 0
    jg = None
    for _ in range(4):
        jg = Jiggler(cur, vlo, vhi, gap, wall_gap, locked, aim=aim, reach=reach)
        jg.start = first                      # (the reach counts from where the parts were given)
        Ms = jg.run(deadline=deadline, on_step=on_step)
        moves += jg.moves
        cur = [(m, M) for (m, _), M in zip(cur, Ms)]
        bad = referee.offenders([('l', m, M) for m, M in locked] + [('p', m, M) for m, M in cur],
                                vlo, vhi, gap, wall_gap, len(locked))
        if not bad or jg.moves == 0 or time.perf_counter() >= deadline:
            break
    jg.moves = moves
    return [M for _, M in cur], jg, moves


def jiggle(items, vlo, vhi, gap, wall_gap, locked=(), seconds=30.0, aim=None, reach=None):
    """New matrices for items [(mesh, 4 x 4)] with small moves that remove
    collisions, gaps under `gap` and parts past the walls; and the last
    Jiggler (its moves are the total)."""
    Ms, jg, _n = mend(items, vlo, vhi, gap, wall_gap, locked, seconds=seconds, aim=aim, reach=reach)
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
        Ms, jg, _n = mend(items, s.vlo, s.vhi, s.gap, s.wall_gap, locked, deadline=deadline, on_step=step,
                          aim=getattr(s, 'gap_aim', None))
    except Stopped:
        jg = Jiggler(items, s.vlo, s.vhi, s.gap, s.wall_gap, locked, aim=getattr(s, 'gap_aim', None))   # (stopped: as it was)
        Ms = [np.asarray(M, float) for _, M in items]
    if tighten and not (stop is not None and stop()):
        Ms, _c = compact.compact(list(zip([m for m, _ in items], Ms)), s.vlo, s.vhi, s.gap, s.wall_gap, locked,
                                 seconds=max(5.0, deadline - time.perf_counter()))
        Ms = [np.asarray(M, float) for M in Ms]
    return keys, Ms, jg
