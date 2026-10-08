# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: problem navigation, printer profiles, housekeeping."""

import math

import bpy
import numpy as np
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator

from . import monitor, props


# ---------------------------------------------------------------------------
# navigation
# ---------------------------------------------------------------------------

def _view3d_space(context):
    space = context.space_data
    if space is not None and space.type == 'VIEW_3D':
        return space
    area = context.area
    if area is not None and area.type == 'VIEW_3D':
        return area.spaces.active
    for area in context.screen.areas if context.screen else ():
        if area.type == 'VIEW_3D':
            return area.spaces.active
    return None


def frame(context, center, radius):
    """Point the 3D viewport at a sphere (no smooth-view; this is instant)."""
    space = _view3d_space(context)
    if space is None:
        return False
    rv3d = space.region_3d
    if rv3d is None:
        return False
    if rv3d.view_perspective == 'CAMERA':
        rv3d.view_perspective = 'PERSP'
    half = math.atan(18.0 / max(space.lens, 1.0))
    rv3d.view_location = (float(center[0]), float(center[1]), float(center[2]))
    rv3d.view_distance = max(float(radius), 1e-6) / math.tan(half) * 2.2
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()
    return True


def _select_parts(context, mon, problem):
    """Make the parts of a problem the selection (so G / R act on them)."""
    uids = [u for u in problem['key'][1:]]
    by_uid = {monitor._uid(o): o for o in context.view_layer.objects}
    targets = [by_uid[u] for u in uids if u in by_uid]
    if not targets or context.mode != 'OBJECT':
        return
    for o in context.selected_objects:
        o.select_set(False)
    for o in targets:
        try:
            o.select_set(True)
        except RuntimeError:
            pass
    context.view_layer.objects.active = targets[-1]


def _goto(context, index, select):
    scene = context.scene
    st = props.settings(scene)
    mon = monitor.get(scene)
    if mon is None:
        return None
    problems = mon.problems()
    if not problems:
        st.problem_index = -1
        return None
    index %= len(problems)
    pr = problems[index]
    st.problem_index = index
    min_r = 4.0 / mon.mm_per_unit
    frame(context, pr['center'], max(pr['radius'], min_r))
    if select:
        _select_parts(context, mon, pr)
    return pr


def describe(pr):
    if pr['kind'] == 'COLLIDE':
        if pr['inside'] == 1:
            return f"{pr['a']} is inside {pr['b']}"
        if pr['inside'] == 2:
            return f"{pr['b']} is inside {pr['a']}"
        return f"{pr['a']} collides with {pr['b']}"
    if pr['kind'] == 'CLEAR':
        approx = "~" if pr['approx'] else ""
        return f"{pr['a']} / {pr['b']}: {approx}{pr['dist_mm']:.2f} mm apart"
    if pr['kind'] == 'PARTIAL':
        return f"{pr['a']} reaches outside the build volume"
    return f"{pr['a']} is outside the build volume"


class BUCKETBUILDER_OT_focus_problem(Operator):
    """Frame this problem in the viewport and select the parts involved"""
    bl_idname = "bucketbuilder.focus_problem"
    bl_label = "Go to Problem"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0)
    select: BoolProperty(name="Select Parts", default=True)

    def execute(self, context):
        pr = _goto(context, self.index, self.select)
        if pr is None:
            self.report({'INFO'}, "No problems")
            return {'CANCELLED'}
        self.report({'INFO'}, describe(pr))
        return {'FINISHED'}


class BUCKETBUILDER_OT_step_problem(Operator):
    """Jump to the next or previous problem"""
    bl_idname = "bucketbuilder.step_problem"
    bl_label = "Next Problem"
    bl_options = {'REGISTER', 'UNDO'}

    direction: IntProperty(default=1)
    select: BoolProperty(name="Select Parts", default=True)

    def execute(self, context):
        st = props.settings(context.scene)
        index = st.problem_index + self.direction
        if st.problem_index < 0 and self.direction < 0:
            index = -1
        pr = _goto(context, index, self.select)
        if pr is None:
            self.report({'INFO'}, "No problems")
            return {'CANCELLED'}
        self.report({'INFO'}, describe(pr))
        return {'FINISHED'}


