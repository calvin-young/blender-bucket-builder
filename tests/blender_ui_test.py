"""Runs every panel and menu draw function against a checking stand-in for
UILayout (background Blender has no window to draw in).

The stand-in verifies what a real layout would reject at draw time: property
names, operator ids and their arguments, menu ids, icon names.
"""

import importlib
import sys
import time

import bpy

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor, ui, ops, props = bc.monitor, bc.ui, bc.ops, bc.props

ICONS = set(bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items.keys())
LOG = []


class OpProps:
    def __init__(self, rna):
        object.__setattr__(self, '_rna', rna)

    def __setattr__(self, name, value):
        assert name in self._rna.properties, f'operator {self._rna.identifier} has no property {name!r}'


class FakeLayout:
    """Accepts the UILayout calls the add-on makes and validates their arguments."""

    def __init__(self):
        object.__setattr__(self, '_attrs', {})

    def __setattr__(self, name, value):
        assert name in ('use_property_split', 'use_property_decorate', 'scale_y', 'scale_x',
                        'alert', 'enabled', 'active', 'alignment', 'operator_context',
                        'ui_units_x'), name
        self._attrs[name] = value

    def _icon(self, kw):
        icon = kw.get('icon')
        assert icon is None or icon in ICONS, f'unknown icon {icon!r}'

    def row(self, **kw):
        assert set(kw) <= {'align', 'heading'}, kw
        return FakeLayout()

    def column(self, **kw):
        assert set(kw) <= {'align', 'heading'}, kw
        return FakeLayout()

    def box(self):
        return FakeLayout()

    def panel(self, idname, **kw):
        # a collapsible section inside a panel: (its header, its body);
        # here the body is always there, as if every section were open
        assert set(kw) <= {'default_closed'}, kw
        assert idname.startswith('bucketbuilder_'), idname
        LOG.append(('panel', idname))
        return FakeLayout(), FakeLayout()

    def separator(self, **kw):
        pass

    def label(self, **kw):
        assert set(kw) <= {'text', 'icon', 'icon_value'}, kw
        self._icon(kw)
        assert not ('icon' in kw and 'icon_value' in kw), kw
        LOG.append(('label', kw.get('text', '')))
        LOG.append(('label_icon', kw.get('icon', 'custom' if 'icon_value' in kw else '')))
        if self._attrs.get('alert'):
            LOG.append(('alert', kw.get('text', '')))
        if self._attrs.get('active') is False:
            LOG.append(('greyed', kw.get('text', '')))

    def template_list(self, cls_name, list_id, data, prop, active_data, active_prop, **kw):
        # Blender's list widget: the rows the list's own filter lets through
        # are drawn, each by the list's draw_item
        assert set(kw) <= {'rows', 'maxrows', 'sort_lock', 'type'}, kw
        cls = getattr(bpy.types, cls_name)
        assert prop in data.bl_rna.properties and active_prop in active_data.bl_rna.properties
        rows = getattr(data, prop)
        flags, order = cls.filter_items(ListHolder, CONTEXT[0], data, prop)
        assert len(flags) == len(rows) and not order
        shown = [i for i, f in enumerate(flags) if f & ListHolder.bitflag_filter_item]
        LOG.append(('list', len(shown)))
        active = getattr(active_data, active_prop)
        for i in shown:
            n0 = len(LOG)
            layout = FakeLayout()
            cls.draw_item(ListHolder, CONTEXT[0], layout, data, rows[i], 0, active_data, active_prop, i)
            texts = [t for k, t in LOG[n0:] if k == 'label']
            assert len(texts) == 1, ('a row of the list has one line of text', texts)
            LOG.append(('row', texts[0]))
            if i == active:
                LOG.append(('lit', texts[0]))

    def prop(self, data, name, **kw):
        assert set(kw) <= {'text', 'icon', 'toggle', 'expand', 'slider', 'emboss'}, kw
        assert name in data.bl_rna.properties, f'{type(data).__name__} has no property {name!r}'
        self._icon(kw)
        LOG.append(('prop', name))
        if self._attrs.get('enabled') is False:
            LOG.append(('shown only', name))

    def prop_enum(self, data, name, value, **kw):
        assert set(kw) <= {'text', 'icon'}, kw
        prop = data.bl_rna.properties[name]
        assert value in prop.enum_items.keys(), f'{name} has no value {value!r}'
        self._icon(kw)
        LOG.append(('prop_enum', name + '=' + value))

    def operator(self, idname, **kw):
        assert set(kw) <= {'text', 'icon', 'icon_value', 'depress', 'emboss'}, kw
        self._icon(kw)
        mod, fn = idname.split('.')
        op = getattr(getattr(bpy.ops, mod), fn)
        rna = op.get_rna_type()            # raises if the operator is not registered
        LOG.append(('operator', idname))
        LOG.append(('operator_text', kw.get('text', rna.name)))      # (its own name if none is given)
        LOG.append(('operator_icon', (idname, kw.get('icon', 'custom' if 'icon_value' in kw else ''))))
        if kw.get('depress'):
            LOG.append(('pressed', (idname, kw.get('icon', ''))))
        return OpProps(rna)

    def menu(self, idname, **kw):
        assert set(kw) <= {'text', 'icon'}, kw
        assert hasattr(bpy.types, idname), f'menu {idname} is not registered'
        LOG.append(('menu', idname))


