# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: problem navigation, printer profiles, housekeeping."""

import math
import os
import platform
import sys
import time

import bpy
from bpy_extras.io_utils import ExportHelper
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
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
    # A build volume in millimeters is larger than a new viewport sees to
    # (Clip End is 1000 units): what is framed has to be inside it.
    far = rv3d.view_distance + 2.0 * float(radius)
    if space.clip_end < 1.5 * far:
        space.clip_end = 4.0 * far
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


def _in_local_view(context):
    area = _view3d_area(context)
    return area is not None and area.spaces.active.local_view is not None


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


def _goto(context, index, select, view=True):
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
    if view:
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
    if pr['kind'] == 'PQ':
        who = f"{pr['a']} and {pr['b']}" if pr['b'] else pr['a']
        return f"{who}: {pr['pq']} ({pr['rule']}, a print-quality note)"
    if pr['kind'] == 'PARTIAL':
        if pr.get('touch'):
            return f"{pr['a']} touching {pr['touch']}: {pr['dist_mm']:.3f} mm, needs at least 0.001 mm"
        return f"{pr['a']} reaches outside the build volume"
    return f"{pr['a']} is outside the build volume"


class BUCKETBUILDER_OT_focus_problem(Operator):
    """Frame this problem in the viewport and select the parts involved"""
    bl_idname = "bucketbuilder.focus_problem"
    bl_label = "Go to Problem"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0, options={'HIDDEN'})
    select: BoolProperty(name="Select Parts", default=True)
    # (a row of the list names its problem: the list may have changed since it was drawn)
    kind: StringProperty(options={'HIDDEN'})
    a: StringProperty(options={'HIDDEN'})
    b: StringProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        # (a row of the list is narrow: the names in full are worth a tooltip)
        mon = monitor.get(context.scene)
        problems = mon.problems() if mon is not None else []
        if 0 <= properties.index < len(problems):
            pr = problems[properties.index]
            return (describe(pr) + (" (ignored)" if pr['ignored'] else "")
                    + ". Click to frame it in the viewport and select the parts involved")
        return cls.__doc__

    def execute(self, context):
        index = self.index
        if self.kind:
            mon = monitor.get(context.scene)
            index = _find_problem(mon, self.kind, self.a, self.b)[0] if mon is not None else -1
            if index < 0:
                self.report({'INFO'}, "That problem is gone")
                return {'CANCELLED'}
        pr = _goto(context, index, self.select)
        if pr is None:
            self.report({'INFO'}, "No problems")
            return {'CANCELLED'}
        self.report({'INFO'}, describe(pr))
        return {'FINISHED'}


def pick_problem(context, index):
    """A click on a row of the list (through props._row_set): as the
    operator, so that it is a step of the undo history and says what the
    problem is in the status bar."""
    mon = monitor.get(context.scene)
    problems = mon.problems() if mon is not None else []
    if not 0 <= index < len(problems):
        return
    pr = problems[index]
    try:
        bpy.ops.bucketbuilder.focus_problem('EXEC_DEFAULT', kind=pr['key'][0], a=pr['names'][0],
                                            b=pr['names'][1])
    except Exception:
        _goto(context, index, True)


class BUCKETBUILDER_OT_step_problem(Operator):
    """Jump to the next or previous problem"""
    bl_idname = "bucketbuilder.step_problem"
    bl_label = "Next Problem"
    bl_options = {'REGISTER', 'UNDO'}

    direction: IntProperty(default=1, options={'HIDDEN'})
    select: BoolProperty(name="Select Parts", default=True)

    def execute(self, context):
        st = props.settings(context.scene)
        mon = monitor.get(context.scene)
        problems = mon.problems() if mon is not None else []
        # from the problem the user is at; if that one is gone (solved, most
        # likely), from where it was in the list
        step = 1 if self.direction >= 0 else -1
        at = mon.active_index(st) if mon is not None else -1
        if at < 0:
            at = st.problem_index - (1 if step > 0 and st.problem_index >= 0 else 0)
        index = at + step
        if st.problem_index < 0 and step < 0:
            index = -1
        # problems that are ignored are passed over (unless there are no others)
        n = len(problems)
        if n:
            index %= n
            for _ in range(n):
                if not problems[index]['ignored']:
                    break
                index = (index + step) % n
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
            _goto(context, at, True, view=False)      # (the view stays where it is)
        else:
            self.report({'INFO'}, "Pick a problem to see its parts on their own")
        monitor.tag_redraw_all()
        return {'FINISHED'}


