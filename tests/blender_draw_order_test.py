# SPDX-License-Identifier: GPL-3.0-or-later
"""The draw order shared with other print add-ons (#28): Bucket Builder enters the registry at layer
100, re-adds its POST_VIEW handler when asked, and calls the re-add of an add-on above it when it
registers. Run with the add-on installed:

    blender --background --python tests/blender_draw_order_test.py
"""
import importlib
import sys

import bpy

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
ov = bc.overlay


def main():
    layers = sys.modules[ov.DRAW_ORDER].layers
    assert layers["bucket_builder"][0] == 100, layers
    before = ov._handles[0][0]
    layers["bucket_builder"][1]()                       # (what Crosscut calls when it loads later)
    assert ov._handles[0][0] is not before, 'the view handler was not re-added'
    # an add-on above Bucket Builder is re-added when Bucket Builder registers; one that fails is dropped
    calls = []
    layers["stand_in_above"] = (200, lambda: calls.append(1))
    layers["stand_in_gone"] = (300, lambda: 1 / 0)
    layers["stand_in_below"] = (0, lambda: calls.append(0))
    ov.unregister()
    assert "bucket_builder" not in layers
    ov.register()
    assert calls == [1], calls
    assert "stand_in_gone" not in layers and "bucket_builder" in layers, layers
    for k in ("stand_in_above", "stand_in_below"):
        layers.pop(k)
    print("DRAW ORDER TEST OK")


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print("DRAW ORDER TEST FAILED")
    sys.exit(1)
