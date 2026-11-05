# SPDX-License-Identifier: GPL-3.0-or-later
"""The chips of the reel: one hundred saddle-shaped chips, every one its own mesh. Sized so that half
the can, laid on its side, fills most of the 5600's width, and two such stacks fit side by side."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "bucket_builder"))
from pack.synthetic import pringle  # noqa: E402

CHIPS = 100


def chip(rng, rings, segs):
    """A chip with its own size, bend and thickness, and a rim and surface a little uneven."""
    m = pringle(radius=float(rng.uniform(60.5, 64.0)), curve=float(rng.uniform(150.0, 172.0)), thick=float(rng.uniform(1.9, 2.3)),
                rings=rings, segs=segs, oval=float(rng.uniform(0.76, 0.80)))
    V = np.asarray(m.verts, float).copy()
    a = np.arctan2(V[:, 1], V[:, 0])
    r = np.hypot(V[:, 0], V[:, 1])
    rim = 1.0
    for k, amp in ((3, 0.012), (5, 0.009), (8, 0.006)):
        rim = rim + amp * np.sin(k * a + rng.uniform(0, 6.28))
    V[:, :2] *= rim[:, None]
    bump = np.zeros(len(V))
    for _ in range(5):
        c = rng.uniform(-45, 45, 2)
        s = rng.uniform(8, 18)
        bump += rng.uniform(-0.45, 0.45) * np.exp(-((V[:, 0] - c[0]) ** 2 + (V[:, 1] - c[1]) ** 2) / (2 * s * s))
    V[:, 2] += bump * np.clip(r / 8, 0, 1)
    m.verts = V
    return m


def chip_meshes(rings=20, segs=64):
    rng = np.random.default_rng(11)
    return [chip(rng, rings, segs) for _ in range(CHIPS)]
