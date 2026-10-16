"""Checks of the anytime packer (pack.job) and what it relies on.

    python3 tests/test_pack_job.py

The cropped correlation gives the same feasible offsets as the full one; a
part taken out of the bucket leaves it as it was; the free-mode frame does
not depend on how the part happens to be turned; every layout of the job
passes the exact engine and is never worse than greedy's; the keycaps stand
up; a job started from a layout keeps it; the worker process reports,
stops, and leaves its folder to the caller.
"""

import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import greedy, job as J, referee, synthetic as S, voxel, worker  # noqa: E402
from pack.bucket import Bucket  # noqa: E402
from pack.problem import Part, Settings, orientations, principal_frame  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg, flush=True)
    if not ok:
        FAILS.append(msg)


def test_crop():
    print('the correlation cropped to the build height is exact')
    rng = np.random.default_rng(1)
    shapes = [S.box(12, 8, 6), S.sphere(6, 16, 8), S.figurine(0.5), S.keycap()]
    bad = 0
    for trial in range(6):
        b = Bucket((0, 0, 0), (120, 90, 160), 1.0, 2.0, 6, 4)
        # some parts low down, at random
        for _ in range(int(rng.integers(1, 6))):
            m = shapes[int(rng.integers(len(shapes)))]
            g = voxel.voxelize(m.verts, m.tris, np.eye(3), 1.0, 0.0)
            cell = [int(rng.integers(b.M, b.M + 100)), int(rng.integers(b.M, b.M + 70)),
                    int(rng.integers(b.M, b.M + 10 + 15 * trial))]
            b.mark(g.solid, cell)
        for m in shapes:
            pg = voxel.voxelize(m.verts, m.tris, S.rot('x', 30 * trial), 1.0, 2.0, align='min')
            for ph in ((0, 0, 0), (2, 2, 2)):
                full = b.search_shape
                mask, c0 = b.coarse_feasible(pg, ('t', trial, id(m)), ph)
                b.search_shape = lambda kz, C=b.count.shape: C            # (no crop)
                ref, r0 = b.coarse_feasible(pg, ('t-full', trial, id(m)), ph)
                b.search_shape = full
                if not (np.array_equal(c0, r0) and np.array_equal(mask, ref)):
                    bad += 1
    check(bad == 0, 'same feasible offsets with and without the crop (%d differ)' % bad)


def test_remove():
    print('taking a part out undoes putting it in')
    parts = [Part('a', S.figurine(), 3, 'z'), Part('b', S.keycap(), 4, 'free')]
    s = Settings(vlo=(0, 0, 0), vhi=(150, 120, 100), gap=2.0, wall_gap=2.0, max_density=None)
    g = greedy.Greedy(parts, s, threads=1)
    lay = J.Layout(g)
    for i, c in g.order():
        best, _ = g.place_one(lay.bucket, i)
        lay.add(i, c, best[1], best[2])
    b = lay.bucket
    snap = (b.fine.copy(), b.count.copy(), b.profile.copy(), b.volume, b.height)
    q = sorted(lay.recs)[2]
    k, cell = lay.remove(*q)
    check(not np.array_equal(b.fine, snap[0]), 'the part is gone from the grid')
    lay.add(q[0], q[1], k, cell)
    same = (np.array_equal(b.fine, snap[0]) and np.array_equal(b.count, snap[1])
            and np.allclose(b.profile, snap[2]) and abs(b.volume - snap[3]) < 1e-6 and abs(b.height - snap[4]) < 1e-9)
    check(same, 'and back in, the bucket is as it was (cells, blocks, profile, volume, height)')
    for q in sorted(lay.recs):
        lay.remove(*q)
    check(not b.fine.any() and not b.count.any() and abs(b.volume) < 1e-6 and b.height == 0.0,
          'all out: an empty bucket')


def _posed(part, A):
    W = np.asarray(part.mesh.verts, float) @ np.asarray(A, float).T
    return W - W.min(0)


def _same_geometry(a, b, tol=1e-6):
    """Two posed vertex sets are the same (every point has a twin)."""
    if a.shape != b.shape:
        return False
    for s in range(0, len(a), 512):
        d = ((a[s:s + 512, None, :] - b[None, :, :]) ** 2).sum(-1).min(1)
        if d.max() > tol * tol:
            return False
    return True