_XRAY_BEFORE = {}        # 3D view space pointer -> its own X-ray setting before the toggle; and 'hatch'


class BUCKETBUILDER_OT_xray(Operator):
    """See through parts to judge clearances: turns Blender's X-ray on in every 3D view and lets the hatching show behind geometry. Off puts both back as they were"""
    bl_idname = "bucketbuilder.xray"
    bl_label = "X-Ray"

    def execute(self, context):
        wm = context.window_manager
        pr = props.prefs(context)
        spaces = [sp for win in wm.windows for area in win.screen.areas if area.type == 'VIEW_3D'
                  for sp in area.spaces if sp.type == 'VIEW_3D']
        if not wm.bucket_builder_xray:
            _XRAY_BEFORE.clear()
            for sp in spaces:
                _XRAY_BEFORE[sp.as_pointer()] = sp.shading.show_xray
                sp.shading.show_xray = True
            if pr is not None:
                _XRAY_BEFORE['hatch'] = pr.use_xray
                pr.use_xray = True
            wm.bucket_builder_xray = True
        else:
            for sp in spaces:
                sp.shading.show_xray = _XRAY_BEFORE.get(sp.as_pointer(), False)
            if pr is not None and 'hatch' in _XRAY_BEFORE:
                pr.use_xray = _XRAY_BEFORE['hatch']
            _XRAY_BEFORE.clear()
            wm.bucket_builder_xray = False
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
        was = mon.active_index(st)
        if st.isolate and was == index and _in_local_view(context):
            st.isolate = False
            local_view(context, [])
        else:
            # (also when the user has left the local view by hand: then the
            # button shows as off, and a click must switch it on)
            st.isolate = True
            _goto(context, index, True, view=(was != index))
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
    freed = set()
    for i in reversed(gone):
        item = st.ignored_problems[i]
        freed.update(o for o in (item.a, item.b) if o is not None)
        st.ignored_problems.remove(i)
    # A part the user deleted lives on for as long as an entry points at it.
    # With the entry gone nothing uses it: it goes now, as it would have when
    # it was deleted (and its name is free for the part that is imported next).
    for o in freed:
        try:
            if o.users == 0 and not o.use_fake_user:
                bpy.data.objects.remove(o)
        except Exception:
            pass
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
    """Ignore this problem: it stays in the list, grayed, and no longer counts. It counts again as soon as it changes, for instance when one of the parts is moved, unless it is locked"""
    bl_idname = "bucketbuilder.ignore_problem"
    bl_label = "Ignore Problem"
    bl_options = {'REGISTER', 'UNDO'}

    lock: BoolProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        if properties.lock:
            return ("Ignore this problem for good: however the parts are moved, it does not "
                    "count. (Two parts locked as too close count again if they collide.) Click "
                    "again to ignore it only for as long as it stays as it is")
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
            try:
                sig, geo, aux = mon.signature(self.kind, *pr['slots'])
            except Exception:             # (a part with a scale of zero has no place to remember)
                self.report({'WARNING'}, "This problem cannot be ignored: a part has no size")
                return {'CANCELLED'}
            item = st.ignored_problems[i] if i >= 0 else st.ignored_problems.add()
            item.kind = kind
            item.a = objs[0]
            item.b = objs[1] if len(objs) > 1 else None
            item.lock = lock
            item.sig, item.geo, item.aux = sig, geo, aux
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
    bl_label = "Bucket Monitoring"        # (true whatever is switched on: the box below says what)

    def execute(self, context):
        return {'CANCELLED'}              # (a sign, not a switch)


