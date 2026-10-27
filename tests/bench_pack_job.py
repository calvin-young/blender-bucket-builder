# SPDX-License-Identifier: GPL-3.0-or-later
"""The anytime packer (pack.job) on the synthetic buckets: the first greedy
layout against the best one within the time budget, checked by the exact
engine.

    python3 tests/bench_pack_job.py [bucket ...] [--time 60] [--seed 0] [--max-density 12]
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import job as J, metrics, referee, synthetic  # noqa: E402
from pack.problem import Settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--time', type=float, default=60.0, help='time budget per bucket, s')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--preset', default='overnight')
    ap.add_argument('--threads', type=int, default=None)
    ap.add_argument('--workers', type=int, default=None, help='processes (default: the job decides)')
    ap.add_argument('--max-density', type=float, default=None,
                    help='3D density limit in %% (default: the Settings default; 0: none)')
    ap.add_argument('--scale-qty', type=int, default=1)
    ap.add_argument('--no-referee', action='store_true')
    ap.add_argument('--log', action='store_true')
    a = ap.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:])
    all_b = synthetic.buckets()
    print('%-11s %8s %10s %10s %8s %6s %8s  %s' % ('bucket', 'placed', 'greedy', 'best', 'density',
                                                    'iters', 'time', 'referee'))
    bad = 0
    for name in a.names or list(all_b):
        desc, parts, kw = all_b[name]
        if a.max_density is not None:
            kw = dict(kw, max_density=(a.max_density / 100.0) or None)
        if a.scale_qty > 1:
            parts = [type(p)(p.name, p.mesh, p.quantity * a.scale_qty, p.mode, p.tier, p.base) for p in parts]
        s = Settings(time_budget=a.time, seed=a.seed, preset=a.preset, **kw)
        t = time.perf_counter()
        job = J.Job(parts, s, threads=a.threads, workers=a.workers)
        first = []

        def upd(j):
            if not first and j.best is not None:
                first.append((j._best_height(), time.perf_counter() - t))
        try:
            res = job.run(upd)
        finally:
            job.close()
        dt = time.perf_counter() - t
        items = res.items(parts)
        rep = metrics.measure(items, s.vlo, s.vhi)
        n = sum(p.quantity for p in parts)
        if a.no_referee:
            verdict = '-'
        else:
            v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
            verdict = 'clean' if v.ok else 'VIOLATIONS %s' % (v,)
            bad += not v.ok
        g = '%.1f mm' % first[0][0] if first else '-'
        print('%-11s %4d/%-3d %10s %7.1f mm %6.2f %% %6d %6.0f s  %s   (first %.0f s, bound %.1f mm, %d process%s)'
              % (name, len(res.placements), n, g, rep.height, 100 * (rep.density or 0), job.iterations, dt,
                 verdict, first[0][1] if first else 0, job._lb, job.workers, '' if job.workers == 1 else 'es'),
              flush=True)
        if a.log:
            for line in job.log:
                print('            ' + line)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
