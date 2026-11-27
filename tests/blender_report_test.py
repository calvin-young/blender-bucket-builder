# SPDX-License-Identifier: GPL-3.0-or-later
"""The density report in Blender, against Blender's own volume calculation.

    BLENDER_USER_RESOURCES=... blender --background --python tests/blender_report_test.py

Volumes come from bmesh (`calc_volume`) on the evaluated, world-space meshes;
the packed height from their vertices; both independent of pack.metrics.
"""

import importlib
import time

import bmesh
import bpy
import numpy as np

PKG = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("bucket_builder"))
bc = importlib.import_module(PKG)
monitor, props = bc.monitor, bc.props
FAILS = []


def check(cond, label, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if detail else ''))
    if not cond:
        FAILS.append(label)


def scene():
    return bpy.context.scene


def update():
    bpy.context.view_layer.update()


def reference(objs, vlo, vhi):
    """(volume counted, packed height, density) by bmesh and the vertices:
    density over the height the parts fill, lowest point to highest."""
    dg = bpy.context.evaluated_depsgraph_get()
    vol, top, low = 0.0, 0.0, None
    for o in objs:
        e = o.evaluated_get(dg)
        me = e.to_mesh()
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.transform(e.matrix_world)
        P = np.array([v.co for v in bm.verts])
        if (P.max(0) <= vlo).any() or (P.min(0) >= vhi).any():
            bm.free()
            e.to_mesh_clear()
            continue
        vol += bm.calc_volume(signed=False)
        top = max(top, P[:, 2].max() - vlo[2])
        low = P[:, 2].min() - vlo[2] if low is None else min(low, P[:, 2].min() - vlo[2])
        bm.free()
        e.to_mesh_clear()
    area = (vhi[0] - vlo[0]) * (vhi[1] - vlo[1])
    fill = top - max(0.0, low if low is not None else 0.0)
    return vol, top, (vol / (area * fill) if fill > 0 else None)


def wait(mon, st):
    vl = bpy.context.view_layer
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 120:
        mon.tick(scene(), vl.depsgraph, vl, live=False, relaxed=True, pace=0.0)
        if mon.report.future is None and mon.report.result_sig == mon.report.guard_sig \
                and not mon.report.dirty and mon.report.guard == mon.report._guard(mon, st) \
                and not mon.queue:
            return True
        time.sleep(0.005)
    return False


def volume(st):
    mon = monitor.get(scene())
    lo, hi = mon.volume_box(st)
    return np.asarray(lo) * mon.mm_per_unit, np.asarray(hi) * mon.mm_per_unit


def compare(label, objs, mon, st):
    ok = wait(mon, st)
    check(ok, label + ': report finished')
    rep = mon.report.result
    vlo, vhi = volume(st)
    vol, top, dens = reference(objs, vlo, vhi)
    check(abs(rep.volume - vol) <= 1e-4 * vol, label + ': volume', '%.3f vs bmesh %.3f' % (rep.volume, vol))
    check(abs(rep.height - top) <= 1e-4, label + ': packed height', '%.4f vs %.4f' % (rep.height, top))
    check(rep.density is not None and abs(rep.density - dens) <= 1e-4 * dens, label + ': density',
          '%.5f vs %.5f' % (rep.density or 0, dens))
    return rep


def add_cube(name, loc, size=40.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.active_object
    o.name = name
    return o


def add_sphere(name, loc, r=20.0):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=r, location=loc)
    o = bpy.context.active_object
    o.name = name
    return o


