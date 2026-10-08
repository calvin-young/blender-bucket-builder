# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panels (3D Viewport > Sidebar > Bucket)."""

import bpy
from bpy.types import Panel

from . import monitor, overlay, props

LIST_ROWS = 10

_KIND_ICON = {'COLLIDE': 'CANCEL', 'CLEAR': 'ERROR', 'WALL': 'ERROR', 'PARTIAL': 'SHADING_BBOX',
              'OUTSIDE': 'SHADING_BBOX'}
_STATE_ICON = {'OK': 'CHECKMARK', 'WARN': 'CHECKMARK', 'FAIL': 'CANCEL', 'BUSY': 'TIME'}


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bucket"


class BUCKETBUILDER_PT_main(_Base, Panel):
    bl_label = "Bucket Builder"
    bl_idname = "BUCKETBUILDER_PT_main"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = props.settings(scene)
        mon = monitor.get(scene)

        row = layout.row()
        row.scale_y = 1.5
        row.prop(st, "enabled", toggle=True,
                 text="Monitoring On" if st.enabled else "Enable Monitoring",
                 icon='HIDE_OFF' if st.enabled else 'HIDE_ON')

        if st.enabled and mon is not None:
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

        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(st, "collision_mm", text="Collision (mm)")
        row = col.row(align=True, heading="Clearance (mm)")
        row.prop(st, "use_clearance", text="")
        sub = row.row(align=True)
        sub.active = st.use_clearance
        sub.prop(st, "clearance_mm", text="")
        layout.prop(st, "detect_enclosed")

        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(st, "unit_mode")
        if st.enabled and mon is not None and mon.unit_note:
            row = col.row()
            row.alignment = 'RIGHT'
            row.label(text=mon.unit_note)


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
        row.enabled = n > 0
        op = row.operator("bucketbuilder.step_problem", text="Previous", icon='TRIA_LEFT')
        op.direction = -1
        op = row.operator("bucketbuilder.step_problem", text="Next", icon='TRIA_RIGHT')
        op.direction = 1
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


class BUCKETBUILDER_PT_volume(_Base, Panel):
    bl_label = "Printer / Build Volume"
    bl_idname = "BUCKETBUILDER_PT_volume"
    bl_parent_id = "BUCKETBUILDER_PT_main"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)

        row = layout.row(align=True)
        row.menu("BUCKETBUILDER_MT_printers", text=st.printer or "Custom")
        row.operator("bucketbuilder.profile_add", text="", icon='ADD')
        row.operator("bucketbuilder.profile_update", text="", icon='FILE_TICK')
        row.operator("bucketbuilder.profile_remove", text="", icon='REMOVE')

        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(st, "volume_size", text="Size (mm)")
        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(st, "volume_align")
        col.prop(st, "volume_offset", text="Offset (mm)")

        col = layout.column(align=True)
        col.prop(st, "show_volume")
        col.prop(st, "use_volume", text="Warn When Parts Exceed Volume")

        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.active = st.use_volume
        row = col.row(align=True, heading="Wall Gap (mm)")
        row.prop(st, "use_wall_clearance", text="")
        sub = row.row(align=True)
        sub.active = st.use_wall_clearance
        sub.prop(st, "wall_clearance_mm", text="")
        layout.operator("bucketbuilder.frame_volume", icon='VIEWZOOM')


class BUCKETBUILDER_PT_display(_Base, Panel):
    bl_label = "Display"
    bl_idname = "BUCKETBUILDER_PT_display"
    bl_parent_id = "BUCKETBUILDER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        p = props.prefs(context)
        col = layout.column(align=True)
        col.prop(st, "show_overlay")
        sub = col.column(align=True)
        sub.active = st.show_overlay
        sub.prop(st, "show_labels")
        sub.prop(st, "show_tint")
        col.prop(st, "show_hud")
        if p is not None:
            col = layout.column(align=True)
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(p, "color_collision")
            col.prop(p, "color_clearance")
            col.prop(p, "color_outside")
            col.prop(p, "color_volume")
            col.prop(p, "tint_strength")
            col.prop(p, "volume_fill")
            col.prop(p, "hatch_spacing")
            col.prop(p, "xray")
            col.prop(p, "badge_scale")


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
        row = layout.row(align=True)
        op = row.operator("bucketbuilder.ignore", text="Ignore Selected")
        op.ignore = True
        op = row.operator("bucketbuilder.ignore", text="Include Selected")
        op.ignore = False

        if st.enabled and mon is not None:
            stats = mon.world.stats()
            col = layout.column(align=True)
            col.label(text=f"{stats['objects']} parts, {stats['triangles'] / 1e6:.2f} M triangles")
            col.label(text=f"{stats['pairs']} neighbouring pairs tracked")
            limit = stats['cache_limit']
            used = f"{stats['bytes'] / 1e6:.0f} MB of cached data"
            col.label(text=used + (f" (limit {limit / 2 ** 30:.1f} GB)" if limit else ""))
            if stats['borrowing']:
                # copies of a part in other rotations using one set of boxes
                col.label(text=f"{stats['borrowing']} parts share data (slower checks)")
            if mon.live_ms:
                col.label(text=f"Live update {max(mon.live_ms):.1f} ms at most recently")
            if mon.draw_ms:
                col.label(text=f"Overlay drawing {max(mon.draw_ms):.1f} ms at most")
            skipped = [o for o in mon.objs.values() if o.skipped]
            if skipped:
                box = layout.box()
                box.label(text=f"{len(skipped)} not checked:", icon='INFO')
                for o in skipped[:6]:
                    box.label(text=f"{o.name}: {o.skipped}")
            un = mon.unchecked
            if un:
                box = layout.box()
                box.label(text="In the scene but not checked:", icon='ERROR')
                if un.get('instances'):
                    box.label(text=f"instances made by {un['instances']} part(s)")
                    box.label(text="(add Realize Instances to check them)")
                if un.get('collections'):
                    box.label(text=f"{un['collections']} collection instance(s)")
                if un.get('other'):
                    box.label(text=f"{un['other']} text, curve or metaball object(s)")
                box.label(text="Convert them to meshes, or ignore them.")
        layout.operator("bucketbuilder.recheck", icon='FILE_REFRESH')


CLASSES = (
    BUCKETBUILDER_PT_main,
    BUCKETBUILDER_PT_problems,
    BUCKETBUILDER_PT_volume,
    BUCKETBUILDER_PT_display,
    BUCKETBUILDER_PT_parts,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
