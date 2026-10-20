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

import collections
import itertools
import os
import random
import time

import numpy as np

from . import score as S
from .greedy import Greedy
from .problem import Placement, Result


class Layout:
    """Parts placed in a bucket that can be taken out again.

    A record is (orientation, cell offset), or ('pin', cell) for a part
    that sits exactly where it was given (Improve Current Layout), off the
    packer's grid: it is voxelised where it is and does not move unless the
    improvement loop takes it out (and then it is placed again like any
    other).  `pin_of(q)` gives its (grid, cell, profile, volume, top)."""

    def __init__(self, g, locked=(), pin_of=None):
        self.g = g
        self.bucket = g.new_bucket(locked)
        self.base_height = self.bucket.height
        self.recs = {}               # (part, copy) -> (orientation, cell offset) or ('pin', cell)
        self.tops = {}
        self.pin_of = pin_of

    def add(self, i, c, k, b):
        if k == 'pin':
            g, cell, prof, vol, top = self.pin_of((i, c))
            self.bucket.place(g, cell, prof, vol, top)
            self.tops[(i, c)] = top
            self.recs[(i, c)] = ('pin', cell)
            return
        b = tuple(int(x) for x in b)
        p = self.g.pose(i, k)
        self.tops[(i, c)] = self.g._top(self.bucket, p, b)
        self.g.put(self.bucket, i, k, b)
        self.recs[(i, c)] = (k, b)

    def remove(self, i, c):
        k, b = self.recs.pop((i, c))
        self.tops.pop((i, c))
        if k == 'pin':
            g, cell, prof, vol, top = self.pin_of((i, c))
            self.bucket.remove(g, cell, prof, vol)
        else:
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

    def _grid(self, q):
        k, b = self.recs[q]
        if k == 'pin':
            return self.pin_of(q)[0], b
        return self.g.pose(q[0], k).grid, b

    def centre(self, q):
        g, b = self._grid(q)
        return np.asarray(b) + 0.5 * (g.s_lo + g.s_hi)

    def bottom(self, q):
        g, b = self._grid(q)
        return b[2] + int(g.s_lo[2])


