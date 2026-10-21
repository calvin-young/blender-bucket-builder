# SPDX-License-Identifier: GPL-3.0-or-later
"""The quality / efficiency dial, the height curve, faces facing up, and
the extra time near the top of the volume (docs/PRINT_QUALITY.md: PQ-07,
PK-01, the dial).

    python3 tests/test_pack_quality.py
"""

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import job as J, quality, referee, score, synthetic as S  # noqa: E402
from pack.problem import Part, Settings, dial_settings  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def test_height_curve():
    print('height curve')
    s = Settings()
    check(all(score.height_cost(h, s) == h for h in (0.0, 57.0, 379.0)), 'linear by default')
    s = Settings(**dial_settings(1.0))
    H = float(s.vhi[2])
    hs = np.linspace(0, H + 20, 801)
    c = np.array([score.height_cost(h, s) for h in hs])
    check(np.all(np.diff(c) > 0), 'rises everywhere')
    far = score.height_cost(164.0, s) - score.height_cost(157.0, s)
    near = score.height_cost(H - 3.0, s) - score.height_cost(H - 10.0, s)
    check(far < 2.0 and near > 15.0, '7 mm far below the top cost %.1f, near the top %.1f' % (far, near))
    d0, d5, d1 = dial_settings(0.0), dial_settings(0.5), dial_settings(1.0)
    check(not d0['flat_rules'] and not d0['spread_z'] and d0['height_far'] == 1.0 and d0['spread_scale'] == 0.0,
          'density end: the soft rules off, height counts everywhere')
    check(abs(d5['flat_pair_weight'] - 0.001) < 1e-12 and d5['flat_top_weight'] == 0.0 and d5['spread_scale'] == 1.0,
          'middle: the weights of 1.4.2, faces facing up not counted')
    check(d1['flat_top_weight'] > 0 and d1['height_far'] < 0.3, 'quality end: faces facing up counted')


def roof(w=80.0, h=20.0):
    """A ridge: an 80 x 80 mm flat base, two slopes."""
    return S.prism([(0, 0), (w, 0), (w / 2, h)], w, 'roof')


def test_top_faces():
    print('faces facing up')
    m = roof()
    up = quality.flat_faces(m.verts, m.tris, np.eye(3))
    flip = np.diag([1.0, -1.0, -1.0])
    A = np.eye(3)                         # (the polygon is in the x-z plane: the base faces down)
    down = quality.flat_faces(m.verts, m.tris, A)
    turned = quality.flat_faces(m.verts, m.tris, flip @ A)
    n_down, a_down = quality.top_area(down)
    n_up, a_up = quality.top_area(turned)
    check(n_down == 0 and n_up == 1 and abs(a_up - 6400.0) < 1.0,
          'base down: nothing faces up; base up: one face of %.0f mm2' % a_up)
    check(quality.top_area(None) == (0, 0.0) and len(up) >= 1, 'no faces, no area')
    parts = [Part('roof', m, 6, 'free')]
    res = {}
    for q in (0.0, 1.0):
        s = Settings(vlo=(0, 0, 0), vhi=(200, 200, 200), gap=2.0, wall_gap=2.0, max_density=None,
                     time_budget=6.0, **dial_settings(q))
        jb = J.Job(parts, s, threads=1, workers=1)
        r = jb.run()
        jb.close()
        a = sum(quality.top_area(quality.flat_faces(m.verts, m.tris, np.asarray(p.matrix)[:3, :3]))[1]
                for p in r.placements)
        v = referee.check(r.items(parts), s.vlo, s.vhi, s.gap, s.wall_gap)
        res[q] = (len(r.placements), a, v.ok, r.height)
        print('    quality %.0f %%: %d placed, %.0f mm2 facing up, %.1f mm, referee %s'
              % (q * 100, len(r.placements), a, r.height, 'clean' if v.ok else 'NOT clean'))
    check(res[1.0][0] == 6 and res[1.0][2] and res[1.0][1] == 0.0,
          'quality end: every base faces down')
    check(res[0.0][0] == 6 and res[0.0][2], 'density end: all placed, referee clean')


def test_try_harder():
    print('more time near the top')
    parts = [Part('box', S.box(40, 40, 40), 30, 'fixed')]
    s = Settings(vlo=(0, 0, 0), vhi=(120, 120, 90), gap=2.0, wall_gap=0.0, max_density=None,
                 time_budget=2.0, near_top_extra=1.0)
    jb = J.Job(parts, s, threads=1, workers=1)
    t0 = time.perf_counter()
    r = jb.run()
    dt = time.perf_counter() - t0
    jb.close()
    more = any('more' in line and 'close to the top' in line for line in jb.log)
    check(r.unplaced and more and dt > 2.5, 'parts left out: %.1f s instead of 2 (%d left out)'
          % (dt, len(r.unplaced)))
    s.near_top_extra = 0.0
    jb = J.Job(parts, s, threads=1, workers=1)
    jb.run()
    jb.close()
    check(not any('close to the top' in line for line in jb.log), 'off: no extra time')


def main():
    names = sys.argv[1:]
    for name, f in list(globals().items()):
        if name.startswith('test_') and (not names or name in names):
            f()
    print('ALL OK' if not FAILS else '%d FAILED' % len(FAILS))
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
