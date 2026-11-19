# SPDX-License-Identifier: GPL-3.0-or-later
"""Spread: in a final layout parts take all the breathing room they have,
from each other and from the walls, without raising the build (the owner,
2026-11-19: ten spheres end up evenly over the bucket, not in a row along
one side). The same machinery as Relax: Jiggle with an aim clearance
(`jiggle.mend(aim=)`), the aim raised step by step while every pair still
reaches it, the walls held to half the aim, and the volume's top lowered to
the build's own top so nothing rises. A step that would break a rule is not
taken: the last layout that kept every rule stands.

Not wanted when more parts will be added later: then the packer leaves one
free region instead (the "Leave room for more parts" option).
"""

import time

import numpy as np

from . import jiggle

GROW = 1.5               # the aim of the next step, times the one just reached
TOP_ROOM = 0.003         # mm above the build's top the volume ends at (inside by the bounds rule)


def build_top(items):
    """The highest point of a layout of (mesh, 4 x 4) pairs."""
    return max(float((np.asarray(m.verts, float) @ np.asarray(M, float)[2, :3]).max() + M[2, 3]) for m, M in items)


def spread(items, vlo, vhi, least, wall_gap=0.0, locked=(), aim=10.0, seconds=60.0, on_step=None, stop=None):
    """Spread the layout `items` ((mesh, 4 x 4) pairs): returns (matrices,
    the clearance every pair reached, the steps taken). `least` is the gap
    no pair may go under; `aim` the first clearance to reach."""
    vlo = np.asarray(vlo, float)
    vhi = np.asarray(vhi, float).copy()
    vhi[2] = min(vhi[2], build_top(items) + TOP_ROOM)              # (spread within the packed height)
    deadline = time.time() + seconds
    best = [np.asarray(M, float).copy() for _, M in items]
    reached, steps, a = least, 0, max(float(aim), least)
    meshes = [m for m, _ in items]
    while time.time() < deadline and not (stop is not None and stop()):
        left = deadline - time.time()
        Ms, jg, _n = jiggle.mend(list(zip(meshes, best)), vlo, vhi, least, max(wall_gap, 0.5 * a), locked,
                                 seconds=min(left, max(10.0, 0.5 * left)), aim=a, reach=None, on_step=on_step)
        col, under, short, low = jg.gaps()
        if col or under or jg.total() > 0:
            break                                                  # (a rule broken: keep the last good layout)
        best = [np.asarray(M, float).copy() for M in Ms]
        steps += 1
        if short:
            reached = max(reached, min(low, a))
            break                                                  # (not every pair reached this aim: done)
        reached = a
        a *= GROW
    return best, reached, steps                                    # (reached: to about 1 %, as Jiggle measures)
