# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: problem navigation, printer profiles, housekeeping."""

import math
import os
import platform
import sys
import time

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


def _parts_of(context, problem):
    """The objects a problem is about (those still in the view layer)."""
    by_uid = {monitor._uid(o): o for o in context.view_layer.objects if o is not None}
    return [by_uid[u] for u in problem['key'][1:] if u in by_uid]


def _select_parts(context, targets):
    """Make the given parts the selection (so G / R act on them).  Returns
    those that could be selected: a hidden part cannot."""
    if not targets or context.mode != 'OBJECT':
        return []
    for o in context.selected_objects:
        o.select_set(False)
    done = []
    for o in targets:
        try:
            o.select_set(True)
            if o.select_get():
                done.append(o)
        except RuntimeError:
            pass
    if done:
        context.view_layer.objects.active = done[-1]
    return done


def _view3d_area(context):
    area = context.area
    if area is not None and area.type == 'VIEW_3D':
        return area
    for area in context.screen.areas if context.screen else ():
        if area.type == 'VIEW_3D':
            return area
    return None


def local_view(context, targets):
    """Show ``targets`` on their own in the 3D viewport (Blender's local
    view), or everything again if there are none.  The selection is changed
    to the targets.  Returns True if the viewport is in local view afterwards.
    """
    area = _view3d_area(context)
    if area is None or context.mode != 'OBJECT':
        return False
    space = area.spaces.active
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return False
    try:
        with context.temp_override(area=area, region=region, space_data=space):
            if space.local_view is not None:
                bpy.ops.view3d.localview(frame_selected=False)      # out of the one we are in
            if targets and _select_parts(bpy.context, targets):
                bpy.ops.view3d.localview(frame_selected=False)      # into one with the selection
    except RuntimeError:                  # no viewport that could do it
        return False
    return space.local_view is not None


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
    st.problem_key = pr['id']
    targets = _parts_of(context, pr)
    if st.isolate:
        local_view(context, targets)
    elif select:
        _select_parts(context, targets)
    min_r = 4.0 / mon.mm_per_unit
    frame(context, pr['center'], max(pr['radius'], min_r))
    return pr


def _find_problem(mon, kind, a, b):
    """(index, problem) of the problem of that kind between the parts with
    those names, or (-1, None).  The buttons of a row name their problem
    this way: the list may have changed since the row was drawn."""
    for i, pr in enumerate(mon.problems()):
        if pr['key'][0] == kind and pr['names'] == (a, b):
            return i, pr
    return -1, None


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
    if pr['kind'] == 'WALL':
        return f"{pr['a']} is {pr['dist_mm']:.2f} mm from a side wall"
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
        mon = monitor.get(context.scene)
        problems = mon.problems() if mon is not None else []
        # from the problem the user is at; if that one is gone (solved, most
        # likely), from where it was in the list
        at = mon.active_index(st) if mon is not None else -1
        if at < 0:
            at = st.problem_index - (1 if self.direction > 0 and st.problem_index >= 0 else 0)
        index = at + self.direction
        if st.problem_index < 0 and self.direction < 0:
            index = -1
        # problems that are ignored are passed over (unless there are no others)
        n = len(problems)
        if n and any(not pr['ignored'] for pr in problems):
            index %= n
            while problems[index]['ignored']:
                index = (index + self.direction) % n
        pr = _goto(context, index, self.select)
        if pr is None:
            self.report({'INFO'}, "No problems")
            return {'CANCELLED'}
        self.report({'INFO'}, describe(pr))
        return {'FINISHED'}


