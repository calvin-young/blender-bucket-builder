# SPDX-License-Identifier: GPL-3.0-or-later
"""Spread (pack/spread.py): parts take the room they have, within the packed height.

    python3 tests/test_pack_spread.py
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import referee, spread, synthetic as S  # noqa: E402

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
    print('ten spheres in a row along one wall spread over the floor')
    ball = S.sphere(15.0, 24, 12)
    vlo, vhi = np.zeros(3), np.array([380.0, 284.0, 380.0])
    items = [(ball, at(16 + 34 * i, 16, 15.001)) for i in range(10)]          # 4 mm apart, on the floor
    Ms, reached, steps = spread.spread(items, vlo, vhi, 3.0, aim=10.0, seconds=90)
    out = list(zip([ball] * 10, Ms))
    v = referee.check([('b', m, M) for m, M in out], vlo, vhi, 0.99 * reached, 0.0)   # (Jiggle's gaps are to about 1 %)
    check(reached >= 30.0 and v.ok, 'every pair at least %.1f mm apart (was 4), referee clean (%s)' % (reached, v))
    check(abs(spread.build_top(out) - spread.build_top(items)) < 1e-6, 'the build is no higher')
    ys = [float(M[1, 3]) for M in Ms]
    check(max(ys) - min(ys) > 100.0, 'they use the depth of the bucket, not one row (y spread %.0f mm)' % (max(ys) - min(ys)))
    print('a layout with no room changes nothing')
    tight = [(ball, at(15.002, 15.002, 15.001)), (ball, at(48.004, 15.002, 15.001))]
    Ms, reached, steps = spread.spread(tight, vlo, np.array([81.007, 30.004, 380.0]), 3.0, aim=10.0, seconds=20)
    check(reached >= 3.0 and np.allclose(Ms[0], tight[0][1], atol=1.0), 'the parts stay where they are (gap %.1f)' % reached)


if __name__ == '__main__':
    main()
    print()
    if FAILS:
        print('%d check(s) failed' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
