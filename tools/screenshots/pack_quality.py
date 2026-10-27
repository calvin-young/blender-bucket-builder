# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshots of the Pack panel (Part Quality at 100 % and 0 %) and the
Build Report beside a packed build of synthetic parts. Not a test: for
reviewing the interface without loading Blender by hand.

    Xvfb :93 -screen 0 1600x1250x24 -fbdir /tmp/fb &
    DISPLAY=:93 BUCKET_BUILDER_XVFB_FB=/tmp/fb/Xvfb_screen0 SDL_VIDEO_FORCE_EGL=1 \
        blender --enable-event-simulate --python tools/screenshots/pack_quality.py -- <out dir>

(On a desktop with a real display, leave out Xvfb and the two variables:
the screenshot falls back to Blender's own screenshot operator.)  The
add-on must be installed and enabled, the splash screen off.
"""
import importlib
import os
import sys
import time
import traceback

import bpy
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tests'))
import gui_common  # noqa: E402

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
S = importlib.import_module(PKG + '.pack.synthetic')
OUT = sys.argv[sys.argv.index('--') + 1]
STATE = {}


def log(*a):
    print('[shots]', *a, flush=True)


def win():
    return bpy.context.window_manager.windows[0]


def area():
    return next(a for a in win().screen.areas if a.type == 'VIEW_3D')


def shot(name):
    gui_common.screenshot(win(), os.path.join(OUT, name + '.png'))
    log('screenshot', name)


def make(mesh, name, n, at):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in np.asarray(mesh.verts, float)], [], [tuple(t) for t in np.asarray(mesh.tris)])
    me.update()
    sc = win().scene
    for i in range(n):
        o = bpy.data.objects.new('%s.%03d' % (name, i + 1), me)
        sc.collection.objects.link(o)
        o.location = at(i)
        o.select_set(True)


def step_setup():
    keep = ('BUCKETBUILDER_PT_pack', 'BUCKETBUILDER_PT_main', 'BUCKETBUILDER_PT_report')
    for cls in list(bc.ui.CLASSES):
        if issubclass(cls, bpy.types.Panel):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
            if cls.__name__ in keep:
                cls.bl_options = set()          # (open, for the picture)
                bpy.utils.register_class(cls)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    make(S.prism([(0, 0), (80, 0), (40, 20)], 80, 'ridge'), 'ridge', 10, lambda i: (420 + 90 * (i % 5), 0, 90 * (i // 5)))
    make(S.plate(150, 100, 4), 'plate', 4, lambda i: (420, 200 + 110 * i, 0))
    make(S.hex_nut(), 'nut', 40, lambda i: (-100 - 25 * (i % 8), 25 * (i // 8), 0))
    sc = win().scene
    st = sc.bucket_builder
    st.detect_collisions = True
    st.monitor_volume = True
    st.report_density = True
    st.pack_time = 25.0
    st.pack_mode = 'FREE'
    st.pack_quality = 100
    st.clearance_mm = st.pack_gap - 0.01
    sp = area().spaces.active
    sp.show_region_ui = True
    sp.overlay.show_floor = False
    from mathutils import Euler, Vector
    r3 = sp.region_3d
    r3.view_location = Vector((190, 142, 40))
    r3.view_rotation = Euler((0.95, 0, 0.55)).to_quaternion()
    r3.view_distance = 650
    sp.clip_end = 10000
    return 2.0


def step_tab():
    for r in area().regions:
        if r.type == 'UI':
            r.active_panel_category = 'Bucket'
    return 1.0


def start(q):
    def f():
        st = win().scene.bucket_builder
        st.pack_quality = q
        for o in win().scene.objects:
            o.select_set(True)
        with bpy.context.temp_override(window=win(), area=area()):
            r = bpy.ops.bucketbuilder.pack('INVOKE_DEFAULT', selected_only=False)
        log('pack at', q, '->', r)
        STATE['started'] = time.perf_counter()
        return 2.0
    return f


def wait(name):
    def f():
        if bc.packing.JOB['running']:
            if time.perf_counter() - STATE['started'] > 600:
                raise RuntimeError('pack did not end')
            return 'again'
        return 6.0                          # (let the report finish)
    f.name = name
    return f


STEPS = [step_setup, step_tab, start(100), wait('q100'), lambda: shot('pack_quality_100') or 0.5,
         start(0), wait('q0'), lambda: shot('pack_quality_0') or 0.5]


def runner():
    if not STEPS:
        log('done')
        os._exit(0)
    step = STEPS[0]
    try:
        r = step()
    except Exception:
        traceback.print_exc()
        shot('failure')
        os._exit(1)
    if r == 'again':
        return 1.0
    STEPS.pop(0)
    return r or 0.2


bpy.app.timers.register(runner, first_interval=1.0)
