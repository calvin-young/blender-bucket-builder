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
   (the top layer, the tallest part and its neighbors, a region, or a few at
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

from . import metrics, score as S
from .greedy import Greedy
from .problem import Placement, Result


class Layout:
    """Parts placed in a bucket that can be taken out again.

    A record is (orientation, cell offset), or ('pin', cell) for a part
    that sits exactly where it was given (Improve Current Layout), off the
    packer's grid: it is voxelized where it is and does not move unless the
    improvement loop takes it out (and then it is placed again like any
    other).  `pin_of(q)` gives its (grid, cell, profile, volume, top)."""

    def __init__(self, g, locked=(), pin_of=None, faces_of=None):
        self.g = g
        self.bucket = g.new_bucket(locked)
        self.faces_of = faces_of     # (part, orientation) -> large flat faces or None (Job._faces)
        self.bucket.flat = {}        # (part, copy) -> (faces, translation), for Job._nudge
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
        if self.faces_of is not None:
            f = self.faces_of(i, k)
            if f is not None:
                self.bucket.flat[(i, c)] = (f, self.bucket.translation(p.grid, b))

    def remove(self, i, c):
        k, b = self.recs.pop((i, c))
        self.tops.pop((i, c))
        self.bucket.flat.pop((i, c), None)
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

    def center(self, q):
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
        if settings.flat_rules:
            self.g.pose_penalty = self._pose_penalty
            self.g.nudge = self._nudge
        # The density limit, met at the end: pack as low as the parts go,
        # then lift them, evenly, until the build is as high as the limit
        # wants (lifting a few parts during packing, as greedy does, gives
        # one crowded layer and a nearly empty one).  Not when parts are to
        # stay low, nor when even the whole volume would be too dense.
        self._cap_late = False
        self.best_ok = self.best_ok_key = self.best_ok_choice = None
        if settings.max_density and settings.spread_z:
            vol = sum(abs(metrics.mesh_stats(p.mesh)[0] * np.linalg.det(np.asarray(p.base, float))) * p.quantity
                      for p in parts)
            vol += sum(abs(metrics.mesh_stats(m)[0] * np.linalg.det(np.asarray(M, float)[:3, :3]))
                       for m, M in locked)
            if vol / (settings.area * settings.max_density) <= settings.vhi[2] - settings.vlo[2]:
                self._cap_late = True
                self.g.cap = None
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
        self._face_cache = {}       # (part, orientation) -> pack.quality.Faces or None
        self._has_faces = None
        self.flat = None            # (facing pairs, area, wall faces, area) of the best layout, at the end
        self._apart = set()         # (part, copy) moved apart by _separate: the compaction leaves them
        self._lattices = {}         # (part, orientation) -> pack.pattern.Lattice or None
        self._pats = {}             # group of parts -> [_Pattern] (job._group_patterns)
        self._seeded = {}           # group -> the kind of lattice its last seeding used
        self._lattice_done = set()  # parts probed before the first layout (job._lattice_first)
        self._too_large = set()     # parts that fit the volume in no orientation (job.lower_bound)
        self._fams = None           # families of similar parts (pack.family), found when first wanted
        self._fcands = {}           # family key -> the reference's orientations, best lattice first
        self._fk = {}               # (family key, member, reference orientation) -> member orientation
        self._place_cost = [0.0, 0]  # seconds spent placing copies with greedy, orientations tried
        self._lattice_skip = set()  # (and those that will be: no probes for them in the processes)
        self._lattice_wanted = False
        self.small_first = False    # full runs smallest part first (more parts in a bucket that is full)
        self.final = None           # {(part, copy): 4 x 4}: the best layout after exact compaction
        self.final_height = None
        self._reserve = 0.0         # seconds kept for compaction at the end
        self._last_better = 0.0     # when the best layout last got better (for Settings.stall_time)
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
        el = self._elapsed + (now - self._t if self._t else 0.0)
        if self.s.stall_time and self.best is not None and self.phase == 'improving' and \
                el - self._last_better >= self.s.stall_time:
            return True                     # (nothing better for a while: Effort Best stops here)
        return el >= self.budget - keep

    # ------------------------------------------------------------ scoring
    def _left(self, recs):
        left = {t: 0 for t in self.tiers}
        for i, c in self.copies:
            if (i, c) not in recs:
                left[self.parts[i].tier] += 1
        return left

    def _cap_height(self, volume):
        """The height the density limit wants for that much volume (0 when
        it is met during packing): the parts must fill at least that much,
        from the lowest to the highest (they stand on the floor)."""
        if not self._cap_late:
            return 0.0
        return volume / (self.s.area * self.s.max_density)

    def key(self, lay):
        b = lay.bucket
        h = max(b.height, self._cap_height(b.volume))       # (lower is not better: it will be lifted)
        k = S.key(self._left(lay.recs), b.volume, h, b.profile, self.s, self.preset)
        q = self._flat_cost(lay)
        return k[:-1] + (k[-1] + q,) if q else k

    # ------------------------------------------------------------ print quality
    def _faces(self, i, k):
        """Large flat faces of part i in orientation k (pack.quality), or None."""
        key = (i, k)
        if key not in self._face_cache:
            from . import quality
            part = self.parts[i]
            A = np.asarray(self.g.orients(i)[k], float)
            f = None
            if k != 0:
                f0 = self._faces(i, 0)
                R = A @ np.linalg.inv(np.asarray(self.g.orients(i)[0], float))
                if np.abs(R @ R.T - np.eye(3)).max() < 1e-9:
                    # a turn of orientation 0: its faces turned (planes through the
                    # origin keep their offsets, rectangles their extents)
                    self._face_cache[key] = None if f0 is None else quality.turned(f0, R)
                    return self._face_cache[key]
            f = quality.flat_faces(part.mesh.verts, part.mesh.tris, A)
            self._face_cache[key] = f if len(f) else None
        return self._face_cache[key]

    def flat_report(self, lay, pairs_out=None, who=None):
        """(facing pairs, facing area, wall faces, wall area) of lay.  With
        pairs_out (see quality.assess) and who, a list that gets the (part,
        copy) of each id."""
        from . import quality
        placed = []
        for (i, c), r in sorted(lay.recs.items()):
            if r[0] == 'pin':
                continue
            k, cell = r
            f = self._faces(i, k)
            if f is not None:
                if who is not None:
                    who.append((i, c))
                placed.append((len(placed), f, lay.bucket.translation(self.g.pose(i, k).grid, cell)))
        return quality.assess(placed, pairs_out=pairs_out)

    def _separate(self):
        """Large flat faces left facing each other closely: move one of the
        two parts straight away from the other, on the grid, as far as the
        rule wants (if there is room and the build gets no higher), and keep
        it there (the compaction leaves it alone).  A generator."""
        from . import quality
        lay = self.cur
        lay.restore(self.best)
        b = lay.bucket
        h = b.h
        H = b.height
        moved = 0
        for rnd in range(3):
            pairs, who = [], []
            self.flat_report(lay, pairs, who)
            if not pairs:
                break
            progress = False
            for pa, pb, n, s, area in sorted(pairs, key=lambda t: -t[4]):
                ax = int(np.argmax(np.abs(n)))
                if abs(n[ax]) < 0.99:
                    continue                    # (only faces along an axis: the grid moves along axes)
                want = int(np.ceil((quality.NEAR - s) / h - 1e-9))
                if want <= 0:
                    continue
                sign = 1 if n[ax] > 0 else -1     # (the second part is in front of the first along n)
                for q, sg in ((who[pb], sign), (who[pa], -sign)):
                    if q in self._apart:
                        continue
                    i, c = q
                    k, cell = lay.remove(i, c)
                    p = self.g.pose(i, k)
                    room = b.slide(p.grid, cell, ax, want, sign=sg)
                    new = list(cell)
                    new[ax] += sg * room
                    ok = room >= want and b.in_range(p.grid, new)
                    lay.add(i, c, k, tuple(new) if ok else cell)
                    if ok and ax == 2 and self.g._top(b, p, new) > H + 1e-9:
                        # (higher than the build: only if the score says it is worth it)
                        k_new = self.key(lay)
                        lay.remove(i, c)
                        lay.add(i, c, k, cell)
                        if not k_new < self.key(lay):
                            ok = False
                        else:
                            lay.remove(i, c)
                            lay.add(i, c, k, tuple(new))
                    if ok:
                        self._apart.update((who[pa], who[pb]))      # (neither may close the gap again)
                        moved += 1
                        progress = True
                        break
                yield
            if not progress:
                break
        if moved:
            self.best = dict(lay.recs)
            self.best_key = self.key(lay)
            self.final = self.final_height = None
            self.version += 1
            self.log.append('%6.1f s  flat faces %d parts moved apart' % (self.elapsed, moved))

    def _flat_cost(self, lay):
        """The flat-face rules' part of the score, in mm of height."""
        if not self.s.flat_rules or not self._any_faces():
            return 0.0
        pairs, a_pair, walls, a_wall = self.flat_report(lay)
        return self.s.flat_pair_weight * a_pair + self.s.flat_wall_weight * a_wall

    def _any_faces(self):
        if self._has_faces is None:
            self._has_faces = any(self._faces(i, 0) is not None for i in range(len(self.parts)))
        return self._has_faces

    def _wall_turns(self):
        """Parts with large flat faces that turn: their flattest orientations
        also turned 45 degrees about the vertical, so that the packer can
        keep those faces off the side walls (it weighs that against
        height)."""
        if not self.s.flat_rules or not self._any_faces():
            return
        from . import quality
        for i, part in enumerate(self.parts):
            if part.mode not in ('free', 'z') or i in self._too_large:
                continue
            ks = sorted(range(len(self.g.orients(i))), key=lambda k: (self._z_extent(i, k), k))[:2]
            extra = list(self._extra.get(i, []))
            n0 = len(extra)
            for k in ks:
                f = self._faces(i, k)
                if f is None:
                    continue
                wall = (abs(f.n[:, 0]) > quality.WALL_COS) | (abs(f.n[:, 1]) > quality.WALL_COS)
                if wall.any():
                    extra.append(_axis_rot(2, 45.0) @ np.asarray(self.g.orients(i)[k], float))
            if len(extra) > n0:
                self._extra[i] = extra
                self.g.add_orientations(i, extra)

    def _meets_cap(self, b):
        """Whether bucket b is within the density limit as it is, over the
        height its parts fill."""
        nz = np.nonzero(b.profile > 1e-9)[0]
        if not len(nz):
            return True
        return b.volume <= self.s.area * self.s.max_density * (b.height - nz[0] * b.h) + 1e-6

    def _keep_honest(self, lay, choice):
        """With the density limit met late, the best layout that meets it as
        it is (scored at its own height): what to fall back on when the best
        one's parts nest and cannot be lifted apart."""
        if not self._cap_late or not self._meets_cap(lay.bucket):
            return
        b = lay.bucket
        k = S.key(self._left(lay.recs), b.volume, b.height, b.profile, self.s, self.preset)
        q = self._flat_cost(lay)
        k = k[:-1] + (k[-1] + q,) if q else k
        if self.best_ok_key is None or k < self.best_ok_key:
            self.best_ok_key, self.best_ok, self.best_ok_choice = k, dict(lay.recs), dict(choice or {})

    def _consider(self, lay, label, choice=None):
        """Keep lay's layout if it is the best so far."""
        self._keep_honest(lay, choice)
        k = self.key(lay)
        if self.best_key is None or k < self.best_key:
            old = self.best_key
            self.best_key = k
            self.best = dict(lay.recs)
            self.final = self.final_height = None
            if choice is not None:
                self.best_choice = dict(choice)
            self.version += 1
            self._last_better = self.elapsed
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
    def _used(self, lay):
        """{part: the orientations its copies have in lay}: a lattice
        layout's choice, for the runs and improvements made from it."""
        out = {}
        for (i, c), r in lay.recs.items():
            if r[0] != 'pin':
                out.setdefault(i, set()).add(r[0])
        return {i: sorted(v) for i, v in out.items()}

    def _build(self, lay, todo, allowed, count=False, stop_above=None, timed=False):
        """Put the copies in `todo` in, in that order (a generator: yields
        after every part); stops once the build is higher than `stop_above`."""
        for n, (i, c) in enumerate(todo):
            if self.cancel or (timed and self._time_up(0.0)):
                return
            if stop_above is not None and lay.bucket.height > stop_above:
                return
            if count:
                self.progress = (n, len(todo))
            t0 = time.perf_counter()
            best, why = self.g.place_one(lay.bucket, i, allowed.get(i))
            na = len(self.g.orients(i)) if allowed.get(i) is None else len(allowed[i])
            self._place_cost[0] += time.perf_counter() - t0
            self._place_cost[1] += na
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
            if part.quantity < 2 or part.mode == 'fixed' or i in self._lattice_done or i in self._lattice_skip:
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

    PATTERN_FROM = 20         # copies: from this many on, a probe is a lattice (pack.pattern; below,
                              # greedy, which stops early, is cheaper: about 12 ms a copy against 0.2 - 0.5 s)

    def _probe(self, i, ks, ranks, limit):
        """Probe orientations ks of part i (a generator); once one has placed
        every copy, the others stop as soon as they are higher."""
        cheap = self.parts[i].quantity >= self.PATTERN_FROM          # (a lattice: the run after it is cheap too)
        for k in ks:
            if self._time_up(self._reserve if cheap else self._probe_keep()):
                return
            out = []
            yield from self._probe_one(i, k, limit[0], out)
            self._rank(i, k, out[0], out[1], ranks, limit)

    def _probe_one(self, i, k, stop_above, out, q=None):
        """The copies of part i alone (q of them: by default its quantity),
        in orientation k, without the density limit: on the best lattice
        (many copies), else one by one with greedy.  Appends (left out,
        height) to `out` (a generator)."""
        q = self.parts[i].quantity if q is None else q
        if q >= self.PATTERN_FROM:
            lay = self._fresh()
            cells, top = self._pattern_cells(i, k, lay, q)
            if cells is not None:
                yield
                p = self.g.pose(i, k)
                top = self.g._top(lay.bucket, p, cells[-1]) if cells else 0.0   # (the highest: all alike, by z)
                out += [q - len(cells), max(float(top), float(lay.bucket.height))]
                return
        cap0 = self.g.cap
        self.g.cap = None
        try:
            lay = self._fresh()
            yield from self._build(lay, [(i, c) for c in range(q)], {i: [k]}, stop_above=stop_above)
        finally:
            self.g.cap = cap0
        out += [q - len(lay.recs), float(lay.bucket.height)]

    def _pattern_cells(self, i, k, lay, q=None):
        """(cells, top) of up to q copies of part i in orientation k on its
        lattice in lay's bucket (around what is there); (None, 0) if it has
        no lattice."""
        from . import pattern
        key = (i, k)
        if key not in self._lattices:
            self._lattices[key] = pattern.lattice(self.g.pose(i, k).grid)
        L = self._lattices[key]
        if L is None:
            return None, 0.0
        return pattern.place(lay.bucket, self.g.pose(i, k).grid, L, self.parts[i].quantity if q is None else q)

    def _run_cost(self, choice, skip=()):
        """Seconds a greedy run of every copy (but those of the parts in
        `skip`, which go on lattices) would take with these orientation
        choices, from what placing copies has cost so far."""
        t, n = self._place_cost
        per = t / n if n else 0.004                 # (per copy and orientation tried)
        return per * sum(len(choice[i]) if choice.get(i) else len(self.g.orients(i))
                         for i, c in self.copies if i not in skip)

    LATTICE_FIRST_GROUPS = 3  # kinds of part (or families) at most for lattices before the first layout

    def _lattice_parts(self):
        """Parts whose lattices come before the first greedy layout: those
        with many copies, when they are most of the build (not for a start
        layout)."""
        if self.start:
            return []
        big = [i for i, p in enumerate(self.parts)
               if p.quantity >= self.PATTERN_FROM and i not in self._too_large and i not in self._in_family()]
        n = sum(p.quantity for p in self.parts)
        covered = sum(self.parts[i].quantity for i in big) + \
            sum(self._family_quantity(F) for F in self._families())
        groups = len(big) + len(self._families())
        # (a few kinds that are most of the build: a mixed build is better
        # off with greedy first, and its lattices later)
        self._lattice_wanted = covered >= 0.9 * n and groups <= self.LATTICE_FIRST_GROUPS
        return big if self._lattice_wanted else []

    # ------------------------------------------------------------ families
    def _families(self):
        """Families of similar parts that turn freely, with many copies in
        all (a part with many copies of its own stays on its own)."""
        if self._fams is None:
            from . import family
            ok = [i for i, p in enumerate(self.parts) if p.mode == 'free' and i not in self._too_large]
            vols = [self.g._part_volume(i) * abs(np.linalg.det(np.asarray(p.base, float)))
                    for i, p in enumerate(self.parts)]
            try:
                fams = family.find(self.parts, vols, ok)
            except Exception:
                fams = []
            self._fams = [F for F in fams if self._family_quantity(F) >= self.PATTERN_FROM]
            for F in self._fams:
                self.log.append('%6.1f s  family     %d parts alike (%d copies), with %s'
                                % (self.elapsed, len(F.members), self._family_quantity(F),
                                   self.parts[F.ref].name))
        return self._fams

    def _in_family(self):
        return {m for F in self._families() for m in F.members}

    def _family_quantity(self, F):
        return sum(self.parts[m].quantity for m in F.members)

    def _family(self, grp):
        return next((F for F in self._families() if F.key() == grp), None)

    def _fam_k(self, F, m, k):
        """Member m's orientation matching the reference's orientation k
        (added to the member's orientations if it has none like it)."""
        key = (F.key(), m, k)
        if key not in self._fk:
            if m == F.ref:
                self._fk[key] = k
            else:
                A = F.matrix(np.asarray(self.g.orients(F.ref)[k], float), m)
                km = next((j for j, B in enumerate(self.g.orients(m)) if np.allclose(A, B, atol=1e-6)), None)
                if km is None:
                    extra = list(self._extra.get(m, [])) + [A]
                    self._extra[m] = extra
                    km = self.g.add_orientations(m, extra)[-1]
                self._fk[key] = km
        return self._fk[key]

    def _fam_env(self, F, k):
        """The envelope of the members of F in the reference's orientation k:
        (Envelope, {member: (orientation, shift)}).  Members are lined up by
        the centers of their bounding boxes."""
        from . import pattern
        items, ks = [], []
        h = self.s.h
        for m in F.members:
            km = self._fam_k(F, m, k)
            p = self.g.pose(m, km)
            W = np.asarray(self.parts[m].mesh.verts, float) @ np.asarray(p.A, float).T
            anchor = (W.max(0) + W.min(0)) / 2
            items.append((p.grid, tuple(int(x) for x in -np.rint((anchor - p.grid.origin) / h))))
            ks.append(km)
        env = pattern.Envelope(items)
        return env, {m: (km, env.shifts[j]) for j, (m, km) in enumerate(zip(F.members, ks))}

    FAMILY_FULL = 24          # members up to which a family also gets double lattices

    def _family_ks(self, F):
        """(the reference's orientations for single lattices, [(k, partner)]
        for double ones)."""
        cands = self._fcands.get(F.key()) or []
        seen, ks = set(), []
        for n, k in enumerate(cands):
            c = self._extents_class(F.ref, k)
            if n < 2 or c not in seen:
                ks.append(k)
            seen.add(c)
        pairs = []
        if len(F.members) > self.FAMILY_FULL:
            return ks[:2], pairs              # (every member is voxelized per orientation: keep it short)
        if cands:
            for ax in range(3):
                kb = self._partner(F.ref, cands[0], ax)
                if kb is not None and kb != cands[0] and (cands[0], kb) not in pairs:
                    pairs.append((cands[0], kb))
        return ks[:3], pairs

    def _family_prepare(self, F):
        """Every member orientation the family's lattices will use, made now
        (so that worker processes number them as this one does)."""
        ks, pairs = self._family_ks(F)
        for k in set(ks) | {k for p in pairs for k in p}:
            for m in F.members:
                self._fam_k(F, m, k)

    def _family_patterns(self, F, out):
        """Lattices for a family (a generator): single ones on its envelope in
        the reference's best orientations, and double ones with the
        partners turned 180 degrees."""
        from . import pattern
        ks, pairs = self._family_ks(F)
        envs = {}
        for k in ks:
            if self.cancel or self._time_up(0.0):
                return
            envs[k] = self._fam_env(F, k)
            yield
            L = pattern.lattice(envs[k][0])
            yield
            if L is not None:
                out.append(_Pattern('single', envs[k][0], L, [envs[k][1]]))
        for k, kb in pairs:
            if self.cancel or self._time_up(0.0):
                return
            ea, sa = envs[k] if k in envs else self._fam_env(F, k)
            eb, sb = self._fam_env(F, kb)
            yield
            for t in pattern.pairings(ea, eb, self.PAIRINGS):
                env = pattern.Envelope([(ea, (0, 0, 0)), (eb, tuple(int(x) for x in t))])
                L = pattern.lattice(env)
                yield
                if L is None:
                    continue
                add = lambda sh, d: tuple(int(a) + int(b) for a, b in zip(sh, d))
                out.append(_Pattern('double', env, L,
                                    [{m: (km, add(sh, env.shifts[0])) for m, (km, sh) in sa.items()},
                                     {m: (km, add(sh, env.shifts[1])) for m, (km, sh) in sb.items()}]))

    def _probe_family(self, F):
        """Lattice probes of a family: its reference in each of its
        orientations, as many copies as the family has (a generator)."""
        q = self._family_quantity(F)
        ks = sorted(self._distinct(F.ref), key=lambda k: (-self._z_extent(F.ref, k), k))
        ranks = []
        for k in ks:
            if self._time_up(self._reserve) or self.cancel:
                break
            out = []
            yield from self._probe_one(F.ref, k, None, out, q)
            if len(out) == 2:
                ranks.append(((out[0], round(out[1], 6), k), k))
        if ranks:
            self._fcands[F.key()] = [k for _, k in sorted(ranks)]
            self.log.append('%6.1f s  lattices   family of %s: orientations %s, best first'
                            % (self.elapsed, self.parts[F.ref].name, self._fcands[F.key()][:4]))

    FIRST_TURNS = 12          # turned lattice probes before the first layout (the rest come later)
    FIRST_SHARE = 0.25        # of the time: the most the lattices before the first layout may take

    def _first_time_up(self):
        return self._time_up(self._reserve) or (self.budget is not None and
                                                self.elapsed > self.FIRST_SHARE * self.budget)

    def _lattice_first(self, lay):
        """Lattice probes of the parts with many copies (with the turned
        ones), then those parts seeded on their best lattices and the rest
        put in around them with a few orientations each: the first layout
        (a generator)."""
        big = self._lattice_parts()
        cap0 = self.g.cap
        self.g.cap = None
        try:
            for n, i in enumerate(big):
                self.progress = (n, len(big) + 1)
                ks = sorted(self._distinct(i), key=lambda k: (-self._z_extent(i, k), k))
                ranks, limit = [], [None]
                yield from self._probe(i, ks, ranks, limit)
                turned = self._turns(i, ranks) if not self._first_time_up() else []
                turned = turned[:self.FIRST_TURNS]
                if turned:
                    yield from self._probe(i, turned, ranks, limit)
                if ranks:
                    self.cands[i] = [k for _, k in sorted(ranks)]
                    self._lattice_done.add(i)
                    self.log.append('%6.1f s  lattices   %s: orientations %s, best first'
                                    % (self.elapsed, self.parts[i].name, self.cands[i][:4]))
        finally:
            self.g.cap = cap0
        for F in self._families():
            if not self._first_time_up():
                yield from self._probe_family(F)
        groups = [(i,) for i in sorted(self._lattice_done, key=lambda i: (self.parts[i].tier,
                                                                         -self._size(i) * self.parts[i].quantity, i))]
        groups += [F.key() for F in self._families() if self._fcands.get(F.key())]
        for F in self._families():
            self._family_prepare(F)
        choice = dict(self._first_allowed())
        choice.update({i: v[:1] for i, v in self.cands.items()})
        self.progress = (len(big), len(big) + 1)
        yield from self._pattern_run(lay, groups, choice)

    def _pattern_types(self):
        """Groups of parts to seed as lattices, by tier, largest share first:
        each part with many copies (and whose probes were lattices) alone."""
        fam = self._in_family()
        ts = [i for i in sorted(range(len(self.parts)),
                                key=lambda i: (self.parts[i].tier, -self._size(i) * self.parts[i].quantity, i))
              if self.parts[i].quantity >= self.PATTERN_FROM and self.cands.get(i) and i not in fam]
        groups = [(i,) for i in ts]
        fs = [F for F in self._families() if self._fcands.get(F.key())]
        fs.sort(key=lambda F: (min(self.parts[m].tier for m in F.members),
                               -sum(self._size(m) * self.parts[m].quantity for m in F.members)))
        return [F.key() for F in fs] + groups

    def _pattern_runs(self):
        """[(label, groups seeded as lattices)]: the largest share alone, and
        every one in turn when there are several."""
        ts = self._pattern_types()
        if not ts:
            return []
        runs = [('pattern', ts[:1])]
        if len(ts) > 1:
            runs.append(('patterns', ts))
        return runs

    def _best_runs(self):
        """[(label, orientation choice, groups seeded as lattices or None)]:
        greedy with the best 1, 2, 3 orientations of each part, the lattice
        runs after the first."""
        if not self.cands:
            return []
        runs = []
        for m in (1, 2, 3):
            if m > 1 and all(len(v) < m for v in self.cands.values()):
                break
            runs.append(('best %d' % m, {i: v[:m] for i, v in self.cands.items()}, None))
        one = {i: v[:1] for i, v in self.cands.items()}
        runs[1:1] = [(label, one, types) for label, types in self._pattern_runs()]
        return runs

    def _pattern_ks(self, i):
        """Orientations a lattice run tries for part i: its two best, then the
        best of each other shape of bounding box (a flat stack may meet the
        density limit where copies standing in one layer do not)."""
        ks, seen = [], set()
        for n, k in enumerate(self.cands[i]):
            c = self._extents_class(i, k)
            if n < 2 or c not in seen:
                ks.append(k)
            seen.add(c)
        return ks[:5]

    DOUBLE_FROM = 2           # the best this many orientations get a partner turned 180 degrees
    PAIRINGS = 3              # offsets tried per partner (the most compact pairs)

    def _partner(self, i, k, ax):
        """The orientation of part i that is k turned 180 degrees about the
        world axis ax, if the part may turn so (None otherwise)."""
        R = np.diag([1.0 if a == ax else -1.0 for a in range(3)])
        B = R @ np.asarray(self.g.orients(i)[k], float)
        for kb, A in enumerate(self.g.orients(i)):
            if np.allclose(A, B, atol=1e-6):
                return kb
        return None

    def _group_patterns(self, grp, out):
        """The lattices a group of parts may be seeded on, into `out` (a
        generator: they are computed once, as needed): for a part, its
        single lattices in the orientations `_pattern_ks` gives, then
        double lattices of its best orientations with their partners turned
        180 degrees, paired in the most compact ways."""
        from . import pattern
        pats = self._pats.get(grp)
        F = self._family(grp) if pats is None and len(grp) > 1 else None
        if F is not None:
            pats = []
            yield from self._family_patterns(F, pats)
            self._pats[grp] = pats
        if pats is None:
            pats = []
            (i,) = grp
            for k in self._pattern_ks(i):
                pg = self.g.pose(i, k).grid
                key = (i, k)
                if key not in self._lattices:
                    self._lattices[key] = pattern.lattice(pg)
                    yield
                if self._lattices[key] is not None:
                    pats.append(_Pattern('single', pg, self._lattices[key], [{i: (k, (0, 0, 0))}]))
            done = set()
            for k in self.cands[i][:self.DOUBLE_FROM]:
                for ax in range(3):
                    if self.cancel or self._time_up(0.0):
                        break
                    kb = self._partner(i, k, ax)
                    if kb is None or (k, kb) in done or (kb, k) in done:
                        continue
                    done.add((k, kb))
                    ga, gb = self.g.pose(i, k).grid, self.g.pose(i, kb).grid
                    for t in pattern.pairings(ga, gb, self.PAIRINGS):
                        env = pattern.Envelope([(ga, (0, 0, 0)), (gb, tuple(int(x) for x in t))])
                        L = pattern.lattice(env)
                        yield
                        if L is not None:
                            pats.append(_Pattern('double', env, L, [{i: (k, env.shifts[0])},
                                                                    {i: (kb, env.shifts[1])}]))
            self._pats[grp] = pats
        out.extend(pats)

    def _pattern_choice(self, lay, groups, choice):
        """The choice to keep with a lattice layout: the orientations its
        seeded parts have in it (a double lattice has two)."""
        seeded = {i for g in groups for i in g}
        out = dict(choice)
        out.update({i: v for i, v in self._used(lay).items() if i in seeded})
        return out

    def _pattern_label(self, label, groups):
        kinds = {self._seeded.get(g) for g in groups}
        if 'double' in kinds:
            return label + ' x2'
        return label

    def _place_pattern(self, lay, pat, todo):
        """The copies in `todo` (in that order) on lattice pattern `pat` in
        lay's bucket, around what is there: ([(part, copy, orientation,
        cell)], highest top)."""
        from . import pattern
        n_slots = len(pat.slots)
        n_pts = -(-len(todo) // n_slots)
        cells, _ = pattern.place(lay.bucket, pat.env, pat.L, n_pts)
        got, queue, top = [], list(todo), 0.0
        for b in cells:
            for slot in pat.slots:
                j = next((j for j, q in enumerate(queue) if q[0] in slot), None)
                if j is None:
                    continue
                i, c = queue.pop(j)
                k, sh = slot[i]
                cell = tuple(int(x) for x in np.asarray(b) + sh)
                got.append((i, c, k, cell))
                top = max(top, float(self.g._top(lay.bucket, self.g.pose(i, k), cell)))
        return got, top, False

    def _pattern_run(self, lay, groups, choice):
        """Seed lay with the copies of each group on the best of its lattices
        (around what is there), then put the rest in with greedy (a
        generator).  Parts larger than every seeded one go in first, with
        greedy: big parts first, and the lattices fill around them."""
        seeded = {i for g in groups for i in g}
        if seeded:
            big = max(self._size(i) for i in seeded)
            rocks = [q for q in self._order() if q[0] not in seeded and self._size(q[0]) > big
                     and q not in lay.recs]
            if rocks:
                yield from self._build(lay, rocks, choice)
        for n_grp, grp in enumerate(groups):
            if n_grp and self.best is not None and self._time_up(0.0):
                return                      # (out of time: this layout would not be finished)
            pats = []
            yield from self._group_patterns(grp, pats)
            todo = [q for q in self._order() if q[0] in grp and q not in lay.recs]
            best = None
            for n, pat in enumerate(pats):
                if self.cancel:
                    return
                got, top, added = self._place_pattern(lay, pat, todo)
                if added:
                    for i, c, k, b in got:
                        lay.remove(i, c)
                yield
                if got:
                    # (a lattice denser than the limit allows cannot be
                    # lifted apart later: its copies nest)
                    vol = sum(self.g.pose(i, k).volume for i, c, k, b in got)
                    dense = bool(self.s.max_density) and vol > self.s.area * self.s.max_density * top
                    if dense and not self._cap_late:
                        continue            # (the limit is kept while packing: never seed above it)
                    key = (dense, -len(got), top, n)
                    if best is None or key < best[0]:
                        best = (key, got, pat.kind)
            if best is not None:
                for i, c, k, b in best[1]:
                    lay.add(i, c, k, b)
                self._seeded[grp] = best[2]
        yield from self._build(lay, [q for q in self._order() if q not in lay.recs], choice)

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
        return Layout(self.g, self.locked, self.pin, self._faces if self.s.flat_rules else None)

    def _pose_penalty(self, i, k):
        """Greedy's extra score for part i in orientation k: its large flat
        faces parallel to a side wall."""
        from . import quality
        f = self._faces(i, k)
        if f is None:
            return 0.0
        wall = (np.abs(f.n[:, 0]) > quality.WALL_COS) | (np.abs(f.n[:, 1]) > quality.WALL_COS)
        return self.s.flat_wall_weight * float(f.area[wall].sum())

    def _nudge(self, bucket, i, k, cell):
        """Greedy's choice for part i moved sideways away from large flat
        faces it would face closely, if there is room (a part placed next
        to a big flat neighbor gets the distance the rule wants before
        smaller parts fill the space)."""
        from . import quality
        f = self._faces(i, k)
        if f is None or not getattr(bucket, 'flat', None):
            return cell
        p = self.g.pose(i, k)
        cell = list(int(x) for x in cell)
        for _ in range(2):
            t = bucket.translation(p.grid, cell)
            placed = [(0, f, t)] + [(n + 1, ff, tt) for n, (ff, tt) in enumerate(bucket.flat.values())]
            pairs = []
            quality.assess(placed, pairs_out=pairs)
            moved = False
            for pa, pb, n, s, area in sorted(pairs, key=lambda t: -t[4]):
                if 0 not in (pa, pb):
                    continue
                ax = int(np.argmax(np.abs(n)))
                if ax == 2 or abs(n[ax]) < 0.99:
                    continue                    # (sideways only: lifting would raise the build)
                want = int(np.ceil((quality.NEAR - s) / bucket.h - 1e-9))
                sign = (1 if n[ax] > 0 else -1) * (1 if pb == 0 else -1)
                room = bucket.slide(p.grid, cell, ax, want, sign=sign)
                new = list(cell)
                new[ax] += sign * room
                if room >= want and bucket.in_range(p.grid, new):
                    cell = new
                    moved = True
                    break
            if not moved:
                break
        return tuple(cell)

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
        self._wall_turns()
        if self.start:
            left = self._load(self.cur, self.start)
            self.log.append('%6.1f s  start      %d parts kept where they are, %d to place'
                            % (self.elapsed, len(self.cur.recs), len(left)))
            yield from self._build(self.cur, left, self._first_allowed(), count=True)
            self._consider(self.cur, 'current', {})
        else:
            done = False
            self._lattice_parts()
            if self._lattice_wanted:
                # most copies are of parts with many: their lattices first
                # (a few seconds), then the rest around them; greedy one
                # copy at a time only if that leaves parts out
                lay = self._fresh()
                yield from self._lattice_first(lay)
                if self.cancel:
                    return
                self._consider(lay, self._pattern_label('pattern', [(i,) for i in self._lattice_done]), self._used(lay))
                self.cur = lay
                done = not self._some_left()
            if not done:
                cur = self._fresh()
                yield from self._build(cur, self.copies, self._first_allowed(), count=True)
                if self.cancel and self.best is None:
                    self.cur = cur
                    return                  # (what is placed so far is the answer; the rest "stopped")
                if self._consider(cur, 'greedy', {}) or self.best is None:
                    self.cur = cur
                if self.cancel:
                    return
        self.first_seconds = time.perf_counter() - t_first
        self.phase = 'improving'
        self._reserve = self._compact_reserve()
        if self._at_bound() or self._time_up():
            yield from self._finish()
            return
        if self._some_left() and self.preset == 'count':
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
        # greedy with the best few orientations of each part, and the parts
        # with many copies on lattices with the rest around them (the first
        # of these runs and the lattice runs are cheap and often the big
        # step: they may use the time kept for compaction)
        for n, (label, choice, types) in enumerate(self._best_runs()):
            if self._at_bound() or self._time_up(0.0):
                break
            est = self._run_cost(choice, {i for g in (types or ()) for i in g})
            if self._time_up((0.0 if n == 0 or types else self._reserve) + est):
                continue                    # (no time for this one; a lattice run may still fit)
            lay = self._fresh()
            if types:
                yield from self._pattern_run(lay, types, choice)
                label = self._pattern_label(label, types)
                choice = self._pattern_choice(lay, types, choice)
            else:
                yield from self._build(lay, self._order(), choice)
            if self._consider(lay, label, choice):
                self.cur = lay
        if self._first_allowed() and not self._time_up(self._reserve + self.first_seconds) \
                and not self._at_bound():
            # greedy with every orientation (the first layout had a few of each shape)
            lay = self._fresh()
            yield from self._build(lay, self._order(), {})
            if self._consider(lay, 'all turns', {}):
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
            yield from self._build(lay, todo, trial, timed=True)
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
                if self.s.restarts and self.rng.random() < 0.5:
                    # start afresh: every part again, in a shuffled order and with
                    # orientations drawn from the good ones, then improve from
                    # there (the best layout is kept whatever happens)
                    trial = {i: self.rng.choice(opts[i]) for i in opts}
                    trial = {i: v for i, v in trial.items() if v}
                    for q in list(lay.recs):
                        if lay.recs[q][0] != 'pin':
                            lay.remove(*q)
                    yield from self._build(lay, self._shuffle(self.copies), trial, timed=True)
                    yield from self._settle(lay)
                    self._consider(lay, 'restart', trial)
                    cur_key = self.key(lay)
                    choice = trial
            yield
        lay.restore(self.best)
        self.cur = lay

    def _size(self, i):
        V = np.asarray(self.parts[i].mesh.verts, float) @ np.asarray(self.parts[i].base, float).T
        return float(np.prod(V.max(0) - V.min(0)))

    FIRST_PER_SHAPE = 2

    def _first_allowed(self):
        """Orientations for the first layout: of a part that turns freely, two
        of each shape of bounding box (tallest first), not all 24.  It comes
        four times sooner for 150 chips, and is often better: copies that
        all lie alike nest, a mix does not.  The job tries every orientation
        later."""
        if getattr(self, '_first', None) is None:
            self._first = {}
            fam = self._in_family()
            for i, part in enumerate(self.parts):
                if part.mode != 'free' or i in self._too_large or i in fam:
                    continue                    # (a family's members go in on its lattices)
                ks = sorted(self._distinct(i), key=lambda k: (-self._z_extent(i, k), k))
                per = {}
                for k in ks:
                    v = per.setdefault(self._extents_class(i, k), [])
                    if len(v) < self.FIRST_PER_SHAPE:
                        v.append(k)
                pick = [k for v in per.values() for k in v]
                if len(pick) < len(ks):
                    self._first[i] = pick
        return self._first

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
        elif kind < 0.6:                      # the tallest part and its neighbors
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
        c0 = lay.center(q0)
        d = sorted(qs, key=lambda q: float(np.sum((lay.center(q) - c0) ** 2)))
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
            low = min([float(((np.asarray(self.parts[p.part].mesh.verts, float) @ p.matrix[:3, :3].T)[:, 2]
                              + p.matrix[2, 3]).min()) - self.s.vlo[2] for p in res.placements] + [res.height])
            fill = res.height - max(0.0, low)
            res.density = res.volume / (b.area * fill) if fill > 0 else None
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
            if self.s.flat_rules and self._any_faces():
                yield from self._separate()
                lay = self.cur
                lay.restore(self.best)
                pairs, a_pair, walls, a_wall = self.flat_report(lay)
                self.flat = (pairs, a_pair, walls, a_wall)
                self.log.append('%6.1f s  flat faces %d pairs close together (%.0f mm2), %d parallel to a wall '
                                '(%.0f mm2)' % (self.elapsed, pairs, a_pair, walls, a_wall))
        yield from self._compact_phase()

    def _spread(self):
        """Even out the density from layer to layer: lift parts straight up,
        on the grid and never above the build's height, where that makes the
        layers more even (one layer of 49 keycaps with one more on top
        becomes a staggered build).  Off with Settings.spread_z (Keep Parts
        Low: for builds the user fills by hand afterwards).  A generator."""
        if not self.s.spread_z:
            return
        even = not self._some_left()        # (a full bucket: lifting parts only takes room)
        lay = self.cur
        lay.restore(self.best)
        g, b = self.g, lay.bucket
        h = b.h
        H = max(self._best_height(), self._cap_height(b.volume))
        before = S.spread(b.profile, H, h)
        moved = 0
        for rnd in range(3 if even else 0):
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
        # the density limit: the build must reach H (lift a part with room)
        need = self._cap_height(b.volume)
        if need and b.height < need - 1e-9:
            for q in sorted((q for q, r in lay.recs.items() if r[0] != 'pin'), key=lambda q: (-lay.tops[q], q)):
                i, c = q
                k, cell = lay.remove(i, c)
                p = g.pose(i, k)
                want = int(np.ceil((need - g._top(b, p, cell)) / h - 1e-9))
                up = b.slide(p.grid, cell, 2, want, sign=1)
                lay.add(i, c, k, (cell[0], cell[1], cell[2] + (want if up >= want else 0)))
                if up >= want:
                    moved += 1
                    break
        if need and b.height < need - 1e-9:
            # no part can go up on its own (they nest; raising the whole build
            # would not make it less dense): pack again, lifting parts as they
            # go in
            self.g.cap = False
            lay2 = self._fresh()
            yield from self._build(lay2, self._order(), self.best_choice)
            self.g.cap = None
            self._cap_late = False
            label = 'packed again, the limit met during packing'
            if self.best_ok is not None and self.best_ok_key < self.key(lay2):
                # an earlier layout met the limit as it was, and is better
                lay2.restore(self.best_ok)
                self.best_choice = dict(self.best_ok_choice)
                label = 'took the best layout that met the limit as it was'
            if len(lay2.recs) >= len(lay.recs):
                self.cur = lay2
                lay, b = lay2, lay2.bucket
                moved += 1
                self.log.append('%6.1f s  density    %s' % (self.elapsed, label))
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
                if qs[j] in self._apart:
                    continue                    # (moved apart on purpose: its faces were too close)
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
        self._wall_turns()
        self._lattice_skip = set(self._lattice_parts())
        lattice_first = self._lattice_wanted
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
        done = False
        if lattice_first:
            # the lattices of the parts with many copies first, here (as in _work)
            lay = self._fresh()
            for _ in self._lattice_first(lay):
                pump()
                collect()
                yield
            if self.cancel:
                return
            self._consider(lay, self._pattern_label('pattern', [(i,) for i in self._lattice_done]), self._used(lay))
            self.cur = lay
            done = not self._some_left()
        if not done:
            cur = self._fresh() if lattice_first else self.cur
            for _ in self._build(cur, todo, self._first_allowed(), count=True):
                pump()
                collect()
                yield
            if self.cancel and not self.start:
                if self.best is None:
                    self.cur = cur
                return
            if self._consider(cur, label, {}) or self.best is None:
                self.cur = cur
        self.phase = 'improving'
        self._reserve = self._compact_reserve()
        if self._at_bound():
            yield from self._finish()
            return
        if self._some_left() and self.preset == 'count':
            inflight[self._pool.submit(_task_run, {}, 'small first', self._extra, True)] = ('run',)
        if self._first_allowed():
            inflight[self._pool.submit(_task_run, {}, 'all turns', self._extra, False)] = ('run',)
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
            for F in self._families():
                self._family_prepare(F)
            for label, choice, types in self._best_runs():
                if types:
                    f = self._pool.submit(_task_pattern, types, choice, label, self._extra, self.cands,
                                          self.small_first, self._families(), self._fcands)
                else:
                    f = self._pool.submit(_task_run, choice, label, self._extra, self.small_first)
                inflight[f] = ('run',)
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
        if recs is None:
            return False
        if self._cap_late:
            tmp = self._fresh()
            tmp.restore(recs)
            self._keep_honest(tmp, choice)
        if self.best_key is not None and not key < self.best_key:
            return False
        old = self.best_key
        self.best_key = key
        self.best = dict(recs)
        self.final = self.final_height = None
        self.best_choice = dict(choice or {})
        self.version += 1
        self._last_better = self.elapsed
        self.log.append('%6.1f s  %-10s height %.1f mm, %d left out%s'
                        % (self.elapsed, label, self._best_height(), sum(self._left(recs).values()),
                           '' if old is None else ' (better)'))
        return True


class _Pattern:
    """A lattice to seed: `env` the grid it was found for (a part's, or an
    envelope), `slots` per point of the lattice {part: (orientation, cell
    shift)} (one slot, or two for a double lattice)."""
    __slots__ = ('kind', 'env', 'L', 'slots')

    def __init__(self, kind, env, L, slots):
        self.kind, self.env, self.L, self.slots = kind, env, L, slots


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
    out = []
    _child_run(job, job._probe_one(i, k, stop_above, out))
    if len(out) < 2:                          # (told to stop)
        return i, k, job.parts[i].quantity, float('inf')
    return i, k, out[0], out[1]


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


def _task_pattern(types, choice, label, extra, cands, small=False, fams=(), fcands=None):
    job = _CHILD['job']
    _sync(job, extra)
    job.small_first = small
    job.cands = dict(cands)
    job._fams = list(fams)
    job._fcands = dict(fcands or {})
    job.why = {}
    lay = job._fresh()
    _child_run(job, job._pattern_run(lay, types, choice))
    if job.cancel:
        return label, choice, None, None, {}
    label = job._pattern_label(label, types)
    choice = job._pattern_choice(lay, types, choice)
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
