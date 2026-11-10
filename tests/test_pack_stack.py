# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks of nesting stacks (pack.stack) and their use in the job.

    python3 tests/test_pack_stack.py

The height maps agree with the exact engine (spacings are upper bounds, close
to the exact ones), an order puts alike parts together, and chips that are all
different meshes come out as low stacks that the exact engine passes, while
parts that don't nest are left to the usual search.
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import job as J, referee, stack, synthetic as S  # noqa: E402
from pack.problem import Part, Settings  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg, flush=True)
    if not ok:
        FAILS.append(msg)


def chips(n=30, seed=1, r=(40.0, 43.0), c=(90.0, 110.0)):
    rng = np.random.default_rng(seed)
    return [S.pringle(radius=float(rng.uniform(*r)), curve=float(rng.uniform(*c)), thick=float(rng.uniform(1.8, 2.2)),
                      rings=10, segs=32, oval=0.8) for _ in range(n)]


def settings(**kw):
    return Settings(vlo=np.zeros(3), vhi=np.array([380.0, 284.0, 380.0]), gap=3.0, wall_gap=5.0, max_density=None, **kw)


def test_spacing_bounds():
    ms = chips(6)
    poses = [stack.Pose(m, np.eye(3)) for m in ms]
    n = 90
    maps = [stack.height_maps(p, n) for p in poses]
    Sp = stack.spacings([t for t, _ in maps], [b for _, b in maps], 3.0)
    for a, b in ((0, 1), (2, 3), (4, 5)):
        ex = stack.exact_spacing(poses[a], poses[b], float(Sp[a, b]), 3.0)
        check(ex <= Sp[a, b] + 1e-6, f'height map spacing {Sp[a, b]:.2f} is an upper bound of the exact {ex:.2f}')
        check(Sp[a, b] - ex < 4.0, 'and not far above it')


def test_order():
    # two kinds of bend, shuffled: an order should mostly keep each kind together
    ms = chips(10, c=(70.0, 70.0)) + chips(10, c=(140.0, 140.0), seed=2)
    idx = np.random.default_rng(0).permutation(20)
    poses = [stack.Pose(ms[i], np.eye(3)) for i in idx]
    maps = [stack.height_maps(p, 90) for p in poses]
    Sp = stack.spacings([t for t, _ in maps], [b for _, b in maps], 3.0)
    o = stack.chain(Sp)
    kinds = [int(idx[i] >= 10) for i in o]
    changes = sum(a != b for a, b in zip(kinds, kinds[1:]))
    check(sorted(o) == list(range(20)), 'the order holds every part once')
    check(changes <= 3, f'alike parts end up together ({changes} changes of kind)')


def test_job_chips():
    ms = chips(40)
    parts = [Part('chip-%d' % i, m, 1, 'free') for i, m in enumerate(ms)]
    s = settings()
    j = J.Job(parts, s, time_budget=20)
    j._try_stacks()
    check(getattr(j, '_stacks', None) is not None, 'forty different chips stack')
    res = j.result()
    check(len(res.placements) == 40 and not res.unplaced, 'all placed')
    check(res.height < 90.0, f'lying in one layer, {res.height:.1f} mm')
    v = referee.check(res.items(parts), s.vlo, s.vhi, s.gap, s.wall_gap, timeout=120)
    check(v.collisions == 0 and v.too_close == 0 and v.outside == 0 and v.near_wall == 0, f'the exact engine agrees: {v}')


def test_density_limit():
    ms = chips(40)
    parts = [Part('chip-%d' % i, m, 1, 'free') for i, m in enumerate(ms)]
    s = Settings(vlo=np.zeros(3), vhi=np.array([380.0, 284.0, 380.0]), gap=3.0, wall_gap=5.0, max_density=0.01)
    j = J.Job(parts, s, time_budget=20)
    j._try_stacks()
    check(getattr(j, '_stacks', None) is None, 'a layout over the density limit is not used')


def test_not_nesting():
    parts = [Part('box', S.box(30, 30, 30) if hasattr(S, 'box') else S.prism([(0, 0), (30, 0), (30, 30), (0, 30)], 30, 'box'), 30, 'free')]
    j = J.Job(parts, settings(), time_budget=5)
    j._try_stacks()
    check(getattr(j, '_stacks', None) is None, 'solid blocks are left to the usual search')


def test_fixed_two_sizes():
    """Parts that may not turn stack as they are, each size in stacks of its
    own beside the other's, every pair at its exact spacing."""
    sys.path.insert(0, os.path.join(HERE, '..', 'benchmarks'))
    from hand_builds import trough
    small, big = trough(30.0, 2.0, 120.0, 150.0, name='small'), trough(40.0, 2.0, 120.0, 150.0, name='big')
    parts = [Part('small', small, 12, 'fixed'), Part('big', big, 12, 'fixed')]
    s = Settings(vlo=np.zeros(3), vhi=np.array([200.0, 284.0, 120.0]), gap=2.0, wall_gap=2.0, max_density=None)
    j = J.Job(parts, s, workers=1)
    res = j.run()
    j.close()
    check(getattr(j, '_stacks', None) is not None, 'fixed troughs stack')
    check(len(res.placements) == 24 and not res.unplaced, f'all placed ({len(res.placements)})')
    check(all(np.allclose(p.matrix[:3, :3], np.eye(3)) for p in res.placements), 'none of them turned')
    items = res.items(parts)
    v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
    check(v.ok, f'the exact engine agrees: {v}')

if __name__ == '__main__':
    test_spacing_bounds()
    test_order()
    test_job_chips()
    test_density_limit()
    test_not_nesting()
    test_fixed_two_sizes()
    print('FAILED' if FAILS else 'all ok')
    sys.exit(1 if FAILS else 0)