def test_frame():
    print('free rotation does not depend on how the part is turned to begin with')
    rng = np.random.default_rng(5)
    # (a cylinder has no preferred turn about its axis: turned by its own
    # symmetry it must come out the same, which is what tells one platform's
    # choice of eigenvectors from another's)
    for name, m, step in (('pringle', S.pringle(), None), ('keycap', S.big_keycap(), None),
                          ('bracket', S.bracket(), None), ('cylinder', S.cylinder(5, 30, 24), 15.0)):
        p1 = Part('p', m, 1, 'free')
        ref = [_posed(p1, A) for A in orientations(p1)]
        ok = True
        for _ in range(4):
            ang = float(rng.uniform(0, 360)) if step is None else step * int(rng.integers(1, 24))
            Q = S.rot('z', ang)
            p2 = Part('p', S.transformed(m, Q), 1, 'free')
            got = [_posed(p2, A) for A in orientations(p2)]
            ok &= all(_same_geometry(a, b) for a, b in zip(ref, got))
        check(ok, '%s turned about z: the same 24 orientations, in the same order' % name)
    R = principal_frame(S.big_keycap().verts)
    P = np.abs(R)
    check(np.allclose(P.sum(0), 1) and np.allclose(P.sum(1), 1) and np.allclose(P.max(1), 1),
          'a square keycap keeps its sides on the axes')


def _job(parts, s, **kw):
    jb = J.Job(parts, s, threads=1, **kw)
    try:
        res = jb.run()
    finally:
        jb.close()
    return jb, res


def test_job_small():
    print('jobs on small problems: valid, never worse than greedy, reproducible')
    rng = np.random.default_rng(11)
    shapes = [S.box(30, 20, 10), S.figurine(), S.pringle(20.0, 25.0, 2.0, 8, 24), S.keycap(), S.bracket()]
    for trial in range(4):
        k = rng.choice(len(shapes), 2, replace=False)
        modes = rng.choice(['z', 'free'], 2)
        parts = [Part('p%d' % j, shapes[j], int(rng.integers(3, 9)), str(m)) for j, m in zip(k, modes)]
        s = Settings(vlo=(0, 0, 0), vhi=(140, 110, 120), gap=2.0, wall_gap=2.0, z_step=90.0,
                     max_density=None, time_budget=None)
        g0 = greedy.solve(parts, s, threads=1)
        jb, res = _job(parts, s, max_iterations=12, seed=trial)
        v = referee.check(res.items(parts), s.vlo, s.vhi, s.gap, s.wall_gap)
        n = sum(p.quantity for p in parts)
        check(v.ok and len(res.placements) >= len(g0.placements),
              'trial %d: %d of %d placed, referee clean' % (trial, len(res.placements), n))
        check(res.height <= g0.height + 1e-6 or len(res.placements) > len(g0.placements),
              '  %.1f mm, greedy %.1f mm' % (res.height, g0.height))
        if trial == 0:
            jb2, res2 = _job(parts, s, max_iterations=12, seed=trial)
            same = [(p.part, p.copy, p.orientation, p.cell) for p in res.placements] == \
                   [(p.part, p.copy, p.orientation, p.cell) for p in res2.placements]
            check(same, '  the same seed gives the same layout')


def test_keycaps():
    print('keycaps stand up')
    desc, parts, kw = S.buckets()['keycaps']
    s = Settings(time_budget=None, **kw)
    g0 = greedy.solve(parts, s)
    jb, res = _job(parts, s, max_iterations=0)
    check(len(res.placements) == 81, 'all 81 placed')
    check(res.height < g0.height - 8 and res.height <= jb._lb + 1.0,
          '%.1f mm (greedy %.1f, the density limit allows no less than %.1f)' % (res.height, g0.height, jb._lb))
    v = referee.check(res.items(parts), s.vlo, s.vhi, s.gap, s.wall_gap)
    check(v.ok, 'referee clean')
    return parts, s, res


