# SPDX-License-Identifier: GPL-3.0-or-later
"""How the packer's time to a first layout grows with the number of parts:
copies of one small part, then a random mix, at increasing counts.

    python3 tools/bench_scale_pack.py [--counts 100,300,1000] [--workers 8]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bucket_builder'))

from pack import job as J, metrics, synthetic as S  # noqa: E402
from pack.problem import Part, Settings  # noqa: E402


def one(parts, args):
    s = Settings(gap=2.0, max_density=None, preset='rush', time_budget=0)
    t0 = time.perf_counter()
    jb = J.Job(parts, s, workers=args.workers)
    try:
        res = jb.run()
    finally:
        jb.close()
    t = time.perf_counter() - t0
    rep = metrics.measure(res.items(parts), s.vlo, s.vhi)
    return t, len(res.placements), rep.height if rep.density is not None else None, rep.density


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--counts', default='100,300,1000,2000')
    ap.add_argument('--workers', type=int, default=None)
    a = ap.parse_args()
    print('%-22s %6s %7s %9s %8s' % ('parts', 'placed', 'time s', 'height mm', 'dens %'))
    for n in [int(x) for x in a.counts.split(',')]:
        for label, parts in (('nuts x%d' % n, [Part('nut', S.hex_nut(), n, 'free')]),
                             ('mix of 20, %d' % n, S.random_mix(11, 20, copies=max(1, n // 20), fixed=False))):
            t, placed, h, d = one(parts, a)
            print('%-22s %6d %7.1f %9s %8s' % (label, placed, t, '%.1f' % h if h else '-',
                                                 '%.1f' % (100 * d) if d else '-'), flush=True)


if __name__ == '__main__':
    main()