class BUCKETBUILDER_OT_isolate(Operator):
    """Show the parts of a problem on their own. While this is on, going to a problem puts its parts in local view; switch it off to see the whole build again"""
    bl_idname = "bucketbuilder.isolate"
    bl_label = "Isolate"
    bl_options = {'REGISTER', 'UNDO'}     # (as Blender's own local view: it changes object flags)

    def execute(self, context):
        st = props.settings(context.scene)
        mon = monitor.get(context.scene)
        if context.mode != 'OBJECT':
            self.report({'WARNING'}, "Isolating parts needs Object Mode")
            return {'CANCELLED'}
        if st.isolate:
            st.isolate = False
            local_view(context, [])
            monitor.tag_redraw_all()
            return {'FINISHED'}
        st.isolate = True
        at = mon.active_index(st) if mon is not None else -1
        if at >= 0:
            _goto(context, at, True)
        else:
            self.report({'INFO'}, "Pick a problem to see its parts on their own")
        monitor.tag_redraw_all()
        return {'FINISHED'}


class _RowOperator:
    """What the buttons in a row of the list of problems have in common: they
    name their problem by its kind and the names of its parts."""
    kind: StringProperty(options={'HIDDEN'})
    a: StringProperty(options={'HIDDEN'})
    b: StringProperty(options={'HIDDEN'})

    def find(self, context):
        mon = monitor.get(context.scene)
        if mon is None:
            return None, -1, None
        index, pr = _find_problem(mon, self.kind, self.a, self.b)
        if pr is None:
            self.report({'INFO'}, "That problem is gone")
        return mon, index, pr


class BUCKETBUILDER_OT_isolate_problem(_RowOperator, Operator):
    """Show the parts of this problem on their own (local view), or everything again"""
    bl_idname = "bucketbuilder.isolate_problem"
    bl_label = "Isolate Problem"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = props.settings(context.scene)
        if context.mode != 'OBJECT':
            self.report({'WARNING'}, "Isolating parts needs Object Mode")
            return {'CANCELLED'}
        mon, index, pr = self.find(context)
        if pr is None:
            return {'CANCELLED'}
        if st.isolate and mon.active_index(st) == index:
            st.isolate = False
            local_view(context, [])
        else:
            st.isolate = True
            _goto(context, index, True)
        monitor.tag_redraw_all()
        return {'FINISHED'}


def prune_ignored(scene):
    """Forget ignored problems whose parts are no longer in the scene.  (An
    entry points at its parts, and what is pointed at is kept in the file.)"""
    st = props.settings(scene)
    if st is None:
        return 0
    here = set(scene.objects)
    gone = [i for i, item in enumerate(st.ignored_problems)
            if item.a is None or item.a not in here
            or (item.kind == 'PAIR' and (item.b is None or item.b not in here))]
    for i in reversed(gone):
        st.ignored_problems.remove(i)
    return len(gone)


def _ignore_entry(st, kind, objs):
    """Index of the entry for a problem in the scene's list, or -1."""
    for i, item in enumerate(st.ignored_problems):
        if item.kind != kind:
            continue
        if kind == 'PAIR':
            if {item.a, item.b} == set(objs):
                return i
        elif item.a == objs[0]:
            return i
    return -1


_KIND_NAME = {'P': 'PAIR', 'V': 'VOLUME', 'W': 'WALL'}


class BUCKETBUILDER_OT_ignore_problem(_RowOperator, Operator):
    """Ignore this problem: it stays in the list, greyed, and no longer counts. It counts again as soon as it changes, for instance when one of the parts is moved, unless it is locked"""
    bl_idname = "bucketbuilder.ignore_problem"
    bl_label = "Ignore Problem"
    bl_options = {'REGISTER', 'UNDO'}

    lock: BoolProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        if properties.lock:
            return ("Ignore this problem for good: whatever happens to the parts, a problem of "
                    "this kind between them does not count. Click again to ignore it only for "
                    "as long as it stays as it is")
        return cls.__doc__

    def execute(self, context):
        scene = context.scene
        st = props.settings(scene)
        mon, index, pr = self.find(context)
        if pr is None:
            return {'CANCELLED'}
        objs = _parts_of(context, pr)
        if len(objs) != len(pr['key']) - 1:
            return {'CANCELLED'}
        prune_ignored(scene)
        kind = _KIND_NAME[self.kind]
        i = _ignore_entry(st, kind, objs)
        level = pr['ignored']
        if self.lock:
            lock = level != 2             # locked -> ignored as it is now; anything else -> locked
            keep = True
        else:
            lock = False
            keep = level == 0             # the plain button switches ignoring on and off
        if not keep:
            if i >= 0:
                st.ignored_problems.remove(i)
            self.report({'INFO'}, "Counts again: " + describe(pr))
        else:
            item = st.ignored_problems[i] if i >= 0 else st.ignored_problems.add()
            item.kind = kind
            item.a = objs[0]
            item.b = objs[1] if len(objs) > 1 else None
            item.lock = lock
            item.sig, item.geo, item.aux = mon.signature(self.kind, *pr['slots'])
            self.report({'INFO'}, ("Ignored for good: " if lock else "Ignored: ") + describe(pr))
        mon._read_ignored(st)             # (at once, so that the list is right when it is redrawn)
        monitor.tag_redraw_all()
        return {'FINISHED'}


