"""Checks greedy placement: every layout passes the exact engine, cavities
are used when they must be, the density limit, the walls, the rotation
modes, and that the referee does catch a bad layout.

    python3 tests/test_pack_greedy.py
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))
sys.path.insert(0, os.path.join(HERE, '..', 'benchmarks'))

import synthetic as S  # noqa: E402
from pack import greedy, metrics, referee  # noqa: E402
from pack.problem import Part, Settings  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def solve_and_referee(parts, s, label):
    res = greedy.solve(parts, s)
    items = res.items(parts)
    v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
    check(v.ok, '%s: %d placed, %d not, referee clean (%s)'
          % (label, len(res.placements), len(res.unplaced), 'ok' if v.ok else v))
    return res, items


def test_random_layouts():
    print('random small problems pass the exact engine')
    rng = np.random.default_rng(3)
    shapes = [S.box(30, 20, 10), S.sphere(12, 24, 12), S.figurine(), S.pringle(20.0, 25.0, 2.0, 8, 24),
              S.keycap(), S.ring(), S.bracket()]
    for trial in range(5):
        k = rng.choice(len(shapes), 3, replace=False)
        modes = rng.choice(['fixed', 'z', 'free'], 3)
        parts = [Part('p%d' % j, shapes[j], int(rng.integers(2, 7)), str(m)) for j, m in zip(k, modes)]
        gap = float(rng.choice([0.5, 1.0, 3.0, 5.0]))
        h = float(rng.choice([1.0, 1.5]))
        s = Settings(vlo=(0, 0, 0), vhi=(160, 120, 120), gap=gap, wall_gap=float(rng.choice([0.0, 3.0])),
                     h=h, z_step=90.0, candidates=4)
        solve_and_referee(parts, s, 'trial %d (gap %.1f, h %.1f, %s)' % (trial, gap, h, list(modes)))


def test_cavity():
    print('a cavity is used when there is no other room')
    sock = S.cup(30.0, 25.0, 100.0, 4.0, 48, 'socket')
    ins = S.insole()
    ins = S.transformed(ins, np.diag([0.6, 0.6, 1.0]))     # 54 x 18 x 4 mm
    parts = [Part('socket', sock, 1, 'fixed'), Part('insole', ins, 1, 'free')]
    s = Settings(vlo=(0, 0, 0), vhi=(66, 66, 110), gap=1.0, wall_gap=0.0, h=1.0, candidates=8,
                 max_density=None)       # (the socket alone is 30 % of this small box)
    res, items = solve_and_referee(parts, s, 'socket in a box just its size')
    ok = len(res.placements) == 2
    if ok:
        c = res.placements[1].matrix[:3, 3] - res.placements[0].matrix[:3, 3]
        ok = np.hypot(c[0], c[1]) < 25.0
    check(ok, 'the insole went inside the socket')


def test_density_limit():
    print('the 3D density limit')
    parts = [Part('cube', S.box(20, 20, 20), 40, 'fixed')]
    s = Settings(vlo=(0, 0, 0), vhi=(200, 200, 200), gap=2.0, wall_gap=0.0, max_density=0.05, candidates=4)
    res, items = solve_and_referee(parts, s, '40 cubes at 5 %')
    rep = metrics.measure(items, s.vlo, s.vhi)
    need = 40 * 8000 / (200 * 200 * 0.05)
    check(rep.density <= 0.05 + 1e-9 and rep.height >= need - 1e-6,
          'density %.2f %% <= 5 %%, height %.1f >= %.1f mm' % (100 * rep.density, rep.height, need))
    s.max_density = None
    res2 = greedy.solve(parts, s)
    check(res2.height < rep.height, 'without the limit the build is lower (%.1f mm)' % res2.height)


def test_walls_and_room():
    print('walls, and parts that do not fit')
    parts = [Part('big', S.box(150, 50, 50), 1, 'fixed'), Part('ok', S.box(20, 20, 20), 3, 'fixed')]
    s = Settings(vlo=(0, 0, 0), vhi=(140, 100, 100), gap=2.0, wall_gap=5.0, candidates=4)
    res, items = solve_and_referee(parts, s, 'one part too long for the volume')
    check([u[0] for u in res.unplaced] == [0] and len(res.placements) == 3, 'only the long one is left out: %s' % res.unplaced)
    check(res.unplaced[0][2] == 'too large for the volume', 'and it says why')
    xs = np.array([[m.verts @ M[:3, :3].T + M[:3, 3]] for _, m, M in items]).reshape(-1, 3)
    check(xs[:, 0].min() >= 5.0 - 1e-9 and xs[:, 1].min() >= 5.0 - 1e-9 and xs[:, 0].max() <= 135 + 1e-9,
          'every vertex is the wall gap from the side walls')


def test_modes():
    print('rotation modes')
    base = S.rot('x', 30) * 1.0
    parts = [Part('fixed', S.bracket(), 2, 'fixed', base=base), Part('z', S.bracket(), 3, 'z'),
             Part('free', S.bracket(), 3, 'free')]
    s = Settings(vlo=(0, 0, 0), vhi=(200, 200, 150), gap=2.0, wall_gap=0.0, candidates=4)
    res, items = solve_and_referee(parts, s, 'brackets in three modes')
    fx = [p for p in res.placements if p.part == 0]
    zz = [p for p in res.placements if p.part == 1]
    check(all(np.allclose(p.matrix[:3, :3], base) for p in fx), 'fixed keeps its rotation')
    check(all(abs(p.matrix[2, 2] - 1) < 1e-12 for p in zz), 'z turns about z only')
    check(all(abs(np.linalg.det(p.matrix[:3, :3]) - 1) < 1e-9 for p in res.placements), 'no mirroring')


def test_referee_catches():
    print('the referee is not blind')
    m = S.box(20, 20, 20)
    a = np.eye(4)
    a[:3, 3] = (50, 50, 20)
    b = a.copy()
    b[0, 3] += 21.0                 # 1 mm apart
    v = referee.check([('a', m, a), ('b', m, b)], (0, 0, 0), (200, 200, 200), 2.0)
    check(v.too_close == 1 and abs(v.closest - 1.0) < 1e-6, 'a pair 1 mm apart with a 2 mm gap: %s' % (v,))
    b[0, 3] -= 2.0
    v = referee.check([('a', m, a), ('b', m, b)], (0, 0, 0), (200, 200, 200), 2.0)
    check(v.collisions == 1, 'overlapping parts collide')
    b[:3, 3] = (195, 50, 20)
    v = referee.check([('b', m, b)], (0, 0, 0), (200, 200, 200), 2.0, 3.0)
    check(v.outside == 1, 'a part through the wall is outside')


if __name__ == '__main__':
    test_referee_catches()
    test_random_layouts()
    test_cavity()
    test_density_limit()
    test_walls_and_room()
    test_modes()
    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        sys.exit(1)
    print('ALL OK')
