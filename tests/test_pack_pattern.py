"""Checks of the pattern solver (pack.pattern) and its use in the job.

    python3 tests/test_pack_pattern.py

The table of free offsets agrees with the bucket's own test; every lattice
it finds is valid (each copy fits where the others are, in an empty bucket
and around a locked part) and passes the exact engine; on the chips a
lattice probe is far lower than a greedy one, and the job's lattice run
finds the one-layer layout.
"""

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import job as J, pattern, referee, synthetic as S  # noqa: E402
from pack.problem import Part, Settings  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg, flush=True)
    if not ok:
        FAILS.append(msg)


def _job(parts, s, **kw):
    j = J.Job(parts, s, threads=1, workers=1, **kw)
    j.cur = j._fresh()
    j._lb = j.lower_bound()
    return j


def _shapes():
    return [('keycap', S.big_keycap(), 'free'), ('chip', S.pringle(), 'free'),
            ('figurine', S.figurine(), 'z'), ('bracket', S.bracket(), 'free'), ('cube', S.box(20, 20, 20), 'z')]


def test_free_offsets():
    print('free offsets agree with the bucket')
    rng = np.random.default_rng(3)
    s = Settings(vlo=(0, 0, 0), vhi=(300, 300, 300), gap=2.0, wall_gap=0.0, max_density=None, time_budget=None)
    for name, mesh, mode in _shapes():
        j = _job([Part(name, mesh, 2, mode)], s)
        for k in range(0, len(j.g.orients(0)), max(1, len(j.g.orients(0)) // 3)):
            pg = j.g.pose(0, k).grid
            free, off = pattern.free_offsets(pg)
            lay = j._fresh()
            b0 = lay.bucket.ilo - pg.s_lo + np.array(pg.kernel.shape)
            lay.bucket.mark(pg.solid, b0)
            hi = np.array(free.shape) - off
            D = np.stack([rng.integers(-o, h, 300) for o, h in zip(off, hi)], 1)
            bad = sum(bool(pattern._free_at(free, off, d[None])[0]) != lay.bucket.fits(pg, b0 + d) for d in D)
            check(bad == 0, '%s, orientation %d: 300 offsets, %d disagree' % (name, k, bad))


def _cells_valid(lay, pg, cells):
    """Whether each copy fits where the ones before it are (as greedy would
    check), into a copy of lay's bucket."""
    import copy
    tmp = copy.deepcopy(lay.bucket)
    for c in cells:
        if not tmp.fits(pg, c):
            return False
        tmp.mark(pg.solid, c)
    return True


def test_lattices():
    print('lattices are valid, on the grid and exactly')
    s = Settings(vlo=(0, 0, 0), vhi=(200, 160, 120), gap=2.0, wall_gap=2.0, max_density=None, time_budget=None)
    for name, mesh, mode in _shapes():
        j = _job([Part(name, mesh, 40, mode)], s)
        ks = sorted(set(j._distinct(0)), key=lambda k: -j._z_extent(0, k))[:3]
        for k in ks:
            lay = j._fresh()
            cells, top = j._pattern_cells(0, k, lay)
            if cells is None:
                check(True, '%s, orientation %d: no lattice' % (name, k))
                continue
            pg = j.g.pose(0, k).grid
            ok = _cells_valid(lay, pg, cells) and all(lay.bucket.in_range(pg, c) for c in cells)
            for c, cell in enumerate(cells):
                lay.add(0, c, k, cell)
            items = [('%s#%d' % (name, c), mesh, j.g.placement(lay.bucket, 0, c, k, cell).matrix)
                     for c, cell in enumerate(cells)]
            v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
            L = j._lattices[(0, k)]
            check(ok and v.ok and len(cells) > 0,
                  '%s, orientation %d: %d copies, v3 %s, top %.1f mm, referee %s'
                  % (name, k, len(cells), list(L.v3), top, 'clean' if v.ok else v))


def test_around_locked():
    print('a lattice around a locked part')
    big = S.box(90, 70, 40)
    L = np.eye(4)
    L[:3, :3] = S.rot('z', 25)
    L[:3, 3] = (100, 80, 20)
    s = Settings(vlo=(0, 0, 0), vhi=(200, 160, 120), gap=2.0, wall_gap=0.0, max_density=None, time_budget=None)
    parts = [Part('keycap', S.big_keycap(), 40, 'free')]
    j = J.Job(parts, s, [(big, L)], threads=1, workers=1)
    j.cur = j._fresh()
    j._lb = j.lower_bound()
    k = sorted(j._distinct(0), key=lambda k: j._z_extent(0, k))[0]
    lay = j._fresh()
    cells, top = j._pattern_cells(0, k, lay)
    pg = j.g.pose(0, k).grid
    ok = _cells_valid(lay, pg, cells)
    items = [('keycap#%d' % c, parts[0].mesh, j.g.placement(lay.bucket, 0, c, k, cell).matrix)
             for c, cell in enumerate(cells)]
    v = referee.check(items + [('locked', big, L)], s.vlo, s.vhi, s.gap, 0.0)
    check(ok and v.ok and len(cells) == 40, '%d copies around it, referee %s' % (len(cells), 'clean' if v.ok else v))


def test_chips():
    print('the chips: lattice probes against greedy probes, and the job')
    desc, parts, kw = S.buckets()['pringles']
    s = Settings(time_budget=None, **dict(kw, max_density=None))
    j = _job(parts, s)
    plan = j._probe_plan()
    first = plan[0][1]
    best = {}
    for label, frm in (('lattice', 1), ('greedy', 10 ** 9)):
        j.PATTERN_FROM = frm
        t = time.perf_counter()
        hs = []
        for k in first:
            out = []
            for _ in j._probe_one(0, k, None, out):
                pass
            hs.append(out[1] if out[0] == 0 else float('inf'))
        best[label] = min(hs)
        print('       %s probes of %s: %s mm, %.1f s' % (label, first, [round(h, 1) for h in hs],
                                                        time.perf_counter() - t))
    check(best['lattice'] < best['greedy'] - 20, 'lattice %.1f mm, greedy %.1f mm' % (best['lattice'], best['greedy']))
    jb = J.Job(parts, s, threads=1, workers=1, max_iterations=0)
    res = jb.run()
    jb.close()
    v = referee.check(res.items(parts), s.vlo, s.vhi, s.gap, s.wall_gap)
    check(any(' pattern ' in line for line in jb.log) and res.height <= 61.0 and len(res.placements) == 150
          and v.ok, 'job: %.1f mm, %d placed, referee %s' % (res.height, len(res.placements), 'clean' if v.ok else v))


if __name__ == '__main__':
    test_free_offsets()
    test_lattices()
    test_around_locked()
    test_chips()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