class Job:
    def __init__(self, parts, settings, locked=(), threads=None, seed=None, preset=None, time_budget=None,
                 start=None, max_iterations=None, workers=None):
        self.parts = parts
        self.s = settings
        self.locked = list(locked)
        self.preset = settings.preset if preset is None else preset
        self.workers = parallel_workers(parts) if workers is None else max(1, int(workers))
        if self.workers > 1 and threads is None:
            threads = max(1, min(4, (os.cpu_count() or 1) - self.workers))
        self.g = Greedy(parts, settings, threads)
        self._pool = None
        self._event = None
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
        self._pins = {}
        self._extra = {}            # part -> orientations added by the turned probes (beyond the mode's)
        self.small_first = False    # full runs smallest part first (more parts in a bucket that is full)
        self.final = None           # {(part, copy): 4 x 4}: the best layout after exact compaction
        self.final_height = None
        self._reserve = 0.0         # seconds kept for compaction at the end
        self.max_iterations = max_iterations        # (of ruin and recreate: for tests, which want the same answer)

    # ------------------------------------------------------------ driving
    def step(self, budget=0.05):
        """Work for about `budget` seconds (at least one placement).  Returns
        True while there is more to do."""
        if self.done:
            return False
        if self._gen is None:
            self._gen = self._work_parallel() if self.workers > 1 else self._work()
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

    def _time_up(self, keep=None):
        """Whether the time is up (less `keep` seconds; by default those
        kept for the compaction at the end)."""
        if self.cancel:
            return True
        if self.budget is None:
            return False
        keep = self._reserve if keep is None else keep
        now = time.perf_counter()
        return self._elapsed + (now - self._t if self._t else 0.0) >= self.budget - keep

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
            self.final = self.final_height = None
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
        self._too_large = set()
        for i, part in enumerate(self.parts):
            if not any(self.g._may_fit(self.cur.bucket, i, k) for k in range(len(self.g.orients(i)))):
                self._too_large.add(i)               # (left out whatever happens: no bound from it)
                continue
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
        if self.best is None:
            return False
        if any(q not in self.best and q[0] not in self._too_large for q in self.copies):
            return False
        return self._best_height() <= self._lb + self.s.h

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
        order = sorted((q for q, r in lay.recs.items() if r[0] != 'pin'), key=lambda q: (lay.bottom(q), q))
        for n, q in enumerate(order):
            i, c = q
            k, b = lay.remove(i, c)
            p = g.pose(i, k)
            nb = g._refine(lay.bucket, p, b, g._need(lay.bucket, p))
            lay.add(i, c, k, nb)
            if n % 16 == 15:
                yield

    def _probe_plan(self):
        """[(part, one orientation of each bounding-box shape, the other
        distinct ones)], tallest first, for every part whose copies are worth
        probing.  The turned probes go between the two (they find more than
        the rest of the mode's own orientations, which are mostly the same
        shapes flipped)."""
        plan = []
        for i, part in sorted(enumerate(self.parts), key=lambda t: -self._size(t[0]) * t[1].quantity):
            if part.quantity < 2 or part.mode == 'fixed':
                continue
            ks = self._distinct(i)
            if len(ks) >= 2:
                # one of each shape of bounding box first (each way up and
                # along), tallest first, then the rest
                ks = sorted(ks, key=lambda k: (-self._z_extent(i, k), k))
                seen, first, rest = set(), [], []
                for k in ks:
                    c = self._extents_class(i, k)
                    (rest if c in seen else first).append(k)
                    seen.add(c)
                plan.append((i, first, rest))
        return plan

    def _probe(self, i, ks, ranks, limit):
        """Probe orientations ks of part i (a generator); once one has placed
        every copy, the others stop as soon as they are higher."""
        q = self.parts[i].quantity
        for k in ks:
            if self._time_up(self._probe_keep()):
                return
            lay = self._fresh()
            yield from self._build(lay, [(i, c) for c in range(q)], {i: [k]}, stop_above=limit[0])
            self._rank(i, k, q - len(lay.recs), lay.bucket.height, ranks, limit)

    def _probe_keep(self):
        """Seconds the probes leave: those kept for compaction and enough
        for a run with the best orientations (much cheaper than the first,
        which tried them all)."""
        return self._reserve + 0.3 * getattr(self, 'first_seconds', 0.0)

    def _rank(self, i, k, left, height, ranks, limit):
        ranks.append(((left, round(height, 6), k), k))
        if left == 0:
            h = height + self.s.h
            limit[0] = h if limit[0] is None else min(limit[0], h)

    TURN_STEP = 15.0

    def _turns(self, i, ranks):
        """More orientations to probe near the best ones: the best one turned
        about the vertical (15 degree steps between the mode's own), and in
        free mode the best of each of the three best shapes of footprint
        turned about its thinnest horizontal axis (a chip standing on edge,
        turned in its own plane, nests at some turns and not at others).
        Added to the part's orientations; returns their indices."""
        part = self.parts[i]
        if part.mode == 'fixed' or not ranks:
            return []
        order = [k for _, k in sorted(ranks)]
        seeds, seen = [], set()
        for k in order:
            c = self._extents_class(i, k)
            if c not in seen:
                seen.add(c)
                seeds.append(k)
            if len(seeds) == 3:
                break
        V = np.asarray(part.mesh.verts, float)
        angles = [a for j in range(1, int(90 // self.TURN_STEP) + 1) for a in (j * self.TURN_STEP, -j * self.TURN_STEP)]
        extra = list(self._extra.get(i, []))
        n0 = len(extra)
        A = np.asarray(self.g.orients(i)[order[0]], float)
        for a in angles:
            if part.mode == 'z' and abs(a) % self.s.z_step < 1e-9:
                continue                                  # (already in the mode's set)
            extra.append(_axis_rot(2, a) @ A)
        if part.mode == 'free':
            for k in seeds:
                A = np.asarray(self.g.orients(i)[k], float)
                W = V @ A.T
                ext = W.max(0) - W.min(0)
                ax = int(np.argmin(ext[:2]))
                if ext[ax] < 0.6 * ext.max():
                    extra += [_axis_rot(ax, a) @ A for a in angles]
        if len(extra) == n0:
            return []
        self._extra[i] = extra
        new = self.g.add_orientations(i, extra)[n0:]
        b = self.cur.bucket
        return [k for k in new if self.g._may_fit(b, i, k) and self.g.pose(i, k).same_as is None]

    def _extents_class(self, i, k):
        W = np.asarray(self.parts[i].mesh.verts, float) @ np.asarray(self.g.orients(i)[k], float).T
        return tuple(np.round(W.max(0) - W.min(0), 1))

    def _z_extent(self, i, k):
        z = np.asarray(self.parts[i].mesh.verts, float) @ np.asarray(self.g.orients(i)[k][2], float)
        return float(z.max() - z.min())

    def _fresh(self):
        return Layout(self.g, self.locked, self.pin)

    def pin(self, q):
        """(grid, cell, profile, volume, top) of part copy q exactly where
        the start layout has it."""
        v = self._pins.get(q)
        if v is None:
            b = self.cur.bucket if self.cur is not None else self.g.new_bucket()
            v = self._pins[q] = self.g.pin(b, self.parts[q[0]].mesh, self.start[q])
        return v

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
        """Keep the given placements that make a valid layout (the exact
        engine decides: of a pair too close the higher part goes, as does a
        part outside or too near a side wall): on the packer's grid where
        they are on it, else pinned exactly where they are.  Returns the
        copies still to place."""
        from . import referee
        qs = [q for q in self.copies if q in start]
        items = [('l', m, M) for m, M in self.locked] + \
                [('p', self.parts[q[0]].mesh, np.asarray(start[q], float)) for q in qs]
        bad = {qs[u - len(self.locked)] for u in
               referee.offenders(items, self.s.vlo, self.s.vhi, self.s.gap, self.s.wall_gap, len(self.locked))}
        b0 = lay.bucket
        left = []
        for q in self.copies:
            if q not in start or q in bad:
                left.append(q)
                continue
            M = np.asarray(start[q], float)
            k = self._match(q[0], M[:3, :3])
            if k is not None:
                g = self.g.pose(q[0], k).grid
                f = (M[:3, 3] - b0.vlo + g.origin) / b0.h + b0.M
                cell = np.rint(f).astype(np.int64)
                if np.abs(f - cell).max() < 1e-6 and b0.in_range(g, cell) and b0.fits(g, cell):
                    lay.add(q[0], q[1], k, cell)
                    continue
            lay.add(q[0], q[1], 'pin', None)
        self.n_bad_start = len(bad)
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
        self._reserve = self._compact_reserve()
        if self._at_bound() or self._time_up():
            yield from self._finish()
            return
        if self._some_left():
            # a full bucket: smallest first places more parts
            lay = self._fresh()
            yield from self._build(lay, self._order(True), {})
            if self._consider(lay, 'small first', {}):
                self.cur = lay
                self.small_first = True
        # probes: each part alone, one orientation at a time, no density limit
        cap0 = self.g.cap
        self.g.cap = None
        try:
            for i, first, rest in self._probe_plan():
                ranks, limit = [], [None]
                yield from self._probe(i, first, ranks, limit)
                turned = self._turns(i, ranks) if not self._time_up(self._probe_keep()) else []
                if turned:
                    yield from self._probe(i, turned, ranks, limit)
                yield from self._probe(i, rest, ranks, limit)
                if ranks:
                    self.cands[i] = [k for _, k in sorted(ranks)]
                    self.log.append('%6.1f s  probes     %s: orientations %s, best first'
                                    % (self.elapsed, self.parts[i].name, self.cands[i][:4]))
        finally:
            self.g.cap = cap0
        # greedy with the best few orientations of each part (the first of
        # these runs is cheap and often the big step: it may use the time
        # kept for compaction)
        for m in (1, 2, 3):
            if not self.cands or self._time_up(0.0 if m == 1 else None) or self._at_bound():
                break
            if m > 1 and all(len(v) < m for v in self.cands.values()):
                break
            choice = {i: v[:m] for i, v in self.cands.items()}
            lay = self._fresh()
            yield from self._build(lay, self._order(), choice)
            if self._consider(lay, 'best %d' % m, choice):
                self.cur = lay
        yield from self._improve()
        yield from self._finish()

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

    def _order(self, small=None):
        """Every copy in the order a full run places them: by tier, then
        largest first (or smallest first)."""
        small = self.small_first if small is None else small
        if not small:
            return self.copies
        return sorted(self.copies, key=lambda q: (self.parts[q[0]].tier, self._size(q[0]), q))

    def _some_left(self):
        """Whether the best layout leaves out parts that would fit the volume."""
        return self.best is not None and any(q not in self.best and q[0] not in self._too_large
                                             for q in self.copies)

    def _shuffle(self, todo):
        r = self.rng
        if r.random() < 0.25:
            todo = list(todo)
            r.shuffle(todo)
            return sorted(todo, key=lambda q: self.parts[q[0]].tier)
        sign = 1.0 if self.small_first else -1.0
        return sorted(todo, key=lambda q: (self.parts[q[0]].tier, sign * self._size(q[0]) * r.uniform(0.6, 1.4), q))

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
            res.placements.append(self._placement(b, i, c, k, cell))
        for q in self.copies:
            if q not in recs:
                why = 'stopped' if self.best is None and self.cancel else self.why.get(q, 'no room')
                res.unplaced.append((q[0], q[1], why))
        res.seconds = self.elapsed
        if self.best is not None and b is not None:
            # (the numbers of the best layout, not of whatever is in the bucket now)
            res.height = self._best_height()
            res.volume = sum(self._volume(q, r) for q, r in recs.items())
            res.density = res.volume / (b.area * res.height) if res.height > 0 else None
        res.log = list(self.log)
        return res

    def _placement(self, b, i, c, k, cell):
        if self.final is not None and (i, c) in self.final:
            M = self.final[(i, c)]
            kk = k if k != 'pin' else self._match(i, M[:3, :3])
            return Placement(i, c, np.array(M), -1 if kk is None else kk, None)
        if k == 'pin':
            M = np.asarray(self.start[(i, c)], float)
            kk = self._match(i, M[:3, :3])
            return Placement(i, c, M.copy(), -1 if kk is None else kk, None)
        return self.g.placement(b, i, c, k, cell)

    def _volume(self, q, r):
        return self.pin(q)[3] if r[0] == 'pin' else self.g.pose(q[0], r[0]).volume

    def _best_height(self):
        if self.final_height is not None:
            return self.final_height
        b = self.cur.bucket
        top = self.cur.base_height
        for q, (k, cell) in self.best.items():
            top = max(top, self.pin(q)[4] if k == 'pin' else self.g._top(b, self.g.pose(q[0], k), cell))
        return top

    # ------------------------------------------------------------ compaction
    SORT_S_PER_TRIANGLE = 0.4e-6      # what the exact engine takes to read a mesh in

    def _compact_reserve(self):
        """Seconds to keep for the compaction at the end (none when reading
        the meshes into the exact engine would take most of them)."""
        if not self.s.compact or self.budget is None or self.budget <= 0:
            return 0.0
        keep = min(0.2 * self.budget, 1.0 + 0.1 * len(self.copies))
        nt = sum(m.nt for m in {id(p.mesh): p.mesh for p in self.parts}.values())
        if nt * self.SORT_S_PER_TRIANGLE > 0.5 * keep:
            self.log.append('%6.1f s  no compaction: the meshes are too large for the time set' % self.elapsed)
            return 0.0
        return keep

    def _finish(self):
        """The last steps on the best layout: even out the layers, then the
        exact gaps (a generator)."""
        if self.best is not None and not self.cancel and self.cur is not None:
            yield from self._spread()
        yield from self._compact_phase()

    def _spread(self):
        """Even out the density from layer to layer: lift parts straight up,
        on the grid and never above the build's height, where that makes the
        layers more even (one layer of 49 keycaps with one more on top
        becomes a staggered build).  Off with Settings.spread_z (Keep Parts
        Low: for builds the user fills by hand afterwards).  A generator."""
        if not self.s.spread_z or self._some_left():
            return                          # (a full bucket: lifting parts only takes room)
        lay = self.cur
        lay.restore(self.best)
        g, b = self.g, lay.bucket
        h = b.h
        H = self._best_height()
        before = S.spread(b.profile, H, h)
        moved = 0
        for rnd in range(3):
            order = sorted((q for q, r in lay.recs.items() if r[0] != 'pin'), key=lambda q: (-lay.tops[q], q))
            n_moved = 0
            for n, q in enumerate(order):
                i, c = q
                k, cell = lay.remove(i, c)
                p = g.pose(i, k)
                top = g._top(b, p, cell)
                room = int(np.floor((H - top) / h + 1e-9))
                room = b.slide(p.grid, cell, 2, room, sign=1) if room > 0 else 0
                d = 0
                if room > 0:
                    z0 = cell[2] - b.M
                    prof = np.asarray(p.profile, float)
                    W = np.zeros(room + len(prof))
                    lo, hi = max(z0, 0), min(z0 + len(W), len(b.profile))
                    if hi > lo:
                        W[lo - z0:hi - z0] = b.profile[lo:hi]
                    cost = np.correlate(W, prof, 'valid')        # (overlap with what is there, per lift)
                    j = int(np.argmin(cost))
                    if cost[j] < cost[0] - 1e-6 * max(1.0, abs(cost[0])):
                        d = j
                new = (cell[0], cell[1], cell[2] + d)
                lay.add(i, c, k, new)
                if d:
                    n_moved += 1
                if n % 16 == 15:
                    yield
            moved += n_moved
            if not n_moved or self.cancel:
                break
        if moved:
            after = S.spread(b.profile, H, h)
            self.best = dict(lay.recs)
            self.best_key = self.key(lay)
            self.final = self.final_height = None
            self.version += 1
            self.log.append('%6.1f s  spread     %d lifts, layer spread %.2f -> %.2f'
                            % (self.elapsed, moved, before, after))

    def _compact_phase(self):
        """Move every part of the best layout to the exact gap: down, back,
        left, lowest first, for the time kept for it (a generator; each
        step leaves a valid layout)."""
        from .compact import Compactor
        if not self._reserve or self.best is None or self.cancel or self._at_bound():
            return
        if self.budget is not None and self.time_left() <= 0.05:
            return
        self.phase = 'compacting'
        b = self.cur.bucket
        qs = sorted(self.best)
        items = [(self.parts[q[0]].mesh, self._placement(b, q[0], q[1], *self.best[q]).matrix) for q in qs]
        vol = sum(self._volume(q, self.best[q]) for q in qs) + \
            sum(self.g.pin(b, m, M)[3] for m, M in self.locked)
        min_h = vol / (b.area * self.s.max_density) if self.s.max_density else 0.0
        h0 = self._best_height()
        c = Compactor(items, self.s.vlo, self.s.vhi, self.s.gap, self.s.wall_gap, self.locked, min_h)
        yield
        tops = [c._extent(j)[1][2] - self.s.vlo[2] for j in range(c.n)]
        order = sorted(range(c.n), key=lambda j: (c._extent(j)[0][2], j))
        done = 0
        for rnd in range(2):
            moved = 0.0
            for j in order:
                for ax in ((1, 0) if self.s.spread_z else (2, 1, 0)):     # (not down again after spreading)
                    if self._time_up(0.0):
                        break
                    moved += c.move(j, ax, tops)
                done += 1
                self._take_final(qs, c)
                yield
            if moved < 0.05 or self._time_up(0.0):
                break
        self._take_final(qs, c)
        self.log.append('%6.1f s  compacted  height %.1f mm (%.1f before), %d parts moved %.0f mm in all'
                        % (self.elapsed, self.final_height, h0, c.moves, c.travel))

    def _take_final(self, qs, c):
        self.final = {q: c.M[j].copy() for j, q in enumerate(qs)}
        top = self.cur.base_height
        for j in range(c.n):
            top = max(top, c._extent(j)[1][2] - self.s.vlo[2])
        self.final_height = float(top)
        self.version += 1

    def close(self):
        if self._event is not None:
            self._event.set()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if self.g._pool is not None:
            self.g._pool.shutdown(wait=False)

    # ------------------------------------------------------------ in parallel
    def _work_parallel(self):
        """The same work with `workers` processes: the first layout here,
        the probes in the processes meanwhile, then the runs with the best
        orientations, then ruin-and-recreate chains from the best layout,
        one per process, in slices of a few seconds."""
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        n = self.workers
        try:
            ctx = mp.get_context('spawn')
            self._event = ctx.Event()
            self._pool = ProcessPoolExecutor(n, mp_context=ctx, initializer=_child_init,
                                             initargs=(self.parts, self.s, self.locked, self._event, self.preset,
                                                       self.start))
        except Exception as ex:                   # (no processes here: do it all in this one)
            self.log.append('%6.1f s  no worker processes (%s): working in one' % (self.elapsed, type(ex).__name__))
            self.workers = 1
            yield from self._work()
            return
        inflight = {}
        self.cur = self._fresh()
        self._lb = self.lower_bound()
        # what the probes will be: each part with copies, tallest orientations first
        pending = collections.deque()
        limit = {}
        ranks = collections.defaultdict(list)
        n_probes = collections.Counter()
        turned = set()
        later = {}
        for i, first, rest in self._probe_plan():
            for k in first:
                pending.append((i, k))
                n_probes[i] += 1
            later[i] = rest
            limit[i] = [None]

        def pump():
            while len(inflight) < n and pending and not self._time_up(self._probe_keep()):
                i, k = pending.popleft()
                inflight[self._pool.submit(_task_probe, i, k, limit[i][0], self._extra.get(i, []))] = \
                    ('probe', i, k)

        def collect():
            got = False
            for f in [f for f in inflight if f.done()]:
                what = inflight.pop(f)
                got = True
                try:
                    r = f.result()
                except Exception as ex:
                    self.log.append('%6.1f s  a worker failed (%s)' % (self.elapsed, type(ex).__name__))
                    continue
                if what[0] == 'probe':
                    i, k, left, h = r
                    self._rank(i, k, left, h, ranks[i], limit[i])
                    if len(ranks[i]) == n_probes[i]:
                        more = [] if i in turned else self._turns(i, ranks[i]) + later.get(i, [])
                        turned.add(i)
                        if more:
                            pending.extendleft((i, k) for k in reversed(more))
                            n_probes[i] += len(more)
                        else:
                            self.cands[i] = [k for _, k in sorted(ranks[i])]
                            self.log.append('%6.1f s  probes     %s: orientations %s, best first'
                                            % (self.elapsed, self.parts[i].name, self.cands[i][:4]))
                elif what[0] == 'run':
                    label, choice, recs, key, why = r
                    self.why.update(why)
                    if self._adopt(recs, key, label, choice) and label == 'small first':
                        self.small_first = True
                elif what[0] == 'rr':
                    recs, key, choice, iters = r
                    self.iterations += iters
                    self._adopt(recs, key, 'improved', choice)
            return got

        def idle():
            if not collect():
                time.sleep(0.01)

        # 1. the first layout, here, while the processes probe
        if self.start:
            left = self._load(self.cur, self.start)
            self.log.append('%6.1f s  start      %d parts kept where they are, %d to place'
                            % (self.elapsed, len(self.cur.recs), len(left)))
            todo, label = left, 'current'
        else:
            todo, label = self.copies, 'greedy'
        for _ in self._build(self.cur, todo, {}, count=True):
            pump()
            collect()
            yield
        if self.cancel and not self.start:
            return
        self._consider(self.cur, label, {})
        self.phase = 'improving'
        self._reserve = self._compact_reserve()
        if self._at_bound():
            yield from self._finish()
            return
        if self._some_left():
            inflight[self._pool.submit(_task_run, {}, 'small first', self._extra, True)] = ('run',)
        # 2. the rest of the probes
        while (pending or inflight) and not self._time_up(self._probe_keep()):
            pump()
            idle()
            yield
        # (probes still running are not waited for)
        for f in [f for f, w in inflight.items() if w[0] == 'probe']:
            f.cancel()
        pending.clear()
        for i in ranks:
            if i not in self.cands and ranks[i]:
                self.cands[i] = [k for _, k in sorted(ranks[i])]
        # 3. greedy with the best few orientations, one run per process
        if self.cands and not self._time_up(0.0) and not self._at_bound():
            for m in (1, 2, 3):
                if m > 1 and all(len(v) < m for v in self.cands.values()):
                    break
                choice = {i: v[:m] for i, v in self.cands.items()}
                inflight[self._pool.submit(_task_run, choice, 'best %d' % m, self._extra, self.small_first)] = \
                    ('run',)
            while any(w[0] == 'run' for w in inflight.values()) and not self._time_up(0.0):
                idle()
                yield
        # 4. ruin and recreate, one chain per process, from the best layout
        seeds = itertools.count(1000 * (self.s.seed or 0) + 1)
        while not self._time_up() and not self._at_bound() and \
                (self.max_iterations is None or self.iterations < self.max_iterations):
            while len(inflight) < n:
                left = self.time_left()
                sl = 8.0 if left is None else max(1.0, min(8.0, left))
                inflight[self._pool.submit(_task_rr, self.best, self.best_key, self.best_choice, self.cands,
                                           next(seeds), sl, self._lb, self._extra, self.small_first)] = ('rr',)
            idle()
            yield
        # (what is still running is told to stop and gives its best so far)
        self._event.set()
        t = time.perf_counter()
        while inflight and time.perf_counter() - t < 3.0:
            idle()
        if self.best is not None and not self.cancel:
            self.cur.restore(self.best)
        yield from self._finish()

    def _adopt(self, recs, key, label, choice):
        """A layout from a worker process, if it is the best so far."""
        if recs is None or (self.best_key is not None and not key < self.best_key):
            return False
        old = self.best_key
        self.best_key = key
        self.best = dict(recs)
        self.final = self.final_height = None
        self.best_choice = dict(choice or {})
        self.version += 1
        self.log.append('%6.1f s  %-10s height %.1f mm, %d left out%s'
                        % (self.elapsed, label, self._best_height(), sum(self._left(recs).values()),
                           '' if old is None else ' (better)'))
        return True


MESH_BYTES_FOR_WORKERS = 4e9      # every worker process holds its own copy of the meshes


def _axis_rot(ax, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    if ax == 0:
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if ax == 1:
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def parallel_workers(parts=()):
    """Processes for a job: none inside Blender itself (its Python can only
    be started as Blender), else up to 8, leaving two cores to the rest, and
    fewer for very large meshes."""
    if __name__ != 'pack.job':
        return 1
    n = max(1, min(8, (os.cpu_count() or 1) - 2))
    seen, size = set(), 0
    for p in parts:
        if id(p.mesh) not in seen:
            seen.add(id(p.mesh))
            size += p.mesh.verts.nbytes + p.mesh.tris.nbytes
    if size:
        n = min(n, max(1, int(MESH_BYTES_FOR_WORKERS // size)))
    return n


# ------------------------------------------------------------ in the worker processes

_CHILD = {}


def _child_init(parts, settings, locked, event, preset, start):
    _CHILD['job'] = Job(parts, settings, locked, threads=1, preset=preset, workers=1, start=start)
    _CHILD['event'] = event
    _CHILD['ppid'] = os.getppid()


def _child_run(job, gen):
    """Drive one generator of the job to its end (or until told to stop)."""
    job._gen = gen
    job.done = False
    job.cancel = False
    ev = _CHILD['event']
    while job.step(0.05):
        if ev.is_set() or (os.name != 'nt' and os.getppid() != _CHILD['ppid']):
            job.cancel = True
    job._gen = None


def _sync(job, extra):
    for i, e in extra.items():
        job.g.add_orientations(i, e)
        job._extra[i] = list(e)


def _task_probe(i, k, stop_above, extra):
    job = _CHILD['job']
    _sync(job, {i: extra} if extra else {})
    lay = job._fresh()
    q = job.parts[i].quantity
    cap0 = job.g.cap
    job.g.cap = None
    try:
        _child_run(job, job._build(lay, [(i, c) for c in range(q)], {i: [k]}, stop_above=stop_above))
    finally:
        job.g.cap = cap0
    return i, k, q - len(lay.recs), float(lay.bucket.height)


def _task_run(choice, label, extra, small=False):
    job = _CHILD['job']
    _sync(job, extra)
    job.small_first = small
    job.why = {}
    lay = job._fresh()
    _child_run(job, job._build(lay, job._order(), choice))
    if job.cancel:
        return label, choice, None, None, {}
    return label, choice, dict(lay.recs), job.key(lay), dict(job.why)


def _task_rr(best, best_key, choice, cands, seed, seconds, lb, extra, small=False):
    job = _CHILD['job']
    _sync(job, extra)
    job.small_first = small
    job.rng = random.Random(seed)
    job.cands = dict(cands)
    job.best, job.best_key, job.best_choice = dict(best), best_key, dict(choice)
    if job.cur is None:
        job.cur = job._fresh()
    job._lb = lb
    job.budget = job.elapsed + seconds
    job.iterations = 0
    _child_run(job, job._improve())
    return dict(job.best), job.best_key, dict(job.best_choice), job.iterations


def solve(parts, settings, locked=(), threads=None, seed=None, preset=None, time_budget=None, on_update=None):
    job = Job(parts, settings, locked, threads, seed, preset, time_budget)
    try:
        return job.run(on_update)
    finally:
        job.close()
