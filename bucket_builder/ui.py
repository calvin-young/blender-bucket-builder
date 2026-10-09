# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panels (3D Viewport > Sidebar > Bucket), and the few places outside
the sidebar where an object can be left out of the checks."""

import bpy
from bpy.types import Panel

from . import monitor, ops, overlay, props

LIST_ROWS = 10
IGNORED_ROWS = 12

_KIND_ICON = {'COLLIDE': 'CANCEL', 'CLEAR': 'ERROR', 'WALL': 'ERROR', 'PARTIAL': 'SHADING_BBOX',
              'OUTSIDE': 'SHADING_BBOX'}
_STATE_ICON = {'OK': 'CHECKMARK', 'WARN': 'ERROR', 'FAIL': 'CANCEL', 'BUSY': 'TIME'}


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bucket"


def _split(layout):
    col = layout.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    return col


class BUCKETBUILDER_PT_main(_Base, Panel):
    bl_label = "Bucket Builder"
    bl_idname = "BUCKETBUILDER_PT_main"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = props.settings(scene)
        mon = monitor.get(scene)

        row = layout.row()
        row.scale_y = 1.6
        row.prop(st, "enabled", toggle=True,
                 text="Monitoring On" if st.enabled else "Enable Monitoring", icon='HIDE_OFF')

        if not st.enabled:
            col = layout.column(align=True)
            col.active = False
            col.label(text="Checks every part while you")
            col.label(text="arrange the build.")
            return
        if mon is None:
            return
        state, head, lines = overlay.badge_text(mon.status())
        box = layout.box()
        col = box.column(align=True)
        row = col.row()
        row.alert = state == 'FAIL'
        row.label(text=head, icon=_STATE_ICON[state])
        for line in lines:
            col.label(text=line, icon='BLANK1')
        if mon.error:
            col.label(text=mon.error[:60], icon='INFO')


class BUCKETBUILDER_PT_problems(_Base, Panel):
    bl_label = "Problems"
    bl_idname = "BUCKETBUILDER_PT_problems"
    bl_parent_id = "BUCKETBUILDER_PT_main"

    @classmethod
    def poll(cls, context):
        st = props.settings(context.scene)
        return st is not None and st.enabled

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
        row.operator("bucketbuilder.isolate", text="Isolate", depress=st.isolate,
                     icon='SOLO_ON' if st.isolate else 'SOLO_OFF')
        if n == 0:
            layout.label(text="Nothing to fix", icon='CHECKMARK')
            return

        active = st.problem_index
        first = 0
        if active >= LIST_ROWS:
            first = min(active - LIST_ROWS // 2, max(0, n - LIST_ROWS))
        col = layout.column(align=True)
        for i in range(first, min(n, first + LIST_ROWS)):
            pr = problems[i]
            if pr['kind'] == 'CLEAR':
                text = f"{pr['a']}  |  {pr['b']}   {'~' if pr['approx'] else ''}{pr['dist_mm']:.2f} mm"
            elif pr['kind'] == 'COLLIDE':
                if pr['inside'] == 1:
                    text = f"{pr['a']}  inside  {pr['b']}"
                elif pr['inside'] == 2:
                    text = f"{pr['b']}  inside  {pr['a']}"
                else:
                    text = f"{pr['a']}  x  {pr['b']}"
            elif pr['kind'] == 'WALL':
                text = f"{pr['a']}   {pr['dist_mm']:.2f} mm to wall"
            elif pr['kind'] == 'PARTIAL':
                text = f"{pr['a']}   partly outside"
            else:
                text = f"{pr['a']}   outside"
            op = col.operator("bucketbuilder.focus_problem", text=text, icon=_KIND_ICON[pr['kind']],
                              depress=(i == active))
            op.index = i
        if n > LIST_ROWS:
            layout.label(text=f"Showing {first + 1}-{min(n, first + LIST_ROWS)} of {n}")


class BUCKETBUILDER_PT_collisions(_Base, Panel):
    bl_label = "Part Collisions"
    bl_idname = "BUCKETBUILDER_PT_collisions"
    bl_parent_id = "BUCKETBUILDER_PT_main"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = props.settings(scene)
        mon = monitor.get(scene)
        layout.active = st.enabled

        col = _split(layout)
        row = col.row(align=True, heading="Clearance (mm)")
        row.prop(st, "use_clearance", text="")
        sub = row.row(align=True)
        sub.active = st.use_clearance
        sub.prop(st, "clearance_mm", text="")

        layout.prop(st, "ignore_hidden")

        header, body = layout.panel("bucketbuilder_collisions_advanced", default_closed=True)
        header.label(text="Advanced")
        if body is not None:
            body.prop(st, "detect_enclosed")
            col = _split(body)
            col.prop(st, "unit_mode")
            if st.enabled and mon is not None and mon.unit_note:
                row = col.row()
                row.alignment = 'RIGHT'
                row.label(text=mon.unit_note)


class BUCKETBUILDER_PT_volume(_Base, Panel):
    bl_label = "Build Volume"
    bl_idname = "BUCKETBUILDER_PT_volume"
    bl_parent_id = "BUCKETBUILDER_PT_main"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        layout.active = st.enabled

        layout.operator("bucketbuilder.frame_volume", icon='VIEWZOOM')

        row = layout.row(align=True)
        row.menu("BUCKETBUILDER_MT_printers", text=props.current_printer_name(st))
        row.operator("bucketbuilder.profile_add", text="", icon='ADD')
        row.operator("bucketbuilder.profile_update", text="", icon='FILE_TICK')
        row.operator("bucketbuilder.profile_remove", text="", icon='REMOVE')

        col = layout.column(align=True)
        col.prop(st, "show_volume")
        col.prop(st, "use_volume")
        col = _split(layout)
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


class BUCKETBUILDER_PT_display(_Base, Panel):
    bl_label = "Display"
    bl_idname = "BUCKETBUILDER_PT_display"
    bl_parent_id = "BUCKETBUILDER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        p = props.prefs(context)
        layout.active = st.enabled
        col = layout.column(align=True)
        col.prop(st, "show_hud")
        col.prop(st, "show_overlay")
        sub = col.column(align=True)
        sub.active = st.show_overlay
        sub.prop(st, "show_tint")
        sub.prop(st, "show_curves")
        sub.prop(st, "show_gap_lines")
        sub.prop(st, "show_markers_collision")
        sub.prop(st, "show_markers_clearance")
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


class BUCKETBUILDER_PT_parts(_Base, Panel):
    bl_label = "Parts"
    bl_idname = "BUCKETBUILDER_PT_parts"
    bl_parent_id = "BUCKETBUILDER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = props.settings(scene)
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

        if st.enabled and mon is not None:
            stats = mon.world.stats()
            s = mon.status()
            col = layout.column(align=True)
            col.label(text=f"{stats['objects']} parts, {ops.triangles_text(stats['triangles'])}")
            if s['hidden']:
                col.label(text=f"{s['hidden']} of them hidden, and checked")
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


class BUCKETBUILDER_PT_performance(_Base, Panel):
    bl_label = "Performance"
    bl_idname = "BUCKETBUILDER_PT_performance"
    bl_parent_id = "BUCKETBUILDER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = props.settings(scene)
        mon = monitor.get(scene)
        if st.enabled and mon is not None:
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


CLASSES = (
    BUCKETBUILDER_PT_main,
    BUCKETBUILDER_PT_problems,
    BUCKETBUILDER_PT_collisions,
    BUCKETBUILDER_PT_volume,
    BUCKETBUILDER_PT_display,
    BUCKETBUILDER_PT_parts,
    BUCKETBUILDER_PT_performance,
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
