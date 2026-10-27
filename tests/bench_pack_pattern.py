# SPDX-License-Identifier: GPL-3.0-or-later
"""The pattern solver against the bounding-box array (docs/AUTOPACK.md §8.4).

    python3 tests/bench_pack_pattern.py [--gap 2] [--cell 1]

For each shape and each of its distinct orientations' bounding-box shapes:
how many copies fit a Jet Fusion 5600 volume (380 x 284 x 380 mm, wall gap
as the gap) on the best lattice found, against the array of bounding boxes
plus the gap (the commercial packer's array), and the time the lattice took.  Only the
best orientation of each shape is printed, and the array's best over the
same orientations.
"""

import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import job as J, synthetic as S  # noqa: E402
from pack.problem import Part, Settings  # noqa: E402

VOLUME = (380.0, 284.0, 380.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gap', type=float, default=2.0)
    ap.add_argument('--cell', type=float, default=1.0)
    a = ap.parse_args()
    shapes = [('chip', S.pringle(), 'free'), ('keycap', S.big_keycap(), 'free'),
              ('small keycap', S.keycap(), 'z'), ('figurine', S.figurine(), 'z'),
              ('bracket', S.bracket(), 'free'), ('ring', S.ring(), 'free'),
              ('cube 20', S.box(20, 20, 20), 'z'), ('insole', S.insole(), 'free')]
    s = Settings(vlo=(0, 0, 0), vhi=VOLUME, gap=a.gap, wall_gap=a.gap, h=a.cell, max_density=None,
                 time_budget=None)
    inner = np.array(VOLUME) - 2 * a.gap
    print('%-13s %8s %8s %6s %10s  %s' % ('shape', 'lattice', 'array', 'gain', 'time', 'lattice (cells)'))
    for name, mesh, mode in shapes:
        j = J.Job([Part(name, mesh, 10 ** 6, mode)], s, threads=1, workers=1)
        j.cur = j._fresh()
        best, arr, t_all = None, 0, 0.0
        for k in j._distinct(0):
            ext = np.ptp(np.asarray(mesh.verts, float) @ np.asarray(j.g.orients(0)[k], float).T, axis=0)
            n_arr = int(np.prod(np.floor((inner + a.gap) / (ext + a.gap))))
            arr = max(arr, n_arr)
            t = time.perf_counter()
            cells, top = j._pattern_cells(0, k, j._fresh(), 10 ** 6)
            t_all += time.perf_counter() - t
            n = 0 if cells is None else len(cells)
            if best is None or n > best[0]:
                L = j._lattices[(0, k)]
                best = (n, k, None if L is None else (list(L.v1), list(L.v2), list(L.v3)))
        print('%-13s %8d %8d %5.0f%% %8.1f s  orientation %d: %s'
              % (name, best[0], arr, 100.0 * (best[0] / max(arr, 1) - 1), t_all, best[1], best[2]), flush=True)


if __name__ == '__main__':
    main()
