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
STALL = 60.0             # s without a better layout: the round ends, a fresh one starts from the best
ROUNDS = 40              # at most this many rounds in one mend
RESEAT_SECONDS = 30.0    # a packing job's time for one new seat (8 s found none on the challenge build)
SOFT_STEP = 0.02         # mm: smaller steps are not worth taking for the aim alone
SOFT_GAIN = 1e-4         # of soft cost: a step must win this much to be kept for the aim alone
DIRS = np.array([(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
                 if (x, y, z) != (0, 0, 0)], float)
DIRS /= np.linalg.norm(DIRS, axis=1)[:, None]


class Jiggler:
    def __init__(self, items, vlo, vhi, gap, wall_gap, locked=(), aim=None, reach=None, turns=0.0,
                 max_turned=2, fixed=(), reseat=0):
        """items: [(mesh, 4 x 4)] that may move; locked: [(mesh, 4 x 4)] that stay.
        gap: the least any pair may have; aim: what every pair should have
        (None or not above gap: one clearance, as before); reach: no part
        ends up farther than this from where it started (mm, None: no limit).
        turns: degrees a part may be turned, at most, about its own center
        (0: never; the owner's choice to allow it, 2026-11-17, as an option);
        only a part that small moves cannot help is turned, and no more than
        max_turned of them; fixed: indices of parts that may not turn.
        reseat: at most this many times, when small moves cannot help, a part
        is taken out and placed again by the packer in the build's free room
        (any of the 24 axis turns unless fixed), every other part held: what
        the owner did by hand on the challenge build (0: never)."""
        self.vlo = np.asarray(vlo, float)
        self.vhi = np.asarray(vhi, float)
        # A pair (or wall) counts as broken under the referee's threshold, a hair below
        # the gap; a part the jiggle moves is held to the gap plus MARGIN, so what it
        # calls mended the referee does too, while pairs left alone keep their gap.
        self.gap = float(gap)
        self.wall = float(wall_gap)
        self.aim = float(aim) if aim is not None and float(aim) > self.gap else None
        self.reach = None if reach is None else float(reach)
        self.turns = float(turns or 0.0)
        self.max_turned = int(max_turned)
        self.fixed = set(fixed)
        self.turned = {}         # part -> (axis, degrees) of the turn it was given
        self.reseat = int(reseat or 0)
        self.reseated = []       # parts placed again by the packer, in order
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
        self._lbox = {}
        self._box = [self._extent(j) for j in range(len(self.M))]
        self._settle()
        # the gaps as they were given: a pair that was at the gap and is no closer now
        # is fine although one of its parts has moved (a part slid along its neighbor)
        self.d0 = {}
        for key, pr in self.w.viol.items():
            if pr.state != COLLIDE:
                a, b = self.w.uid(key[0]), self.w.uid(key[1])
                self.d0[(min(a, b), max(a, b))] = float(pr.dist)
        self._turn_tried = set()
        self._reseat_tried = set()
        self.moves = 0
        self.rows = 0            # row pushes made
        self.travel = np.zeros(self.n)
        self.start = [M[:3, 3].copy() for M in self.M[:self.n]]
        self.start_index = set(range(self.n))

    # ------------------------------------------------------------ measuring

    def _settle(self):
        while self.w.step(budget=0.05):
            pass

    def _extent(self, j):
        """Bounding box of part j: its posed box (cached per rotation) plus its place."""
        L = self.M[j][:3, :3]
        c = self._lbox.get(j)
        if c is None or not np.array_equal(c[0], L):
            V = np.asarray(self.meshes[j].verts, float) @ L.T
            c = self._lbox[j] = (L.copy(), V.min(axis=0), V.max(axis=0))
        t = self.M[j][:3, 3]
        return c[1] + t, c[2] + t

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

    def _kept(self, a, b, dist):
        """A pair at the gap needs no margin: neither part has moved, or the
        two are no closer than they were given."""
        if a not in self.moved and b not in self.moved:
            return True
        return dist >= self.d0.get((min(a, b), max(a, b)), float('inf')) - 1e-7

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
                if dist >= self.floor and self._kept(j, o, dist):
                    pen = 0.0                 # (at the gap, and left alone or no closer than it was: fine)
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
            elif float(pr.dist) < self.floor or (float(pr.dist) < self.gap + MARGIN
                                                 and not self._kept(a, b, float(pr.dist))):
                pairs += max(0.0, self.gap + MARGIN - float(pr.dist))
        return pairs + sum(self._wall_penalty(j) for j in range(self.n))

    # ------------------------------------------------------------ moving

    def _set(self, j, t):
        """Move part j and settle its pairs exactly (and whatever trials left quick)."""
        self.M[j][:3, 3] = t
        self.w.set_matrix(j, self.M[j], hot=False)
        self._box[j] = self._extent(j)
        self._settle()

    def _exact_cost(self, j):
        """cost(j) at its place now, measured exactly (trials may have left quick results)."""
        if self.w._pend_refine or self.w._pend_hot or self.w._dirty:
            self._settle()
        return self.cost(j)

    def _set_M(self, j, M, exact=True):
        self.M[j] = np.array(M, float)
        self.w.set_matrix(j, self.M[j], hot=not exact)
        self._box[j] = self._extent(j)
        if exact:
            self._settle()
        else:
            while self.w._pend_hot or self.w._dirty:
                self.w.step(budget=0.0, hot_budget=1.0, idle=False)

    def _turn(self, j, deadline):
        """Small turns of part j about its center (x, y, z; 1, 2, 4 ... degrees
        up to ``turns``, smallest first), each also followed by its way out of
        a collision.  Returns (cost after, 4 x 4, (axis, degrees)) for the best
        that lowers its hard penalty, or None.  Leaves the part as it was."""
        if not self.turns or j in self.fixed or j >= self.n:
            return None
        if j not in self.turned and len(self.turned) >= self.max_turned:
            return None
        M0 = self.M[j].copy()
        lo, hi = self._box[j]
        c = 0.5 * (lo + hi)
        was = j in self.moved
        self.moved.add(j)
        base = self._exact_cost(j)
        degs, d = [], 1.0
        while d <= self.turns + 1e-9:
            degs += [d, -d]
            d *= 2
        if self.turns not in [abs(x) for x in degs]:
            degs += [self.turns, -self.turns]
        best = None
        for deg in degs:
            for ax in range(3):
                if time.perf_counter() > deadline:
                    break
                a, b = [k for k in range(3) if k != ax]
                co, si = np.cos(np.radians(deg)), np.sin(np.radians(deg))
                R = np.eye(3)
                R[a, a] = R[b, b] = co
                R[a, b], R[b, a] = -si, si
                M = M0.copy()
                M[:3, :3] = R @ M0[:3, :3]
                M[:3, 3] = R @ (M0[:3, 3] - c) + c
                self._set_M(j, M, exact=False)
                if self._wall_penalty(j) > self._wall0(j, M0) + TOL:
                    continue
                cand = (self.cost(j), M.copy())
                if self._collides(j):
                    esc = self._escape(j, deadline)
                    if esc is not None and esc[0] < cand[0]:
                        M2 = M.copy()
                        M2[:3, 3] = esc[1]
                        cand = (esc[0], M2)
                if self._better(cand[0], base) and (best is None or cand[0] < best[0]):
                    best = (cand[0], cand[1], (ax, deg))
            if best is not None and best[0][0] <= TOL:
                break                         # (the smallest turn that clears it)
        self._set_M(j, M0, exact=best is None)
        if best is None and not was:
            self.moved.discard(j)
        return best

    def _wall0(self, j, M0):
        """Wall penalty of part j at the matrix M0 (for comparing a turn with no turn)."""
        M, box = self.M[j], self._box[j]
        self.M[j] = M0
        self._box[j] = self._extent(j)
        w = self._wall_penalty(j)
        self.M[j], self._box[j] = M, box
        return w

    def _restore(self, j, t0, moving_on):
        """Part j back where it was after its trials; settled exactly unless a
        step was found, which ``_set`` settles when it is taken."""
        if moving_on:
            self._probe(j, t0)
        else:
            self._set(j, t0)

    def _probe(self, j, t):
        """Move part j for a trial: its pairs get the world's quick solve.
        Whether parts collide is decided in full; a clearance may come out a
        little high (the search is capped), which is fine for choosing a
        step: the step taken is then settled exactly by ``_set``, and the run
        keeps the best layout it has measured exactly.  Quick results left
        behind are redone by the next ``_set``."""
        self.M[j][:3, 3] = t
        self.w.set_matrix(j, self.M[j], hot=True)
        self._box[j] = self._extent(j)
        while self.w._pend_hot or self.w._dirty:
            self.w.step(budget=0.0, hot_budget=1.0, idle=False)

    def _try(self, j, step):
        """The best step for part j: (penalty after, translation), or None.
        The directions away from its problems first; the 26 grid directions
        only if none of those helps."""
        t0 = self.M[j][:3, 3].copy()
        was = j in self.moved
        self.moved.add(j)                    # (a part that moves is held to the gap plus MARGIN)
        base = self.cost(j)
        wall0 = self._wall_penalty(j)
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
                self._probe(j, t)
                if self._wall_penalty(j) > wall0 + TOL:
                    continue                  # (never farther out of the volume to get away from a part)
                p = self.cost(j)
                if self._better(p, base) and (best is None or p < best[0]):
                    best = (p, t)
                    if p == (0.0, 0.0):
                        break
            if best is not None:
                break
        self._restore(j, t0, best is not None)
        if best is None and not was:
            self.moved.discard(j)
        return best

    def _reseat_one(self, c, deadline):
        """Part c taken out and placed again by the packer, every other part
        held (at the aim if given and it fits, else at the least).  Returns
        the new 4 x 4, or None."""
        from .problem import Part, Settings
        held = [(self.meshes[k], self.M[k]) for k in range(len(self.M)) if k != c]
        L = self.M[c][:3, :3].copy()
        mode = 'fixed' if c in self.fixed else 'free'
        part = Part('reseat', self.meshes[c], 1, mode, 1, L)
        left = deadline - time.perf_counter()
        if left < 2.0:
            return None
        from . import job as J
        for gap in ([self.aim] if self.aim else []) + [self.gap + MARGIN]:
            # (a short packing job: its first layout tries few turns, the rest of
            # its time all 24, which is where a new seat is usually found; the
            # pinned parts' grids are cached across tries, pack.greedy.pin)
            left = deadline - time.perf_counter()
            if left < 2.0:
                break
            s = Settings(vlo=self.vlo, vhi=self.vhi, gap=gap, wall_gap=self.wall, max_density=None,
                         time_budget=max(1.0, min(RESEAT_SECONDS, 0.5 * left)), seed=0, spread_z=False,
                         compact=False)
            jb = J.Job([part], s, held, workers=1, threads=1)
            try:
                res = jb.run()
            finally:
                jb.close()
            if res.placements:
                return np.asarray(res.placements[0].matrix, float)
        return None

    def _try_reseat(self, j, deadline):
        """Re-seat the part whose removal frees most for part j: j itself or a
        part it breaks a rule with.  True if the layout is better for it."""
        if len(self.reseated) >= self.reseat or j in self._reseat_tried:
            return False
        self._reseat_tried.add(j)
        cands = [j] + [o for o, pen, _ in self.problems(j) if pen > 0.0 and o < self.n]
        was_key = (self.total(), self.soft_total())
        far = np.array([0.0, 0.0, 10.0 * float(np.max(self.vhi - self.vlo))])
        scored = []
        for c in dict.fromkeys(cands):
            M0 = self.M[c].copy()
            M = M0.copy()
            M[:3, 3] = M0[:3, 3] + far           # (out of the way: what is left without it)
            self._set_M(c, M)
            scored.append((self.total(), c))
            self._set_M(c, M0)
        scored.sort()
        for _, c in scored[:2]:
            if time.perf_counter() > deadline:
                break
            M0 = self.M[c].copy()
            M = M0.copy()
            M[:3, 3] = M0[:3, 3] + far
            self._set_M(c, M)
            new = self._reseat_one(c, deadline)
            if new is not None:
                self._set_M(c, new)
                if self._better((self.total(), self.soft_total()), was_key):
                    self.reseated.append(c)
                    self.travel[c] += float(np.linalg.norm(new[:3, 3] - M0[:3, 3]))
                    self.moves += 1
                    if c in self.start_index:
                        self.start[c] = new[:3, 3].copy()   # (its reach counts from its new seat)
                    return True
            self._set_M(c, M0)
        return False

    def _try_turn(self, j, deadline):
        """Turn part j if that helps (once per part and run); True if it was turned."""
        if not self.turns or j in self._turn_tried:
            return False
        self._turn_tried.add(j)
        got = self._turn(j, deadline)
        if got is None:
            return False
        was_key = (self.total(), self.soft_total())
        M_old = self.M[j].copy()
        self._set_M(j, got[1])
        if not self._better((self.total(), self.soft_total()), was_key):
            self._set_M(j, M_old)
            return False
        ax, deg = got[2]
        prev = self.turned.get(j)
        self.turned[j] = (ax, deg + (prev[1] if prev and prev[0] == ax else 0.0))
        self.travel[j] += float(np.linalg.norm(got[1][:3, 3] - M_old[:3, 3]))
        self.moves += 1
        return True

    def _collides(self, j):
        s = self.w.slot(j)
        return any(s in key and pr.state == COLLIDE for key, pr in self.w.viol.items())

    def _free_of(self, j, o):
        """Part j keeps the hard rules with part o."""
        return not any(q == o and pen > 0.0 for q, pen, _ in self.problems(j))

    def _escape(self, j, deadline):
        """A step that takes part j clear of a part it collides with: along
        each direction the shortest way out (found by doubling, then halving),
        and of those the one that leaves j with the least hard penalty, then
        the shortest.  A collision has no slope to follow (its penalty is the
        same however deep), so small steps cannot find this.  Returns
        (cost after, translation) or None."""
        t0 = self.M[j][:3, 3].copy()
        was = j in self.moved
        self.moved.add(j)
        base = self.cost(j)
        wall0 = self._wall_penalty(j)
        s = self.w.slot(j)
        others = []
        for key, pr in self.w.viol.items():
            if s in key and pr.state == COLLIDE:
                o = self.w.uid(key[1] if key[0] == s else key[0])
                if o is not None:
                    others.append(o)
        far = FAR if self.reach is None else min(FAR, self.reach)
        best = None
        for o in others:
            away = [p[2] for p in self.problems(j) if p[0] == o and p[2] is not None]
            for group in (away + list(DIRS[np.abs(DIRS).max(axis=1) == 1.0]), list(DIRS[np.abs(DIRS).max(axis=1) < 1.0])):
                for d in group:
                    if time.perf_counter() > deadline:
                        break
                    lo, hi, step = 0.0, None, 0.25
                    while step <= far:
                        if self.reach is not None and np.linalg.norm(t0 + step * d - self.start[j]) > self.reach:
                            break
                        self._probe(j, t0 + step * d)
                        if self._free_of(j, o):
                            hi = step
                            break
                        lo, step = step, step * 2
                    if hi is None:
                        continue
                    for _ in range(5):
                        mid = 0.5 * (lo + hi)
                        self._probe(j, t0 + mid * d)
                        if self._free_of(j, o):
                            hi = mid
                        else:
                            lo = mid
                    self._probe(j, t0 + hi * d)
                    if self._wall_penalty(j) > wall0 + TOL:
                        continue
                    c = self.cost(j)
                    if c[0] < base[0] - 1e-9 and (best is None or (c[0], hi) < (best[0][0], best[2])):
                        best = (c, t0 + hi * d, hi)
                if best is not None and best[0][0] <= TOL:
                    break                     # (clear of everything: the diagonals need not be tried)
            if best is not None and best[0][0] <= TOL:
                break
        self._restore(j, t0, best is not None)
        if best is None:
            if not was:
                self.moved.discard(j)
            return None
        return best[0], best[1]

    def _way_out(self, j, o, d, far):
        """How far part j must go along d to keep the hard rules with part o
        (None: not within `far`).  Leaves j where it was."""
        t0 = self.M[j][:3, 3].copy()
        lo, hi, step = 0.0, None, min(0.25, far)
        while True:
            self._probe(j, t0 + step * d)
            if self._free_of(j, o):
                hi = step
                break
            if step >= far:
                break
            lo, step = step, min(step * 2, far)
        if hi is not None:
            for _ in range(5):
                mid = 0.5 * (lo + hi)
                self._probe(j, t0 + mid * d)
                if self._free_of(j, o):
                    hi = mid
                else:
                    lo = mid
        self._probe(j, t0)
        return hi

    def _hard_pairs(self):
        """The pairs that break a hard rule now, as a set of (uid, uid)."""
        out = set()
        for key, pr in self.w.viol.items():
            a, b = self.w.uid(key[0]), self.w.uid(key[1])
            if a is None or b is None:
                continue
            if pr.state == COLLIDE or float(pr.dist) < self.floor or (
                    float(pr.dist) < self.gap + MARGIN and not self._kept(a, b, float(pr.dist))):
                out.add((min(a, b), max(a, b)))
        return out

    def _push(self, j, d, dist, deadline, limit=12):
        """Part j goes `dist` along d, and each part it is then pushed into
        goes along d too, as far as it needs, and so on down the row: a row of
        parts moved up together (the owner's request #27).  Stops at a wall,
        a locked part, the reach, or `limit` parts.  Returns {part: new
        translation} if the whole layout breaks fewer hard rules for it, else
        None; the layout is left as it was either way."""
        t0 = {j: self.M[j][:3, 3].copy()}
        was = set(self.moved)
        before_total = self.total()
        old = self._hard_pairs()
        wall0 = {j: self._wall_penalty(j)}
        ok = True

        def place(k, t):
            if self.reach is not None and np.linalg.norm(t - self.start[k]) > self.reach:
                return False
            self.moved.add(k)
            self._probe(k, t)
            return self._wall_penalty(k) <= wall0[k] + TOL

        ok = place(j, t0[j] + dist * d)
        queue = [j]
        while ok and queue:
            if time.perf_counter() > deadline:
                ok = False
                break
            a = queue.pop(0)
            for k, pen, _ in self.problems(a):
                if pen <= 0.0 or (min(a, k), max(a, k)) in old:
                    continue                  # (not this push's doing)
                if k >= self.n or len(t0) >= limit:
                    ok = False                # (a locked part is in the way, or the row is too long)
                    break
                if k not in t0:
                    t0[k] = self.M[k][:3, 3].copy()
                    wall0[k] = self._wall_penalty(k)
                self.moved.add(k)
                need = self._way_out(k, a, d, dist + 1.0)
                if need is None or not place(k, self.M[k][:3, 3] + need * d):
                    ok = False
                    break
                queue.append(k)
        result = None
        if ok and self.total() < before_total - 1e-9:
            result = {k: self.M[k][:3, 3].copy() for k in t0}
        for k, t in t0.items():
            self._probe(k, t)
        if result is None:
            self._settle()
        self.moved = was
        return result

    def _push_out(self, j, deadline):
        """The best row push that takes part j out of a collision: along the
        six axes and straight away from the other part.  Returns {part:
        translation} or None."""
        s = self.w.slot(j)
        best = None
        for key, pr in list(self.w.viol.items()):
            if s not in key or pr.state != COLLIDE:
                continue
            o = self.w.uid(key[1] if key[0] == s else key[0])
            if o is None:
                continue
            self.moved.add(j)
            away = [p[2] for p in self.problems(j) if p[0] == o and p[2] is not None]
            self.moved.discard(j)
            far = FAR if self.reach is None else min(FAR, self.reach)
            ways = []
            for d in away + list(DIRS[np.abs(DIRS).max(axis=1) == 1.0]):
                if time.perf_counter() > deadline:
                    break
                was = j in self.moved
                self.moved.add(j)
                h = self._way_out(j, o, d, far)
                if not was:
                    self.moved.discard(j)
                if h is not None:
                    ways.append((h, d))
            ways.sort(key=lambda x: x[0])
            for h, d in ways[:4]:
                got = self._push(j, d, h, deadline)
                if got is not None:
                    size = sum(np.linalg.norm(t - self.M[k][:3, 3]) for k, t in got.items())
                    if best is None or size < best[0]:
                        best = (size, got)
        return None if best is None else best[1]

    def run(self, seconds=30.0, deadline=None, on_step=None, stall=None):
        """Mend the layout; returns the new matrices of the parts that may move.
        `stall`: end early when the best layout has not improved for this many
        seconds (``mend`` then starts a fresh round from the best)."""
        if deadline is None:
            deadline = time.perf_counter() + seconds
        step = {j: max(self.gap, 1.0) for j in range(self.n)}
        stuck = set()
        tried = set()           # parts that had their turn in this round
        escaped = set()         # parts whose way out of a collision was looked for since the last reset
        pushed = set()          # and those for which a row push was looked for
        resets = 0
        best_key, best_M = (self.total(), self.soft_total()), [M.copy() for M in self.M[:self.n]]
        round_key = best_key
        t_best = time.perf_counter()
        self.stalled = False
        while time.perf_counter() < deadline:
            if stall is not None:
                if best_key != getattr(self, '_seen_best', None):
                    self._seen_best, t_best = best_key, time.perf_counter()
                elif time.perf_counter() - t_best > stall:
                    self.stalled = True
                    break
            costs = [(self.cost(j), j) for j in range(self.n)]
            # (a part with a hard problem goes down to the finest step; one that only
            # wants more room stops at SOFT_STEP)
            # The hard rules first: parts that only want more room wait until no part
            # with a hard problem can be helped, so they do not take the room a fix needs.
            # (A collision's penalty goes by how far the boxes overlap, which for two
            # large parts hooked into each other says little: such a part still gets
            # one look for a way out before it is left for packing again.)
            bad = sorted((c, j) for c, j in costs if j not in stuck
                         and (TOL < c[0] < FAR or (c[0] >= FAR and j not in escaped and self._collides(j))))
            if not bad:
                bad = sorted((c, j) for c, j in costs if j not in stuck and c[0] <= TOL and c[1] > SOFT_GAIN)
            if not bad:
                if not stuck:
                    break
                now = (self.total(), self.soft_total())
                if not self._better(now, round_key):
                    break                     # (a whole pass over every part won nothing)
                round_key = now
                stuck.clear()                 # (others have moved since: try again)
                resets += 1
                if resets <= 2:               # (the searches for a way out are dear: twice more, then small steps only)
                    escaped.clear()
                    pushed.clear()
                tried.clear()
                step = {j: max(self.gap, 1.0) for j in range(self.n)}
                continue
            # every part with something to gain gets its turn, the worst first: one
            # part that cannot be helped must not keep the others waiting
            turn = [(c, j) for c, j in bad if j not in tried]
            if not turn:
                tried.clear()
                continue
            (hard, _soft), j = turn[-1]
            tried.add(j)
            got = None
            if hard > TOL and j not in escaped:
                escaped.add(j)
                got = self._escape(j, deadline)
            if hard > TOL and j not in pushed and self._collides(j) and (got is None or got[0][0] > TOL):
                pushed.add(j)
                row = self._push_out(j, deadline)
                if row is not None:           # (a row of parts moves up together)
                    was_key = (self.total(), self.soft_total())
                    old = {k: self.M[k][:3, 3].copy() for k in row}
                    for k, t in row.items():
                        self.moved.add(k)
                        self._set(k, t)
                    if not self._better((self.total(), self.soft_total()), was_key):
                        for k, t in old.items():    # (measured exactly it was no better: undone)
                            self._set(k, t)
                        row = None
                if row is not None:
                    for k, t in row.items():
                        self.travel[k] += np.linalg.norm(t - old[k])
                    self.moves += len(row)
                    self.rows += 1
                    key = (self.total(), self.soft_total())
                    if self._better(key, best_key):
                        best_key, best_M = key, [M.copy() for M in self.M[:self.n]]
                    if on_step is not None:
                        on_step(self)
                    continue
            if got is None and hard >= FAR:
                if self._try_turn(j, deadline) or self._try_reseat(j, deadline):
                    key = (self.total(), self.soft_total())
                    if self._better(key, best_key):
                        best_key, best_M = key, [M.copy() for M in self.M[:self.n]]
                    stuck.clear()
                else:
                    stuck.add(j)              # (no way out within reach: for packing again)
                continue
            if got is None:
                got = self._try(j, step[j])
            if got is not None:
                # (trials see quick results; the step is kept only if, measured
                # exactly, the part is better off)
                t_old = self.M[j][:3, 3].copy()
                was_cost = self._exact_cost(j)
                was_all = (self.total(), self.soft_total())
                self._set(j, got[1])
                if not self._better(self.cost(j), was_cost):
                    self._set(j, t_old)
                    got = None
                elif not self._better((self.total(), self.soft_total()), was_all):
                    # (a move made only for the aim must help the whole layout: one
                    # that widens this part's gaps by narrowing its neighbors' makes
                    # the parts trade room for ever, and the run never moves on)
                    self._set(j, t_old)
                    got = None
            if got is not None:
                self.travel[j] += np.linalg.norm(got[1] - t_old)
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
                if hard > TOL and (self._try_turn(j, deadline) or self._try_reseat(j, deadline)):
                    key = (self.total(), self.soft_total())
                    if self._better(key, best_key):
                        best_key, best_M = key, [M.copy() for M in self.M[:self.n]]
                    step[j] = max(self.gap, 1.0)
                    stuck.clear()             # (the room changed: everyone may try again)
                    continue
                stuck.add(j)
        for j in range(self.n):             # (back to the best layout seen)
            if not np.array_equal(self.M[j], best_M[j]):
                self.M[j] = best_M[j]
                self.w.set_matrix(j, self.M[j], hot=False)
                self._box[j] = self._extent(j)
        self._settle()
        return [M.copy() for M in self.M[:self.n]]


def mend(items, vlo, vhi, gap, wall_gap, locked=(), deadline=None, seconds=30.0, on_step=None, aim=None,
         reach=None, turns=0.0, max_turned=2, fixed=(), reseat=0, stall=STALL):
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
    turned = {}
    reseated = []
    jg = None
    for _ in range(ROUNDS):
        jg = Jiggler(cur, vlo, vhi, gap, wall_gap, locked, aim=aim, reach=reach, turns=turns,
                     max_turned=max_turned, fixed=fixed, reseat=max(0, reseat - len(reseated)))
        jg.turned = dict(turned)              # (the count of turned parts holds across rounds)
        jg.start = first                      # (the reach counts from where the parts were given)
        Ms = jg.run(deadline=deadline, on_step=on_step, stall=stall)
        moves += jg.moves
        turned.update(jg.turned)
        reseated += jg.reseated
        cur = [(m, M) for (m, _), M in zip(cur, Ms)]
        bad = referee.offenders([('l', m, M) for m, M in locked] + [('p', m, M) for m, M in cur],
                                vlo, vhi, gap, wall_gap, len(locked))
        if not bad or time.perf_counter() >= deadline or (jg.moves == 0 and not jg.stalled):
            break
    jg.moves = moves
    jg.turned = turned
    jg.reseated = reseated
    return [M for _, M in cur], jg, moves


def jiggle(items, vlo, vhi, gap, wall_gap, locked=(), seconds=30.0, aim=None, reach=None, turns=0.0,
           max_turned=2, fixed=(), reseat=0):
    """New matrices for items [(mesh, 4 x 4)] with small moves that remove
    collisions, gaps under `gap` and parts past the walls; and the last
    Jiggler (its moves are the total)."""
    Ms, jg, _n = mend(items, vlo, vhi, gap, wall_gap, locked, seconds=seconds, aim=aim, reach=reach,
                      turns=turns, max_turned=max_turned, fixed=fixed, reseat=reseat)
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
                          aim=getattr(s, 'gap_aim', None), reach=getattr(s, 'jiggle_reach', None),
                          reseat=getattr(s, 'jiggle_reseat', 0), turns=getattr(s, 'jiggle_turns', 0.0))
    except Stopped:
        jg = Jiggler(items, s.vlo, s.vhi, s.gap, s.wall_gap, locked, aim=getattr(s, 'gap_aim', None))   # (stopped: as it was)
        Ms = [np.asarray(M, float) for _, M in items]
    if tighten and not (stop is not None and stop()):
        Ms, _c = compact.compact(list(zip([m for m, _ in items], Ms)), s.vlo, s.vhi, s.gap, s.wall_gap, locked,
                                 seconds=max(5.0, deadline - time.perf_counter()))
        Ms = [np.asarray(M, float) for M in Ms]
    return keys, Ms, jg