class Holder:
    def __init__(self):
        self.layout = FakeLayout()


class ListHolder:
    """What a list's methods are given as ``self``."""
    bitflag_filter_item = 1 << 30


CONTEXT = [None]          # the context of the panel that is being drawn


class Context:
    """The context as a panel of the 3D viewport's sidebar sees it: with the
    viewport as its space.  (In background mode nothing is under the mouse.)"""

    def __init__(self, context):
        self._context = context
        self.space_data = next(a.spaces.active for a in context.screen.areas if a.type == 'VIEW_3D')

    def __getattr__(self, name):
        return getattr(self._context, name)


class PrefHolder:
    """The preferences object with a checking layout in place of the real one."""

    def __init__(self, prefs):
        self._prefs = prefs
        self.layout = FakeLayout()

    def __getattr__(self, name):
        return getattr(self._prefs, name)


def draw_all(context, tag):
    n0 = len(LOG)
    context = Context(context)
    CONTEXT[0] = context
    for cls in ui.CLASSES:
        if not issubclass(cls, bpy.types.Panel):
            continue
        poll = getattr(cls, 'poll', None)
        if poll is not None and not cls.poll(context):
            continue
        cls.draw(Holder(), context)
    for cls in ops.CLASSES:
        if issubclass(cls, bpy.types.Menu):
            cls.draw(Holder(), context)
    # what the add-on adds to Blender's own panels and menus
    for host, fn in ui._EXTRA:
        assert hasattr(bpy.types, host), f'{host} does not exist in this Blender'
        fn(Holder(), context)
    # the preferences page too
    p = props.prefs(context)
    if p is not None:
        type(p).draw(PrefHolder(p), context)
    print(f'  {tag}: {len(LOG) - n0} layout calls, no errors')


def settle():
    sc = bpy.context.scene
    mon = monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    time.sleep(monitor.IDLE_SECONDS + 0.05)
    mon.settle(sc, vl.depsgraph, vl)
    return mon


def labels_since(n0):
    return [t for k, t in LOG[n0:] if k == 'label']


