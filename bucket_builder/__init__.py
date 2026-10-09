# SPDX-License-Identifier: GPL-3.0-or-later
"""Bucket Builder: live collision, clearance and build-volume monitor for
3D-print build preparation (aimed at HP Multi Jet Fusion nesting)."""

bl_info = {
    "name": "Bucket Builder",
    "author": "Bucket Builder contributors",
    "version": (1, 0, 1),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Bucket",
    "description": "Live collision, clearance and build-volume monitor for 3D-print build preparation",
    "category": "3D View",
}

if "bpy" in locals():
    # "Reload Scripts": refresh sub-modules, dependencies first
    import importlib
    for _name in ("core.tritri", "core.arena", "core.bvh", "core.narrow", "core.world", "core",
                  "props", "monitor", "overlay", "ops", "ui"):
        importlib.reload(importlib.import_module("." + _name, __package__))

import bpy

from . import monitor, ops, overlay, props, ui


def _seed_later():
    try:
        props.seed_profiles(props.prefs())
    except Exception:
        pass
    return None


def register():
    props.register()
    ops.register()
    ui.register()
    overlay.register()
    monitor.register()
    # the preferences of a freshly enabled add-on are not reachable inside register()
    bpy.app.timers.register(_seed_later, first_interval=0.2)


def unregister():
    if bpy.app.timers.is_registered(_seed_later):
        bpy.app.timers.unregister(_seed_later)
    monitor.unregister()
    overlay.unregister()
    ui.unregister()
    ops.unregister()
    props.unregister()
