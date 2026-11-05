# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic builds with a known layout that fits one bucket, laid out by
construction the way a person packs by hand, for tools/pack_vs_hand.py.

    python3 benchmarks/hand_builds.py out_dir
    python3 tools/pack_vs_hand.py out_dir/troughs.3mf --label troughs --mode fixed --time 300

Two patterns a person packs well and a packer may not (none of the shapes is
anybody's real part):

* troughs: 51 long, thin curved shells (four sizes) nested in four stacks,
  gap 2 mm.  Thin walls nested at a small gap: a grid that adds a cell or two
  to every gap loses most of a stack.
* housing: one open box holding most of the volume, with the 22 small parts
  inside it, gap 6 mm.  A packer that spreads the small parts over the floor
  first has no room left for the box.

Each layout is checked by the referee before it is written.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bucket_builder'))

from pack import meshio, referee, synthetic as S  # noqa: E402
from pack.compact import Compactor  # noqa: E402

FLOOR = (380.0, 284.0)


def trough(R, t, span, L, segs=40, name='trough'):
    """A curved shell: an arc of an annulus (inner radius R, wall t, span in
    degrees) extruded L long, lying along y, open side up, on z = 0."""
    a = np.radians(np.linspace(-90 - span / 2, -90 + span / 2, segs))
    outer = np.c_[(R + t) * np.cos(a), (R + t) * np.sin(a)]
    inner = np.c_[R * np.cos(a[::-1]), R * np.sin(a[::-1])]
    m = S.extrude(np.vstack([outer, inner]), L, name)
    v = m.verts[:, [0, 2, 1]]                         # the swap mirrors it: turn the faces round
    v = v - [(v[:, 0].min() + v[:, 0].max()) / 2, 0.0, v[:, 2].min()]
    return S._mesh(v, [tuple(f[::-1]) for f in m.tris], name)


def at(m, x, y, z):
    """The matrix putting mesh m's bounding box's low corner at (x, y, z)."""
    M = np.eye(4)
    M[:3, 3] = np.array([x, y, z]) - m.verts.min(axis=0)
    return M


def pitch(m, gap):
    """The exact height of one trough over the next in a nested stack."""
    M = np.eye(4)
    M[2, 3] = 200.0
    c = Compactor([(m, M)], (-500, -500, -500), (500, 500, 1000), gap, 0.0, locked=[(m, np.eye(4))])
    while c.move(0, 2, [1e9]) > 0.01:
        pass
    return float(c.M[0][2, 3])


def troughs():
    gap, wall = 2.0, 2.0
    items, x, top = [], wall, 0.0
    for R, t, span, L, n in ((40, 6, 150, 240, 12), (36, 5, 150, 220, 13), (44, 6, 140, 200, 12), (32, 4, 150, 230, 14)):
        m = trough(R, t, span, L, name='trough R%d' % R)
        p = pitch(m, gap) + 0.01
        for k in range(n):
            items.append((m, at(m, x, wall, k * p)))
        top = max(top, m.verts[:, 2].max() + p * (n - 1))
        x += float(np.ptp(m.verts[:, 0])) + gap + 0.01
    return items, gap, wall, top


def housing():
    gap, wall = 6.0, 0.5
    gc = gap + 0.1                                    # (a hair over the gap)
    box = S.transformed(S.cup(130, 126, 300, 4, 4, 'housing', square=True), np.diag([300 / 260, 1, 1]))
    items = [(box, at(box, wall, wall, 0.0))]
    x0, y0, z0 = wall + 4.6 + gc, wall + 4 + gc, 4 + gc      # inside it, the gap from its walls
    sock, cup, dome, br, ang, fig = S.socket(), S.tapered_cup(), S.dome(), S.bracket(), S.angle(), S.figurine()

    def row(meshes, y, z):
        x = x0
        for m in meshes:
            items.append((m, at(m, x, y, z)))
            x += float(np.ptp(m.verts[:, 0])) + gc

    y1 = y0 + 80 + gc
    y2 = y1 + 60 + gc
    row([sock, sock, cup], y0, z0)
    row([cup] * 4, y1, z0)
    row([dome] * 4, y2, z0)
    row([br] * 4, y1, z0 + 40 + gc)                 # on the cups
    row([ang] * 4, y2, z0 + 30 + gc)                # on the domes
    row([fig] * 3, y1, z0 + 80 + 2 * gc)            # on the brackets
    return items, gap, wall, 300.0


BUILDS = {'troughs': troughs, 'housing': housing}


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else '.'
    os.makedirs(out, exist_ok=True)
    for name, make in BUILDS.items():
        items, gap, wall, top = make()
        named = [('part-%03d' % (i + 1), m, M) for i, (m, M) in enumerate(items)]
        v = referee.check(named, (0, 0, 0), FLOOR + (top + 0.01,), gap, wall)
        assert v.ok, (name, v)
        meshio.write_3mf(os.path.join(out, name + '.3mf'), [(m, M, n) for n, m, M in named])
        print('%-8s %2d parts, %.1f mm high, gap %.1f, wall %.1f, referee clean' % (name, len(items), top, gap, wall))


if __name__ == '__main__':
    main()
