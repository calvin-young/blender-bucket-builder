# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panels (3D Viewport > Sidebar > Bucket), and the few places outside
the sidebar where an object can be left out of the checks.

    Bucket Builder        how the build stands, and the list of problems
    Collision Detection   its switch and settings; which parts are checked
    Build Volume          its switch, the printer, the wall gap
    Options               what is drawn, performance, units
    Export                (to come)
"""

import bpy
from bpy.types import Panel, UIList

from . import icons, monitor, ops, overlay, props

LIST_ROWS = 10
IGNORED_ROWS = 12
TAB_WIDTH = 0.3       # of the colour tab of a row in the list of problems, in interface units

# the icon of every tone of overlay.summary: (ours, Blender's if ours could not be made)
_TONE_ICON = {'fail': ('cross', 'CANCEL'), 'out': ('box', 'SHADING_BBOX'), 'warn': ('warn', 'ERROR'),
              'wall': ('gap', 'SHADING_BBOX'), 'ok': ('ok', 'CHECKMARK'), 'note': ('note', 'INFO'),
              'dim': ('dot', 'DOT'), '': (None, 'BLANK1'), 'printer': (None, 'BLANK1'),
              'view': (None, 'VIEWZOOM')}
# The list of problems has Blender's own icons, in the colour of its text: a
# coloured icon on every row was too loud.  The colour of a kind is a slim
# tab at the left end of its row (see props._tab).
_KIND_ICON = {'PARTIAL': 'SHADING_BBOX', 'OUTSIDE': 'SHADING_BBOX', 'COLLIDE': 'CANCEL',
              'CLEAR': 'ERROR', 'WALL': 'OBJECT_HIDDEN'}
_KIND_TAB = {'PARTIAL': 'tab_outside', 'OUTSIDE': 'tab_outside', 'COLLIDE': 'tab_collision',
             'CLEAR': 'tab_clearance', 'WALL': 'tab_wall'}
ISOLATE_ICON = 'VIEWZOOM'


def _icon(tone):
    """Keyword arguments that give a label or a button the icon of a tone."""
    name, fallback = _TONE_ICON[tone]
    value = icons.get(name) if name else 0
    return {'icon_value': value} if value else {'icon': fallback}


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bucket"


def _split(layout):
    col = layout.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    return col


def _limit(layout, data, switch, value):
    """A check that has a distance: the checkbox with its name at the left
    edge, as every other checkbox of the sidebar, and the distance beside it."""
    row = layout.row(align=True)
    row.prop(data, switch)
    sub = row.row(align=True)
    sub.ui_units_x = 3.2
    sub.active = getattr(data, switch)
    sub.prop(data, value, text="")


def _big(layout, data, prop, text):
    """One of the two main switches: a large button with an eye."""
    row = layout.row()
    row.scale_y = 1.6
    row.prop(data, prop, toggle=True, text=text,
             icon='HIDE_OFF' if getattr(data, prop) else 'HIDE_ON')


# ---------------------------------------------------------------------------
# Bucket Builder
# ---------------------------------------------------------------------------

class BUCKETBUILDER_PT_main(_Base, Panel):
    bl_label = "Bucket Builder"
    bl_idname = "BUCKETBUILDER_PT_main"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        mon = monitor.get(scene)

        # a sign, not a switch: the switches are in the two panels below.  It
        # is lit while something is being checked.
        row = layout.row()
        row.scale_y = 1.6
        row.operator("bucketbuilder.status", depress=props.active(props.settings(scene)))

        status = mon.status() if mon is not None else None
        local = overlay.local_count(context, mon) if mon is not None else None
        state, rows = overlay.summary(status, local)
        box = layout.box()
        col = box.column(align=True)
        # (The colour of a line is in its icon.  A panel's text can be made
        # red and nothing else, and one red line among white ones looked like
        # a mistake: so all of them are plain.)
        for text, tone in rows:
            col.label(text=text, **_icon(tone))
        if status and status.get('printer'):
            col.label(text=status['printer'], icon='INFO')
        if mon is not None and mon.error:
            col.label(text=mon.error[:60], icon='INFO')


def _mm(pr):
    """A distance for the list: "[3.0 mm]", with two decimals under a millimetre."""
    d = pr['dist_mm']
    return f"[{'~' if pr['approx'] else ''}{d:.1f} mm]" if d >= 0.995 else \
        f"[{'~' if pr['approx'] else ''}{d:.2f} mm]"


def problem_text(pr):
    """One problem as a line of the list.  A distance comes first, so that
    down the list one sees at a glance how many there are and how bad."""
    if pr['kind'] == 'CLEAR':
        return f"{_mm(pr)}  {pr['a']}  |  {pr['b']}"
    if pr['kind'] == 'COLLIDE':
        if pr['inside'] == 1:
            return f"{pr['a']}  inside  {pr['b']}"
        if pr['inside'] == 2:
            return f"{pr['b']}  inside  {pr['a']}"
        return f"{pr['a']}  x  {pr['b']}"
    if pr['kind'] == 'WALL':
        return f"{_mm(pr)}  {pr['a']}  |  wall"
    if pr['kind'] == 'PARTIAL':
        return f"{pr['a']}   partly outside"
    return f"{pr['a']}   outside"


class BUCKETBUILDER_UL_problems(UIList):
    """The list of problems.  Blender's own list widget: text at the left,
    the row one is at lit, a scroll bar when there are many.  Its rows come
    from the monitor, not from the collection it is given (see props.ensure_rows)."""
    bl_idname = "BUCKETBUILDER_UL_problems"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0,
                  flt_flag=0):
        scene = context.scene
        mon = monitor.get(scene)
        problems = mon.problems() if mon is not None else ()
        if index >= len(problems):
            return
        pr = problems[index]
        st = props.settings(scene)
        p = props.prefs(context)
        ignored = pr['ignored']
        row = layout.row(align=True)
        if p is not None:
            # the colour the problem has in the viewport (grey: ignored)
            tab = row.row(align=True)
            tab.ui_units_x = TAB_WIDTH
            tab.enabled = False
            tab.prop(p, 'tab_ignored' if ignored else _KIND_TAB[pr['kind']], text="")
        # the problem itself: a click on the row goes there.  Greyed when it is ignored.
        sub = row.row(align=True)
        sub.active = not ignored
        sub.label(text=problem_text(pr), icon=_KIND_ICON[pr['kind']])
        # ... and its three buttons: on its own, ignore, ignore for good
        local = getattr(context.space_data, 'local_view', None) is not None
        alone = st.isolate and local and index == mon.active_index(st)
        names = pr['names']
        for idname, icon_name, down, lock in (
                ("bucketbuilder.isolate_problem", ISOLATE_ICON, alone, None),
                ("bucketbuilder.ignore_problem", 'HIDE_ON' if ignored else 'HIDE_OFF',
                 bool(ignored), False),
                ("bucketbuilder.ignore_problem", 'LOCKED' if ignored == 2 else 'UNLOCKED',
                 ignored == 2, True)):
            op = row.operator(idname, text="", icon=icon_name, depress=down)
            op.kind = pr['key'][0]
            op.a, op.b = names
            if lock is not None:
                op.lock = lock

    def filter_items(self, context, data, propname):
        # as many rows as there are problems, in their order
        rows = getattr(data, propname)
        mon = monitor.get(context.scene)
        n = len(mon.problems()) if mon is not None else 0
        show = self.bitflag_filter_item
        return [show if i < n else 0 for i in range(len(rows))], []

    def draw_filter(self, context, layout):
        pass                      # (nothing to search by: the rows have no names of their own)


class BUCKETBUILDER_PT_problems(_Base, Panel):
    bl_label = "Problems"
    bl_idname = "BUCKETBUILDER_PT_problems"
    bl_parent_id = "BUCKETBUILDER_PT_main"

    @classmethod
    def poll(cls, context):
        return monitor.get(context.scene) is not None

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = props.settings(scene)
        mon = monitor.get(scene)
        problems = mon.problems() if mon is not None else []
        n = len(problems)

        # previous | on its own | next.  (A button has its icon before its
        # text, so the two arrows are text: the same triangles, on the outside.)
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = n > 0
        op = sub.operator("bucketbuilder.step_problem", text="\u25c0  Previous")
        op.direction = -1
        # going to a problem then shows its parts on their own (local view)
        row.operator("bucketbuilder.isolate", text="", depress=st.isolate, icon=ISOLATE_ICON)
        sub = row.row(align=True)
        sub.enabled = n > 0
        op = sub.operator("bucketbuilder.step_problem", text="Next  \u25b6")
        op.direction = 1
        if n == 0:
            layout.label(text="Nothing to fix", **_icon('ok'))
        else:
            wm = context.window_manager
            layout.template_list("BUCKETBUILDER_UL_problems", "", wm, "bucket_builder_rows",
                                 wm, "bucket_builder_row", rows=max(2, min(n, LIST_ROWS)),
                                 maxrows=LIST_ROWS, sort_lock=True)
        entries = len(st.ignored_problems)
        if entries:
            # (also with nothing in the list: a lock on two parts that are
            # apart just now is still there, and must be seen to be)
            k = sum(1 for pr in problems if pr['ignored'])
            row = layout.row()
            row.label(text=f"{k} ignored" + (f", {entries - k} not in the list" if entries > k else ""),
                      **_icon('note'))
            row.operator("bucketbuilder.count_all_problems")


# ---------------------------------------------------------------------------
# Collision Detection
# ---------------------------------------------------------------------------

class BUCKETBUILDER_PT_collisions(_Base, Panel):
    bl_label = "Collision Detection"
    bl_idname = "BUCKETBUILDER_PT_collisions"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        _big(layout, st, "detect_collisions", "Detect Collisions")

        body = layout.column()
        body.active = st.detect_collisions
        _limit(body, st, "use_clearance", "clearance_mm")
        # (which parts are checked goes for the build volume as well)
        layout.prop(st, "ignore_hidden")
        body = layout.column()
        body.active = st.detect_collisions
        body.prop(st, "detect_enclosed")


class BUCKETBUILDER_PT_parts(_Base, Panel):
    bl_label = "Parts"
    bl_idname = "BUCKETBUILDER_PT_parts"
    bl_parent_id = "BUCKETBUILDER_PT_collisions"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        mon = monitor.get(scene)
        obj = context.active_object
        if obj is not None and obj.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'EMPTY'}:
            layout.prop(obj, "bucket_builder_ignore", text=f"Ignore '{obj.name}'")
        ignored = ops.ignored_objects(scene)
        col = layout.column(align=True)
        row = col.row(align=True)
        op = row.operator("bucketbuilder.ignore", text="Ignore Selected")
        op.ignore = True
        op = row.operator("bucketbuilder.ignore", text="Include Selected")
        op.ignore = False
        row = col.row(align=True)
        row.enabled = bool(ignored)
        row.operator("bucketbuilder.include_all")

        if ignored:
            # so that nothing stays left out of the checks by oversight
            box = layout.box()
            box.label(text=f"{len(ignored)} ignored:", icon='HIDE_ON')
            col = box.column(align=True)
            for o in ignored[:IGNORED_ROWS]:
                row = col.row(align=True)
                row.label(text=o.name)
                op = row.operator("bucketbuilder.include", text="", icon='X', emboss=False)
                op.name = o.name
            if len(ignored) > IGNORED_ROWS:
                col.label(text=f"... and {len(ignored) - IGNORED_ROWS} more")

        if mon is not None:
            stats = mon.world.stats()
            s = mon.status()
            col = layout.column(align=True)
            col.label(text=f"{stats['objects']} parts, {ops.triangles_text(stats['triangles'])}")
            if s['hidden']:
                col.label(text=f"{s['hidden']} of them hidden, and checked")
            if s['hidden_skipped']:
                col.label(text=f"{s['hidden_skipped']} hidden parts left out")
            skipped = [o for o in mon.objs.values() if o.skipped]
            un = mon.unchecked
            if skipped or un or mon.disabled:
                box = layout.box()
                box.label(text="In the scene but not checked:", icon='ERROR' if (skipped or un) else 'INFO')
                for o in skipped[:6]:
                    box.label(text=f"{o.name}: {o.skipped}")
                if un.get('instances'):
                    box.label(text=f"instances made by {un['instances']} part(s)")
                    box.label(text="(add Realize Instances to check them)")
                if un.get('collections'):
                    box.label(text=f"{un['collections']} collection instance(s)")
                if un.get('other'):
                    box.label(text=f"{un['other']} text, curve or metaball object(s)")
                if un:
                    box.label(text="Convert them to meshes, or ignore them.")
                if mon.disabled:
                    box.label(text=f"{mon.disabled} mesh object(s) disabled in viewports")


# ---------------------------------------------------------------------------
# Build Volume
# ---------------------------------------------------------------------------

class BUCKETBUILDER_PT_volume(_Base, Panel):
    bl_label = "Build Volume"
    bl_idname = "BUCKETBUILDER_PT_volume"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        _big(layout, st, "monitor_volume", "Monitor Build Volume")

        row = layout.row(align=True)
        row.menu("BUCKETBUILDER_MT_printers", text=props.current_printer_name(st))
        row.operator("bucketbuilder.profile_add", text="", icon='ADD')
        row.operator("bucketbuilder.profile_update", text="", icon='FILE_TICK')
        row.operator("bucketbuilder.profile_remove", text="", icon='REMOVE')
        layout.operator("bucketbuilder.frame_volume", icon='VIEWZOOM')

        # the two checkboxes are under the switch above: off there is off
        body = layout.column()
        body.active = st.monitor_volume
        col = body.column(align=True)
        col.prop(st, "show_volume")
        col.prop(st, "use_volume")
        col = body.column()
        col.active = st.use_volume
        _limit(col, st, "use_wall_clearance", "wall_clearance_mm")

        header, body = layout.panel("bucketbuilder_volume_advanced", default_closed=True)
        header.label(text="Advanced")
        if body is not None:
            col = _split(body)
            col.prop(st, "volume_size", text="Size (mm)")
            col = _split(body)
            col.prop(st, "volume_align")
            col.prop(st, "volume_offset", text="Offset (mm)")


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

class BUCKETBUILDER_PT_options(_Base, Panel):
    bl_label = "Options"
    bl_idname = "BUCKETBUILDER_PT_options"

    def draw(self, context):
        layout = self.layout
        space = context.space_data
        shading = getattr(space, "shading", None)
        if shading is None:
            return
        # Random colours tell the parts apart; one plain colour lets the
        # problems stand out.  Both are worth a click away.
        col = layout.column(align=True)
        col.label(text="Part Colors")
        row = col.row(align=True)
        row.active = shading.type == 'SOLID'
        row.prop_enum(shading, "color_type", 'SINGLE', text="Custom")
        row.prop_enum(shading, "color_type", 'RANDOM', text="Random")
        if shading.type != 'SOLID':
            # (the colours above are those of solid shading)
            col.prop_enum(shading, "type", 'SOLID', text="Use Solid Shading")
        elif shading.color_type == 'SINGLE':
            col.prop(shading, "single_color", text="")


class BUCKETBUILDER_PT_display(_Base, Panel):
    bl_label = "Display"
    bl_idname = "BUCKETBUILDER_PT_display"
    bl_parent_id = "BUCKETBUILDER_PT_options"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        p = props.prefs(context)
        col = layout.column(align=True)
        col.prop(st, "show_hud")
        col.prop(st, "show_overlay")
        for heading, names in (("Collision", ("show_tint", "show_curves", "show_markers_collision")),
                               ("Clearance", ("show_gap_lines", "show_markers_clearance",
                                              "show_labels_clearance"))):
            col = layout.column(align=True)
            col.active = st.show_overlay
            col.label(text=heading)
            for name in names:
                col.prop(st, name)
        col = _split(layout)
        col.prop(st, "local_view_mode")
        if p is None:
            return
        col = _split(layout)
        col.prop(p, "tint_strength")
        col.prop(p, "hatch_pixels")
        row = col.row(align=True, heading="Hatch X-Ray")
        row.prop(p, "use_xray", text="")
        if p.use_xray:
            row.prop(p, "xray", text="")
        col.prop(p, "volume_fill")
        col.prop(p, "badge_scale")
        header, body = layout.panel("bucketbuilder_display_colors", default_closed=True)
        header.label(text="Colors")
        if body is not None:
            col = _split(body)
            col.prop(p, "color_collision")
            col.prop(p, "color_clearance")
            col.prop(p, "color_outside")
            col.prop(p, "color_volume")


class BUCKETBUILDER_PT_performance(_Base, Panel):
    bl_label = "Performance"
    bl_idname = "BUCKETBUILDER_PT_performance"
    bl_parent_id = "BUCKETBUILDER_PT_options"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        mon = monitor.get(context.scene)
        if mon is not None:
            stats = mon.world.stats()
            col = layout.column(align=True)
            col.label(text=f"{stats['pairs']} neighbouring pairs tracked")
            limit = stats['cache_limit']
            used = f"{stats['bytes'] / 1e6:.0f} MB of cached data"
            col.label(text=used + (f" (limit {limit / 2 ** 30:.1f} GB)" if limit else ""))
            if stats['evictions']:
                # the limit is in the way: data is dropped and rebuilt
                col.label(text="Memory limit reached (see Preferences)", icon='INFO')
            col = layout.column(align=True)
            if mon.ready_s is not None:
                col.label(text=f"First complete check after {mon.ready_s:.1f} s")
            col.label(text="While editing, in milliseconds:")
            for name, values in (("Update", mon.live_ms), ("Overlay", mon.draw_ms),
                                 ("Redraw to redraw", mon.frame_ms)):
                col.label(text=f"{name}: {ops._spread(list(values), unit='')}", icon='BLANK1')
            col.label(text=f"Overlay triangles: {mon.draw_tris[0] + mon.draw_tris[1]:,}")
        col = layout.column(align=True)
        col.operator("bucketbuilder.copy_report", icon='COPYDOWN')
        col.operator("bucketbuilder.recheck", icon='FILE_REFRESH')


class BUCKETBUILDER_PT_units(_Base, Panel):
    bl_label = "Units"
    bl_idname = "BUCKETBUILDER_PT_units"
    bl_parent_id = "BUCKETBUILDER_PT_options"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = props.settings(scene)
        mon = monitor.get(scene)
        col = _split(layout)
        col.prop(st, "unit_mode")
        if mon is not None and mon.unit_note:
            row = col.row()
            row.alignment = 'RIGHT'
            row.label(text=mon.unit_note)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class BUCKETBUILDER_PT_export(_Base, Panel):
    bl_label = "Export"
    bl_idname = "BUCKETBUILDER_PT_export"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.3
        row.operator("bucketbuilder.export_3mf", icon='EXPORT')
        col = layout.column(align=True)
        col.active = False
        col.label(text="Coming in a later version.")


CLASSES = (
    BUCKETBUILDER_UL_problems,
    BUCKETBUILDER_PT_main,
    BUCKETBUILDER_PT_problems,
    BUCKETBUILDER_PT_collisions,
    BUCKETBUILDER_PT_parts,
    BUCKETBUILDER_PT_volume,
    BUCKETBUILDER_PT_options,
    BUCKETBUILDER_PT_display,
    BUCKETBUILDER_PT_performance,
    BUCKETBUILDER_PT_units,
    BUCKETBUILDER_PT_export,
)


# ---------------------------------------------------------------------------
# outside the sidebar
# ---------------------------------------------------------------------------
# (The Outliner's columns of toggles cannot be added to by an add-on; its
# context menu and the object's Visibility panel can.)

def draw_visibility(self, context):
    obj = context.object
    if obj is None:
        return
    layout = self.layout
    layout.use_property_split = True
    col = layout.column(heading="Bucket Builder")
    col.prop(obj, "bucket_builder_ignore", text="Ignore")


def draw_object_menu(self, context):
    layout = self.layout
    layout.separator()
    op = layout.operator("bucketbuilder.ignore", text="Ignore in Bucket Builder", icon='HIDE_ON')
    op.ignore = True
    op = layout.operator("bucketbuilder.ignore", text="Include in Bucket Builder", icon='HIDE_OFF')
    op.ignore = False


_EXTRA = (('OBJECT_PT_visibility', draw_visibility),
          ('VIEW3D_MT_object_context_menu', draw_object_menu),
          ('OUTLINER_MT_object', draw_object_menu))


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for name, fn in _EXTRA:
        host = getattr(bpy.types, name, None)
        if host is not None:
            host.append(fn)


def unregister():
    for name, fn in _EXTRA:
        host = getattr(bpy.types, name, None)
        if host is not None:
            try:
                host.remove(fn)
            except Exception:
                pass
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
