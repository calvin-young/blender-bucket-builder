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
    for _ in range(20000):
        mon.tick(sc, vl.depsgraph, vl, live=False)
        if not mon.busy:
            break
    return mon


def main():
    ctx = bpy.context
    sc = ctx.scene
    st = sc.bucket_builder
    props.seed_profiles(props.prefs())

    draw_all(ctx, 'monitoring off')

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
    draw_all(ctx, 'scene with problems')
    labels = [t for k, t in LOG if k == 'label']
    assert any('collision' in t for t in labels), labels[-12:]
    st.problem_index = len(mon.problems()) - 1          # scrolled list
    draw_all(ctx, 'last problem active')

    # the same status text feeds the viewport badge
    state, head, lines = bc.overlay.badge_text(mon.status())
    assert state == 'FAIL' and lines, (state, head, lines)
    print('  badge:', state, '|', head, '|', lines)
    print('UI TEST OK')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('UI TEST FAILED')
    sys.exit(1)
