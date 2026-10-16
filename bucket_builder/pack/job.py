# SPDX-License-Identifier: GPL-3.0-or-later
"""Anytime packing (docs/AUTOPACK.md §4.3, §8.3): a first greedy layout,
then better ones for as long as the time budget allows.

    job = Job(parts, settings)
    while not job.done:
        job.step(0.05)               # a slice of work
        res = job.result()           # the best layout so far
    job.keep_going(60)               # more time, from the best layout

What it tries, in this order:

1. **Greedy** with every orientation the parts' modes allow: the first
   result, the Stage 2 packer.
2. **Probes**: the copies of one part, alone in the bucket, in one
   orientation at a time.  Greedy looks one part ahead, so it lays every
   keycap flat (the lowest bottom wins) where all of them standing would fit
   in one layer, and it mixes the orientations of 150 chips that would nest
   if they were all turned the same way.  The probes rank each part's
   orientations by how well its copies pack together.
3. **Greedy with the best few** orientations of every part (1, 2, 3).
4. **Ruin and recreate** from the best layout so far: take some parts out
   (the top layer, the tallest part and its neighbours, a region, or a few at
   random), put them and anything left over back in a shuffled order, let
   every part settle (down, back, left), and keep the result if it is better,
   or not much worse early on (threshold accepting).

It stops early when the best layout is as low as it can be (the tallest
part, or the height the density limit demands).  Every layout is checked in
the voxel grid, so each one is valid; the referee checks the final one.
"""

import random
import time

import numpy as np

from . import score as S
from .greedy import Greedy
from .problem import Result


class Layout:
    """Parts placed in a bucket that can be taken out again."""

    def __init__(self, g, locked=()):
        self.g = g
        self.bucket = g.new_bucket(locked)
        self.base_height = self.bucket.height
        self.recs = {}               # (part, copy) -> (orientation, cell offset)
        self.tops = {}

    def add(self, i, c, k, b):
        b = tuple(int(x) for x in b)
        p = self.g.pose(i, k)
        self.tops[(i, c)] = self.g._top(self.bucket, p, b)
        self.g.put(self.bucket, i, k, b)
        self.recs[(i, c)] = (k, b)

    def remove(self, i, c):
        k, b = self.recs.pop((i, c))
        self.tops.pop((i, c))
        self.g.take(self.bucket, i, k, b)
        self.bucket.height = max([self.base_height] + list(self.tops.values()))
        return k, b

    def restore(self, recs):
        """Back to an earlier set of records."""
        for q in [q for q, r in self.recs.items() if recs.get(q) != r]:
            self.remove(*q)
        for q, (k, b) in recs.items():
            if q not in self.recs:
                self.add(q[0], q[1], k, b)

    def centre(self, q):
        i, c = q
        k, b = self.recs[q]
        g = self.g.pose(i, k).grid
        return np.asarray(b) + 0.5 * (g.s_lo + g.s_hi)

    def bottom(self, q):
        i, c = q
        k, b = self.recs[q]
        return b[2] + int(self.g.pose(i, k).grid.s_lo[2])


