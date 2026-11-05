# SPDX-License-Identifier: GPL-3.0-or-later
"""The packer's inputs for the extra shots: chips that are all different meshes, ridges, wedges.

    python3 demo/pack/inputs.py <cache dir>     # run from anywhere; make.sh pack does this
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "bucket_builder"))
from pack.meshio import write_3mf  # noqa: E402
from pack.synthetic import pringle, prism, wedge  # noqa: E402

CHIPS = 100
SIZE = 1.6                          # large enough that they have to stack in this volume


def chip_meshes(rings=16, segs=48):
    """Every chip its own mesh: alike, not the same."""
    rng = np.random.default_rng(3)
    return [pringle(radius=SIZE * float(rng.uniform(24, 31)), curve=SIZE * float(rng.uniform(34, 46)),
                    thick=SIZE * float(rng.uniform(1.6, 2.4)), rings=rings, segs=segs) for _ in range(CHIPS)]


def ridge():
    """A flat 80 x 80 base and two slopes: the base is the face the dial cares about."""
    return prism([(0, 0), (80, 0), (40, 20)], 80, "ridge")


def row(meshes, step):
    out = []
    for i, m in enumerate(meshes):
        T = np.eye(4)
        T[:3, 3] = [(i % 15) * step, (i // 15) * step, 0]
        out.append((m, T, "part-%d" % (i + 1)))
    return out


if __name__ == "__main__":
    d = sys.argv[1]
    write_3mf(os.path.join(d, "fam_in.3mf"), row(chip_meshes(), 150))
    write_3mf(os.path.join(d, "ridges.3mf"), row([ridge()] * 24, 110))
    write_3mf(os.path.join(d, "wedges_in.3mf"), row([wedge()] * 300, 80))
    print("inputs written")
