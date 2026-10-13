# SPDX-License-Identifier: GPL-3.0-or-later
"""The exact engine as referee of a layout (docs/AUTOPACK.md §8.8): every
pair at least the gap apart, every part inside the volume and the wall gap
from its side walls."""

import time
from dataclasses import dataclass

import numpy as np

from ._core import World, COLLIDE


@dataclass
class Verdict:
    collisions: int = 0
    too_close: int = 0               # pairs closer than the gap
    outside: int = 0                 # parts partly or wholly outside
    near_wall: int = 0               # parts closer to a side wall than the wall gap
    closest: float = float('inf')    # smallest distance found below the gap (mm)
    seconds: float = 0.0

    @property
    def ok(self):
        return not (self.collisions or self.too_close or self.outside or self.near_wall)


def check(items, vlo, vhi, gap, wall_gap=0.0, timeout=600.0):
    """items: [(name, mesh, 4x4 matrix)] in mm."""
    t0 = time.perf_counter()
    vlo = np.asarray(vlo, float)
    vhi = np.asarray(vhi, float)
    w = World()
    size = float(np.max(vhi - vlo))
    w.set_scale(size, 0.5)
    # (a pair exactly at the gap passes: the threshold sits a hair below it)
    w.set_thresholds(0.0, max(0.0, gap - 1e-6 * size) if gap > 0 else 0.0)
    w.set_volume(vlo, vhi, wall_gap)
    keys = {}
    for uid, (name, mesh, M) in enumerate(items):
        k = keys.get(id(mesh))
        if k is None:
            k = keys[id(mesh)] = 'm%d' % len(keys)
            w.add_geom(k, np.asarray(mesh.verts, np.float64), np.asarray(mesh.tris))
        w.add_object(uid, k, np.asarray(M, float))
    while w.step(budget=0.05):
        if time.perf_counter() - t0 > timeout:
            raise TimeoutError('the referee did not finish in %g s' % timeout)
    nc, ncl, npart, nout, nwall = w.counts()
    v = Verdict(nc, ncl, npart + nout, nwall)
    for pr in w.viol.values():
        if pr.state != COLLIDE:
            v.closest = min(v.closest, float(pr.dist))
        else:
            v.closest = 0.0
    v.seconds = time.perf_counter() - t0
    return v