class BUCKETBUILDER_OT_count_all_problems(Operator):
    """Stop ignoring problems: every one of them counts again"""
    bl_idname = "bucketbuilder.count_all_problems"
    bl_label = "Ignore None"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        st = props.settings(scene)
        n = len(st.ignored_problems)
        st.ignored_problems.clear()
        mon = monitor.get(scene)
        if mon is not None:
            mon._read_ignored(st)
        monitor.tag_redraw_all()
        self.report({'INFO'}, f"{n} ignored problem(s) forgotten")
        return {'FINISHED'}


class BUCKETBUILDER_OT_status(Operator):
    """Bucket Builder watches the build while you arrange it. What it checks is switched on below: collisions, the build volume, or both"""
    bl_idname = "bucketbuilder.status"
    bl_label = "Monitoring On"

    def execute(self, context):
        return {'CANCELLED'}              # (a sign, not a switch)


class BUCKETBUILDER_OT_export_3mf(Operator):
    """Write the build to a 3MF file for the printer. Not in this version: it is what comes next"""
    bl_idname = "bucketbuilder.export_3mf"
    bl_label = "Export 3MF"

    def execute(self, context):
        def draw(menu, _context):
            col = menu.layout.column(align=True)
            col.label(text="Not in this version yet.")
            col.label(text="Writing the build to a 3MF file is what comes next.")

        if context.window is not None and not bpy.app.background:
            context.window_manager.popup_menu(draw, title="Export 3MF", icon='EXPORT')
        self.report({'INFO'}, "3MF export is not in this version yet: it is what comes next")
        return {'CANCELLED'}


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
    """Leave the selected objects out of the checks, or take them in again"""
    bl_idname = "bucketbuilder.ignore"
    bl_label = "Ignore Selected"
    bl_options = {'REGISTER', 'UNDO'}

    ignore: BoolProperty(default=True)

    def execute(self, context):
        n = 0
        for o in context.selected_objects:
            # (any kind of object: what is not a mesh is not checked, but it
            # is reported as such unless it is ignored)
            if o.bucket_builder_ignore != self.ignore:
                o.bucket_builder_ignore = self.ignore
                n += 1
        monitor.on_settings_changed(context.scene)
        self.report({'INFO'}, f"{n} object(s) {'ignored' if self.ignore else 'included'}")
        return {'FINISHED'}


def ignored_objects(scene):
    """The scene's objects that are left out of the checks on purpose."""
    return [o for o in scene.objects if o.bucket_builder_ignore]


class BUCKETBUILDER_OT_include(Operator):
    """Check this object again"""
    bl_idname = "bucketbuilder.include"
    bl_label = "Include"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        obj = context.scene.objects.get(self.name)
        if obj is None:
            return {'CANCELLED'}
        obj.bucket_builder_ignore = False
        monitor.on_settings_changed(context.scene)
        return {'FINISHED'}


