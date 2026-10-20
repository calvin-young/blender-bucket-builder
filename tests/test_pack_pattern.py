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


def test_double():
    print('double lattices: every other copy turned 180 degrees')
    s = Settings(vlo=(0, 0, 0), vhi=(380, 284, 45), gap=2.0, wall_gap=2.0, max_density=None, time_budget=None)
    for name, mesh, want in (('gusset', S.gusset(), 1.1), ('peg', S.peg(), 1.1), ('chip', S.pringle(), None)):
        j = _job([Part(name, mesh, 10 ** 5, 'free')], s)
        ks = sorted(j._distinct(0), key=lambda k: (j._z_extent(0, k), k))
        j.cands = {0: ks}
        pats = []
        for _ in j._group_patterns((0,), pats):
            pass
        todo = [(0, c) for c in range(10 ** 4)]
        best = {}
        for pat in pats:
            lay = j._fresh()
            got, top, added = j._place_pattern(lay, pat, todo)
            if len(got) > best.get(pat.kind, (0,))[0]:
                best[pat.kind] = (len(got), pat, got, lay)
        n1, n2 = best.get('single', (0,))[0], best.get('double', (0,))[0]
        if want:
            check(n2 >= want * n1, '%s: %d copies on a double lattice, %d on a single one' % (name, n2, n1))
        else:
            check(n2 <= n1, '%s: the single lattice wins (%d against %d): chips nest without turning'
                  % (name, n1, n2))
        if n2:
            _, pat, got, lay = best['double']
            for i, c, k, cell in got[:60]:
                lay.add(i, c, k, cell)
            items = [('%s#%d' % (name, n), mesh, j.g.placement(lay.bucket, i, c, k, cell).matrix)
                     for n, (i, c, k, cell) in enumerate(got[:60])]
            v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
            check(v.ok, '  60 of them, referee %s' % ('clean' if v.ok else v))


def _chips(n, seed=5):
    rng = np.random.default_rng(seed)
    return [Part('chip%d' % k, S.pringle(float(rng.uniform(27, 33)), float(rng.uniform(36, 44)),
                                         float(rng.uniform(1.8, 2.4))), 1, 'free') for k in range(n)]


def test_family_find():
    print('families of similar parts')
    from pack import family, metrics
    rng = np.random.default_rng(2)
    chip = S.pringle()
    turned = []
    for k in range(6):
        q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        if np.linalg.det(q) < 0:
            q[:, 0] *= -1
        turned.append(Part('turned%d' % k, chip, 1, 'free', base=q))
    parts = _chips(20) + turned + [Part('wedge%d' % k, S.wedge(55 + k, 25), 1, 'free') for k in range(4)] + \
        [Part('keycap', S.big_keycap(), 1, 'free'), Part('tee', S.tee(), 1, 'free'),
         Part('fixed chip', S.pringle(), 1, 'fixed')]
    vols = [abs(metrics.mesh_stats(p.mesh)[0]) for p in parts]
    fams = family.find(parts, vols, range(len(parts)))
    names = sorted(sorted(parts[m].name[:4] for m in F.members)[0] + ' x%d' % len(F.members) for F in fams)
    check(names == ['chip x26', 'wedg x4'], 'found %s' % names)
    F = next(F for F in fams if len(F.members) == 26)
    A = np.asarray(J.Job(parts, Settings(time_budget=None), workers=1).g.orients(F.ref)[0], float)
    ext = [np.ptp(np.asarray(parts[m].mesh.verts) @ F.matrix(A, m).T, axis=0) for m in F.members]
    E = np.array(ext)
    same = np.ptp(E[[F.members.index(i) for i in range(20, 26)]], axis=0).max()
    check(same < 1e-6, 'the six turned copies of one chip line up exactly (extents agree to %.1e mm)' % same)
    thin = set(np.argmin(E, axis=1).tolist())
    check(len(thin) == 1, 'every member lies the same way (thinnest along axis %s; extents %s to %s)'
          % (sorted(thin), E.min(0).round(1), E.max(0).round(1)))


def test_family_job():
    print('a family packed: different chips nest like copies')
    from pack import family
    parts = _chips(40)
    s = Settings(vlo=(0, 0, 0), vhi=(240, 200, 200), gap=2.0, wall_gap=2.0, max_density=None, time_budget=40.0)
    res = {}
    find = family.find
    try:
        for mode in ('families', 'none'):
            if mode == 'none':
                family.find = lambda *a, **k: []
            jb = J.Job(parts, s, threads=1, workers=1)
            r = jb.run()
            jb.close()
            v = referee.check(r.items(parts), s.vlo, s.vhi, s.gap, s.wall_gap)
            res[mode] = (r.height, len(r.placements), v.ok, jb.log)
    finally:
        family.find = find
    (h1, n1, ok1, log), (h0, n0, ok0, _) = res['families'], res['none']
    check(any(' family ' in line for line in log), 'one family of 40')
    check(ok1 and ok0 and n1 == n0 == 40, 'all placed both ways, referee clean')
    check(h1 < h0 - 5, '%.1f mm as a family, %.1f mm one by one' % (h1, h0))


if __name__ == '__main__':
    only = sys.argv[1:]
    for t in (test_free_offsets, test_lattices, test_around_locked, test_chips, test_double, test_family_find,
              test_family_job):
        if not only or t.__name__ in only:
            t()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
