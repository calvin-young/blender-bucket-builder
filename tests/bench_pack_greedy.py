# SPDX-License-Identifier: GPL-3.0-or-later
"""Greedy placement on the synthetic buckets, checked by the exact engine.

    python3 tests/bench_pack_greedy.py [bucket ...] [--h 1.0] [--coarse 4] [--candidates 8]

Prints, per bucket: parts placed, packed height, 3D density, time, and the
referee's verdict (which must be clean).
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))
sys.path.insert(0, os.path.join(HERE, '..', 'benchmarks'))

import synthetic  # noqa: E402
from pack import greedy, metrics, referee  # noqa: E402
from pack.problem import Settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--h', type=float, default=1.0)
    ap.add_argument('--coarse', type=int, default=4)
    ap.add_argument('--candidates', type=int, default=3)
    ap.add_argument('--phases', type=int, default=1)
    ap.add_argument('--no-referee', action='store_true')
    ap.add_argument('--max-density', type=float, default=None,
                    help='3D density limit in %% (default: the Settings default; 0: none)')
    ap.add_argument('--scale-qty', type=int, default=1, help='multiply every quantity (to fill the bucket)')
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    a = ap.parse_args(argv)   # (inside Blender: blender --background --python this -- args)
    all_b = synthetic.buckets()
    names = a.names or list(all_b)
    print('%-11s %8s %8s %8s %8s %9s  %s' % ('bucket', 'placed', 'height', 'density', 'time', 'referee', 'verdict'))
    bad = 0
    for name in names:
        desc, parts, kw = all_b[name]
        if a.max_density is not None:
            kw = dict(kw, max_density=(a.max_density / 100.0) or None)
        if a.scale_qty > 1:
            parts = [type(p)(p.name, p.mesh, p.quantity * a.scale_qty, p.mode, p.tier, p.base) for p in parts]
        s = Settings(h=a.h, coarse=a.coarse, candidates=a.candidates, phases=a.phases, **kw)
        t = time.perf_counter()
        res = greedy.solve(parts, s)
        dt = time.perf_counter() - t
        items = res.items(parts)
        rep = metrics.measure(items, s.vlo, s.vhi)
        n = sum(p.quantity for p in parts)
        if a.no_referee:
            verdict, rt = '-', 0.0
        else:
            v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
            verdict, rt = ('clean' if v.ok else 'VIOLATIONS %s' % (v,)), v.seconds
            bad += not v.ok
        print('%-11s %4d/%-3d %6.1f mm %6.2f %% %7.1f s %7.1f s  %s'
              % (name, len(res.placements), n, rep.height, 100 * (rep.density or 0), dt, rt, verdict))
        if res.unplaced:
            why = {}
            for _, _, r in res.unplaced:
                why[r] = why.get(r, 0) + 1
            print('            not placed: %s' % why)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
