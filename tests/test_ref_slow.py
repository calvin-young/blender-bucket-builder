# SPDX-License-Identifier: GPL-3.0-or-later
"""The independent check (tests/ref_slow.py) on cases with a known answer,
and against the referee on layouts the packer makes.

    python3 tests/test_ref_slow.py
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

import ref_slow  # noqa: E402
from pack import job as J, synthetic as S  # noqa: E402
from pack.problem import Settings  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def at(x, y, z):
    M = np.eye(4)
    M[:3, 3] = (x, y, z)
    return M


def known():
    print('known answers')
    box, small, ball = S.box(20, 20, 20), S.box(4, 4, 4), S.sphere(10)
    vol = (0, 0, 0), (200, 150, 100)
    v = ref_slow.check([('a', box, at(50, 50, 20)), ('b', box, at(70.5, 50, 20))], *vol, 1.0)
    check(len(v.close) == 1 and v.close[0][2] >= 0.5, 'boxes 0.5 mm apart, gap 1: too close (samples %.3f)' % v.close[0][2])
    v = ref_slow.check([('a', box, at(50, 50, 20)), ('b', box, at(71.5, 50, 20))], *vol, 1.0)
    check(v.ok, 'boxes 1.5 mm apart, gap 1: fine')
    v = ref_slow.check([('a', box, at(50, 50, 20)), ('b', box, at(65, 52, 23))], *vol, 1.0)
    check(len(v.close) == 1 and v.close[0][2] < 0.5, 'boxes cutting through each other: found (samples %.3f)' % v.close[0][2])
    v = ref_slow.check([('a', box, at(50, 50, 20)), ('b', small, at(50, 50, 20))], *vol, 1.0)
    check(v.enclosed == [(1, 0)], 'a small box wholly inside a big one: found')
    v = ref_slow.check([('a', ball, at(50, 50, 20)), ('b', ball, at(70.8, 50, 20))], *vol, 1.0)
    check(len(v.close) == 1, 'spheres 0.8 mm apart, gap 1: too close (samples %.3f)' % v.close[0][2])
    v = ref_slow.check([('a', box, at(195, 50, 20))], *vol, 1.0)
    check(v.outside == [0], 'a box past the wall: outside')


def packed():
    print('the referee and the slow check agree on packed layouts')
    every = {**S.buckets(), **S.library()}
    for name in ('sockets', 'figurines', 'keycap-sizes'):
        _desc, parts, extra = every[name]
        gap = float(extra.get('gap', 2.0))
        s = Settings(gap=gap, wall_gap=5.0, time_budget=8.0, seed=0)
        jb = J.Job(parts, s, workers=1, threads=1)
        try:
            res = jb.run()
        finally:
            jb.close()
        items = res.items(parts)
        problems, slow, ref = ref_slow.compare(items, s.vlo, s.vhi, gap, 5.0, spacing=0.5)
        check(not problems, '%s: %d parts, %s, the referee faults %d pairs%s' % (
            name, len(items), slow, len(ref), '' if not problems else ': ' + '; '.join(problems)))
    # and a layout made bad on purpose: both must see it
    _desc, parts, extra = every['sockets']
    s = Settings(gap=3.0, wall_gap=5.0, time_budget=5.0, seed=0)
    jb = J.Job(parts, s, workers=1, threads=1)
    try:
        items = jb.run().items(parts)
    finally:
        jb.close()
    name, mesh, M = items[1]
    M = np.array(M, float)
    M[:3, 3] = np.asarray(items[0][2], float)[:3, 3] + (1.0, 0.5, 0.0)
    items[1] = (name, mesh, M)
    problems, slow, ref = ref_slow.compare(items, s.vlo, s.vhi, 3.0, 5.0, spacing=0.5)
    check(not problems and (0, 1) in slow.pairs() and (0, 1) in ref,
          'a part dropped onto another: both find the pair')


if __name__ == '__main__':
    known()
    packed()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
