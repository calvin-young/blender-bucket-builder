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


def _world(items, vlo, vhi, gap, wall_gap, timeout):
    t0 = time.perf_counter()
    vlo = np.asarray(vlo, float)
    vhi = np.asarray(vhi, float)
    w = World()
    size = float(np.max(vhi - vlo))
    w.set_scale(size, 0.5)
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
    return w


def offenders(items, vlo, vhi, gap, wall_gap=0.0, fixed=0, timeout=600.0):
    """Indices of items to take out so that the rest is a valid layout:
    every part outside the volume or too near a side wall, and of every
    pair too close the higher one (never one of the first `fixed` items,
    which stay: locked parts)."""
    w = _world(items, vlo, vhi, gap, wall_gap, timeout)
    out = set()
    for slot in list(w.oob) + list(w.wall):
        u = w.uid(slot)
        if u is not None and u >= fixed:
            out.add(u)
    tops = [float((np.asarray(m.verts, float) @ np.asarray(M, float)[:3, :3].T)[:, 2].max()
                  + np.asarray(M, float)[2, 3]) for _, m, M in items]
    for a, b in w.viol:
        ua, ub = w.uid(a), w.uid(b)
        if ua is None or ub is None or ua in out or ub in out:
            continue
        cand = [u for u in (ua, ub) if u >= fixed]
        if cand:
            out.add(max(cand, key=lambda u: (tops[u], u)))
    return out


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
