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
                        'alert', 'enabled', 'active', 'alignment', 'operator_context'), name
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
        assert set(kw) <= {'text', 'icon'}, kw
        self._icon(kw)
        LOG.append(('label', kw.get('text', '')))

    def prop(self, data, name, **kw):
        assert set(kw) <= {'text', 'icon', 'toggle', 'expand', 'slider', 'emboss'}, kw
        assert name in data.bl_rna.properties, f'{type(data).__name__} has no property {name!r}'
        self._icon(kw)
        LOG.append(('prop', name))

    def operator(self, idname, **kw):
        assert set(kw) <= {'text', 'icon', 'depress', 'emboss'}, kw
        self._icon(kw)
        mod, fn = idname.split('.')
        op = getattr(getattr(bpy.ops, mod), fn)
        rna = op.get_rna_type()            # raises if the operator is not registered
        LOG.append(('operator', idname))
        LOG.append(('operator_text', kw.get('text', '')))
        return OpProps(rna)

    def menu(self, idname, **kw):
        assert set(kw) <= {'text', 'icon'}, kw
        assert hasattr(bpy.types, idname), f'menu {idname} is not registered'
        LOG.append(('menu', idname))


class Holder:
    def __init__(self):
        self.layout = FakeLayout()


class PrefHolder:
    """The preferences object with a checking layout in place of the real one."""

    def __init__(self, prefs):
        self._prefs = prefs
        self.layout = FakeLayout()

    def __getattr__(self, name):
        return getattr(self._prefs, name)


def draw_all(context, tag):
    n0 = len(LOG)
    for cls in ui.CLASSES:
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

    draw_all(ctx, 'monitoring off')
    assert ('prop', 'enabled') in LOG and ('prop', 'ignore_hidden') in LOG, 'the main switches are missing'
    for idname in ('bucketbuilder_collisions_advanced', 'bucketbuilder_volume_advanced',
                   'bucketbuilder_display_colors'):
        assert ('panel', idname) in LOG, idname
    for name in ('collision_mm', 'show_labels'):
        assert ('prop', name) not in LOG, name

    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    st.enabled = True
    settle()
    draw_all(ctx, 'monitoring on, empty scene')

    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, location=(100, 100, 100))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, location=(130, 100, 100))
    bpy.ops.mesh.primitive_cube_add(size=30, location=(100, 100, 142))      # 2 mm above the first sphere
    bpy.ops.mesh.primitive_cube_add(size=30, location=(-5, 100, 100))       # through the wall
    for i in range(14):                                                    # a long problem list
        bpy.ops.mesh.primitive_cube_add(size=10, location=(20 + 12 * i, 250, 20))
    ctx.view_layer.update()
    mon = settle()
    s = mon.status()
    assert s['collisions'] >= 1 and s['clearance'] >= 1 and s['partly_out'] == 1, s
    assert len(mon.problems()) > ui.LIST_ROWS, len(mon.problems())
    n0 = len(LOG)
    draw_all(ctx, 'scene with problems')
    labels = labels_since(n0)
    assert any('collision' in t for t in labels), labels[-12:]
    st.problem_index = len(mon.problems()) - 1          # scrolled list
    draw_all(ctx, 'last problem active')

    # the same status text feeds the viewport badge
    state, head, lines = bc.overlay.badge_text(mon.status())
    assert state == 'FAIL' and head == 'Collision Detected' and lines, (state, head, lines)
    print('  badge:', state, '|', head, '|', lines)

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
    st.problem_index = 0                                # the list from its top
    n0 = len(LOG)
    draw_all(ctx, 'one part hidden, one disabled in viewports')
    labels = labels_since(n0)
    assert '1 of them hidden, and checked' in labels, labels
    assert any('disabled in viewports' in t for t in labels), labels
    assert any('(hidden)' in t for k, t in LOG[n0:] if k == 'operator_text'), 'hidden part not marked'
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
    st.enabled = False
    assert 'Monitoring is off' in '\n'.join(ops.report_lines(ctx))
    st.enabled = True
    settle()

    # isolating needs a viewport; without one the operator must not fail
    st.problem_index = 0
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
