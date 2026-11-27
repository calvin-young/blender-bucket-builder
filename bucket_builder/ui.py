# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panels (3D Viewport > Sidebar > Bucket), and the few places outside
the sidebar where an object can be left out of the checks.

    Bucket Builder        how the build stands, and the list of problems
    Collision Detection   its switch and settings; which parts are checked
    Build Volume          its switch, the printer, the wall gap
    Build Report          density, packed height, layer profile
    Options               what is drawn, performance, units
    Export                (to come)
"""

import bpy
from bpy.types import Panel, UIList

from . import icons, monitor, ops, overlay, packing, props, report

LIST_ROWS = 10
IGNORED_ROWS = 12
TAB_WIDTH = 0.3       # of the color tab of a row in the list of problems, in interface units

# the icon of every tone of overlay.summary: (ours, Blender's if ours could not be made)
_TONE_ICON = {'fail': ('cross', 'CANCEL'), 'out': ('box', 'SHADING_BBOX'), 'warn': ('warn', 'ERROR'),
              'wall': ('gap', 'SHADING_BBOX'), 'ok': ('ok', 'CHECKMARK'), 'note': ('note', 'INFO'),
              'dim': ('dot', 'DOT'), '': (None, 'BLANK1'), 'printer': (None, 'BLANK1'),
              'view': (None, 'VIEWZOOM'), 'staged': ('box', 'SHADING_BBOX')}
# The list of problems has Blender's own icons, in the color of its text: a
# colored icon on every row was too loud.  The color of a kind is a slim
# tab at the left end of its row (see props._tab).
_KIND_ICON = {'PARTIAL': 'SHADING_BBOX', 'OUTSIDE': 'SHADING_BBOX', 'COLLIDE': 'CANCEL',
              'CLEAR': 'ERROR', 'WALL': 'OBJECT_HIDDEN', 'MESH': 'MESH_DATA', 'PQ': 'INFO'}
_KIND_TAB = {'PARTIAL': 'tab_outside', 'OUTSIDE': 'tab_outside', 'COLLIDE': 'tab_collision',
             'CLEAR': 'tab_clearance', 'WALL': 'tab_wall', 'MESH': 'tab_clearance', 'PQ': 'tab_quality'}
ISOLATE_ICON = 'VIEWZOOM'


def _icon(tone):
    """Keyword arguments that give a label or a button the icon of a tone."""
    name, fallback = _TONE_ICON[tone]
    value = icons.get(name) if name else 0
    return {'icon_value': value} if value else {'icon': fallback}


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bucket Monitor"


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
        # (The color of a line is in its icon.  A panel's text can be made
        # red and nothing else, and one red line among white ones looked like
        # a mistake: so all of them are plain.)
        for text, tone in rows:
            col.label(text=text, **_icon(tone))
        off = status.get('scale_off') if status else None
        if off:
            # parts that look imported in the wrong unit: the fix, one click (the owner, 2026-11-23)
            op = box.operator("bucketbuilder.fix_scale", icon='FULLSCREEN_ENTER',
                              text="Scale \u00d71000" if off[0] > 1 else "Scale \u00f71000")
            op.factor, op.names = off[0], "\n".join(off[1])
        if status and status.get('touching'):
            row = box.row(align=True)
            row.operator("bucketbuilder.move_inside", text="Nudge All Inside", icon='ARROW_LEFTRIGHT').selected_only = False
            row.operator("bucketbuilder.move_inside", text="Nudge Selected", icon='RESTRICT_SELECT_OFF').selected_only = True
        if status and status.get('printer'):
            col.label(text=status['printer'], icon='INFO')
        last = props.settings(scene).last_message
        if last:
            # the last result line, where it isn't missed (the owner, 2026-11-24)
            _wrapped(box.column(align=True), last, 'TIME')
        if mon is not None and mon.error:
            col.label(text=mon.error[:60], icon='INFO')


def _mm(pr):
    """A distance for the list: "[3.0 mm]", with two decimals under a millimeter."""
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
    if pr['kind'] == 'PQ':
        return f"{pr['a']}  |  {pr['b']}  {pr['pq']}" if pr['b'] else f"{pr['a']}   {pr['pq']}"
    if pr['kind'] == 'MESH':
        return f"{pr['a']}   {pr['mesh']}"
    if pr['kind'] == 'PARTIAL':
        if pr.get('touch'):
            return f"{pr['a']}   touching {pr['touch']}"
        return f"{pr['a']}   partly outside"
    return f"{pr['a']}   outside"


_PACK_COUNT = {}        # mesh pointer -> (copies to pack, a name to show), filled by filter_items


class BUCKETBUILDER_UL_pack_parts(UIList):
    """The parts the packer will pack, one row per mesh (copies of a mesh are
    one row): how many, how they may turn, whether they are essential.  The
    list is bpy.data.meshes, filtered to those of the parts to pack."""
    bl_idname = "BUCKETBUILDER_UL_pack_parts"

    def filter_items(self, context, data, propname):
        meshes = getattr(data, propname)
        sel = [o for o in context.selected_objects if o.type == 'MESH']
        move, _ = packing.candidates(context, bool(sel))
        _PACK_COUNT.clear()
        for o in move:
            k = o.data.as_pointer()
            n, name = _PACK_COUNT.get(k, (0, o.name))
            _PACK_COUNT[k] = (n + 1, min(name, o.name))
        flags = [self.bitflag_filter_item if m.as_pointer() in _PACK_COUNT else 0 for m in meshes]
        order = sorted(range(len(meshes)), key=lambda j: (-_PACK_COUNT.get(meshes[j].as_pointer(), (0, ''))[0],
                                                          meshes[j].name))
        new = [0] * len(meshes)
        for pos, j in enumerate(order):
            new[j] = pos
        return flags, new

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0,
                  flt_flag=0):
        n, name = _PACK_COUNT.get(item.as_pointer(), (0, item.name))
        row = layout.row(align=True)
        row.label(text="%s  \u00d7 %d" % (name, n), icon='MESH_DATA')
        m = row.row(align=True)
        m.ui_units_x = 4.5
        m.prop(item, "bucket_builder_pack_mode", text="")
        row.prop(item, "bucket_builder_pack_essential", text="", icon='SOLO_ON' if item.bucket_builder_pack_essential
                 else 'SOLO_OFF', emboss=False)


def _selected_names(context):
    return {o.name for o in context.selected_objects}


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
            # the color the problem has in the viewport (gray: ignored)
            tab = row.row(align=True)
            tab.ui_units_x = TAB_WIDTH
            tab.enabled = False
            tab.prop(p, 'tab_ignored' if ignored else _KIND_TAB[pr['kind']], text="")
        # the problem itself: a click on the row goes there.  Grayed when it is ignored.
        sub = row.row(align=True)
        sub.active = not ignored
        # a row about a part that is selected in the viewport carries the selection's arrow
        sel = _selected_names(context)
        picked = any(n in sel for n in pr['names'])
        sub.label(text=problem_text(pr), icon=_KIND_ICON[pr['kind']])
        if picked:
            row.label(text="", icon='RESTRICT_SELECT_OFF')
        # ... and its three buttons: on its own, ignore, ignore for good
        local = getattr(context.space_data, 'local_view', None) is not None
        alone = st.isolate and local and index == mon.active_index(st)
        names = pr['names']
        if pr['kind'] in ('PQ', 'MESH'):
            return                                  # (about the part itself: nothing to ignore between parts)
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
        row.operator("bucketbuilder.xray", text="", depress=context.window_manager.bucket_builder_xray,
                     icon='XRAY')
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
        layout.prop(st, "check_mesh")              # (works with either check: it reads the same parts)


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
        row.operator("bucketbuilder.profile_update", text="", icon='GREASEPENCIL')
        row.operator("bucketbuilder.profile_remove", text="", icon='REMOVE')
        layout.operator("bucketbuilder.frame_volume", icon='VIEWZOOM')

        # the two checkboxes are under the switch above: off there is off
        body = layout.column()
        body.active = st.monitor_volume
        col = body.column(align=True)
        col.prop(st, "show_volume")
        col.prop(st, "use_volume")
        col.prop(st, "snap_to_volume")          # (also in Manual Packing: the owner, 2026-11-23)
        col = body.column()
        col.active = st.use_volume
        _limit(col, st, "use_wall_clearance", "wall_clearance_mm")
        # (the size and placement of the volume are edited in the printer's own dialog: + and the pencil)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Build Report
# ---------------------------------------------------------------------------

def _wrapped(layout, text, icon, width=34):
    """A sentence over as many lines as it needs (a label is cut off at the
    edge of the sidebar); the icon goes at the first line."""
    import textwrap
    for i, line in enumerate(textwrap.wrap(text, width) or ['']):
        layout.label(text=line, icon=icon if i == 0 else 'BLANK1')


class BUCKETBUILDER_PT_report(_Base, Panel):
    bl_label = "Build Report"
    bl_idname = "BUCKETBUILDER_PT_report"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        _big(layout, st, "report_density", "Evaluate Density")
        if st.report_density:
            layout.prop(st, "show_pq")
        if not st.report_density:
            return
        mon = monitor.get(context.scene)
        if not (st.detect_collisions or st.monitor_volume) or mon is None:
            col = layout.column(align=True)
            col.label(text="Switch on Detect Collisions or", icon='INFO')
            col.label(text="Monitor Build Volume to read the parts.")
            return
        rep = mon.report
        if rep.error:
            layout.label(text=rep.error[:60], icon='ERROR')
        res = rep.result
        working = rep.pending or rep.result_sig != rep.guard_sig or bool(mon.queue)
        if res is None:
            layout.label(text="Reading the build...", icon='TIME')
            return
        col = layout.column(align=True)
        for key, value, tone in report.rows(res):
            row = col.row()
            row.label(text=key)
            row.label(text=value, **({'icon': 'ERROR'} if tone == 'warn' else {}))
        n_in = getattr(mon, 'hidden_inside', 0)
        if n_in:
            row = col.row()
            row.label(text="Hidden, Not Counted")
            row.label(text='%d' % n_in, icon='ERROR')
        if working:
            col.label(text="Updating...", icon='TIME')
        if res.warnings:
            box = layout.box()
            wcol = box.column(align=True)
            for text in res.warnings:
                _wrapped(wcol, text, 'ERROR')
        value = icons.get('profile')
        if rep.pixels is not None and value:
            box = layout.box()
            row = box.row(align=True)
            row.label(text="Layer Density", icon='GRAPH')
            row.operator("bucketbuilder.density_view", text="", icon='FULLSCREEN_ENTER', emboss=False)
            draw_density_figure(box, res, value, 9.0)
            bands = ops.dense_bands(context.scene)
            if bands:
                # the dense layers in the viewport, one band at a time (#53)
                box.prop(st, "show_dense")
                row = box.row(align=True)
                row.operator("bucketbuilder.dense_band_step", text="\u25C0").direction = -1
                row.label(text="Dense Band %d of %d" % (min(st.dense_band, len(bands) - 1) + 1, len(bands)))
                row.operator("bucketbuilder.dense_band_step", text="\u25B6").direction = 1


def draw_density_figure(layout, res, value, scale):
    """The scatter, the build plate under it, its scale and legend, one line each (they must read in
    a narrow sidebar; the owner, 2026-11-23)."""
    row = layout.row()
    row.alignment = 'CENTER'
    row.template_icon(icon_value=value, scale=scale)
    row = layout.row()
    row.alignment = 'CENTER'
    row.label(text="Build Plate")
    col = layout.column(align=True)
    col.label(text="Across: 0 to %.1f %% of the layer" % (max(res.peak_layer, 0.05) * 108.0))
    col.label(text="Up: %.0f mm" % res.height)
    col.label(text="Orange: dense layers")
    col.label(text="Gray: sparse layers")
    col.label(text="Line: the mean")


# ---------------------------------------------------------------------------
# Pack (experimental)
# ---------------------------------------------------------------------------

class BUCKETBUILDER_PT_pack(_Base, Panel):
    bl_label = "Autopacking"
    bl_idname = "BUCKETBUILDER_PT_pack"
    bl_category = "Bucket Packer"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        sel = [o for o in context.selected_objects if o.type == 'MESH']
        job = packing.JOB
        if job['running']:
            done, total = job['done'], max(job['total'], 1)
            left = "" if job['eta'] is None else ", ~%s left" % packing._duration(job['eta'])
            row = layout.row()
            row.scale_y = 1.4
            if job['stopping']:
                row.progress(factor=done / total, type='BAR', text="Stopping...")
            elif job.get('phase', 'greedy') == 'greedy':
                row.progress(factor=done / total, type='BAR', text="%d of %d placed%s" % (done, total, left))
            else:
                b = job.get('budget') or 0.0
                frac = 1.0 - (job['time_left'] or 0.0) / b if b > 0 else 0.0
                placed = job.get('placed', done)
                text = ("Jiggling%s" % left) if job.get('phase') == 'jiggling' else \
                    "Spreading the parts out" if job.get('phase') == 'spreading' else \
                    ("All in, improving: %.1f mm%s" % (job['height'], left) if placed >= total
                     else "%d of %d in, still trying%s" % (placed, total, left))
                row.progress(factor=frac, type='BAR', text=text)
                if job.get('phase') not in ('jiggling', 'spreading'):
                    _wrapped(layout.column(align=True), "Esc or Stop keeps the layout you see now.", 'INFO')
            row = layout.row()
            row.operator("bucketbuilder.pack_stop", icon='CANCEL')
        else:
            # three actions (the owner's choice, 2026-11-19): from scratch, keep trying, fix issues
            row = layout.row()
            row.scale_y = 1.6
            op = row.operator("bucketbuilder.pack", icon='MOD_ARRAY',
                              text=("Pack %d Selected From Scratch" % len(sel)) if sel else "Pack From Scratch")
            op.selected_only = bool(sel)
            op.improve = False
            op.fix = 'NONE'
            row = layout.row()
            # "Keep Trying" when parts wait beside the bucket, "Improve Packing" when every part is in (the
            # owner, 2026-11-24: the same action, named for what the user wants from it)
            mon_ = monitor.get(context.scene)
            waiting = (mon_.status().get('staged', 0) if mon_ is not None else 0)
            op = row.operator("bucketbuilder.pack", icon='FILE_REFRESH',
                              text="Keep Trying (%d waiting)" % waiting if waiting else "Improve Packing")
            op.selected_only = bool(sel)
            op.improve = True
            op.fix = 'NONE'
            row = layout.row(align=True)
            op = row.operator("bucketbuilder.pack", icon='MOD_NOISE', text="Fix Issues")
            op.selected_only = bool(sel)
            op.improve = False
            op.fix = 'FIX'
            row.prop(st, "pack_jiggle_time", text="")
            header, body = layout.panel("bucketbuilder_pack_fix", default_closed=True)
            header.label(text="Fix Issues Options")
            if body is not None:
                row = body.row(align=True)
                row.prop(st, "pack_relax", toggle=True)
                row = body.row(align=True)
                row.prop(st, "pack_jiggle_minor", toggle=True)
                row.prop(st, "pack_jiggle_major", toggle=True)
                row.prop(st, "pack_jiggle_reseat", toggle=True)
        layout.template_list("BUCKETBUILDER_UL_pack_parts", "", bpy.data, "meshes", context.window_manager,
                             "bucket_builder_pack_part", rows=3, maxrows=6)
        col = _split(layout)
        sub = col.row()
        sub.active = st.use_clearance          # (the aim is above the checker's clearance)
        sub.prop(st, "clearance_aim_mm")
        col.prop(st, "pack_preset")
        col.prop(st, "pack_quality", slider=True)
        col.prop(st, "pack_effort")
        if st.pack_effort == 'STANDARD':
            col.prop(st, "pack_time")
        elif st.pack_effort == 'BEST':
            col.prop(st, "pack_best_hours")
            col.prop(st, "pack_stall_minutes")
        elif st.pack_effort == 'OVERNIGHT':
            col.prop(st, "pack_overnight_hours")
        col.prop(st, "pack_mode")
        sub = col.column(align=True)
        sub.active = st.pack_mode == 'Z' or any(o.bucket_builder_pack_mode == 'Z' for o in sel)
        sub.prop(st, "pack_z_step")
        # one clearance for checker and packer: the checker's, when it checks it
        if st.use_clearance:
            col.label(text=f"Gap: {st.clearance_mm:.2f} mm (the clearance checked)")
        else:
            col.prop(st, "pack_gap")
        if st.use_wall_clearance:
            col.label(text=f"Wall Gap: {st.wall_clearance_mm:.2f} mm (the clearance checked)")
        else:
            col.prop(st, "pack_wall_gap")
        col.prop(st, "pack_max_density")
        layout.prop(st, "pack_leave_room")
        header, body = layout.panel("bucketbuilder_pack_advanced", default_closed=True)
        header.label(text="Advanced")
        if body is not None:
            _split(body).prop(st, "pack_cell")
            body.prop(st, "pack_keep_low")
            idle = packing.TIMING.get('proc') is None
            row = body.row()
            row.enabled = idle
            row.operator("bucketbuilder.pack_timing", icon='TIME',
                         text="Measure Packer Speed" if idle else "Measuring...")
        obj = context.active_object
        if obj is not None and obj.type == 'MESH':
            box = layout.box()
            box.label(text=obj.name, icon='OBJECT_DATA')
            col = _split(box)
            col.prop(obj, "bucket_builder_pack_mode")
            col.prop(obj, "bucket_builder_pack_lock")
            col.prop(obj, "bucket_builder_pack_essential")
        if st.pack_last:
            col = layout.column(align=True)
            col.active = False
            _wrapped(col, st.pack_last, 'INFO')
        col = layout.column(align=True)
        col.active = False
        _wrapped(col, "From Scratch packs every part again, first moving them beside the bucket; Improve "
                      "Packing (Keep Trying while parts wait) starts from the layout as it is and keeps the "
                      "best it finds; Fix Issues only makes small moves. All run in the "
                      "background: Esc or Stop keeps the best so far, Ctrl+Z puts the parts back.", 'INFO')


class BUCKETBUILDER_PT_options(_Base, Panel):
    bl_label = "Settings"
    bl_idname = "BUCKETBUILDER_PT_options"

    def draw(self, context):
        pass


def _view3d(context):
    """This 3D view, or (drawn in the Properties editor) the largest one on the screen."""
    space = context.space_data
    if space is not None and space.type == 'VIEW_3D':
        return space
    areas = [a for a in (context.screen.areas if context.screen else ()) if a.type == 'VIEW_3D']
    return max(areas, key=lambda a: a.width * a.height).spaces.active if areas else None


def _part_colors(context, layout):
    space = _view3d(context)
    shading = getattr(space, "shading", None)
    if shading is None:
        return
    # Random colors tell the parts apart; one plain color lets the
    # problems stand out.  Both are worth a click away.
    col = layout.column(align=True)
    col.label(text="Part Colors")
    row = col.row(align=True)
    row.active = shading.type == 'SOLID'
    row.prop_enum(shading, "color_type", 'SINGLE', text="Custom")
    row.prop_enum(shading, "color_type", 'RANDOM', text="Random")
    if shading.type != 'SOLID':
        # (the colors above are those of solid shading)
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
        _part_colors(context, layout)
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
            col.label(text=f"{stats['pairs']} neighboring pairs tracked")
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
        sub = col.row()
        sub.active = st.unit_mode == 'AUTO'
        sub.prop(st, "dynamic_bucket_size")
        if mon is not None and mon.unit_note:
            row = col.row()
            row.alignment = 'RIGHT'
            row.label(text=mon.unit_note)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class BUCKETBUILDER_PT_arrange(_Base, Panel):
    bl_label = "Manual Packing"
    bl_idname = "BUCKETBUILDER_PT_arrange"
    bl_category = "Bucket Packer"

    def draw(self, context):
        layout = self.layout
        st = props.settings(context.scene)
        col = layout.column(align=True)
        col.operator("bucketbuilder.drop_to_floor", icon='TRIA_DOWN_BAR')
        col = layout.column(align=True)
        col.label(text="Orient")
        col.operator("bucketbuilder.minimize_box", icon='SHADING_BBOX')
        col.operator("bucketbuilder.minimize_height", icon='SORT_DESC')
        col.operator("bucketbuilder.tilt_off_axis", icon='DRIVER_ROTATIONAL_DIFFERENCE')
        col.operator("bucketbuilder.align_to_plane", icon='EYEDROPPER')
        layout.prop(st, "snap_to_volume", icon='SNAP_ON' if st.snap_to_volume else 'SNAP_OFF')


class BUCKETBUILDER_PT_export(_Base, Panel):
    bl_label = "Export"
    bl_idname = "BUCKETBUILDER_PT_export"

    def draw(self, context):
        layout = self.layout
        from . import export
        warn = export.problems(context)
        if warn:
            # what would go wrong with this file, where it cannot be missed (#31: parts staged outside are
            # quiet while arranging, loud here)
            box = layout.box()
            box.alert = True
            col = box.column(align=True)
            for i, line in enumerate(warn):
                col.label(text=line, icon='ERROR' if i == 0 else 'BLANK1')
        row = layout.row()
        row.scale_y = 1.3
        ready = export.threemf_api() is not None
        row.enabled = ready
        row.operator("bucketbuilder.export_3mf", icon='EXPORT')
        if not ready:
            col = layout.column(align=True)
            _wrapped(col, "Export writes through the 3MF Import/Export add-on, which is not installed.", 'INFO')
            col.operator("bucketbuilder.get_3mf_addon", icon='IMPORT')


CLASSES = (
    BUCKETBUILDER_UL_problems,
    BUCKETBUILDER_UL_pack_parts,
    BUCKETBUILDER_PT_main,
    BUCKETBUILDER_PT_problems,
    BUCKETBUILDER_PT_volume,
    BUCKETBUILDER_PT_collisions,
    BUCKETBUILDER_PT_parts,
    BUCKETBUILDER_PT_arrange,
    BUCKETBUILDER_PT_report,
    BUCKETBUILDER_PT_pack,
    BUCKETBUILDER_PT_options,
    BUCKETBUILDER_PT_display,
    BUCKETBUILDER_PT_units,
    BUCKETBUILDER_PT_performance,
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


# ---------------------------------------------------------------------------
# where the panels live (#38)
# ---------------------------------------------------------------------------

def _panels():
    return [c for c in CLASSES if issubclass(c, Panel)]


# which tab each panel was written for (read once, before any is moved)
_TAB = {c: ('packer' if getattr(c, 'bl_category', '') == "Bucket Packer" else 'monitor') for c in CLASSES
        if issubclass(c, Panel)}


def _place():
    """Set every panel's space, region and tab from the preferences."""
    p = props.prefs()
    loc = getattr(p, 'panel_location', 'SIDEBAR') if p is not None else 'SIDEBAR'
    tabs = {'monitor': (getattr(p, 'sidebar_tab', '') or "Bucket Monitor") if p is not None else "Bucket Monitor",
            'packer': (getattr(p, 'packer_tab', '') or "Bucket Packer") if p is not None else "Bucket Packer"}
    for cls in _panels():
        if loc == 'SCENE':
            cls.bl_space_type, cls.bl_region_type, cls.bl_context = 'PROPERTIES', 'WINDOW', "scene"
        else:
            cls.bl_space_type, cls.bl_region_type, cls.bl_context = 'VIEW_3D', 'UI', ""
            cls.bl_category = tabs[_TAB[cls]]


def relocate():
    """Move the panels after a preference changed: unregister them, place them, register again."""
    panels = _panels()
    for cls in reversed(panels):
        if getattr(cls, 'is_registered', False):
            bpy.utils.unregister_class(cls)
    _place()
    for cls in panels:
        bpy.utils.register_class(cls)


def register():
    _place()
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
