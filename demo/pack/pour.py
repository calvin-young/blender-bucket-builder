# SPDX-License-Identifier: GPL-3.0-or-later
"""Chips poured from a can, simulated with Blender's rigid bodies and saved for the capture.

    demo/pack/make.sh pour

Saved chips overlap in a stack, and a convex hull can't sit inside a saddle, so they aren't
simulated until they leave the can: inside it they follow it, then they fall touching only the
floor for a few frames, until the next one is clear, and only then touch each other.
"""
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inputs  # noqa: E402

OUT = sys.argv[sys.argv.index("--") + 1]
R, H = 60.0, 480.0                  # the can, mm
STEP = 4.6                          # chips in the stack
SPOT = Vector((-30.0, -520.0, 0.0))  # the middle of where they land
TILT_END, SLIDE_FROM = 40, 34
CLEAR_AGE = 6                       # frames before a falling chip touches the others
EVERY = 2                           # frames between chips
SETTLE = 50

meshes = inputs.chip_meshes(rings=6, segs=24)
N = len(meshes)
sc = bpy.context.scene
sc.render.fps = 30
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
bpy.ops.rigidbody.world_add()
w = sc.rigidbody_world
w.substeps_per_frame = 30
w.solver_iterations = 25
sc.gravity = (0, 0, -9810.0)
LAST = TILT_END + N * EVERY + SETTLE
w.point_cache.frame_start, w.point_cache.frame_end = 1, LAST + 5


def add_rb(ob, kind):
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.rigidbody.object_add(type=kind)
    ob.select_set(False)


def layers(*on):
    return [i in on for i in range(20)]


FLOOR, FALLING, HELD = 0, 1, 2
me = bpy.data.meshes.new("floor")
me.from_pydata([(-3000, -3000, 0), (3000, -3000, 0), (3000, 3000, 0), (-3000, 3000, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("floor", me)
sc.collection.objects.link(floor)
add_rb(floor, 'PASSIVE')
floor.rigid_body.collision_collections = layers(FLOOR, FALLING)
floor.rigid_body.friction = 0.7

# out of shot: the odd chip that gets knocked away stays near the pile
for x, y, sx, sy in ((-600, 0, 20, 340), (600, 0, 20, 340), (0, -340, 620, 20), (0, 340, 620, 20)):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=SPOT + Vector((x, y, 150.0)), scale=(2 * sx, 2 * sy, 300.0))
    wall = bpy.context.object
    wall.select_set(False)
    add_rb(wall, 'PASSIVE')
    wall.rigid_body.collision_shape = 'BOX'
    wall.rigid_body.collision_collections = layers(FLOOR, FALLING)

rng = np.random.default_rng(11)
chips = []
for k, m in enumerate(meshes):
    me = bpy.data.meshes.new(f"chip{k}")
    me.from_pydata(np.asarray(m.verts).tolist(), [], np.asarray(m.tris).tolist())
    ob = bpy.data.objects.new(f"chip{k}", me)
    sc.collection.objects.link(ob)
    add_rb(ob, 'ACTIVE')
    b = ob.rigid_body
    b.collision_shape = 'CONVEX_HULL'
    b.collision_margin = 0.3
    b.friction = 0.55
    b.restitution = 0.0
    b.linear_damping = 0.45
    b.angular_damping = 0.45
    b.mass = 0.02
    b.kinematic = True
    b.collision_collections = layers(HELD)
    chips.append(ob)
spin = [float(a) for a in rng.uniform(-0.3, 0.3, N)]
kick = [Matrix.Rotation(float(rng.uniform(-0.6, 0.6)), 4, 'X') @ Matrix.Rotation(float(rng.uniform(-0.6, 0.6)), 4, 'Y')
        for _ in range(N)]


def can_matrix(f):
    """Lifted and tipped, swung over the landing spot while it pours, then put down to the side."""
    t = min(1.0, (f - 1) / TILT_END)
    t = t * t * (3 - 2 * t)
    theta = math.radians(122) * t
    p = (f - TILT_END) / (N * EVERY)
    swing = Vector((150 * math.sin(4.2 * max(p, 0)), 90 * math.sin(2.7 * max(p, 0) + 0.7), 0))
    mouth = SPOT + swing + Vector((0, 0, 260.0))
    a = Vector((math.sin(theta), 0, math.cos(theta)))
    base0 = SPOT + Vector((-720.0, 120.0, 0.0))
    pour_base = mouth - H * a
    base = base0.lerp(pour_base, t)
    done = max(0.0, min(1.0, (f - (TILT_END + N * EVERY + 5)) / 35.0))
    if done > 0:
        done = done * done * (3 - 2 * done)
        theta *= 1 - done
        base = base.lerp(base0, done)
    return Matrix.Translation(base) @ Matrix.Rotation(theta, 4, 'Y')


def stack_z(k, f):
    return 8.0 + STEP * k + max(0.0, f - SLIDE_FROM) * STEP / EVERY


released = {}
frames_out = np.zeros((LAST, N, 4, 4), np.float32)
can_out = np.zeros((LAST, 4, 4), np.float32)
for f in range(1, LAST + 1):
    M = can_matrix(f)
    can_out[f - 1] = np.array(M)
    for k, ob in enumerate(chips):
        if k in released:
            if f - released[k] == CLEAR_AGE:
                ob.rigid_body.collision_collections = layers(FLOOR)
            continue
        z = stack_z(k, f)
        if z >= H - 2.0:                     # at the mouth: let go, a little askew
            ob.matrix_world = M @ Matrix.Translation((0, 0, z + 45.0)) @ Matrix.Rotation(spin[k], 4, 'Z') @ kick[k]
            ob.rigid_body.kinematic = False
            ob.rigid_body.collision_collections = layers(FALLING)
            released[k] = f
        else:
            ob.matrix_world = M @ Matrix.Translation((0, 0, z)) @ Matrix.Rotation(spin[k], 4, 'Z')
    sc.frame_set(f)
    bpy.context.view_layer.update()
    for k, ob in enumerate(chips):
        # what the capture plays: while held, where the script put it
        frames_out[f - 1, k] = np.array(ob.matrix_world)
    if f % 30 == 0:
        P = frames_out[f - 1, :, :3, 3]
        q = np.percentile(P, [5, 95], axis=0)
        print(f"frame {f}: released {len(released)}, z {P[:, 2].min():.0f}..{P[:, 2].max():.0f}, "
              f"x {P[:, 0].min():.0f}..{P[:, 0].max():.0f} (5-95% {q[0, 0]:.0f}..{q[1, 0]:.0f}), "
              f"y {P[:, 1].min():.0f}..{P[:, 1].max():.0f} ({q[0, 1]:.0f}..{q[1, 1]:.0f})", flush=True)
np.savez_compressed(OUT, chips=frames_out, can=can_out, R=R, H=H)
print("saved", OUT, frames_out.shape)