def export_warnings(scene):
    """What whoever exports the build should be told first: parts outside
    the build volume above all (a file with those in it is not a build the
    printer can make), and that the volume was not checked at all."""
    st = props.settings(scene)
    mon = monitor.get(scene)
    lines = []
    if mon is None or st is None or not props.volume_checked(st):
        lines.append("The build volume is not being checked")
    else:
        s = mon.status()
        n = s['partly_out'] + s['outside'] + s.get('staged', 0)    # (staged beside it: quiet while arranging, not here)
        if n:
            lines.append(f"{n} part{'s are' if n != 1 else ' is'} outside the build volume")
        if s['busy'] or s['preparing']:
            lines.append("The build is still being checked")
    return lines


class BUCKETBUILDER_OT_get_3mf_addon(Operator):
    """Install the 3MF Import/Export add-on, which Export Print File writes through"""
    bl_idname = "bucketbuilder.get_3mf_addon"
    bl_label = "Install the 3MF Add-on"

    def execute(self, context):
        from . import export
        if export.install_addon():
            self.report({'INFO'}, "The 3MF add-on is installed: Export Print File is ready")
            return {'FINISHED'}
        try:
            bpy.ops.screen.userpref_show(section='EXTENSIONS')    # (Blender's own Get Extensions, to search there)
        except Exception:                                        # noqa: BLE001
            pass
        self.report({'WARNING'}, "Could not install it here (online access may be off): in Preferences > "
                                 "Get Extensions, search for 3MF")
        return {'CANCELLED'}


