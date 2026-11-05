# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender side of a reel: a timer-driven runner that plays scripted segments
in a live window and screenshots every frame.

A reel script defines segments as generator functions.  Each `yield` hands a
frame to the runner, which redraws the window and saves a screenshot to
<out>/<segment>/NNNN.png; `yield 0.5` waits half a second without capturing
(for caches to settle).  Run it with lib/blender.sh.
"""
import math
import os
import shutil
import sys
import time
import traceback

import bpy
from mathutils import Euler, Quaternion, Vector

FPS = 30
DRAWS = 2               # redraws before a screenshot (more lets a Material Preview viewport settle)


def log(*a):
    print("[reel]", *a, flush=True)


# ------------------------------------------------------------------ easing

def clamp01(t):
    return 0.0 if t < 0 else 1.0 if t > 1 else t


def smooth(t):
    t = clamp01(t)
    return t * t * (3 - 2 * t)


def smoother(t):
    t = clamp01(t)
    return t * t * t * (t * (6 * t - 15) + 10)


def ease_out(t, p=3):
    return 1 - (1 - clamp01(t)) ** p


def ease_in(t, p=2):
    return clamp01(t) ** p


def lerp(a, b, t):
    return a + (b - a) * t


def frames(seconds):
    return max(1, int(round(seconds * FPS)))


# ------------------------------------------------------------------ window

def win():
    return bpy.context.window_manager.windows[0]


def view3d():
    a = max((a for a in win().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return a, next(r for r in a.regions if r.type == 'WINDOW')


def space():
    return view3d()[0].spaces.active


def rv3d():
    return space().region_3d


def override(**kw):
    a, r = view3d()
    return bpy.context.temp_override(window=win(), screen=win().screen, area=a, region=r, **kw)


def single_viewport(ui_scale=1.0):
    """One 3D viewport filling the window under the top bar (no timeline,
    outliner or properties), toolbar and navigation buttons hidden."""
    bpy.context.preferences.view.ui_scale = ui_scale
    for _ in range(8):
        others = [a for a in win().screen.areas if a.type != 'VIEW_3D']
        if not others:
            break
        a = others[0]
        with bpy.context.temp_override(window=win(), screen=win().screen, area=a):
            bpy.ops.screen.area_close()


def configure_viewport():
    """Run in a later tick than single_viewport(): the closed areas are only
    freed on the next redraw, and touching a stale space then crashes."""
    sp = space()
    sp.show_region_toolbar = False
    sp.show_region_ui = False
    sp.show_gizmo_navigate = False
    sp.overlay.show_cursor = False
    sp.overlay.show_text = True
    sp.clip_start, sp.clip_end = 1.0, 20000.0
    sp.shading.type = 'SOLID'
    sp.shading.color_type = 'OBJECT'


def sidebar_first(category):
    """The active sidebar tab is read-only from Python, but a fresh sidebar
    opens on its first tab: re-register every other sidebar panel so that
    `category` comes first."""
    others = [c for c in bpy.types.Panel.__subclasses__()
              if getattr(c, "bl_space_type", "") == 'VIEW_3D' and getattr(c, "bl_region_type", "") == 'UI'
              and getattr(c, "bl_category", "") != category and c.is_registered]
    order, seen = [], set()

    def visit(c):
        if c in seen:
            return
        seen.add(c)
        order.append(c)
        for k in others:
            if getattr(k, "bl_parent_id", "") == c.__name__:
                visit(k)
    for c in others:
        if not getattr(c, "bl_parent_id", ""):
            visit(c)
    for c in reversed(order):
        bpy.utils.unregister_class(c)
    for c in order:
        bpy.utils.register_class(c)


def only_panels(module, keep, prefix):
    """Declutter a sidebar tab for the camera: unregister the add-on's panels
    (classes in `module` whose name starts with `prefix`) except `keep`, and
    re-register those open. Parent panels must come before their children in
    `keep`."""
    classes = [c for c in vars(module).values()
               if isinstance(c, type) and issubclass(c, bpy.types.Panel) and c.__name__.startswith(prefix)]
    for c in classes:
        if c.is_registered:
            bpy.utils.unregister_class(c)
    by_name = {c.__name__: c for c in classes}
    for name in keep:
        c = by_name[name]
        c.bl_options = set(getattr(c, "bl_options", set())) - {'DEFAULT_CLOSED'}
        bpy.utils.register_class(c)


def set_units_mm(scene=None):
    us = (scene or bpy.context.scene).unit_settings
    us.system = 'METRIC'
    us.scale_length = 0.001
    us.length_unit = 'MILLIMETERS'


# ------------------------------------------------------------------ camera

class Pose:
    """A viewport camera: orbit target, distance, rotation (Euler degrees, as
    the view's X/Y/Z rotation) and orthographic or not."""

    def __init__(self, target, dist, rot, ortho=False):
        self.target = Vector(target)
        self.dist = float(dist)
        self.quat = rot if isinstance(rot, Quaternion) else \
            Euler([math.radians(x) for x in rot]).to_quaternion()
        self.ortho = ortho

    def apply(self):
        r = rv3d()
        r.view_location = self.target
        r.view_distance = self.dist
        r.view_rotation = self.quat
        r.view_perspective = 'ORTHO' if self.ortho else 'PERSP'

    def lerp(self, other, t):
        return Pose(self.target.lerp(other.target, t),
                    math.exp(lerp(math.log(self.dist), math.log(other.dist), t)),
                    self.quat.slerp(other.quat, t), other.ortho if t >= 1 else self.ortho)

    @staticmethod
    def current():
        r = rv3d()
        return Pose(r.view_location.copy(), r.view_distance, r.view_rotation.copy(),
                    r.view_perspective == 'ORTHO')


def orbit(target, dist, elev, az, ortho=False):
    """Pose from elevation above the horizon-ish (Blender's view X rotation)
    and azimuth (view Z rotation), in degrees."""
    return Pose(target, dist, (elev, 0.0, az), ortho)


# ------------------------------------------------------------------ overlay

_overlays = []          # callables drawn in POST_PIXEL of the viewport
_handle = None


def add_overlay(fn):
    global _handle
    _overlays.append(fn)
    if _handle is None:
        def draw():
            for f in list(_overlays):
                try:
                    f()
                except Exception:
                    traceback.print_exc()
                    _overlays.remove(f)
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')
    return fn


def remove_overlay(fn):
    if fn in _overlays:
        _overlays.remove(fn)


# ------------------------------------------------------------------ runner

class Reel:
    """Plays segments (name, generator function) and captures their frames.

        Reel(out_dir, [("intro", intro), ...], setup=[step1, step2]).start(only={"intro"})

    Each setup step runs in its own timer tick.
    """

    def __init__(self, out, segments, setup=None):
        self.out = out
        self.segments = list(segments)
        self.setup = list(setup or [])
        self.gen = None
        self.name = None
        self.n = 0
        self.t0 = time.perf_counter()

    def start(self, only=None):
        if only:
            unknown = set(only) - {n for n, _ in self.segments}
            if unknown:
                raise SystemExit(f"unknown segments: {sorted(unknown)}")
        self.only = only
        self.queue = list(self.segments)
        bpy.app.timers.register(self._tick, first_interval=1.5)

    def shot(self, path):
        for _ in range(DRAWS):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        with bpy.context.temp_override(window=win(), screen=win().screen):
            bpy.ops.screen.screenshot(filepath=path)

    def _next_segment(self):
        while self.queue:
            name, fn = self.queue.pop(0)
            # segments not asked for still run (their state carries over), but
            # fast: frames are skipped
            self.name, self.n = name, 0
            self.capture = not self.only or name in self.only
            if self.capture:
                d = os.path.join(self.out, name)
                shutil.rmtree(d, ignore_errors=True)
                os.makedirs(d)
            log(f"segment {name}{'' if self.capture else ' (not captured)'}")
            self.gen = fn()
            return True
        return False

    def _tick(self):
        try:
            if self.setup:
                r = self.setup.pop(0)()
                return r if isinstance(r, float) else 0.5
            if self.gen is None and not self._next_segment():
                log(f"done in {time.perf_counter() - self.t0:.0f} s")
                os._exit(0)
            try:
                r = next(self.gen)
            except StopIteration:
                log(f"  {self.name}: {self.n} frames")
                self.gen = None
                return 0.05
            if isinstance(r, (int, float)) and not isinstance(r, bool):
                return float(r)
            if self.capture:
                self.shot(os.path.join(self.out, self.name, "%04d.png" % self.n))
            else:
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1) if r == "draw" else None
            self.n += 1
            return 0.001
        except Exception:
            traceback.print_exc()
            os._exit(1)


def args():
    """Arguments after '--' on Blender's command line."""
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
