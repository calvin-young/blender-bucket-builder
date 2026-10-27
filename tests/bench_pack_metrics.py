# SPDX-License-Identifier: GPL-3.0-or-later
"""Timing of pack.metrics on builds of the owner's sizes (docs/AUTOPACK.md 16).

    python3 tests/bench_pack_metrics.py [--small]

60 parts of 1 M triangles, all distinct and then 12 meshes copied five times,
and 5 parts of 20 M triangles.  Synthetic UV spheres; nothing is read from
disk.  --small divides every count by 10.
"""

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

from pack import metrics  # noqa: E402
from pack.meshio import Mesh  # noqa: E402


def sphere(radius, tris, name):
    """A closed UV sphere of about `tris` triangles, as a Mesh."""
    rings = max(4, int(np.sqrt(tris / 4.0)))
    segs = 2 * rings
    th = np.linspace(0, np.pi, rings + 1)[1:-1]
    ph = np.linspace(0, 2 * np.pi, segs, endpoint=False)
    z = np.repeat(np.cos(th) * radius, segs)
    r = np.repeat(np.sin(th) * radius, segs)
    x = r * np.tile(np.cos(ph), rings - 1)
    y = r * np.tile(np.sin(ph), rings - 1)
    v = np.concatenate([np.stack([x, y, z], 1), [[0, 0, radius], [0, 0, -radius]]])
    top, bot = v.shape[0] - 2, v.shape[0] - 1
    i = np.arange((rings - 2) * segs).reshape(rings - 2, segs)
    j = (i // segs) * segs + (i + 1) % segs
    a, b, c, d = i, j, i + segs, j + segs
    quads = np.concatenate([np.stack([a, c, b], -1).reshape(-1, 3),
                            np.stack([b, c, d], -1).reshape(-1, 3)])
    first = np.arange(segs)
    t1 = np.stack([np.full(segs, top), (first + 1) % segs, first], 1)
    last = (rings - 2) * segs + first
    t2 = np.stack([np.full(segs, bot), last, (last - first) + (first + 1) % segs], 1)
    f = np.concatenate([quads, t1, t2]).astype(np.int32)
    return Mesh(v, f, name)


def timeit(label, parts, vlo, vhi, **kw):
    n = sum(m.nt for _, m, _ in parts)
    t = time.perf_counter()
    rep = metrics.measure(parts, vlo, vhi, **kw)
    dt = time.perf_counter() - t
    print('%-46s %6.2f s   density %.2f %%, %d parts (%d M triangles counted)'
          % (label, dt, 100 * rep.density, rep.n_parts, n / 1e6))
    return rep, dt


def main():
    div = 10 if '--small' in sys.argv else 1
    vlo, vhi = np.zeros(3), np.array([380.0, 284.0, 380.0])
    rng = np.random.default_rng(5)

    def place(mesh, k, name):
        q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        M = np.eye(4)
        M[:3, :3] = q * np.linalg.det(q)
        M[:3, 3] = [40 + 60 * (k % 6), 40 + 55 * ((k // 6) % 5), 40 + 90 * (k // 30)]
        return (name, mesh, M)

    n_each = 1_000_000 // div
    print('60 parts of %.1f M triangles' % (n_each / 1e6))
    meshes = [sphere(15 + (k % 7), n_each, 'm%d' % k) for k in range(60)]
    parts = [place(m, k, 'p%d' % k) for k, m in enumerate(meshes)]
    timeit('60 distinct meshes', parts, vlo, vhi)
    parts = [place(meshes[k % 12], k, 'p%d' % k) for k in range(60)]
    timeit('12 meshes x 5 copies (random rotations)', parts, vlo, vhi)
    parts = []
    for k in range(60):
        name, m, M = place(meshes[k % 12], k, 'p%d' % k)
        M[:3, :3] = np.eye(3)
        parts.append((name, m, M))
    timeit('12 meshes x 5 copies (same orientation)', parts, vlo, vhi)
    del meshes, parts

    n_big = 20_000_000 // div
    print('5 parts of %.0f M triangles' % (n_big / 1e6))
    big = [sphere(60 + 5 * k, n_big, 'b%d' % k) for k in range(5)]
    parts = [place(m, k, 'p%d' % k) for k, m in enumerate(big)]
    for k, p in enumerate(parts):
        p[2][:3, 3] = [100 + 60 * (k % 3), 100 + 80 * (k // 3), 80]
    timeit('5 distinct large meshes', parts, vlo, vhi)
    mem = sum(m.verts.nbytes + m.tris.nbytes for m in big) / 1e9
    print('meshes in memory: %.1f GB' % mem)


if __name__ == '__main__':
    main()