class BUCKETBUILDER_OT_export_3mf(Operator, ExportHelper):
    """Write the build to a 3MF file for the printer: every part the check looks at, where it is, with the build's
    printer, packing settings and last check in the file"""
    bl_idname = "bucketbuilder.export_3mf"
    bl_label = "Export Print File"
    filename_ext = ".3mf"
    filter_glob: StringProperty(default="*.3mf", options={'HIDDEN'})
    # the 3MF add-on's options that matter for a print file (passed to its export)
    use_mesh_modifiers: BoolProperty(name="Apply Modifiers", default=True,
                                     description="Write each part as it looks, with its modifiers applied")
    coordinate_precision: IntProperty(name="Precision", default=9, min=1, max=12,
                                      description="Decimal places of each coordinate")
    thumbnail_mode: EnumProperty(name="Thumbnail", default='AUTO',
                                 items=(('AUTO', "Render", "A picture of the build"), ('NONE', "None", "No picture")),
                                 description="The preview picture inside the file")
    strip_identifying_data: BoolProperty(name="Leave Out Names and Metadata", default=False,
                                         description="Write generic part names and no identifying metadata "
                                                     "(the build facts in bb: are kept)")

    def invoke(self, context, event):
        if not self.filepath:
            name = bpy.path.basename(bpy.data.filepath) or "build"
            self.filepath = bpy.path.ensure_ext(name.rsplit('.', 1)[0], ".3mf")
        return super().invoke(context, event)

    def draw(self, context):
        from . import export
        layout = self.layout
        col = layout.column(align=True)
        for line in export.problems(context):
            col.label(text=line, icon='ERROR')
        layout.use_property_split = True
        for name in ('use_mesh_modifiers', 'coordinate_precision', 'thumbnail_mode',
                     'strip_identifying_data'):
            layout.prop(self, name)

    def execute(self, context):
        from . import export
        if export.threemf_api() is None:
            self.report({'ERROR'}, "Export needs the 3MF add-on: install it from the Export panel")
            return {'CANCELLED'}
        for line in export.problems(context):
            self.report({'WARNING'}, line)               # (a warning, not a refusal: the file is still written)
        try:
            n = export.write(context, self.filepath, use_mesh_modifiers=self.use_mesh_modifiers,
                             coordinate_precision=self.coordinate_precision,
                             thumbnail_mode=self.thumbnail_mode, strip_identifying_data=self.strip_identifying_data)
        except Exception as e:                           # noqa: BLE001
            self.report({'ERROR'}, "Could not write the file: %s" % e)
            return {'CANCELLED'}
        self.report({'INFO'}, "%d part%s written to %s" % (n, "s" if n != 1 else "", bpy.path.basename(self.filepath)))
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
    """Discard all cached data and analyze the scene again"""
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
        targets = list(context.selected_objects)
        if context.area is not None and context.area.type == 'OUTLINER':
            # (what is picked in the Outliner, hidden objects included)
            ids = getattr(context, 'selected_ids', None) or ()
            targets = [o for o in ids if isinstance(o, bpy.types.Object)] or targets
        for o in targets:
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
    return [o for o in scene.objects if o.bucket_builder_ignore and not o.get("bucket_builder_helper")]


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
    lines.append(f"Neighboring pairs: {stats['pairs']} | collisions {s['collisions']}, clearance "
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


_SIZE = dict(name="Size (mm)", size=3, subtype='XYZ', min=1.0, soft_max=1000.0, precision=1, step=100)


def _draw_printer(op, context, layout):
    """The fields of the New and Edit Printer dialogs: name, size, and where the volume sits (the
    owner, 2026-11-23: in place of an Advanced section in the panel)."""
    st = props.settings(context.scene)
    layout.use_property_split = True
    layout.prop(op, "name")
    layout.prop(op, "size")
    layout.prop(st, "volume_align")
    layout.prop(st, "volume_offset", text="Offset (mm)")


class BUCKETBUILDER_OT_profile_add(Operator):
    """Add a printer: its name and build volume"""
    bl_idname = "bucketbuilder.profile_add"
    bl_label = "New Printer"
    bl_options = {'UNDO'}          # (the scene's printer; no redo panel, which would run it again)

    name: StringProperty(name="Name", default="Custom Printer")
    size: FloatVectorProperty(default=(380.0, 284.0, 380.0), **_SIZE)

    def invoke(self, context, event):
        st = props.settings(context.scene)
        name = props.current_printer_name(st)
        if name != "Custom":
            self.name = name + " (copy)"
        self.size = st.volume_size
        return context.window_manager.invoke_props_dialog(self, confirm_text="Add Printer")

    def draw(self, context):
        _draw_printer(self, context, self.layout)

    def execute(self, context):
        p = _library(context)
        if p is None:
            self.report({'ERROR'}, "Add-on preferences are not available")
            return {'CANCELLED'}
        st = props.settings(context.scene)
        if self.properties.is_property_set('size'):
            st.volume_size = self.size
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
    """Edit the selected printer: its name and build volume"""
    bl_idname = "bucketbuilder.profile_update"
    bl_label = "Edit Printer"
    bl_options = {'UNDO'}          # (the scene's printer; no redo panel, which would run it again)

    name: StringProperty(name="Name", default="")
    size: FloatVectorProperty(default=(380.0, 284.0, 380.0), **_SIZE)

    def invoke(self, context, event):
        st = props.settings(context.scene)
        self.name = props.current_printer_name(st)
        self.size = st.volume_size
        return context.window_manager.invoke_props_dialog(self, confirm_text="Save Printer")

    def draw(self, context):
        _draw_printer(self, context, self.layout)

    def execute(self, context):
        p = _library(context)
        st = props.settings(context.scene)
        if p is None:
            return {'CANCELLED'}
        if self.properties.is_property_set('size'):
            st.volume_size = self.size
        prof = _current_profile(p, st)
        if prof is None and st.printer == "Custom" and st.printer_last:
            # (typing a size turns the printer into "Custom": the size goes
            # into the printer it was typed over)
            prof = next((x for x in p.profiles if x.name == st.printer_last), None)
        if prof is None:
            self.report({'WARNING'}, "Pick a printer first, or save the volume as a new printer")
            return {'CANCELLED'}
        new_name = self.name.strip()
        if new_name and new_name != prof.name and new_name in {x.name for x in p.profiles}:
            self.report({'WARNING'}, f"There is already a printer called '{new_name}': the name was kept")
        elif new_name and new_name != prof.name:
            prof.name = new_name
        prof.size = st.volume_size
        apply_volume(st, prof.name, tuple(st.volume_size))
        _mark_prefs_dirty(context)
        self.report({'INFO'}, f"Updated '{prof.name}'")
        return {'FINISHED'}


class BUCKETBUILDER_OT_profile_remove(Operator):
    """Delete the selected printer profile from the library"""
    bl_idname = "bucketbuilder.profile_remove"
    bl_label = "Delete Printer Profile"
    bl_options = {'UNDO'}          # (the scene's printer; no redo panel, which would run it again)

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
    bl_options = {'UNDO'}          # (the scene's printer; no redo panel, which would run it again)

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
        if name != prof.name and name in {x.name for x in p.profiles}:
            self.report({'WARNING'}, f"There is already a printer called '{name}'")
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


class BUCKETBUILDER_OT_move_inside(Operator):
    """Nudge parts that touch a wall of the build volume just inside it: 0.002 mm from every side
    wall and the top they are too close to. The rule asks for 0.001 mm; twice that, so that the
    rounding of single-precision coordinates can't put a part back on the line"""
    bl_idname = "bucketbuilder.move_inside"
    bl_label = "Nudge Inside"
    # (no redo panel: changing the option there re-ran the nudge on the monitor's view of the scene
    # before the undo had reached it, and parts jumped; the owner, 2026-11-23)
    bl_options = {'UNDO'}

    selected_only: BoolProperty(name="Selected Only", default=False, options={'SKIP_SAVE', 'HIDDEN'},
                                description="Only the selected parts that touch a wall")

    @classmethod
    def description(cls, context, properties):
        who = "the selected parts" if properties.selected_only else "every part"
        return (f"Nudge {who} that touch a wall just inside it: 0.002 mm from the wall, twice the "
                "0.001 mm the rule asks, so that rounding can't put a part back on the line")

    def execute(self, context):
        from mathutils import Matrix
        from .core.world import INSIDE_MARGIN
        mon = monitor.get(context.scene)
        if mon is None:
            return {'CANCELLED'}
        w = mon.world
        if w.volume is None:
            return {'CANCELLED'}
        vlo, vhi = w.volume
        want = 2.0 * INSIDE_MARGIN / mon.mm_per_unit          # (twice the margin: room for rounding)
        by_uid = {monitor._uid(o): o for o in context.view_layer.objects if o is not None}
        n = 0
        for slot, r in list(w.oob.items()):
            obj = by_uid.get(w.uid(slot))
            if getattr(r, 'touch', None) is None or obj is None or (self.selected_only and not obj.select_get()):
                continue
            # every side wall and the top it is too close to, not only the nearest (a part in a corner)
            d = [0.0, 0.0, 0.0]
            for axis in range(3):
                low, high = r.lo[axis] - vlo[axis], vhi[axis] - r.hi[axis]
                if axis < 2 and low < want:
                    d[axis] += want - low
                if high < want:
                    d[axis] -= want - high
            if any(d):
                obj.matrix_world = Matrix.Translation(d) @ obj.matrix_world
                n += 1
        self.report({'INFO'}, f"{n} part{'s' if n != 1 else ''} nudged 0.002 mm off the wall")
        return {'FINISHED'}


def set_message(context, text):
    """The last result line (a pack, a fix): shown in the panel under the verdict, where it isn't
    missed as Blender's own reports are (the owner, 2026-11-24), and read by the PPP template."""
    st = props.settings(context.scene)
    if st is not None:
        st.last_message = text[:200]


class BUCKETBUILDER_OT_fix_scale(Operator):
    """Scale parts 1000 times about the world origin (location and size), which exactly undoes a file
    read in the wrong unit and keeps the parts' arrangement. The PPP template calls this one too, so
    its button and the monitor's are the same thing"""
    bl_idname = "bucketbuilder.fix_scale"
    bl_label = "Fix the Scale"
    bl_options = {'UNDO'}

    factor: FloatProperty(default=1000.0, options={'HIDDEN'})
    names: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def _objects(self):
        names = [n for n in self.names.split("\n") if n]
        if not names:
            # (empty: every part flagged now, as the PPP template agreed)
            mon = monitor.get(bpy.context.scene)
            off = mon.scale_problem() if mon is not None else None
            names = off[1] if off and (off[0] > 1) == (self.factor > 1) else []
        return [o for o in (bpy.data.objects.get(n) for n in names) if o is not None]

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=420, title="Check the Scale",
            confirm_text="Scale \u00d71000" if self.factor > 1 else "Scale \u00f71000")

    def draw(self, context):
        n = len(self._objects())
        col = self.layout.column(align=True)
        what = "under 0.5 mm: imported in meters?" if self.factor > 1 else "over 1 m: imported in micrometers?"
        col.label(text=f"{n} part{'s' if n != 1 else ''} {what}", icon='ERROR')
        col.label(text="Bucket Builder reads 1 unit as 1 mm. Scale them to fit?")
        col.label(text="Afterwards, check that they look right; Ctrl+Z undoes it.")

    def execute(self, context):
        objs = self._objects()
        if not objs:
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in objs:
            o.location = o.location * self.factor
            o.scale = o.scale * self.factor
        # show them, so the user can see whether they look right
        try:
            bpy.ops.object.select_all(action='DESELECT')
            for o in objs:
                if o.visible_get():
                    o.select_set(True)
            if context.area is not None and context.area.type == 'VIEW_3D':
                bpy.ops.view3d.view_selected()
        except Exception:                                # noqa: BLE001  (no viewport: a script)
            pass
        sign = "\u00d7" if self.factor > 1 else "\u00f7"
        set_message(context, f"Scaled {len(objs)} part{'s' if len(objs) != 1 else ''} {sign}1000. "
                             "Does this look right? Ctrl+Z undoes it.")
        self.report({'INFO'}, props.settings(context.scene).last_message)
        return {'FINISHED'}