def test_start(parts, s, res):
    print('a job started from a layout keeps it')
    start = {(p.part, p.copy): p.matrix for p in res.placements}
    jb, r2 = _job(parts, s, start=start, max_iterations=3)
    check(jb.log and 'kept where they are' in jb.log[0] and ' 81 parts kept' in jb.log[0], jb.log[0] if jb.log else 'no log')
    check(r2.height <= res.height + 1e-6 and len(r2.placements) == 81, '%.1f mm, no worse' % r2.height)
    moved = dict(start)
    M = moved[(0, 5)].copy()
    M[:3, 3] = moved[(0, 6)][:3, 3]                     # (on top of another: must be placed again)
    moved[(0, 5)] = M
    jb3 = J.Job(parts, s, threads=1, start=moved, max_iterations=0)
    lay = jb3._fresh()
    jb3.cur = lay
    left = jb3._load(lay, moved)
    jb3.close()
    check(len(left) == 1, 'a part that overlaps another is placed again (%d)' % len(left))


def test_cancel_and_locked():
    print('stop, and locked parts')
    parts = [Part('cube', S.box(20, 20, 20), 12, 'z')]
    big = S.box(120, 80, 30)
    L = np.eye(4)
    L[:3, :3] = S.rot('z', 20)
    L[:3, 3] = (90, 60, 15)
    s = Settings(vlo=(0, 0, 0), vhi=(180, 120, 100), gap=2.0, wall_gap=0.0, max_density=None, time_budget=None)
    jb = J.Job(parts, s, [(big, L)], threads=1, max_iterations=6)
    res = jb.run()
    jb.close()
    v = referee.check(res.items(parts) + [('locked', big, L)], s.vlo, s.vhi, s.gap, 0.0)
    check(v.ok and len(res.placements) == 12, '12 cubes around a locked slab, referee clean')
    jb = J.Job(parts, s, threads=1)
    jb.step(0.0)                    # (one part)
    jb.cancel = True
    jb.step(0.0)
    res = jb.result()
    jb.close()
    check(jb.done and 0 < len(res.placements) < 12 and all(u[2] == 'stopped' for u in res.unplaced),
          'cancelled during the first layout: %d placed, the rest "stopped"' % len(res.placements))


def test_worker():
    print('the worker process')
    import subprocess
    parts = [Part('ball', S.sphere(10, 16, 8), 6, 'fixed'), Part('chip', S.pringle(20.0, 25.0, 2.0, 8, 24), 6, 'free')]
    s = Settings(vlo=(0, 0, 0), vhi=(120, 100, 80), gap=2.0, wall_gap=2.0, max_density=None, time_budget=3.0)
    d = tempfile.mkdtemp(prefix='bb_test_job_')
    try:
        worker.write_job(d, parts, s)
        addon = os.path.join(HERE, '..', 'bucket_builder')
        code = "import sys; sys.path.insert(0, %r); from pack.worker import main; sys.exit(main(%r))" % (addon, d)
        t = time.perf_counter()
        p = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, timeout=300)
        r = worker.read_progress(d)
        check(p.returncode == 0 and r and r['finished'] and not r['error'], 'it finishes (%.1f s)' % (time.perf_counter() - t))
        check(len(r['placements']) == 12 and r['phase'] == 'improving' and r['version'] >= 1,
              'all 12 placed, phase %s, layout version %s' % (r['phase'], r['version']))
        items = [(parts[i].name, parts[i].mesh, np.array(m).reshape(4, 4)) for i, c, k, m in r['placements']]
        v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
        check(v.ok, 'the reported layout passes the referee')
        # stopped at once
        os.remove(os.path.join(d, 'progress.json'))
        s.time_budget = 600.0
        worker.write_job(d, parts * 1, s)
        open(os.path.join(d, 'stop'), 'w').close()
        t = time.perf_counter()
        p = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, timeout=300)
        r = worker.read_progress(d)
        check(r and r['finished'] and time.perf_counter() - t < 30, 'a stop file ends it at once (%.1f s)'
              % (time.perf_counter() - t))
        with open(os.path.join(d, 'job.json')) as f:
            check('ball' not in f.read(), 'the job file holds no names')
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    test_crop()
    test_remove()
    test_frame()
    test_job_small()
    kc = test_keycaps()
    test_start(*kc)
    test_cancel_and_locked()
    test_worker()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
