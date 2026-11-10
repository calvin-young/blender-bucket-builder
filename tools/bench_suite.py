# SPDX-License-Identifier: GPL-3.0-or-later
"""Pack synthetic buckets and print one line each: parts placed, first and final
height, density, time, referee verdict.  For finding where the packer is slow
or loose, and checking a change against the last run.

    python3 tools/bench_suite.py --time 30 [--buckets bureau-day,nuts] [--json out.json]
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bucket_builder'))

from pack import job as J, metrics, referee, synthetic  # noqa: E402
from pack.problem import Settings  # noqa: E402

DEFAULT = ('bureau-day,pringles,figurines,sockets,keycaps,giant,varied-chips,nuts,tees,'
           'huge-and-tiny,mix-a,one-offs,workshop')


def run(name, desc, parts, extra, args):
    s = Settings(gap=extra.get('gap', 2.0), wall_gap=args.wall_gap, h=args.cell,
                 max_density=(args.max_density / 100.0) or None, preset=args.goal,
                 time_budget=args.time, seed=args.seed)
    first = []
    t0 = time.perf_counter()

    def upd(j):
        if not first and j.best is not None:
            first.append((j._best_height(), time.perf_counter() - t0))
    jb = J.Job(parts, s, workers=args.workers)
    try:
        res = jb.run(upd)
    finally:
        jb.close()
    wall = time.perf_counter() - t0
    items = res.items(parts)
    rep = metrics.measure(items, s.vlo, s.vhi)
    n = sum(p.quantity for p in parts)
    ok = None
    if args.referee:
        ok = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap).ok
    row = dict(bucket=name, placed=len(res.placements), parts=n,
               first_h=first[0][0] if first else None, first_s=first[0][1] if first else None,
               height=rep.height if rep.density is not None else None,
               density=rep.density, peak=rep.peak_layer if rep.density is not None else None,
               seconds=wall, referee=ok)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--buckets', default=DEFAULT)
    ap.add_argument('--time', type=float, default=30.0)
    ap.add_argument('--cell', type=float, default=1.0)
    ap.add_argument('--wall-gap', type=float, default=5.0)
    ap.add_argument('--max-density', type=float, default=0.0)
    ap.add_argument('--goal', default='rush')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--workers', type=int, default=None)
    ap.add_argument('--referee', action='store_true')
    ap.add_argument('--json')
    a = ap.parse_args()
    every = {**synthetic.buckets(), **synthetic.library()}
    rows = []
    print('%-14s %9s %8s %7s %8s %7s %6s %7s %s' % ('bucket', 'placed', 'first mm', 'first s', 'final mm',
                                                     'dens %', 'peak', 'time s', 'referee'))
    for name in a.buckets.split(','):
        desc, parts, extra = every[name]
        r = run(name, desc, parts, extra, a)
        rows.append(r)
        f = lambda v, fmt: (fmt % v) if v is not None else '-'  # noqa: E731
        print('%-14s %4d/%-4d %8s %7s %8s %7s %6s %7.1f %s' % (
            name, r['placed'], r['parts'], f(r['first_h'], '%.1f'), f(r['first_s'], '%.1f'), f(r['height'], '%.1f'),
            f(r['density'] and 100 * r['density'], '%.2f'), f(r['peak'] and 100 * r['peak'], '%.0f'),
            r['seconds'], {None: '', True: 'clean', False: 'VIOLATIONS'}[r['referee']]), flush=True)
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(rows, fh, indent=1)


if __name__ == '__main__':
    main()
