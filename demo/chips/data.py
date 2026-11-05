# SPDX-License-Identifier: GPL-3.0-or-later
"""Everything the chip reel plays, worked out once: how the chips sit in the can, the layout by hand,
and the packers' inputs.

    python3 demo/chips/data.py <cache dir>
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "..", "bucket_builder")]
import chips  # noqa: E402
from pack import stack  # noqa: E402
from pack.meshio import write_3mf  # noqa: E402

VOL = np.array([380.0, 284.0, 380.0])
GAP, WALL = 3.0, 5.0
CAN_GAP = 0.7               # chips in a can nearly touch


def pose_all(meshes):
    return [stack.Pose(m, np.eye(3)) for m in meshes]


def run(cache):
    os.makedirs(cache, exist_ok=True)
    ms = chips.chip_meshes()
    N = len(ms)
    poses = pose_all(ms)
    rng = np.random.default_rng(5)
    order = [int(i) for i in rng.permutation(N)]        # as they happen to lie in the can
    n = int(np.ceil((max(float(np.max(p.hi[:2] - p.lo[:2])) for p in poses) + 2 * GAP) / stack.CELL)) + 8
    maps = [stack.height_maps(p, n) for p in poses]
    tops, bots = [t for t, _ in maps], [b for _, b in maps]

    # in the can
    S_can = stack.spacings(tops, bots, CAN_GAP)
    can_z, z = {}, 0.0
    for j, i in enumerate(order):
        if j:
            a = order[j - 1]
            z += stack.exact_spacing(poses[a], poses[i], float(S_can[a, i]), CAN_GAP)
        can_z[i] = z
    # by hand: the same order, half and half, laid down side by side at the print gap
    S = stack.spacings(tops, bots, GAP)
    pitch = []
    for a, b in zip(order, order[1:]):
        S[a, b] = stack.exact_spacing(poses[a], poses[b], float(S[a, b]), GAP) + stack.EXACT_MARGIN
        pitch.append(S[a, b])
    got = stack.plan(poses, S, order, np.zeros(3), VOL, GAP, WALL)
    height, M, stacks, what, ax = got
    print("in the can: %.1f mm of chips, spacing %.2f" % (max(can_z.values()), max(can_z.values()) / (N - 1)))
    print("by hand: %s, %.1f mm, spacing %.2f, stacks of %s" % (what, height, np.mean(pitch), [len(s) for s in stacks]))
    np.savez(os.path.join(cache, "data.npz"),
             order=np.array(order), can_z=np.array([can_z[i] for i in range(N)]),
             hand=np.array([M[i] for i in range(N)]),
             stack_of=np.array([next(k for k, s in enumerate(stacks) if i in s) for i in range(N)]),
             height=height, ax=ax)

    # the packer's input: in a row, each just as it was made
    row = []
    for i, m in enumerate(ms):
        T = np.eye(4)
        T[:3, 3] = [(i % 15) * 150, (i // 15) * 150, 0]
        row.append((m, T, "chip-%03d" % (i + 1)))
    write_3mf(os.path.join(cache, "chips_in.3mf"), row)
    # for a packer that only tries things at random: each chip tumbled
    tumble = []
    for i, m in enumerate(ms):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        w, x, y, z_ = q
        R = np.array([[1 - 2 * (y * y + z_ * z_), 2 * (x * y - z_ * w), 2 * (x * z_ + y * w)],
                      [2 * (x * y + z_ * w), 1 - 2 * (x * x + z_ * z_), 2 * (y * z_ - x * w)],
                      [2 * (x * z_ - y * w), 2 * (y * z_ + x * w), 1 - 2 * (x * x + y * y)]])
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = [(i % 15) * 180, (i // 15) * 180, 100]
        tumble.append((m, T, "chip-%03d" % (i + 1)))
    write_3mf(os.path.join(cache, "chips_tumbled.3mf"), tumble)


if __name__ == "__main__":
    run(sys.argv[1])
