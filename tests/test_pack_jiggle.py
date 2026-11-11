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


if __name__ == '__main__':
    main()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