def main():
    ctx = bpy.context
    sc = ctx.scene
    st = sc.bucket_builder
    props.seed_profiles(props.prefs())

    n0 = len(LOG)
    draw_all(ctx, 'monitoring off')
    for name in ('detect_collisions', 'monitor_volume', 'ignore_hidden', 'detect_enclosed', 'unit_mode',
                 'show_labels_clearance', 'show_markers_clearance', 'local_view_mode'):
        assert ('prop', name) in LOG, f'{name} is missing from the sidebar'
    for idname in ('bucketbuilder_volume_advanced', 'bucketbuilder_display_colors'):
        assert ('panel', idname) in LOG, idname
    for name in ('collision_mm', 'show_labels', 'enabled'):
        assert ('prop', name) not in LOG, name
    for idname in ('bucketbuilder.status', 'bucketbuilder.export_3mf', 'bucketbuilder.frame_volume'):
        assert ('operator', idname) in LOG, idname
    assert ('prop_enum', 'color_type=SINGLE') in LOG and ('prop_enum', 'color_type=RANDOM') in LOG, \
        'the two colour buttons are missing'
    labels = labels_since(n0)
    assert 'Not checking collisions' in labels and 'Not checking the build volume' in labels, labels[:6]
    assert 'Collision' in labels and 'Clearance' in labels, 'the display switches have no headings'
    # the panels are separate ones now, each with its own header
    tops = [c.bl_label for c in ui.CLASSES if issubclass(c, bpy.types.Panel) and not getattr(c, 'bl_parent_id', '')]
    assert tops == ['Bucket Builder', 'Collision Detection', 'Build Volume', 'Options', 'Export'], tops
    assert bpy.ops.bucketbuilder.export_3mf() == {'CANCELLED'}, 'the placeholder must do nothing'
    assert bpy.ops.bucketbuilder.status() == {'CANCELLED'}
    # the sign at the top says nothing that is untrue while both switches are off
    texts = [t for k, t in LOG[n0:] if k == 'operator_text']
    assert 'Bucket Monitoring' in texts and not any('Monitoring On' in t for t in texts), texts[:4]

    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    st.detect_collisions = True
    st.monitor_volume = True
    settle()
    n0 = len(LOG)
    draw_all(ctx, 'monitoring on, empty scene')
    assert 'No Parts' in labels_since(n0), labels_since(n0)[:6]

    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, location=(100, 100, 100))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, location=(130, 100, 100))
    bpy.ops.mesh.primitive_cube_add(size=30, location=(100, 100, 142))      # 2 mm above the first sphere
    bpy.ops.mesh.primitive_cube_add(size=30, location=(-5, 100, 100))       # through the wall
    for i in range(14):                                                    # a long problem list
        bpy.ops.mesh.primitive_cube_add(size=10, location=(20 + 12 * i, 250, 20))
    ctx.view_layer.update()
    mon = settle()
    mon.need_resync = True                              # (as the timer does: names may be new)
    mon = settle()
    s = mon.status()
    assert s['collisions'] >= 1 and s['clearance'] >= 1 and s['partly_out'] == 1, s
    assert len(mon.problems()) > ui.LIST_ROWS, len(mon.problems())
    n0 = len(LOG)
    draw_all(ctx, 'scene with problems')
    labels = labels_since(n0)
    alerts = [t for k, t in LOG[n0:] if k == 'alert']
    assert '1 Part Outside Build Volume' in labels and any('Clearance Warning' in t for t in labels), labels[:8]
    assert not alerts, ('the lines of the verdict are all plain: their colour is in the icons', alerts)
    assert 'HP MJF 5600' in labels and LOG[n0:][LOG[n0:].index(('label', 'HP MJF 5600')) + 1] == ('label_icon', 'INFO')
    # The list of problems: previous, on its own, next, in that order; then
    # Blender's list widget with a row for every problem, each with one of
    # Blender's own icons (one per kind), a colour tab that is shown and
    # cannot be clicked, and a magnifier for "on its own".
    called = [t for k, t in LOG[n0:] if k == 'operator']
    at = called.index('bucketbuilder.step_problem')
    assert called[at:at + 3] == ['bucketbuilder.step_problem', 'bucketbuilder.isolate',
                                 'bucketbuilder.step_problem'], called[at:at + 4]
    texts = [t for k, t in LOG[n0:] if k == 'operator_text']
    assert any(t.endswith('Previous') for t in texts) and any(t.startswith('Next') for t in texts), texts[:6]
    problems = mon.problems()
    rows = [t for k, t in LOG[n0:] if k == 'row']
    assert ('list', len(problems)) in LOG[n0:] and rows == [ui.problem_text(pr) for pr in problems], rows[:4]
    kinds = [pr['kind'] for pr in problems]
    icons = [LOG[i + 1][1] for i in range(n0, len(LOG) - 1) if LOG[i][0] == 'label' and LOG[i][1] in rows
             and LOG[i + 1][0] == 'label_icon'][:len(rows)]
    assert icons == [ui._KIND_ICON[k] for k in kinds], (icons, kinds)
    assert len(set(ui._KIND_ICON[k] for k in ('PARTIAL', 'COLLIDE', 'CLEAR', 'WALL'))) == 4
    assert ui._KIND_ICON['WALL'] == 'OBJECT_HIDDEN'
    op_icons = [t for k, t in LOG[n0:] if k == 'operator_icon']
    assert ('bucketbuilder.isolate', 'VIEWZOOM') in op_icons and \
        ('bucketbuilder.isolate_problem', 'VIEWZOOM') in op_icons, 'the magnifier is missing'
    assert not any(icon.startswith('SOLO') for _, icon in op_icons), 'a star is left'
    tabs = [t for k, t in LOG[n0:] if k == 'shown only']
    assert tabs == [ui._KIND_TAB[k] for k in kinds], (tabs, kinds)
    p = props.prefs()
    assert tuple(p.tab_collision) == tuple(p.color_collision)[:3] and \
        tuple(p.tab_outside) == tuple(p.color_outside)[:3], 'a tab is not in the colour of its kind'
    p.tab_collision = (0.0, 1.0, 0.0)                   # (cannot be set: it follows the colour)
    assert tuple(p.tab_collision) == tuple(p.color_collision)[:3]
    # clearance warnings: the closest pair first, the distance at the front
    gaps = [pr for pr in problems if pr['kind'] == 'CLEAR']
    assert len(gaps) >= 2 and [pr['dist_mm'] for pr in gaps] == sorted(pr['dist_mm'] for pr in gaps)
    assert ui.problem_text(gaps[0]).startswith('[') and ' mm]  ' in ui.problem_text(gaps[0]), ui.problem_text(gaps[0])
    assert ui._mm({'dist_mm': 3.0, 'approx': False}) == '[3.0 mm]' and \
        ui._mm({'dist_mm': 0.4, 'approx': True}) == '[~0.40 mm]'
    # a click on a row goes to its problem, and the row is then the one that is lit
    wm = bpy.context.window_manager
    wm.bucket_builder_row = 2
    assert mon.active_index(st) == 2 and wm.bucket_builder_row == 2, (mon.active_index(st), wm.bucket_builder_row)
    n1 = len(LOG)
    draw_all(ctx, 'a row clicked')
    assert [t for k, t in LOG[n1:] if k == 'lit'] == [ui.problem_text(problems[2])]
    last = len(mon.problems()) - 1
    assert bpy.ops.bucketbuilder.focus_problem(index=last) == {'FINISHED'} and mon.active_index(st) == last
    draw_all(ctx, 'last problem active')
    assert bpy.ops.bucketbuilder.step_problem(direction=0) == {'FINISHED'}      # (it used to go round for ever)

    # the same lines are in the viewport badge
    state, head, lines = bc.overlay.badge_text(mon.status())
    assert state == 'FAIL' and head == '1 Part Outside Build Volume' and lines[0].endswith('Detected'), (
        state, head, lines)
    assert labels[:len(lines) + 1] == [head] + lines, (labels[:6], head, lines)
    print('  badge:', state, '|', head, '|', lines)

    # a problem of the list ignored, and ignored for good: greyed, counted, and back
    assert bpy.ops.bucketbuilder.focus_problem(index=0) == {'FINISHED'}
    first = mon.problems()[1]
    assert first['kind'] == 'COLLIDE', first['kind']
    args = dict(kind=first['key'][0], a=first['names'][0], b=first['names'][1])
    assert bpy.ops.bucketbuilder.ignore_problem(**args) == {'FINISHED'}
    n0 = len(LOG)
    draw_all(ctx, 'a problem ignored')
    greyed = [t for k, t in LOG[n0:] if k == 'greyed' and ('row', t) in LOG[n0:]]
    pressed = [t for k, t in LOG[n0:] if k == 'pressed']
    assert greyed == [ui.problem_text(first)], greyed
    assert [t for k, t in LOG[n0:] if k == 'shown only'][1] == 'tab_ignored', 'an ignored row keeps its colour'
    assert ('bucketbuilder.ignore_problem', 'HIDE_ON') in pressed and \
        ('bucketbuilder.ignore_problem', 'LOCKED') not in pressed, pressed
    assert '1 ignored' in labels_since(n0) and '1 problem ignored' in labels_since(n0), labels_since(n0)[:8]
    assert bpy.ops.bucketbuilder.ignore_problem(lock=True, **args) == {'FINISHED'}
    n0 = len(LOG)
    draw_all(ctx, 'a problem ignored for good')
    assert ('bucketbuilder.ignore_problem', 'LOCKED') in [t for k, t in LOG[n0:] if k == 'pressed']
    assert bpy.ops.bucketbuilder.step_problem(direction=1) == {'FINISHED'} and st.problem_index == 2, (
        'Next passes over what is ignored', st.problem_index)
    assert bpy.ops.bucketbuilder.count_all_problems() == {'FINISHED'}
    # The row the user is at is that problem, wherever the list puts it: when
    # the problem above it is solved it moves up, and stays the active row.
    at = mon.problems()[2]
    assert mon.active_index(st) == 2 and at['id'] == st.problem_key
    out = next(o for o in sc.objects if o.name == mon.problems()[0]['names'][0])
    home = tuple(out.location)
    out.location = (100, 20, 300)                       # the part that was outside: now inside
    ctx.view_layer.update()
    mon = settle()
    assert mon.problems()[1]['id'] == at['id'] and mon.active_index(st) == 1, (
        'the active row did not follow its problem', mon.active_index(st))
    assert bpy.ops.bucketbuilder.step_problem(direction=1) == {'FINISHED'} and mon.active_index(st) == 2
    out.location = home
    ctx.view_layer.update()
    mon = settle()
    n0 = len(LOG)
    draw_all(ctx, 'none ignored')
    assert not [t for k, t in LOG[n0:] if k == 'greyed' and ('row', t) in LOG[n0:]] and \
        '1 ignored' not in labels_since(n0)
    assert bpy.ops.bucketbuilder.ignore_problem(kind='P', a='nobody', b='nothing') == {'CANCELLED'}
    print('  problems: ignored, locked, passed over by Next, and counted again')

    # ignoring parts: the list of what is left out, and the ways back in
    objs = [o for o in sc.objects if o.type == 'MESH']
    for o in objs[:15]:
        o.bucket_builder_ignore = True
    mon = settle()
    assert len(ops.ignored_objects(sc)) == 15 and mon.status()['objects'] == len(objs) - 15
    n0 = len(LOG)
    draw_all(ctx, '15 parts ignored')
    labels = labels_since(n0)
    assert '15 ignored:' in labels and objs[0].name in labels, labels
    assert sum(1 for o in objs[:15] if o.name in labels) == ui.IGNORED_ROWS, 'the list is not capped'
    assert f'... and {15 - ui.IGNORED_ROWS} more' in labels, labels
    assert bpy.ops.bucketbuilder.include(name=objs[0].name) == {'FINISHED'}
    assert not objs[0].bucket_builder_ignore and len(ops.ignored_objects(sc)) == 14
    assert bpy.ops.bucketbuilder.include(name='no such object') == {'CANCELLED'}
    assert bpy.ops.bucketbuilder.include_all() == {'FINISHED'}
    mon = settle()
    assert not ops.ignored_objects(sc) and mon.status()['objects'] == len(objs), mon.status()
    n0 = len(LOG)
    draw_all(ctx, 'everything included again')
    assert not any('ignored' in t for t in labels_since(n0)), labels_since(n0)
    print('  ignore list: one part and all parts can be taken in again')

    # a hidden part, and one that Blender does not evaluate
    objs[1].hide_set(True)
    objs[2].hide_viewport = True
    ctx.view_layer.update()
    mon = settle()
    bpy.ops.bucketbuilder.focus_problem(index=0)        # the list from its top
    n0 = len(LOG)
    draw_all(ctx, 'one part hidden, one disabled in viewports')
    labels = labels_since(n0)
    assert '1 of them hidden, and checked' in labels, labels
    assert any('disabled in viewports' in t for t in labels), labels
    assert any('(hidden)' in t for k, t in LOG[n0:] if k == 'row'), 'hidden part not marked'
    objs[1].hide_set(False)
    objs[2].hide_viewport = False
    ctx.view_layer.update()
    mon = settle()

    # the report for whoever is asked why something is slow
    lines = ops.report_lines(ctx)
    text = '\n'.join(lines)
    for word in ('Bucket Builder', 'Blender', 'Parts:', 'Neighbouring pairs', 'Cached data',
                 'While editing', 'Overlay', 'Clearance'):
        assert word in text, (word, text)
    assert lines[0].startswith('Bucket Builder 1.'), lines[0]      # the version is found
    assert bpy.ops.bucketbuilder.copy_report() == {'FINISHED'}
    print('  report:')
    for line in lines:
        print('    ' + line)
    assert 'Checking: collisions on, build volume on' in text, text
    st.detect_collisions = False
    st.monitor_volume = False
    assert 'Monitoring is off' in '\n'.join(ops.report_lines(ctx))
    n0 = len(LOG)
    draw_all(ctx, 'both switches off again')
    assert 'Not checking collisions' in labels_since(n0)
    st.detect_collisions = True
    settle()
    n0 = len(LOG)
    draw_all(ctx, 'collisions only')
    assert 'Not checking the build volume' in labels_since(n0) and \
        'Not checking collisions' not in labels_since(n0), labels_since(n0)[:8]
    st.monitor_volume = True
    settle()

    # A viewport in local view shows some of the parts only: the status box
    # says so, under a verdict that is still the whole build's.  (No viewport
    # here that could go into local view: the count is put in its place.)
    real = bc.overlay.local_count
    assert real(Context(ctx), mon) is None, 'this viewport is not in local view'
    n = mon.status()['objects']
    bc.overlay.local_count = lambda context, mon: (2, n)
    try:
        n0 = len(LOG)
        draw_all(ctx, 'viewport in local view')
    finally:
        bc.overlay.local_count = real
    labels = labels_since(n0)
    line = f'Local view: 2 of {n} parts shown'
    assert line in labels and labels.index(line) > 0, labels[:8]
    state, head, lines = bc.overlay.badge_text(mon.status(), (2, n))
    assert head == bc.overlay.badge_text(mon.status())[1] and line in lines, (head, lines)
    assert bc.overlay.badge_text(mon.status(), (1, 1))[2].count('Local view: 1 of 1 part shown') == 1
    print('  local view:', line)

    # isolating needs a viewport; without one the operator must not fail
    bpy.ops.bucketbuilder.focus_problem(index=0)
    assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'} and st.isolate
    assert bpy.ops.bucketbuilder.step_problem(direction=1) == {'FINISHED'}
    assert bpy.ops.bucketbuilder.isolate() == {'FINISHED'} and not st.isolate
    print('UI TEST OK')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('UI TEST FAILED')
    sys.exit(1)
