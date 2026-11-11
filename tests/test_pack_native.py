# SPDX-License-Identifier: GPL-3.0-or-later
"""The compiled voxel steps (pack/native) against their NumPy originals:
identical grids, cell for cell, and how much faster.

    python3 tests/test_pack_native.py        (needs tools/build_native.sh first)
"""

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import native, synthetic as S, voxel  # noqa: E402
from pack.problem import orientations, Part  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def both(fn):
    """fn() with the compiled library, then without; (with, without, seconds each)."""
    lib = native.lib
    t0 = time.perf_counter()
    a = fn()
    t1 = time.perf_counter()
    native.lib = None
    try:
        b = fn()
    finally:
        native.lib = lib
    return a, b, t1 - t0, time.perf_counter() - t1


def shapes():
    return [('box', S.box(30, 20, 10)), ('figurine', S.figurine()), ('keycap', S.keycap()),
            ('bracket', S.bracket()), ('pringle', S.pringle(20.0, 25.0, 2.0, 8, 24))]


def test_grids():
    print('the same cells as NumPy')
    rng = np.random.default_rng(3)
    tn = tp = 0.0
    for name, m in shapes():
        part = Part(name, m, 1, 'free')
        turns = list(orientations(part))[:6]
        for _ in range(3):                       # arbitrary turns too, not just quarter turns
            Q, _r = np.linalg.qr(rng.normal(size=(3, 3)))
            turns.append(Q * np.sign(np.linalg.det(Q)))
        for h in (1.0, 0.5):
            for A in turns:
                (g1, o1), (g2, o2), a, b = both(lambda: voxel.surface_grid(m.verts, m.tris, A, h, 2))
                same = g1.shape == g2.shape and (g1 == g2).all() and np.allclose(o1, o2)
                tn += a; tp += b
                if not same:
                    check(False, '%s at h=%g: surface differs' % (name, h))
                    return
                s1, s2, a, b = both(lambda: voxel.outside(g1))
                tn += a; tp += b
                if not (s1 == s2).all():
                    check(False, '%s at h=%g: outside differs' % (name, h))
                    return
    check(True, 'surface and flood fill of 5 parts, 9 turns each, two cell sizes (%.2f s against %.2f s)' % (tn, tp))


def test_soup():
    print('a part cut once, turned many times')
    for name, m in shapes():
        part = Part(name, m, 1, 'free')
        B = np.asarray(part.base, float)
        s1, s2, a, b = both(lambda: voxel.Soup(m.verts, m.tris, B, 1.0))
        P1 = s1.pieces[np.lexsort(s1.pieces.reshape(-1, 9).T)]
        P2 = s2.pieces[np.lexsort(s2.pieces.reshape(-1, 9).T)]
        if P1.shape != P2.shape or not (P1 == P2).all():
            check(False, '%s: pieces differ' % name)
            return
        for A in orientations(part):
            R = np.asarray(A, float) @ np.linalg.inv(B)
            (g1, _), (g2, _), _a, _b = both(lambda: voxel.surface_grid_soup(s1, R, 1.0, 2))
            if not (g1 == g2).all():
                check(False, '%s: turned soup differs' % name)
                return
    check(True, 'the same pieces, and the same cells in all 24 turns')


if __name__ == '__main__':
    if native.lib is None:
        print('no compiled library: run tools/build_native.sh')
        sys.exit(0)
    test_grids()
    test_soup()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