class BUCKETBUILDER_OT_frame_volume(Operator):
    """Frame the whole build volume in the viewport"""
    bl_idname = "bucketbuilder.frame_volume"
    bl_label = "View Build Volume"

    def execute(self, context):
        scene = context.scene
        st = props.settings(scene)
        mon = monitor.get(scene)
        if mon is None:
            tmp = monitor.Monitor(scene)
            tmp.mm_per_unit, _ = tmp._resolve_units(scene, st)
            mon = tmp
        lo, hi = mon.volume_box(st)
        frame(context, 0.5 * (lo + hi), 0.5 * float(np.linalg.norm(hi - lo)) * 0.6)
        return {'FINISHED'}


class BUCKETBUILDER_OT_recheck(Operator):
    """Discard all cached data and analyse the scene again"""
    bl_idname = "bucketbuilder.recheck"
    bl_label = "Recheck Everything"

    def execute(self, context):
        monitor.force_recheck(context.scene)
        return {'FINISHED'}


class BUCKETBUILDER_OT_ignore(Operator):
    """Include or exclude the selected objects from the checks"""
    bl_idname = "bucketbuilder.ignore"
    bl_label = "Ignore Selected"
    bl_options = {'REGISTER', 'UNDO'}

    ignore: BoolProperty(default=True)

    def execute(self, context):
        n = 0
        for o in context.selected_objects:
            if o.type == 'MESH' and o.bucket_builder_ignore != self.ignore:
                o.bucket_builder_ignore = self.ignore
                n += 1
        monitor.on_settings_changed(context.scene)
        self.report({'INFO'}, f"{n} object(s) {'ignored' if self.ignore else 'included'}")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# printer profiles
# ---------------------------------------------------------------------------

def profile_items(context):
    """[(name, (x, y, z)), ...] from the library, or the defaults before it exists."""
    p = props.prefs(context)
    if p is None or (len(p.profiles) == 0 and not p.profiles_seeded):
        return list(props.DEFAULT_PROFILES)
    return [(prof.name, tuple(prof.size)) for prof in p.profiles]


def _library(context):
    p = props.prefs(context)
    props.seed_profiles(p)
    return p


def _mark_prefs_dirty(context):
    try:
        context.preferences.is_dirty = True
    except Exception:
        pass


def apply_volume(st, name, size):
    st.lock_printer_name = True
    st.volume_size = size
    st.printer = name
    st.lock_printer_name = False


class BUCKETBUILDER_OT_profile_apply(Operator):
    """Use this printer's build volume"""
    bl_idname = "bucketbuilder.profile_apply"
    bl_label = "Select Printer"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0)

    def execute(self, context):
        items = profile_items(context)
        if not 0 <= self.index < len(items):
            return {'CANCELLED'}
        name, size = items[self.index]
        apply_volume(props.settings(context.scene), name, size)
        monitor.on_settings_changed(context.scene)
        return {'FINISHED'}


class BUCKETBUILDER_OT_profile_add(Operator):
    """Save the current build volume as a new printer profile"""
    bl_idname = "bucketbuilder.profile_add"
    bl_label = "Save as New Printer"

    name: StringProperty(name="Name", default="Custom Printer")

    def invoke(self, context, event):
        st = props.settings(context.scene)
        if st.printer and st.printer != "Custom":
            self.name = st.printer + " (copy)"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        p = _library(context)
        if p is None:
            self.report({'ERROR'}, "Add-on preferences are not available")
            return {'CANCELLED'}
        st = props.settings(context.scene)
        name = self.name.strip() or "Custom Printer"
        existing = {prof.name for prof in p.profiles}
        base, n = name, 2
        while name in existing:
            name = f"{base} {n}"
            n += 1
        item = p.profiles.add()
        item.name = name
        item.size = st.volume_size
        apply_volume(st, name, tuple(st.volume_size))
        _mark_prefs_dirty(context)
        self.report({'INFO'}, f"Saved printer profile '{name}'")
        return {'FINISHED'}


