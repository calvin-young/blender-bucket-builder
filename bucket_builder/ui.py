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
from bpy.types import Panel

from . import icons, monitor, ops, overlay, props

LIST_ROWS = 10
IGNORED_ROWS = 12

# the icon of every tone of overlay.summary: (ours, Blender's if ours could not be made)
_TONE_ICON = {'fail': ('cross', 'CANCEL'), 'out': ('box', 'SHADING_BBOX'), 'warn': ('warn', 'ERROR'),
              'wall': ('gap', 'SHADING_BBOX'), 'ok': ('ok', 'CHECKMARK'), 'note': ('note', 'INFO'),
              'dim': ('dot', 'DOT'), '': (None, 'BLANK1'), 'printer': (None, 'BLANK1')}
_KIND_TONE = {'COLLIDE': 'fail', 'CLEAR': 'warn', 'WALL': 'wall', 'PARTIAL': 'out', 'OUTSIDE': 'out'}


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

        # a sign, not a switch: the switches are in the two panels below
        row = layout.row()
        row.scale_y = 1.6
        row.operator("bucketbuilder.status", text="Monitoring On", depress=True)

        status = mon.status() if mon is not None else None
        state, rows = overlay.summary(status)
        box = layout.box()
        col = box.column(align=True)
        for text, tone in rows:
            row = col.row()
            row.alert = tone == 'fail'            # (red is the one colour a panel's text can have)
            row.label(text=text, **_icon(tone))
        if status and status.get('printer'):
            col.label(text=status['printer'], icon='BLANK1')
        if mon is not None and mon.error:
            col.label(text=mon.error[:60], icon='INFO')


def problem_text(pr):
    """One problem as a line of the list."""
    if pr['kind'] == 'CLEAR':
        return f"{pr['a']}  |  {pr['b']}   {'~' if pr['approx'] else ''}{pr['dist_mm']:.2f} mm"
    if pr['kind'] == 'COLLIDE':
        if pr['inside'] == 1:
            return f"{pr['a']}  inside  {pr['b']}"
        if pr['inside'] == 2:
            return f"{pr['b']}  inside  {pr['a']}"
        return f"{pr['a']}  x  {pr['b']}"
    if pr['kind'] == 'WALL':
        return f"{pr['a']}   {pr['dist_mm']:.2f} mm to wall"
    if pr['kind'] == 'PARTIAL':
        return f"{pr['a']}   partly outside"
    return f"{pr['a']}   outside"


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

        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = n > 0
        op = sub.operator("bucketbuilder.step_problem", text="Previous", icon='TRIA_LEFT')
        op.direction = -1
        op = sub.operator("bucketbuilder.step_problem", text="Next", icon='TRIA_RIGHT')
        op.direction = 1
        # going to a problem then shows its parts on their own (local view)
        row.operator("bucketbuilder.isolate", text="", depress=st.isolate,
                     icon='SOLO_ON' if st.isolate else 'SOLO_OFF')
        if n == 0:
            layout.label(text="Nothing to fix", **_icon('ok'))
            return

        active = mon.active_index(st)
        first = 0
        if active >= LIST_ROWS:
            first = min(active - LIST_ROWS // 2, max(0, n - LIST_ROWS))
        col = layout.column(align=True)
        for i in range(first, min(n, first + LIST_ROWS)):
            pr = problems[i]
            ignored = pr['ignored']
            row = col.row(align=True)
            # the problem itself: click to go there.  Greyed when it is ignored.
            sub = row.row(align=True)
            sub.active = not ignored
            op = sub.operator("bucketbuilder.focus_problem", text=problem_text(pr),
                              depress=(i == active), **_icon(_KIND_TONE[pr['kind']]))
            op.index = i
            # ... and its three buttons: on its own, ignore, ignore for good
            alone = st.isolate and i == active
            names = pr['names']
            for idname, icon, down, lock in (
                    ("bucketbuilder.isolate_problem", 'SOLO_ON' if alone else 'SOLO_OFF', alone, None),
                    ("bucketbuilder.ignore_problem", 'HIDE_ON' if ignored else 'HIDE_OFF',
                     bool(ignored), False),
                    ("bucketbuilder.ignore_problem", 'LOCKED' if ignored == 2 else 'UNLOCKED',
                     ignored == 2, True)):
                op = row.operator(idname, text="", icon=icon, depress=down)
                op.kind = pr['key'][0]
                op.a, op.b = names
                if lock is not None:
                    op.lock = lock
        if n > LIST_ROWS:
            layout.label(text=f"Showing {first + 1}-{min(n, first + LIST_ROWS)} of {n}")
        if len(st.ignored_problems):
            row = layout.row()
            k = sum(1 for pr in problems if pr['ignored'])
            row.label(text=f"{k} ignored", **_icon('note'))
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
        col = _split(body)
        row = col.row(align=True, heading="Clearance (mm)")
        row.prop(st, "use_clearance", text="")
        sub = row.row(align=True)
        sub.active = st.use_clearance
        sub.prop(st, "clearance_mm", text="")
        body.prop(st, "detect_enclosed")
        # (which parts are checked goes for the build volume as well)
        layout.prop(st, "ignore_hidden")


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
        col = _split(body)
        col.active = st.use_volume
        row = col.row(align=True, heading="Wall Gap (mm)")
        row.prop(st, "use_wall_clearance", text="")
        sub = row.row(align=True)
        sub.active = st.use_wall_clearance
        sub.prop(st, "wall_clearance_mm", text="")

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
