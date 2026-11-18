# SPDX-License-Identifier: GPL-3.0-or-later
"""Packing with two clearances (pack/soft.py): the aim where it fits, the
least where the aim would leave parts out.

    python3 tests/test_pack_soft.py
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import referee, soft, synthetic as S  # noqa: E402
from pack.problem import Part, Settings  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def gaps(items, s, upto):
    """Pairs closer than `upto` (by the referee's world), as a sorted list of distances."""
    w = referee._world(items, s.vlo, s.vhi, upto, s.wall_gap, 600)
    return sorted(float(pr.dist) for pr in w.viol.values())


def main():
    print('room for every part at the aim: packed at the aim')
    box = S.box(19, 19, 19)
    s = Settings(vlo=np.zeros(3), vhi=np.array([200.0, 200.0, 40.0]), gap=1.0, gap_aim=3.0, wall_gap=0.0,
                 max_density=None, time_budget=10.0, seed=0)
    parts = [Part('cube', box, 16, 'fixed')]
    res, squeezed, log = soft.pack(parts, s, workers=1)
    items = res.items(parts)
    check(len(res.placements) == 16 and not squeezed, '16 of 16 placed, none squeezed (%s)' % '; '.join(log))
    check(referee.check(items, s.vlo, s.vhi, 3.0, 0.0).ok, 'every pair at 3 mm or more')

    print('five across fit at 1 mm, four at 3 mm: the least where the aim leaves parts out')
    s = Settings(vlo=np.zeros(3), vhi=np.array([103.0, 103.0, 19.5]), gap=1.0, gap_aim=3.0, wall_gap=0.0,
                 max_density=None, time_budget=30.0, seed=0)
    parts = [Part('cube', box, 25, 'fixed')]
    res, squeezed, log = soft.pack(parts, s, workers=1)
    items = res.items(parts)
    v1 = referee.check(items, s.vlo, s.vhi, 1.0, 0.0)
    g = gaps(items, s, 3.0)
    check(len(res.placements) >= 24, '%d of 25 placed (at 3 mm alone: 16) (%s)' % (len(res.placements), '; '.join(log)))
    check(v1.ok, 'every pair at least 1 mm apart')
    # against packing at 1 mm alone, in the same time: as many parts, fewer pairs short of 3 mm
    from pack import job as J
    from dataclasses import replace
    jb = J.Job(parts, replace(s, gap_aim=None), workers=1)
    try:
        plain = jb.run()
    finally:
        jb.close()
    gp = gaps(plain.items(parts), s, 3.0)
    check(len(res.placements) >= len(plain.placements) and len(g) < len(gp),
          'packing at 1 mm alone: %d placed, %d pairs under 3 mm; with the aim: %d placed, %d pairs under 3 mm' % (
              len(plain.placements), len(gp), len(res.placements), len(g)))

if __name__ == '__main__':
    main()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