class BUCKETBUILDER_OT_include_all(Operator):
    """Check every object again: nothing is ignored any more"""
    bl_idname = "bucketbuilder.include_all"
    bl_label = "Include All"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objs = ignored_objects(context.scene)
        for o in objs:
            o.bucket_builder_ignore = False
        monitor.on_settings_changed(context.scene)
        self.report({'INFO'}, f"{len(objs)} object(s) included")
        return {'FINISHED'}


def triangles_text(n):
    """A triangle count for people: 36,420 triangles, 31.4 M triangles."""
    if n < 1000000:
        return f"{n:,} triangles"
    return f"{n / 1e6:.1f} M triangles"


def _spread(values, unit=' ms'):
    if not values:
        return "no figures yet"
    v = sorted(values)
    return f"median {v[len(v) // 2]:.1f}{unit}, longest {v[-1]:.1f}{unit}"


def addon_version():
    """The add-on's version as text.  (Blender takes ``bl_info`` away from an
    extension's module; the manifest is where it reads the version.)"""
    try:
        import addon_utils
        info = addon_utils.module_bl_info(sys.modules[__package__])
        return ".".join(str(x) for x in info.get('version', ()))
    except Exception:
        return "?"


def report_lines(context):
    """What is being checked and how long things take, as lines of text: for
    the Performance panel and for sending to whoever looks after the add-on."""
    scene = context.scene
    st = props.settings(scene)
    mon = monitor.get(scene)
    lines = [f"Bucket Builder {addon_version()} | Blender {bpy.app.version_string} | "
             f"{platform.system()} {platform.release()}"]
    try:
        import gpu
        lines.append(f"Graphics: {gpu.platform.renderer_get()} | {gpu.platform.backend_type_get()} | "
                     f"{gpu.platform.version_get()}")
    except Exception:
        pass
    total = props.installed_memory()
    lines.append(f"Processor threads: {os.cpu_count()} | memory: "
                 + (f"{total / props.GIB:.0f} GB" if total else "unknown")
                 + f" | workers: {monitor.worker_count()}")
    if mon is None or st is None or not props.active(st):
        lines.append("Monitoring is off")
        return lines
    lines.append(f"Checking: collisions {'on' if st.detect_collisions else 'off'}, build volume "
                 f"{'on' if props.volume_checked(st) else 'off'}")
    stats = mon.world.stats()
    s = mon.status()
    largest = max((o.ntri for o in mon.objs.values() if o.in_world), default=0)
    lines.append(f"Parts: {stats['objects']} ({s['hidden']} hidden), {triangles_text(stats['triangles'])}; "
                 f"{stats['geoms']} different meshes, {triangles_text(stats['unique_triangles'])}; "
                 f"largest part {triangles_text(largest)}")
    lines.append(f"Neighbouring pairs: {stats['pairs']} | collisions {s['collisions']}, clearance "
                 f"{s['clearance']}, outside {s['partly_out'] + s['outside']}, near wall {s['near_wall']}"
                 f" | ignored: {s['ignored_problems']} problems, {s['ignored_parts']} parts")
    limit = stats['cache_limit']
    lines.append(f"Cached data: {stats['bytes'] / 1e6:.0f} MB"
                 + (f" of {limit / 1e6:.0f} MB allowed" if limit else "")
                 + f" | {stats['poses']} poses | dropped for memory: {stats['evictions']}")
    if mon.ready_s is not None:
        lines.append(f"First complete check after {mon.ready_s:.1f} s")
    else:
        lines.append(f"Still preparing after {time.perf_counter() - mon.t_start:.1f} s")
    lines.append(f"While editing: update {_spread(list(mon.live_ms))}"
                 + (f" | up to {max(mon.live_parts)} parts at once" if mon.live_parts else ""))
    lines.append(f"While editing: from one redraw to the next {_spread(list(mon.frame_ms))}")
    lines.append(f"Overlay drawing: {_spread(list(mon.draw_ms))}")
    lines.append(f"Overlay triangles: shading {mon.draw_tris[0]:,}, hatching {mon.draw_tris[1]:,}")
    lines.append(f"Background slices: {_spread(list(mon.bg_ms))} | being edited: {len(mon.hot)}"
                 f" | still to do: {mon.world.pending} | meshes to sort: {len(mon.unsorted)}"
                 f" | searched to the end: {stats['scans']}")
    lines.append(f"Clearance: {st.clearance_mm:g} mm ({'on' if st.use_clearance else 'off'}) | "
                 f"volume {' x '.join(f'{v:g}' for v in st.volume_size)} mm | {mon.unit_note}")
    return lines


