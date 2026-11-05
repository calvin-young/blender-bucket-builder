# SPDX-License-Identifier: GPL-3.0-or-later
"""Chips poured from a can, simulated with Blender's rigid bodies and saved for the capture.

    demo/chips/make.sh pour

Saved chips overlap in a stack, and a convex hull can't sit inside a saddle, so they aren't
simulated until they leave the can: inside it they follow it, then they fall touching only the
floor for a few frames, until the next one is clear, and only then touch each other.
"""
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chips as CH  # noqa: E402

OUT = sys.argv[sys.argv.index("--") + 1]
D = np.load(os.path.join(os.path.dirname(OUT), "data.npz"))
CAN_Z = D["can_z"]
R, H = 72.0, 445.0                  # the can, mm (look.py)
FLOOR_Z = 8.0                       # (shots.py)
STEP = float(CAN_Z.max()) / 99      # chips in the stack
SPOT = Vector((-40.0, -560.0, 0.0))  # the middle of where they land
HOME = Vector((-430.0, -40.0, 0.0))  # where the can stands
TILT_END, SLIDE_FROM = 100, 84
CLEAR_AGE = 9                       # frames before a falling chip touches the others
EVERY = 1.7                         # frames between chips, on average
SETTLE = 50

meshes = CH.chip_meshes(rings=6, segs=24)
LOW = -float(min(np.asarray(m.verts)[:, 2].min() for m in meshes))
N = len(meshes)
sc = bpy.context.scene
sc.render.fps = 30
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
bpy.ops.rigidbody.world_add()
w = sc.rigidbody_world
w.substeps_per_frame = 30
w.solver_iterations = 25
sc.gravity = (0, 0, -3800.0)          # (large chips: real gravity looks like they are thrown)
LAST = int(TILT_END + N * EVERY + SETTLE + 40)
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
floor.rigid_body.friction = 0.95

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
    b.friction = 0.8
    b.restitution = 0.0
    b.linear_damping = 0.6
    b.angular_damping = 0.7
    b.mass = 0.02
    b.kinematic = True
    b.collision_collections = layers(HELD)
    chips.append(ob)
spin = [float(a) for a in rng.uniform(-0.3, 0.3, N)]
kick = [Matrix.Rotation(float(rng.uniform(-0.35, 0.35)), 4, 'X') @ Matrix.Rotation(float(rng.uniform(-0.35, 0.35)), 4, 'Y')
        for _ in range(N)]


def noise(f, seed, freqs=(0.7, 1.3, 2.9)):
    """A hand's unsteadiness: a few slow sines, -1 to 1."""
    return sum(math.sin(2 * math.pi * q * f / 30.0 + 2.1 * seed + q) for q in freqs) / len(freqs)


def shake(f):
    """Flicks of the wrist while it pours, about twice a second, stronger near the end."""
    if f < TILT_END - 10:
        return 0.0
    grow = min(1.0, (f - TILT_END + 10) / (N * EVERY))
    return (0.35 + 0.65 * grow) * math.sin(2 * math.pi * 2.2 * f / 30.0) ** 3


def can_matrix(f):
    """Picked up, carried over the landing spot leaning more and more, tipped until the chips slide,
    shaken a little to keep them coming, then set back down. Never quite still: a hand holds it."""
    t = min(1.0, (f - 1) / TILT_END)
    t = t * t * (3 - 2 * t)
    theta = math.radians(112) * t ** 1.3
    p = max(0.0, (f - TILT_END) / (N * EVERY))
    theta += math.radians(14) * min(1.0, p)               # (steeper as it empties)
    drift = Vector((50 * math.sin(2.1 * p), 30 * math.sin(1.4 * p + 0.7), 0))
    mouth = SPOT + drift + Vector((0, 0, 215.0))
    a = Vector((math.sin(theta), 0, math.cos(theta)))
    pour_base = mouth - H * a
    base = HOME.lerp(pour_base, t)
    lift = 60 * 4 * t * (1 - t)                           # (up off the table first)
    base = base + Vector((0, 0, lift))
    done = max(0.0, min(1.0, (f - (TILT_END + N * EVERY + 12)) / 42.0))
    hand = 1.0
    if done > 0:
        done = done * done * (3 - 2 * done)
        theta *= 1 - done
        base = base.lerp(HOME, done) + Vector((0, 0, 50 * 4 * done * (1 - done)))
        hand = 1 - done
    on = min(1.0, f / 12.0) * hand
    sh = shake(f) * hand
    base = base + on * Vector((3.5 * noise(f, 1), 3.5 * noise(f, 2), 3.0 * noise(f, 3))) + a * (9.0 * sh)
    return (Matrix.Translation(base) @ Matrix.Rotation(theta + on * math.radians(1.3) * noise(f, 4) + math.radians(3.0) * sh, 4, 'Y')
            @ Matrix.Rotation(on * math.radians(1.6) * noise(f, 5), 4, 'X'))


SLID = {}


def slid(f):
    """How far the stack has slid along the can: in fits and starts, with the shaking."""
    if f in SLID:
        return SLID[f]
    v = 0.0 if f <= SLIDE_FROM else STEP / EVERY * (0.45 + 1.1 * abs(shake(f)) ** 0.7)
    SLID[f] = (slid(f - 1) if f > 1 else 0.0) + v
    return SLID[f]


def stack_z(k, f):
    return FLOOR_Z + LOW + float(CAN_Z[k]) + slid(f)


released = {}
frames_out = np.zeros((LAST, N, 4, 4), np.float32)
can_out = np.zeros((LAST, 4, 4), np.float32)
for f in range(1, LAST + 1):
    M = can_matrix(f)
    can_out[f - 1] = np.array(M)
    for k, ob in enumerate(chips):
        if k in released:
            age = f - released[k]
            if age == CLEAR_AGE:
                # (overlapping hulls push apart hard when they start to touch: damped until they have)
                ob.rigid_body.collision_collections = layers(FLOOR)
                ob.rigid_body.linear_damping = 0.985
                ob.rigid_body.angular_damping = 0.985
            elif age == CLEAR_AGE + 16:
                ob.rigid_body.linear_damping = 0.6
                ob.rigid_body.angular_damping = 0.7
            continue
        z = stack_z(k, f)
        # (it leans out as it comes to the mouth, and then just falls: no jump, or it would be shot out)
        lean = max(0.0, min(1.0, (z - (H - 40.0)) / 50.0))
        tilt = Quaternion().slerp(kick[k].to_quaternion(), lean).to_matrix().to_4x4()
        ob.matrix_world = M @ Matrix.Translation((0, 0, z)) @ Matrix.Rotation(spin[k] * min(1.0, f / 40.0), 4, 'Z') @ tilt
        if z >= H + 8.0:                     # past the mouth: let go
            ob.rigid_body.kinematic = False
            ob.rigid_body.collision_collections = layers(FALLING)
            released[k] = f
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
left = np.array([released.get(k, LAST) for k in range(N)])
np.savez_compressed(OUT, chips=frames_out, can=can_out, R=R, H=H, left=left)
print("saved", OUT, frames_out.shape)