class BUCKETBUILDER_OT_profile_update(Operator):
    """Store the current build volume in the selected printer profile"""
    bl_idname = "bucketbuilder.profile_update"
    bl_label = "Update Printer Profile"

    def execute(self, context):
        p = _library(context)
        st = props.settings(context.scene)
        if p is None:
            return {'CANCELLED'}
        for prof in p.profiles:
            if prof.name == st.printer:
                prof.size = st.volume_size
                _mark_prefs_dirty(context)
                self.report({'INFO'}, f"Updated '{prof.name}'")
                return {'FINISHED'}
        self.report({'WARNING'}, "Pick a printer first, or save the volume as a new printer")
        return {'CANCELLED'}


class BUCKETBUILDER_OT_profile_remove(Operator):
    """Delete the selected printer profile from the library"""
    bl_idname = "bucketbuilder.profile_remove"
    bl_label = "Delete Printer Profile"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        p = _library(context)
        st = props.settings(context.scene)
        if p is None:
            return {'CANCELLED'}
        for i, prof in enumerate(p.profiles):
            if prof.name == st.printer:
                p.profiles.remove(i)
                st.lock_printer_name = True
                st.printer = "Custom"
                st.lock_printer_name = False
                _mark_prefs_dirty(context)
                return {'FINISHED'}
        self.report({'WARNING'}, "The current volume is not a saved printer")
        return {'CANCELLED'}


class BUCKETBUILDER_OT_profile_rename(Operator):
    """Rename the selected printer profile"""
    bl_idname = "bucketbuilder.profile_rename"
    bl_label = "Rename Printer Profile"

    name: StringProperty(name="Name")

    def invoke(self, context, event):
        self.name = props.settings(context.scene).printer
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        p = _library(context)
        st = props.settings(context.scene)
        name = self.name.strip()
        if p is None or not name:
            return {'CANCELLED'}
        for prof in p.profiles:
            if prof.name == st.printer:
                prof.name = name
                st.lock_printer_name = True
                st.printer = name
                st.lock_printer_name = False
                _mark_prefs_dirty(context)
                return {'FINISHED'}
        self.report({'WARNING'}, "The current volume is not a saved printer")
        return {'CANCELLED'}


class BUCKETBUILDER_OT_profiles_reset(Operator):
    """Add the built-in printer profiles again (existing ones are kept)"""
    bl_idname = "bucketbuilder.profiles_reset"
    bl_label = "Restore Default Printers"

    def execute(self, context):
        p = _library(context)
        if p is None:
            return {'CANCELLED'}
        existing = {prof.name for prof in p.profiles}
        for name, size in props.DEFAULT_PROFILES:
            if name not in existing:
                item = p.profiles.add()
                item.name = name
                item.size = size
        _mark_prefs_dirty(context)
        return {'FINISHED'}


class BUCKETBUILDER_MT_printers(Menu):
    bl_label = "Printer"
    bl_idname = "BUCKETBUILDER_MT_printers"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        for i, (name, size) in enumerate(profile_items(context)):
            text = f"{name}   ({size[0]:g} x {size[1]:g} x {size[2]:g} mm)"
            op = layout.operator("bucketbuilder.profile_apply", text=text,
                                 icon='CHECKMARK' if name == st.printer else 'BLANK1')
            op.index = i
        layout.separator()
        layout.operator("bucketbuilder.profile_add", icon='ADD')
        layout.operator("bucketbuilder.profile_update", icon='FILE_TICK')
        layout.operator("bucketbuilder.profile_rename")
        layout.operator("bucketbuilder.profile_remove", icon='REMOVE')
        layout.separator()
        layout.operator("bucketbuilder.profiles_reset")


CLASSES = (
    BUCKETBUILDER_OT_focus_problem,
    BUCKETBUILDER_OT_step_problem,
    BUCKETBUILDER_OT_frame_volume,
    BUCKETBUILDER_OT_recheck,
    BUCKETBUILDER_OT_ignore,
    BUCKETBUILDER_OT_profile_apply,
    BUCKETBUILDER_OT_profile_add,
    BUCKETBUILDER_OT_profile_update,
    BUCKETBUILDER_OT_profile_remove,
    BUCKETBUILDER_OT_profile_rename,
    BUCKETBUILDER_OT_profiles_reset,
    BUCKETBUILDER_MT_printers,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