class BUCKETBUILDER_OT_copy_report(Operator):
    """Copy a few lines about this build and how fast it is being checked to the clipboard, to paste into a message"""
    bl_idname = "bucketbuilder.copy_report"
    bl_label = "Copy Report"

    def execute(self, context):
        context.window_manager.clipboard = "\n".join(report_lines(context))
        self.report({'INFO'}, "Report copied to the clipboard")
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


def _current_profile(p, st):
    """The scene's printer in the library, or None.  (A scene saved with
    version 1.0 carries that version's name for it.)"""
    name = props.current_printer_name(st)
    for prof in p.profiles:
        if prof.name == name:
            return prof
    return None


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
        name = props.current_printer_name(props.settings(context.scene))
        if name != "Custom":
            self.name = name + " (copy)"
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
        prof = _current_profile(p, st)
        if prof is None:
            self.report({'WARNING'}, "Pick a printer first, or save the volume as a new printer")
            return {'CANCELLED'}
        prof.size = st.volume_size
        apply_volume(st, prof.name, tuple(st.volume_size))
        _mark_prefs_dirty(context)
        self.report({'INFO'}, f"Updated '{prof.name}'")
        return {'FINISHED'}


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
        prof = _current_profile(p, st)
        if prof is None:
            self.report({'WARNING'}, "The current volume is not a saved printer")
            return {'CANCELLED'}
        p.profiles.remove(next(i for i, q in enumerate(p.profiles) if q.name == prof.name))
        st.lock_printer_name = True
        st.printer = "Custom"
        st.lock_printer_name = False
        monitor.on_settings_changed(context.scene)
        _mark_prefs_dirty(context)
        return {'FINISHED'}


class BUCKETBUILDER_OT_profile_rename(Operator):
    """Rename the selected printer profile"""
    bl_idname = "bucketbuilder.profile_rename"
    bl_label = "Rename Printer Profile"

    name: StringProperty(name="Name")

    def invoke(self, context, event):
        self.name = props.current_printer_name(props.settings(context.scene))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        p = _library(context)
        st = props.settings(context.scene)
        name = self.name.strip()
        if p is None or not name:
            return {'CANCELLED'}
        prof = _current_profile(p, st)
        if prof is None:
            self.report({'WARNING'}, "The current volume is not a saved printer")
            return {'CANCELLED'}
        prof.name = name
        st.lock_printer_name = True
        st.printer = name
        st.lock_printer_name = False
        monitor.on_settings_changed(context.scene)
        _mark_prefs_dirty(context)
        return {'FINISHED'}


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
        current = props.current_printer_name(st)
        for i, (name, size) in enumerate(profile_items(context)):
            text = f"{name}   ({size[0]:g} x {size[1]:g} x {size[2]:g} mm)"
            op = layout.operator("bucketbuilder.profile_apply", text=text,
                                 icon='CHECKMARK' if name == current else 'BLANK1')
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
    BUCKETBUILDER_OT_isolate,
    BUCKETBUILDER_OT_isolate_problem,
    BUCKETBUILDER_OT_ignore_problem,
    BUCKETBUILDER_OT_count_all_problems,
    BUCKETBUILDER_OT_status,
    BUCKETBUILDER_OT_export_3mf,
    BUCKETBUILDER_OT_frame_volume,
    BUCKETBUILDER_OT_recheck,
    BUCKETBUILDER_OT_ignore,
    BUCKETBUILDER_OT_include,
    BUCKETBUILDER_OT_include_all,
    BUCKETBUILDER_OT_copy_report,
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
