# SPDX-License-Identifier: GPL-3.0-or-later
"""Packing with two clearances (#35; docs/SPEC_PACK.md, section 4).

What a person packing by hand does: keep parts the full clearance apart
(the aim, say 3 mm), and only where the build would otherwise leave a part
out, accept less, down to a minimum (say 1 mm).  An autopacker with one
gap cannot: at 3 mm it leaves parts out, at 1 mm every pair is crowded.

In steps, cheapest first:

1. Pack everything at the aim.  If nothing is left out, that is the answer.
2. Otherwise two ways to get more in, and the one that places more parts
   wins (a tie goes to the first, which keeps more pairs at the aim):
   a. the parts left out, packed into the build as it is at the least,
      every part of step 1 staying where it is ("squeezed in");
   b. everything packed again at the least.  (a. alone cannot help when
      the aim is lost in every row: five parts that fit across at 1 mm and
      four at 3 mm.)
3. Jiggle with both clearances spreads the slack: gaps under the aim widen
   where there is room, never below the least.

The result says which parts were squeezed in (2a), or that the build was
packed at the least (2b).
"""

import time
from dataclasses import replace

import numpy as np

from . import job as J
from .problem import Part, Placement


def pack(parts, s, workers=None, on_update=None, aim_share=0.45, locked=()):
    """Pack `parts` with settings `s`, where `s.gap` is the least and
    `s.gap_aim` the aim.  Returns (Result, squeezed placements, log lines)."""
    aim = float(s.gap_aim or 0.0)
    least = float(s.gap)
    t0 = time.perf_counter()
    total = s.time_budget
    if not aim or aim <= least:
        jb = J.Job(parts, s, locked, workers=workers)
        try:
            return jb.run(on_update), [], []
        finally:
            jb.close()
    log = []
    s_aim = replace(s, gap=aim, gap_aim=None,
                    time_budget=None if total is None else total * aim_share)
    jb = J.Job(parts, s_aim, locked, workers=workers)
    try:
        res = jb.run(on_update)
    finally:
        jb.close()
    log.append('%6.1f s  at the aim (%.2f mm): %d placed, %d left out' % (
        time.perf_counter() - t0, aim, len(res.placements), len(res.unplaced)))
    if not res.unplaced:
        return res, [], log
    # step 2a: what was left out, at the least, into the build as it is
    left = {}
    for u in res.unplaced:
        left[u[0]] = left.get(u[0], 0) + 1
    order = sorted(left)
    sub = [replace(parts[i], quantity=left[i]) for i in order]
    fixed = list(locked) + [(parts[p.part].mesh, np.asarray(p.matrix, float)) for p in res.placements]

    def remaining():
        return None if total is None else total - (time.perf_counter() - t0)

    def share(f):
        """A share of the whole time, never more than is left (the run keeps to its time)."""
        if total is None:
            return None
        return max(0.5, min(f * total, remaining()))
    s_least = replace(s, gap=least, gap_aim=None, time_budget=share(0.15), spread_z=False, compact=False)
    jb2 = J.Job(sub, s_least, fixed, workers=workers)
    try:
        res2 = jb2.run()
    finally:
        jb2.close()
    copy0 = {}
    for p in res.placements:
        copy0[p.part] = max(copy0.get(p.part, -1), p.copy)
    squeezed = []
    for p in res2.placements:
        i = order[p.part]
        copy0[i] = copy0.get(i, -1) + 1
        squeezed.append(Placement(i, copy0[i], np.asarray(p.matrix, float), p.orientation, None))
    log.append('%6.1f s  left out, at the least (%.2f mm): %d of %d squeezed in' % (
        time.perf_counter() - t0, least, len(squeezed), sum(left.values())))
    got = {}
    for p in res2.placements:
        got[order[p.part]] = got.get(order[p.part], 0) + 1
    unplaced = []
    for u in res.unplaced:
        if got.get(u[0], 0) > 0:
            got[u[0]] -= 1
        else:
            unplaced.append(u)
    best = (list(res.placements) + squeezed, unplaced, squeezed, 'squeezed')
    # step 2b: everything at the least (only with time enough to pack it all again)
    if unplaced and (total is None or remaining() > 0.2 * total):
        s_all = replace(s, gap=least, gap_aim=None, time_budget=share(0.25))
        jb3 = J.Job(parts, s_all, locked, workers=workers)
        try:
            res3 = jb3.run()
        finally:
            jb3.close()
        log.append('%6.1f s  everything at the least: %d placed, %d left out' % (
            time.perf_counter() - t0, len(res3.placements), len(res3.unplaced)))
        if len(res3.placements) > len(best[0]):
            best = (list(res3.placements), list(res3.unplaced), list(res3.placements), 'at the least')
    placements, unplaced, squeezed, how = best
    if squeezed and (total is None or remaining() > 1.0):
        # step 3: spread the slack toward the aim, by small moves, in what time is left
        from . import jiggle
        items = [(parts[p.part].mesh, np.asarray(p.matrix, float)) for p in placements]
        rest = remaining()
        Ms, jg, _n = jiggle.mend(items, s.vlo, s.vhi, least, s.wall_gap, locked,
                                 seconds=rest if rest is not None else 30.0, aim=aim, reach=max(aim, 3.0))
        ids = {id(p) for p in squeezed}
        new = [replace(p, matrix=M, cell=None) for p, M in zip(placements, Ms)]
        squeezed = [q for p, q in zip(placements, new) if id(p) in ids]
        placements = new
        col, under, short, low = jg.gaps()
        log.append('%6.1f s  spread (%s): %d pairs still short of the aim, smallest gap %.2f mm' % (
            time.perf_counter() - t0, how, short, low))
    out = replace(res, placements=placements, unplaced=unplaced, log=list(res.log) + log,
                  seconds=time.perf_counter() - t0)
    return out, squeezed, log