class Job:
    def __init__(self, parts, settings, locked=(), threads=None, seed=None, preset=None, time_budget=None,
                 start=None, max_iterations=None):
        self.parts = parts
        self.s = settings
        self.locked = list(locked)
        self.preset = settings.preset if preset is None else preset
        self.g = Greedy(parts, settings, threads)
        self.copies = self.g.order()
        self.tiers = sorted({p.tier for p in parts})
        self.rng = random.Random(settings.seed if seed is None else seed)
        self.budget = settings.time_budget if time_budget is None else time_budget
        self.log = []
        self.phase = 'greedy'
        self.progress = (0, len(self.copies))      # (placed, total) while the first layout is built
        self.cancel = False
        self.done = False
        self.version = 0                            # goes up whenever the best layout changes
        self.best = None                            # {(part, copy): (orientation, cell)}
        self.best_key = None
        self.best_choice = {}
        self.why = {}                               # (part, copy) -> why it was left out last time
        self.cands = {}                             # part -> its orientations, best first (from the probes)
        self.iterations = 0
        self._elapsed = 0.0
        self._t = None
        self._gen = None
        self.cur = None
        self.start = start          # {(part, copy): 4 x 4 in mm}: a layout to improve instead of a new one
        self.max_iterations = max_iterations        # (of ruin and recreate: for tests, which want the same answer)

    # ------------------------------------------------------------ driving
    def step(self, budget=0.05):
        """Work for about `budget` seconds (at least one placement).  Returns
        True while there is more to do."""
        if self.done:
            return False
        if self._gen is None:
            self._gen = self._work()
        t0 = time.perf_counter()
        self._t = t0
        try:
            while True:
                next(self._gen)
                now = time.perf_counter()
                self._elapsed += now - self._t
                self._t = now
                if self.cancel or now - t0 >= budget:
                    break
        except StopIteration:
            self.done = True
            self._elapsed += time.perf_counter() - self._t
        if self.cancel:
            self.done = True
        return not self.done

    def run(self, on_update=None, slice_s=0.25):
        """Until done (or cancelled); on_update(job) whenever the best
        layout changes and after every slice."""
        while self.step(slice_s):
            if on_update is not None:
                on_update(self)
        if on_update is not None:
            on_update(self)
        return self.result()

    def keep_going(self, seconds):
        """More time for the improvement loop, from the best layout."""
        self.budget = self.elapsed + seconds
        self.done = False
        self.cancel = False
        self._gen = self._improve_more()

    @property
    def elapsed(self):
        return self._elapsed

    def time_left(self):
        return None if self.budget is None else max(0.0, self.budget - self._elapsed)

    def _time_up(self):
        if self.cancel:
            return True
        if self.budget is None:
            return False
        now = time.perf_counter()
        return self._elapsed + (now - self._t if self._t else 0.0) >= self.budget

    # ------------------------------------------------------------ scoring
    def _left(self, recs):
        left = {t: 0 for t in self.tiers}
        for i, c in self.copies:
            if (i, c) not in recs:
                left[self.parts[i].tier] += 1
        return left

    def key(self, lay):
        b = lay.bucket
        return S.key(self._left(lay.recs), b.volume, b.height, b.profile, self.s, self.preset)

    def _consider(self, lay, label, choice=None):
        """Keep lay's layout if it is the best so far."""
        k = self.key(lay)
        if self.best_key is None or k < self.best_key:
            old = self.best_key
            self.best_key = k
            self.best = dict(lay.recs)
            if choice is not None:
                self.best_choice = dict(choice)
            self.version += 1
            self.log.append('%6.1f s  %-10s height %.1f mm, %d left out%s'
                            % (self.elapsed, label, lay.bucket.height, sum(self._left(lay.recs).values()),
                               '' if old is None else ' (better)'))
            return True
        return False

    def lower_bound(self):
        """No layout of every part can be lower than this (mm): the tallest
        part at its flattest, and the height the density limit demands."""
        lb = self.cur.base_height
        vol = self.cur.bucket.volume                 # (the locked parts')
        for i, part in enumerate(self.parts):
            ext = []
            for A in self.g.orients(i):
                z = np.asarray(part.mesh.verts, float) @ np.asarray(A[2], float)
                ext.append(float(z.max() - z.min()))
            lb = max(lb, min(ext))
            vol += self.g._part_volume(i) * abs(np.linalg.det(self.g.orients(i)[0])) * part.quantity
        if self.s.max_density:
            lb = max(lb, vol / (self.cur.bucket.area * self.s.max_density))
        return lb

    def _at_bound(self):
        if self.best is None or len(self.best) < len(self.copies):
            return False
        return self._best_height() <= self._lb + 0.5 * self.s.h

    # ------------------------------------------------------------ building
    def _build(self, lay, todo, allowed, count=False, stop_above=None):
        """Put the copies in `todo` in, in that order (a generator: yields
        after every part); stops once the build is higher than `stop_above`."""
        for n, (i, c) in enumerate(todo):
            if self.cancel:
                return
            if stop_above is not None and lay.bucket.height > stop_above:
                return
            if count:
                self.progress = (n, len(todo))
            best, why = self.g.place_one(lay.bucket, i, allowed.get(i))
            if best is None:
                self.why[(i, c)] = why
            else:
                lay.add(i, c, best[1], best[2])
            yield
        if count:
            self.progress = (len(todo), len(todo))

    def _settle(self, lay):
        """Let every part move down, back and left while it fits, lowest
        first (a generator)."""
        g = self.g
        order = sorted(lay.recs, key=lambda q: (lay.bottom(q), q))
        for n, q in enumerate(order):
            i, c = q
            k, b = lay.remove(i, c)
            p = g.pose(i, k)
            nb = g._refine(lay.bucket, p, b, g._need(lay.bucket, p))
            lay.add(i, c, k, nb)
            if n % 16 == 15:
                yield

    def _z_extent(self, i, k):
        z = np.asarray(self.parts[i].mesh.verts, float) @ np.asarray(self.g.orients(i)[k][2], float)
        return float(z.max() - z.min())

    def _fresh(self):
        return Layout(self.g, self.locked)

    def _distinct(self, i):
        """Orientations of part i that fit the volume and differ in the grid."""
        out = []
        b = self.cur.bucket
        for k in range(len(self.g.orients(i))):
            if not self.g._may_fit(b, i, k):
                continue
            p = self.g.pose(i, k)
            if p.same_as is None:
                out.append(k)
        return out

    def _match(self, i, A):
        """The orientation of part i whose linear part is A, or None."""
        A = np.asarray(A, float)
        tol = 1e-5 * max(1.0, float(np.abs(A).max()))
        for k, B in enumerate(self.g.orients(i)):
            if float(np.abs(np.asarray(B) - A).max()) < tol:
                return k
        return None

    def _load(self, lay, start):
        """Put the given placements in, each on the nearest cell, where the
        grid has room for it; returns the copies that are still to place."""
        b0 = lay.bucket
        left = []
        for q in self.copies:
            M = start.get(q)
            k = None if M is None else self._match(q[0], np.asarray(M, float)[:3, :3])
            if k is None:
                left.append(q)
                continue
            g = self.g.pose(q[0], k).grid
            cell = np.rint((np.asarray(M, float)[:3, 3] - b0.vlo + g.origin) / b0.h).astype(np.int64) + b0.M
            if b0.in_range(g, cell) and b0.fits(g, cell):
                lay.add(q[0], q[1], k, cell)
            else:
                left.append(q)
        return left

    # ------------------------------------------------------------ the work
    def _work(self):
        t_first = time.perf_counter()
        self.cur = self._fresh()
        self._lb = self.lower_bound()
        if self.start:
            left = self._load(self.cur, self.start)
            self.log.append('%6.1f s  start      %d parts kept where they are, %d to place'
                            % (self.elapsed, len(self.cur.recs), len(left)))
            yield from self._build(self.cur, left, {}, count=True)
            self._consider(self.cur, 'current', {})
        else:
            yield from self._build(self.cur, self.copies, {}, count=True)
            if self.cancel:
                return                      # (what is placed so far is the answer; the rest "stopped")
            self._consider(self.cur, 'greedy', {})
        self.first_seconds = time.perf_counter() - t_first
        self.phase = 'improving'
        if self._at_bound() or self._time_up():
            return
        # probes: each part alone, one orientation at a time, no density limit
        cap0 = self.g.cap
        self.g.cap = None
        try:
            for i, part in enumerate(self.parts):
                if part.quantity < 2 or part.mode == 'fixed':
                    continue
                ks = self._distinct(i)
                if len(ks) < 2:
                    continue
                ranks = []
                # tallest first: a layer of parts standing on edge is often
                # the answer, and once one probe is done the others stop as
                # soon as they are higher
                ks.sort(key=lambda k: (-self._z_extent(i, k), k))
                limit = None
                for k in ks:
                    if self._time_up():
                        break
                    lay = self._fresh()
                    yield from self._build(lay, [(i, c) for c in range(part.quantity)], {i: [k]},
                                           stop_above=limit)
                    left = part.quantity - len(lay.recs)
                    ranks.append(((left, round(lay.bucket.height, 6), k), k))
                    if left == 0:
                        h = lay.bucket.height + self.s.h
                        limit = h if limit is None else min(limit, h)
                if ranks:
                    self.cands[i] = [k for _, k in sorted(ranks)]
                    self.log.append('%6.1f s  probes     %s: orientations %s, best first'
                                    % (self.elapsed, part.name, self.cands[i][:4]))
        finally:
            self.g.cap = cap0
        # greedy with the best few orientations of each part
        for m in (1, 2, 3):
            if not self.cands or self._time_up() or self._at_bound():
                break
            if m > 1 and all(len(v) < m for v in self.cands.values()):
                break
            choice = {i: v[:m] for i, v in self.cands.items()}
            lay = self._fresh()
            yield from self._build(lay, self.copies, choice)
            if self._consider(lay, 'best %d' % m, choice):
                self.cur = lay
        yield from self._improve()

    def _improve_more(self):
        if self.cur is None:
            yield from self._work()
            return
        yield from self._improve()

    # ------------------------------------------------------------ ruin and recreate
    def _choices(self):
        """Orientation sets the improvement loop may give a part."""
        opts = {}
        for i in range(len(self.parts)):
            o = [None]
            v = self.cands.get(i)
            if v:
                o = [v[:1], v[:2], v[:3], None]
            opts[i] = o
        return opts

    def _improve(self):
        if self.best is None:
            return
        lay = self.cur
        lay.restore(self.best)
        # settle the best layout first: cheap, and it often helps
        yield from self._settle(lay)
        self._consider(lay, 'settled', self.best_choice)
        cur_key = self.key(lay)
        choice = dict(self.best_choice)
        opts = self._choices()
        t_start = self.elapsed
        rejects = 0
        while not self._time_up() and not self._at_bound() and \
                (self.max_iterations is None or self.iterations < self.max_iterations):
            self.iterations += 1
            before = dict(lay.recs)
            trial = dict(choice)
            if self.cands and self.rng.random() < 0.15:
                i = self.rng.choice(list(self.cands))
                trial[i] = self.rng.choice(opts[i])
            removed = self._ruin(lay)
            todo = removed + [q for q in self.copies if q not in lay.recs and q not in removed]
            todo = self._shuffle(todo)
            yield from self._build(lay, todo, trial)
            yield from self._settle(lay)
            k = self.key(lay)
            # threshold accepting on the value, among layouts that place as much
            if self.max_iterations:
                frac = self.iterations / self.max_iterations
            elif self.budget is not None:
                frac = min(1.0, (self.elapsed - t_start) / max(1e-9, self.budget - t_start))
            else:
                frac = 0.0
            thr = 2.0 * self.s.h * (1.0 - frac)
            if k < cur_key or (k[:-1] == cur_key[:-1] and k[-1] < cur_key[-1] + thr):
                if self._consider(lay, 'improved', trial):
                    rejects = 0
                cur_key = k
                choice = trial
            else:
                lay.restore(before)
                rejects += 1
            if rejects > 40:
                lay.restore(self.best)
                cur_key = self.best_key
                choice = dict(self.best_choice)
                rejects = 0
            yield
        lay.restore(self.best)
        self.cur = lay

    def _size(self, i):
        V = np.asarray(self.parts[i].mesh.verts, float) @ np.asarray(self.parts[i].base, float).T
        return float(np.prod(V.max(0) - V.min(0)))

    def _shuffle(self, todo):
        r = self.rng
        if r.random() < 0.25:
            todo = list(todo)
            r.shuffle(todo)
            return sorted(todo, key=lambda q: self.parts[q[0]].tier)
        return sorted(todo, key=lambda q: (self.parts[q[0]].tier, -self._size(q[0]) * r.uniform(0.6, 1.4), q))

    def _ruin(self, lay):
        """Take some parts out; returns them."""
        r = self.rng
        qs = sorted(lay.recs)
        n = len(qs)
        if n == 0:
            return []
        most = max(3, n // 5)
        kind = r.random()
        if kind < 0.35:                       # the top layer
            H = lay.bucket.height
            d = r.uniform(0.1, 0.35) * H
            out = [q for q in qs if lay.tops[q] > H - d]
            out = sorted(out, key=lambda q: -lay.tops[q])[:most]
        elif kind < 0.6:                      # the tallest part and its neighbours
            top = max(qs, key=lambda q: lay.tops[q])
            out = self._near(lay, top, qs, r.randint(2, max(2, min(most, 8))))
        elif kind < 0.85:                     # a region
            out = self._near(lay, r.choice(qs), qs, r.randint(2, max(2, min(most, 10))))
        else:                                 # a few at random
            out = r.sample(qs, min(n, r.randint(2, max(2, min(most, 6)))))
        for q in out:
            lay.remove(*q)
        return out

    def _near(self, lay, q0, qs, k):
        c0 = lay.centre(q0)
        d = sorted(qs, key=lambda q: float(np.sum((lay.centre(q) - c0) ** 2)))
        return d[:k]

    # ------------------------------------------------------------ the answer
    def result(self):
        """The best layout so far as a Result (or what the first greedy run
        has placed so far)."""
        res = Result()
        recs = self.best if self.best is not None else (dict(self.cur.recs) if self.cur else {})
        b = self.cur.bucket if self.cur is not None else None
        for (i, c), (k, cell) in sorted(recs.items()):
            res.placements.append(self.g.placement(b, i, c, k, cell))
        for q in self.copies:
            if q not in recs:
                why = 'stopped' if self.best is None and self.cancel else self.why.get(q, 'no room')
                res.unplaced.append((q[0], q[1], why))
        res.seconds = self.elapsed
        if self.best is not None and self.best_key is not None and b is not None:
            # (the numbers of the best layout, not of whatever is in the bucket now)
            res.height = self._best_height()
            res.volume = sum(self.g.pose(i, k).volume for (i, c), (k, _) in recs.items())
            area = b.area
            res.density = res.volume / (area * res.height) if res.height > 0 else None
        res.log = list(self.log)
        return res

    def _best_height(self):
        b = self.cur.bucket
        top = self.cur.base_height
        for (i, c), (k, cell) in self.best.items():
            top = max(top, self.g._top(b, self.g.pose(i, k), cell))
        return top

    def close(self):
        if self.g._pool is not None:
            self.g._pool.shutdown(wait=False)


def solve(parts, settings, locked=(), threads=None, seed=None, preset=None, time_budget=None, on_update=None):
    job = Job(parts, settings, locked, threads, seed, preset, time_budget)
    try:
        return job.run(on_update)
    finally:
        job.close()
