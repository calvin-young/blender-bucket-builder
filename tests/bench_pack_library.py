"""The packer on the shape library (pack.synthetic.library).

    python3 tests/bench_pack_library.py [names] [--time 60] [--baseline] [--out dir]

For each bucket: the parts placed, the height and density after the time
given, how long the first layout took, and the exact referee's verdict.
With --baseline also what one copy at a time gives (greedy, no lattices,
no families: the packer before Stage 4).  --out writes each packed bucket
as a 3MF (generic part names) for a look in Blender.
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import family, greedy, job as J, meshio, metrics, referee, synthetic  # noqa: E402
from pack.problem import Settings  # noqa: E402


def run(parts, s, workers):
    t = time.perf_counter()
    first = []

    def upd(j):
        if not first and j.best is not None:
            first.append(time.perf_counter() - t)
    jb = J.Job(parts, s, workers=workers)
    try:
        res = jb.run(upd)
    finally:
        jb.close()
    return res, time.perf_counter() - t, (first[0] if first else 0.0), jb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--time', type=float, default=60.0)
    ap.add_argument('--workers', type=int, default=1)
    ap.add_argument('--baseline', action='store_true')
    ap.add_argument('--max-density', type=float, default=0.0, help='%% (0: none, the default here)')
    ap.add_argument('--out')
    a = ap.parse_args()
    lib = synthetic.library()
    print('%-13s %8s %9s %8s %7s %7s  %s' % ('bucket', 'placed', 'height', 'density', 'first', 'time', 'referee'),
          flush=True)
    for name in a.names or list(lib):
        desc, parts, kw = lib[name]
        s = Settings(time_budget=a.time, max_density=(a.max_density / 100.0) or None, **kw)
        res, dt, t1, jb = run(parts, s, a.workers)
        items = res.items(parts)
        rep = metrics.measure(items, s.vlo, s.vhi)
        v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
        n = sum(p.quantity for p in parts)
        how = sorted({line.split()[2] for line in jb.log if len(line.split()) > 2 and
                      line.split()[2] in ('pattern', 'patterns', 'family')})
        print('%-13s %4d/%-4d %6.1f mm %6.2f %% %5.0f s %5.0f s  %s  %s' % (
            name, len(res.placements), n, rep.height, 100 * (rep.density or 0), t1, dt,
            'clean' if v.ok else 'VIOLATIONS %s' % (v,), ' '.join(how)), flush=True)
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            meshio.write_3mf(os.path.join(a.out, name + '.3mf'),
                             [(m, M, 'part-%03d' % (k + 1)) for k, (_, m, M) in enumerate(items)])
        if a.baseline:
            find, pf = family.find, J.Job.PATTERN_FROM
            family.find = lambda *x, **y: []
            J.Job.PATTERN_FROM = 10 ** 9
            try:
                s0 = Settings(time_budget=0.0, max_density=s.max_density, compact=False, **kw)
                r0, d0, _, _ = run(parts, s0, 1)
            finally:
                family.find, J.Job.PATTERN_FROM = find, pf
            rep0 = metrics.measure(r0.items(parts), s.vlo, s.vhi)
            print('%-13s %4d/%-4d %6.1f mm %6.2f %%         %5.0f s  (one copy at a time)' % (
                '', len(r0.placements), n, rep0.height, 100 * (rep0.density or 0), d0), flush=True)
            if a.out:
                meshio.write_3mf(os.path.join(a.out, name + '-greedy.3mf'),
                                 [(m, M, 'part-%03d' % (k + 1)) for k, (_, m, M) in enumerate(r0.items(parts))])


if __name__ == '__main__':
    main()
