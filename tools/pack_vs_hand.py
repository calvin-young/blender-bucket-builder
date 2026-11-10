# SPDX-License-Identifier: GPL-3.0-or-later
"""How many parts of a hand-packed build does the packer get back in?

The hand layout sets the test: same parts, same floor area, same height, and
the smallest gaps the hand layout itself used. The result is the number of
parts in the first bucket and how many buckets all of them need, not height.

    python tools/pack_vs_hand.py build.3mf --label bucket-01 --mode free --time 300 \
        --json results/bucket-01-free.json --out-dir results

Prints and writes generic labels only (benchmarks/private/README.md). The
input's file and part names are never written.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bucket_builder'))

from pack import job as J, meshio, metrics, printers, referee  # noqa: E402
from pack.cli import parts_of  # noqa: E402
from pack.problem import Part, Settings, dial_settings  # noqa: E402


def extents(items):
    lo = np.full(3, np.inf)
    hi = -lo
    for _, m, M in items:
        M = np.asarray(M, float)
        v = np.asarray(m.verts, float) @ M[:3, :3].T + M[:3, 3]
        lo = np.minimum(lo, v.min(axis=0))
        hi = np.maximum(hi, v.max(axis=0))
    return lo, hi


def hand_facts(items, vlo, vhi):
    lo, hi = extents(items)
    # the engine lists pairs under its threshold, so ask for a wide one
    v = referee.check(items, vlo, vhi, 10.0, 0.0, timeout=3600.0)
    wall = float(min((lo - vlo)[:2].min(), (vhi - hi)[:2].min()))
    return {'lo': lo.tolist(), 'hi': hi.tolist(), 'collisions': v.collisions, 'outside': v.outside,
            'closest': None if v.closest == float('inf') else float(v.closest), 'wall': wall,
            'height': float(hi[2] - lo[2])}


def pack_bucket(parts, s, workers, curve, t_all):
    def upd(j):
        if j.best is not None:
            n = len(j.best)
            if not curve or curve[-1][1] != n:
                curve.append((round(time.perf_counter() - t_all, 1), n))
    jb = J.Job(parts, s, workers=workers)
    try:
        res = jb.run(upd)
        if os.environ.get('PACK_LOG'):
            for line in res.log:
                print('   ', line)
        return res
    finally:
        jb.close()


def remaining(parts, res):
    """Copies not placed, per part (Placement.part indexes the parts given to the job)."""
    placed = [0] * len(parts)
    for pl in res.placements:
        placed[pl.part] += 1
    return [Part(p.name, p.mesh, p.quantity - n, p.mode, base=p.base)
            for p, n in zip(parts, placed) if p.quantity > n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--label', default='bucket-01')
    ap.add_argument('--printer', default='HP MJF 5600')
    ap.add_argument('--mode', default='free', choices=('fixed', 'z', 'free'))
    ap.add_argument('--time', type=float, default=300.0)
    ap.add_argument('--gap', type=float, help='default: the closest pair by hand')
    ap.add_argument('--wall-gap', type=float, help='default: the closest to a side wall by hand, 5 mm at most')
    ap.add_argument('--height', type=float, help='default: the height used by hand')
    ap.add_argument('--goal', default='count', choices=('count', 'overnight', 'rush', 'quality'))
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--cell', type=float, default=1.0)
    ap.add_argument('--max-buckets', type=int, default=6)
    ap.add_argument('--json')
    ap.add_argument('--out-dir')
    a = ap.parse_args()

    scene = meshio.read(a.file)
    _, size = printers.find(a.printer)
    vlo, vhi = np.zeros(3), np.asarray(size, float)
    items = [('part-%03d' % (i + 1), it.mesh, it.matrix) for i, it in enumerate(scene.items)]
    hand = hand_facts(items, vlo, vhi)
    gap = a.gap if a.gap is not None else max(0.1, np.floor((hand['closest'] or 10.0) * 10) / 10)
    wall = a.wall_gap if a.wall_gap is not None else float(np.clip(np.floor(hand['wall'] * 10) / 10, 0.0, 5.0))
    height = a.height if a.height is not None else np.ceil(hand['height'] * 10) / 10
    vlo2 = np.array([vlo[0], vlo[1], 0.0])
    vhi2 = np.array([vhi[0], vhi[1], height])

    parts = parts_of(scene, a.mode, 1)
    total = sum(p.quantity for p in parts)
    vol_all = metrics.measure(items, vlo, vhi).volume
    out = {'label': a.label, 'mode': a.mode, 'goal': a.goal, 'time': a.time, 'parts': total, 'hand': hand, 'gap': gap,
           'wall': wall, 'height': height, 'volume_mm3': vol_all, 'buckets': []}
    print('%s  %d parts, %s mode, %.0f s; by hand: height %.1f, closest %s, wall %.1f, collisions %d, outside %d; '
          'test: gap %.1f, wall %.1f, height %.1f'
          % (a.label, total, a.mode, a.time, hand['height'], hand['closest'], hand['wall'], hand['collisions'],
             hand['outside'], gap, wall, height), flush=True)

    t0 = time.perf_counter()
    todo = parts
    for b in range(a.max_buckets):
        s = Settings(vlo=vlo2, vhi=vhi2, gap=gap, wall_gap=wall, h=a.cell, max_density=None, preset=a.goal, seed=b,
                     time_budget=a.time, **dial_settings(0.0))
        curve = []
        res = pack_bucket(todo, s, a.workers, curve, time.perf_counter())
        placed = res.items(todo)
        rep = metrics.measure(placed, vlo2, vhi2) if placed else None
        v = referee.check(placed, vlo2, vhi2, gap, wall, timeout=3600.0) if placed else None
        row = {'bucket': b + 1, 'placed': len(res.placements), 'asked': sum(p.quantity for p in todo),
               'volume_mm3': rep.volume if rep else 0.0, 'height': rep.height if rep else 0.0,
               'seconds': round(time.perf_counter() - t0, 1), 'curve': curve,
               'referee_ok': bool(v.ok) if v else None}
        if v and not v.ok:
            row['referee'] = {'collisions': v.collisions, 'too_close': v.too_close, 'outside': v.outside,
                              'near_wall': v.near_wall, 'closest': v.closest}
        out['buckets'].append(row)
        print('  bucket %d: placed %d of %d, volume %.0f cm3, height %.1f mm, referee %s, %.0f s'
              % (b + 1, row['placed'], row['asked'], row['volume_mm3'] / 1e3, row['height'],
                 'clean' if row['referee_ok'] else row.get('referee'), row['seconds']), flush=True)
        if a.out_dir and placed:
            os.makedirs(a.out_dir, exist_ok=True)
            meshio.write_3mf(os.path.join(a.out_dir, '%s-%s-b%d.3mf' % (a.label, a.mode, b + 1)),
                             [(m, M, 'part-%03d' % (i + 1)) for i, (_, m, M) in enumerate(placed)])
        todo = remaining(todo, res)
        if a.json:
            with open(a.json, 'w') as f:
                json.dump(out, f, indent=1)
        if not todo:
            break
    return 0


if __name__ == '__main__':
    sys.exit(main())