class BUCKETBUILDER_OT_density_view(Operator):
    """Show the layer density larger, with its statistics"""
    bl_idname = "bucketbuilder.density_view"
    bl_label = "Layer Density"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        from . import icons, report, ui
        layout = self.layout
        mon = monitor.get(context.scene)
        res = mon.report.result if mon is not None else None
        value = icons.get('profile')
        if res is None or not value:
            layout.label(text="No density evaluated yet", icon='INFO')
            return
        layout.label(text="Layer Density", icon='GRAPH')
        split = layout.split(factor=0.62)
        ui.draw_density_figure(split.column(), res, value, 15.0)
        col = split.column(align=True)
        for key, val, tone in report.rows(res):
            row = col.row()
            row.label(text=key)
            row.label(text=val, **({'icon': 'ERROR'} if tone == 'warn' else {}))

    def execute(self, context):
        return {'FINISHED'}


CLASSES = (
    BUCKETBUILDER_OT_density_view,
    BUCKETBUILDER_OT_fix_scale,
    BUCKETBUILDER_OT_move_inside,
    BUCKETBUILDER_OT_focus_problem,
    BUCKETBUILDER_OT_step_problem,
    BUCKETBUILDER_OT_isolate,
    BUCKETBUILDER_OT_xray,
    BUCKETBUILDER_OT_isolate_problem,
    BUCKETBUILDER_OT_ignore_problem,
    BUCKETBUILDER_OT_count_all_problems,
    BUCKETBUILDER_OT_status,
    BUCKETBUILDER_OT_get_3mf_addon,
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
