# SPDX-License-Identifier: GPL-3.0-or-later
"""Jiggle (pack/jiggle.py): small moves mend a layout, nothing far moves.

    python3 tests/test_pack_jiggle.py
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import jiggle, referee, synthetic as S  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def at(x, y, z):
    M = np.eye(4)
    M[:3, 3] = (x, y, z)
    return M


def main():
    print('small moves mend a layout')
    box = S.box(20, 20, 20)
    vlo, vhi, gap = (0, 0, 0), (200, 150, 100), 3.0
    # a row of boxes 3 mm apart, one pushed 2 mm into its neighbor, one 1 mm past a wall
    items = [(box, at(15 + 23 * i, 30, 10)) for i in range(6)]
    items[2][1][0, 3] -= 2.0
    items.append((box, at(14.0, 70, 10)))
    v0 = referee.check([('p', m, M) for m, M in items], vlo, vhi, gap, 5.0)
    check(not v0.ok, 'the start breaks rules (%d too close, %d at walls)' % (v0.too_close, v0.near_wall))
    Ms, jg = jiggle.jiggle(items, vlo, vhi, gap, 5.0, seconds=30)
    v = referee.check([('p', m, M) for (m, _), M in zip(items, Ms)], vlo, vhi, gap, 5.0)
    moved = [float(np.linalg.norm(M[:3, 3] - M0[:3, 3])) for (_, M0), M in zip(items, Ms)]
    check(v.ok, 'and the result keeps every rule')
    check(max(moved) < 10.0, 'with small moves (largest %.2f mm, half a part)' % max(moved))
    # a part far outside is left alone: it is for packing again
    far = [(box, at(50, 50, 10)), (box, at(500, 50, 10))]
    Ms, jg = jiggle.jiggle(far, vlo, vhi, gap, 5.0, seconds=5)
    check(np.allclose(Ms[1], far[1][1]) and jg.moves == 0, 'a part far outside is not nudged')
    # locked parts stay
    lock = [(box, at(50, 50, 10))]
    near = [(box, at(70, 50, 10))]                          # 0 mm apart from the locked one
    Ms, jg = jiggle.jiggle(near, vlo, vhi, gap, 5.0, locked=lock, seconds=10)
    v = referee.check([('p', box, Ms[0]), ('l', box, lock[0][1])], vlo, vhi, gap, 5.0)
    check(v.ok and np.allclose(lock[0][1], at(50, 50, 10)), 'only the free part moves, away from a locked one')


def gaps_x(boxes, size=20.0):
    """Gaps along x between boxes of one size in a row, left to right."""
    xs = sorted(float(M[0, 3]) for M in boxes)
    return [b - a - size for a, b in zip(xs, xs[1:])]


def soft():
    print('two clearances: at least 1 mm, aim for 3')
    box = S.box(20, 20, 20)
    vlo, vhi = (0, 0, 0), (300, 150, 100)
    least, aim = 1.0, 3.0
    # room to spare: gaps of 0.5 (under the least) and 1.5 (short of the aim) both open to the aim
    items = [(box, at(50, 50, 10)), (box, at(70.5, 50, 10)), (box, at(92.0, 50, 10))]
    Ms, jg = jiggle.jiggle(items, vlo, vhi, least, 5.0, seconds=30, aim=aim)
    g = gaps_x(Ms)
    v = referee.check([('p', box, M) for M in Ms], vlo, vhi, aim, 5.0)
    moved = max(float(np.linalg.norm(M[:3, 3] - M0[:3, 3])) for (_, M0), M in zip(items, Ms))
    check(v.ok, 'with room, every pair reaches the aim (gaps %s)' % ', '.join('%.2f' % x for x in g))
    check(moved < 4.0, 'by small moves (largest %.2f mm)' % moved)
    # no room for the aim: three boxes between two locked ones, 8 mm of slack for four gaps
    lock = [(box, at(50, 50, 10)), (box, at(138, 50, 10))]
    free = [(box, at(70.2, 50, 10)), (box, at(94.0, 50, 10)), (box, at(115.0, 50, 10))]      # gaps 0.2, 3.8, 1.0, 3.0
    Ms, jg = jiggle.jiggle(free, vlo, vhi, least, 5.0, locked=lock, seconds=30, aim=aim)
    g = gaps_x([M for _, M in lock] + Ms)
    v = referee.check([('l', m, M) for m, M in lock] + [('p', box, M) for M in Ms], vlo, vhi, least, 5.0)
    check(v.ok, 'without room, every pair has at least the least (gaps %s)' % ', '.join('%.2f' % x for x in g))
    check(min(g) > 1.8, 'and the slack is shared: the tightest gap is %.2f mm of a possible 2.00' % min(g))
    col, under, short, low = jg.gaps()
    check((col, under) == (0, 0) and short == 4, 'the jiggler counts 0 collisions, 0 under the least, 4 short of the aim')
    # the hard rule is never traded for the aim: a pair at 1.2 mm between locked neighbors stays legal
    lock = [(box, at(50, 50, 10)), (box, at(92.4, 50, 10))]
    free = [(box, at(71.2, 50, 10))]                         # gaps 1.2 and 1.2: nowhere better to go
    Ms, jg = jiggle.jiggle(free, vlo, vhi, least, 5.0, locked=lock, seconds=10, aim=aim)
    g = gaps_x([M for _, M in lock] + Ms)
    check(min(g) >= 1.0 - 1e-6 and abs(g[0] - g[1]) < 0.1, 'a part with no better place stays put (gaps %.2f, %.2f)' % tuple(g))
    # reach: no part ends farther from its start than allowed
    items = [(box, at(50, 50, 10)), (box, at(70.5, 50, 10))]
    Ms, jg = jiggle.jiggle(items, vlo, vhi, least, 5.0, seconds=10, aim=aim, reach=0.8)
    moved = max(float(np.linalg.norm(M[:3, 3] - M0[:3, 3])) for (_, M0), M in zip(items, Ms))
    g = gaps_x(Ms)
    check(moved <= 0.8 + 1e-9 and g[0] >= 1.0, 'reach 0.8 mm: parts move %.2f mm at most, the gap is %.2f' % (moved, g[0]))
    # one clearance (no aim) behaves as before: it stops at the gap
    items = [(box, at(50, 50, 10)), (box, at(70.5, 50, 10))]
    Ms, jg = jiggle.jiggle(items, vlo, vhi, least, 5.0, seconds=10)
    g = gaps_x(Ms)
    check(1.0 <= g[0] < aim, 'without an aim the pair is moved past the gap and not on toward 3 mm (%.2f mm)' % g[0])


if __name__ == '__main__':
    main()
    soft()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