def main():
    print('Blender', bpy.app.version_string, '| add-on', PKG)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    sc = scene()
    st = sc.bucket_builder
    a = add_cube('A', (50, 50, 21))              # (1 mm off the floor: a part must be inside, not on it)
    b = add_sphere('B', (150, 50, 31))
    c = add_cube('C', (250, 50, 20), 30.0)
    c.rotation_euler = (0.3, 0.5, 0.2)
    c.location.z = 40
    update()

    check(not st.report_density, 'the report is off in a new scene')
    st.monitor_volume = True
    st.report_density = True
    mon = monitor.get(sc, create=True)
    rep = compare('three parts', [a, b, c], mon, st)
    check(rep.n_inside == 3 and not rep.warnings, 'all inside, no warnings', str(rep.warnings))
    check(mon.report.pixels is not None, 'a profile picture exists')
    n0 = len(mon.report.cache)

    # sideways: nothing to compute
    a.location.x += 30.0
    update()
    compare('moved sideways', [a, b, c], mon, st)
    check(len(mon.report.cache) == n0, 'a sideways move adds nothing to the cache', '%d' % len(mon.report.cache))

    # lifted
    b.location.z += 17.5
    update()
    compare('lifted', [a, b, c], mon, st)

    # rotated and scaled
    a.rotation_euler = (0.0, 0.0, 0.7)
    a.scale = (1.0, 2.0, 1.5)
    a.location.z = 45.0
    update()
    compare('rotated and scaled', [a, b, c], mon, st)

    # a linked copy shares the mesh: two parts, one mesh, same orientation
    d = a.copy()
    sc.collection.objects.link(d)
    d.location = (50, 150, 45)
    update()
    compare('linked copy', [a, b, c, d], mon, st)

    # partly out of the volume
    vlo, vhi = volume(st)
    c.location.x = vhi[0] - 5.0
    update()
    rep = compare('partly out', [a, b, c, d], mon, st)
    check(rep.n_partial == 1 and any('partly outside' in w for w in rep.warnings),
          'the warning that sits with the density', str(rep.warnings))
    rows = bc.report.rows(rep)
    check(any(k == '3D Density' and t == 'warn' for k, v, t in rows), 'and the density row is flagged')

    # a part wholly outside is left out of the sum
    c.location.x = vhi[0] + 100.0
    update()
    rep = compare('wholly out', [a, b, c, d], mon, st)
    check(rep.n_outside == 1, 'listed as outside')

    # deleting a part
    bpy.data.objects.remove(d, do_unlink=True)
    update()
    compare('deleted', [a, b, c], mon, st)

    # modifier on a part (the evaluated mesh counts)
    mod = b.modifiers.new('sub', 'SUBSURF')
    mod.levels = 1
    mod.render_levels = 1
    update()
    compare('with a modifier', [a, b, c], mon, st)

    # print-quality notes (#51): a large plate lying flat faces up (capillary); one tilted 5 degrees is
    # shallow (stair-stepping); both become rows of the Problems list, never fail the build
    flat = add_cube('Plate-flat', (100, 150, 6))
    flat.scale = (2.0, 2.0, 0.2)                    # (80 x 80 x 8 mm)
    tilt = add_cube('Plate-tilt', (230, 150, 30))
    tilt.scale = (2.0, 2.0, 0.2)
    tilt.rotation_euler = (0.0, 0.0873, 0.0)
    update()
    wait(mon, st)
    rows = [q for q in mon.problems() if q['kind'] == 'PQ']
    texts = {(q['a'], q['pq']) for q in rows}
    check(('Plate-flat', 'flat face up') in texts and ('Plate-tilt', 'shallow face') in texts,
          'flat and shallow faces are listed as print-quality notes', sorted(texts))
    check(mon.status()['pq'] == len(rows) and mon.status()['collisions'] == 0, 'counted as notes', mon.status()['pq'])
    q = next(q for q in rows if q['a'] == 'Plate-flat')
    check(len(mon.pq_issues()[q['key'][4]]['tris']) > 0, 'with the faces to shade')
    st.show_pq = False
    update()
    mon.tick(scene(), bpy.context.view_layer.depsgraph, bpy.context.view_layer, live=False, relaxed=True, pace=0.0)
    check(not any(q['kind'] == 'PQ' for q in mon.problems()), 'hidden with the switch')
    st.show_pq = True
    bpy.data.objects.remove(flat)
    bpy.data.objects.remove(tilt)
    update()
    wait(mon, st)

    # dense layers (#53): a floor of plates and one tall part above them make the bottom layers dense
    plates = [add_cube('Dense-%d' % i, (30 + 45 * (i % 6), 30 + 45 * (i // 6), 2.5)) for i in range(24)]
    for o in plates:
        o.scale = (1.0, 1.0, 0.125)                 # (40 x 40 x 5 mm)
    tall = add_cube('Tall', (330, 250, 100))
    tall.scale = (0.25, 0.25, 5.0)                  # (10 x 10 x 200 mm)
    update()
    wait(mon, st)
    bands = bc.ops.dense_bands(scene())
    check(len(bands) >= 1 and bands[0][0] < 5.0, 'the floor of plates is a dense band', bands)
    st.dense_band = 0
    check(bpy.ops.bucketbuilder.dense_band_step(direction=1) == {'FINISHED'} and st.show_dense
          and st.dense_band == (1 % len(bands)), 'stepping turns the highlight on and moves', (st.show_dense, st.dense_band))
    # with Crosscut (a stand-in for its interface, 0.24.0): a section at the band's middle, given back after
    import types
    calls = []
    cc = types.ModuleType('crosscut_standin')
    cc.API_VERSION = 1
    cc.set_section = lambda z, normal=(0, 0, 1), collection=None, owner='', force=False, scene=None: \
        calls.append(('set', round(z, 3), owner, force)) or True
    cc.clear_section = lambda owner, scene=None: calls.append(('clear', owner)) or True
    bpy.app.driver_namespace['crosscut'] = cc
    try:
        bpy.ops.bucketbuilder.dense_band_step(direction=0)
        z0, z1 = bands[st.dense_band]
        st.show_dense = False
        check(calls == [('set', round((z0 + z1) / 2, 3), 'bucket_builder', False), ('clear', 'bucket_builder')],
              'a Crosscut section through the band, never forced, and given back', calls)
        cc.set_section = lambda *a, **k: False          # (the user's own section is showing)
        r = bpy.ops.bucketbuilder.dense_band_step(direction=0)
        check(r == {'FINISHED'}, 'a refusal leaves the band framed all the same', r)
    finally:
        del bpy.app.driver_namespace['crosscut']
    for o in plates + [tall]:
        bpy.data.objects.remove(o)
    st.show_dense = False
    update()
    wait(mon, st)

    # switching it off keeps the monitor but stops the work
    st.report_density = False
    update()
    check(True, 'switched off without error')

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        raise SystemExit(1)
    print('ALL OK')


main()
