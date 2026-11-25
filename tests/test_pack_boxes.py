# SPDX-License-Identifier: GPL-3.0-or-later
"""Easy builds pack in seconds, every time (the owner, 2026-11-24): a few identical parts that fit by
their smallest boxes must all go in, at once, whatever orientation they arrive in. The box pass
(pack/boxpack.py) runs before the full search. NumPy only.

    python3 tests/test_pack_boxes.py
"""

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))
sys.path.insert(0, HERE)

import meshes                                           # noqa: E402
from pack import boxpack, job as J, referee             # noqa: E402
from pack.meshio import Mesh                            # noqa: E402
from pack.problem import Part, Settings                 # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg, flush=True)
    if not ok:
        FAILS.append(msg)


def socket_like():
    """A part 162 x 255 x 188 mm that is not a box (a block with a deep pocket and a boss), the
    size of the owner's socket: four fit a 380 x 284 x 380 bucket only 2 x 1 x 2, with 376 mm of
    parts on one 380 mm axis, which the voxel grid's rounding loses."""
    v, f = meshes.grid_box((162.0, 255.0, 120.0), 6)
    v2, f2 = meshes.cylinder(radius=60.0, height=68.0, segs=40)
    v2 = v2 + np.array([0.0, 0.0, 60.0 + 34.0])
    V = np.concatenate([v, v2])                       # (the block spans z -60..60, the boss 60..128)
    F = np.concatenate([f, f2 + len(v)])
    V[:, 2] -= V[:, 2].min()
    return Mesh(V.astype(np.float64), F.astype(np.int32), 'socket-like')


def turned(rng):
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(q) < 0:
        q[:, 0] = -q[:, 0]
    return q


def run(mesh, n, base, wall_gap, mode='free', budget=60.0):
    s = Settings(vlo=(0, 0, 0), vhi=(380, 284, 380), gap=1.5, wall_gap=wall_gap, max_density=None,
                 time_budget=budget)
    parts = [Part('socket', mesh, quantity=n, mode=mode, base=base)]
    job = J.Job(parts, s, workers=1)
    t0 = time.perf_counter()
    first = None
    while job.step(0.25):
        if getattr(job, '_stacks', None) is not None and first is None:
            first = time.perf_counter() - t0
            job.cancel = True
    res = job.result()
    items = [(p.part, mesh, p.matrix) for p in res.placements]
    v = referee.check([('p', m, M) for _, m, M in items], s.vlo, s.vhi, s.gap, s.wall_gap)
    return res, first, v


def main():
    mesh = socket_like()
    ext = np.ptp(np.asarray(mesh.verts), axis=0)
    check(np.allclose(sorted(ext), [162.0, 188.0, 255.0], atol=0.5), 'the test part is %s mm' % np.round(ext, 1))
    rng = np.random.default_rng(7)
    for trial in range(3):
        for wall_gap in (0.0, 5.0):
            base = turned(rng)
            res, first, v = run(mesh, 4, base, wall_gap)
            check(len(res.placements) == 4 and not res.unplaced and first is not None and first < 10.0 and v.ok,
                  'four socket-sized parts, turned at random (trial %d, wall gap %g): all in, %.1f s, referee %s'
                  % (trial, wall_gap, first or -1, v.ok))
    # too many by their boxes: the box pass says so, and leaves it to the search
    s = Settings(vlo=(0, 0, 0), vhi=(380, 284, 380), gap=1.5, wall_gap=0.0, max_density=None)
    parts = [Part('socket', mesh, quantity=5, mode='free')]
    got = boxpack.pack(parts, [(0, c) for c in range(5)], s.pack_lo, s.pack_hi, s.gap, s.wall_gap)
    check(got is None, 'five do not fit by their boxes: no box layout')
    # a mix: two kinds of boxes, every copy in, none closer than the gap, none outside
    a = Mesh(*[np.asarray(x) for x in meshes.box((60.0, 40.0, 30.0))], 'a')
    b = Mesh(*[np.asarray(x) for x in meshes.box((100.0, 80.0, 50.0))], 'b')
    parts = [Part('a', a, quantity=10, mode='free', base=turned(rng)), Part('b', b, quantity=6, mode='z')]
    copies = [(i, c) for i, p in enumerate(parts) for c in range(p.quantity)]
    got = boxpack.pack(parts, copies, s.pack_lo, s.pack_hi, s.gap, 2.0)
    ok = got is not None and len(got['M']) == len(copies)
    if ok:
        v = referee.check([('p', parts[q[0]].mesh, M) for q, M in got['M'].items()], s.vlo, s.vhi, s.gap, 2.0)
        ok = v.ok
    check(ok, 'a mix of two kinds: every copy placed, referee clean')


main()
print()
if FAILS:
    print('FAILED: %d' % len(FAILS))
    sys.exit(1)
print('ALL OK')
