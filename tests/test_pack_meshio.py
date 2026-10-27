# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks pack.meshio: STL (binary, ASCII), 3MF (units, transforms, instancing,
components, production-extension paths, skipped object types).

    python3 tests/test_pack_meshio.py
"""

import os
import sys
import tempfile
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))
sys.path.insert(0, HERE)

import meshes  # noqa: E402
from pack import meshio  # noqa: E402
from pack.meshio import Mesh  # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix='meshio_')


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def mk(vf, name='m'):
    return Mesh(np.asarray(vf[0], float), np.asarray(vf[1], np.int32), name)


def tri_points(mesh, M=None):
    """Triangle corner coordinates (nt, 3, 3), optionally transformed."""
    P = mesh.verts[mesh.tris]
    if M is not None:
        P = P @ M[:3, :3].T + M[:3, 3]
    return P


def same_triangles(a, b, tol=1e-5):
    return a.shape == b.shape and float(np.max(np.abs(a - b))) <= tol


def matrix(L=None, t=(0, 0, 0)):
    M = np.eye(4)
    if L is not None:
        M[:3, :3] = L
    M[:3, 3] = t
    return M


def rz(deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def path(name):
    return os.path.join(TMP, name)


def write_zip(p, model, extra=None):
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('_rels/.rels',
                   '<Relationships><Relationship Target="/3D/3dmodel.model" Id="r" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                   '</Relationships>')
        z.writestr('3D/3dmodel.model', model)
        for k, v in (extra or {}).items():
            z.writestr(k, v)


CUBE_XML = ('<mesh><vertices>'
            '<vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>'
            '<vertex x="1" y="1" z="0"/><vertex x="0" y="1" z="0"/>'
            '<vertex x="0" y="0" z="1"/><vertex x="1" y="0" z="1"/>'
            '<vertex x="1" y="1" z="1"/><vertex x="0" y="1" z="1"/></vertices><triangles>'
            '<triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="3" v3="2"/>'
            '<triangle v1="4" v2="5" v3="6"/><triangle v1="4" v2="6" v3="7"/>'
            '<triangle v1="0" v2="1" v3="5"/><triangle v1="0" v2="5" v3="4"/>'
            '<triangle v1="1" v2="2" v3="6"/><triangle v1="1" v2="6" v3="5"/>'
            '<triangle v1="2" v2="3" v3="7"/><triangle v1="2" v2="7" v3="6"/>'
            '<triangle v1="3" v2="0" v3="4"/><triangle v1="3" v2="4" v3="7"/>'
            '</triangles></mesh>')
NS = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'


def test_stl():
    print('STL')
    m = mk(meshes.uv_sphere(5.0, 24, 12), 'ball')
    p = path('ball.stl')
    meshio.write_stl(p, m)
    s = meshio.read(p)
    check(len(s.items) == 1 and s.items[0].mesh.nt == m.nt, 'binary STL: %d triangles' % m.nt)
    check(same_triangles(tri_points(s.items[0].mesh), tri_points(m)), 'binary STL: same triangles')
    check(s.items[0].name == 'ball', 'the file name is the part name')

    w = meshio.weld(s.items[0].mesh)
    check(w.verts.shape[0] == m.verts.shape[0], 'weld restores the shared vertices (%d)' % w.verts.shape[0])
    check(same_triangles(tri_points(w), tri_points(m)), 'weld keeps the triangles')

    # ASCII
    q = path('box.stl')
    b = mk(meshes.box((2, 3, 4)))
    with open(q, 'w') as f:
        f.write('solid box\n')
        for t in tri_points(b):
            f.write('facet normal 0 0 0\nouter loop\n')
            for v in t:
                f.write('vertex %r %r %r\n' % tuple(float(x) for x in v))
            f.write('endloop\nendfacet\n')
        f.write('endsolid box\n')
    s = meshio.read(q)
    check(same_triangles(tri_points(s.items[0].mesh), tri_points(b), 1e-9), 'ASCII STL')

    # An ASCII file whose size happens to be wrong for binary is not misread, and
    # junk is refused.
    j = path('junk.stl')
    with open(j, 'wb') as f:
        f.write(b'hello, this is not a mesh at all')
    try:
        meshio.read(j)
        check(False, 'junk is refused')
    except meshio.ReadError:
        check(True, 'junk is refused')
    try:
        meshio.read(path('x.obj'))
        check(False, 'unknown type is refused')
    except meshio.ReadError:
        check(True, 'unknown type is refused')


def test_3mf_roundtrip():
    print('3MF round trip, instancing')
    a = mk(meshes.torus(5, 1.5, 16, 8), 'ring')
    b = mk(meshes.box((4, 5, 6)), 'brick')
    rng = np.random.default_rng(3)
    parts = []
    for i in range(5):
        parts.append((a, matrix(rz(30 * i), rng.uniform(-50, 50, 3)), 'ring-%d' % i))
    for i in range(3):
        L = rz(10 * i) * np.array([1, 1, 1.0]) * (1 + 0.1 * i)
        parts.append((b, matrix(L, rng.uniform(-50, 50, 3)), 'brick-%d' % i))
    p = path('rt.3mf')
    meshio.write_3mf(p, parts)
    s = meshio.read(p)
    check(len(s.items) == 8, '8 items')
    check(len(s.meshes) == 2, 'copies share a mesh: %d unique' % len(s.meshes))
    check(len({id(i.mesh) for i in s.items[:5]}) == 1, 'the five rings share one Mesh')
    ok = True
    for it, (mesh, M, nm) in zip(s.items, parts):
        ok &= same_triangles(tri_points(it.mesh, it.matrix), tri_points(mesh, M), 1e-9)
        ok &= it.name == nm
    check(ok, 'every instance lands where it was written, with its name')


def test_units_and_transform():
    print('units and transforms')
    for unit, f in (('centimeter', 10.0), ('inch', 25.4), ('micron', 1e-3), ('meter', 1000.0)):
        model = ('<model unit="%s" %s><resources><object id="1" type="model">%s</object>'
                 '</resources><build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 2 3 4"/>'
                 '</build></model>' % (unit, NS, CUBE_XML))
        p = path('u.3mf')
        write_zip(p, model)
        s = meshio.read(p)
        it = s.items[0]
        P = tri_points(it.mesh, it.matrix)
        lo, hi = P.reshape(-1, 3).min(0), P.reshape(-1, 3).max(0)
        want_lo = np.array([2, 3, 4]) * f
        want_hi = (np.array([2, 3, 4]) + 1) * f
        check(np.allclose(lo, want_lo) and np.allclose(hi, want_hi),
              '%s: the cube and its offset are in mm' % unit)

    # row-vector convention: the file's matrix is 90 degrees about z, then offset
    model = ('<model unit="millimeter" %s><resources><object id="1">%s</object></resources>'
             '<build><item objectid="1" transform="0 1 0 -1 0 0 0 0 1 10 0 0"/></build></model>'
             % (NS, CUBE_XML))
    p = path('r.3mf')
    write_zip(p, model)
    it = meshio.read(p).items[0]
    M = it.matrix
    check(np.allclose(M[:3, :3], rz(90)), 'rotation matches the row-vector convention')
    P = tri_points(it.mesh, M).reshape(-1, 3)
    check(np.allclose(P.min(0), [9, 0, 0]) and np.allclose(P.max(0), [10, 1, 1]),
          'a quarter turn about z moves the unit cube to x in [9, 10]')

    try:
        write_zip(p, model.replace('0 1 0 -1 0 0 0 0 1 10 0 0', '1 2 3'))
        meshio.read(p)
        check(False, 'a broken transform is refused')
    except meshio.ReadError:
        check(True, 'a broken transform is refused')


def test_components():
    print('components, skipped types, production paths')
    model = ('<model unit="millimeter" %s><resources>'
             '<object id="1" type="model" name="leaf">%s</object>'
             '<object id="2" type="model" name="pair"><components>'
             '<component objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
             '<component objectid="1" transform="1 0 0 0 1 0 0 0 1 5 0 0"/>'
             '</components></object>'
             '<object id="3" type="support" name="scaffold">%s</object>'
             '<object id="4" type="model" name="outer"><components>'
             '<component objectid="2" transform="0 1 0 -1 0 0 0 0 1 0 0 20"/>'
             '</components></object>'
             '</resources><build>'
             '<item objectid="2" partnumber="pair-a" transform="1 0 0 0 1 0 0 0 1 100 0 0"/>'
             '<item objectid="3" partnumber="scaffold"/>'
             '<item objectid="4" partnumber="outer-a"/>'
             '</build></model>' % (NS, CUBE_XML, CUBE_XML))
    p = path('c.3mf')
    write_zip(p, model)
    s = meshio.read(p)
    check(len(s.items) == 4, 'a pair makes two items, a nested pair two more, the support none: %d' % len(s.items))
    check(len(s.meshes) == 1, 'all share the leaf mesh')
    check(any(n == 'scaffold' for n, _ in s.skipped), 'the support is listed as skipped: %s' % s.skipped)
    xs = sorted(round(float(it.matrix[0, 3]), 6) for it in s.items[:2])
    check(xs == [100.0, 105.0], 'component offsets add to the item offset: %s' % xs)
    nested = s.items[2:]
    zs = {round(float(it.matrix[2, 3]), 6) for it in nested}
    check(zs == {20.0}, 'a nested transform is applied')
    # nested pair: second leaf is 5 mm along the pair's x, which the outer rotates to y
    ys = sorted(round(float(it.matrix[1, 3]), 6) for it in nested)
    check(np.allclose(ys, [0.0, 5.0]) or np.allclose(ys, [-5.0, 0.0]),
          'the outer rotation turns the pair: y offsets %s' % ys)

    # production extension: leaf in another model file
    leaf = ('<model unit="millimeter" %s><resources><object id="7" type="model" name="far">%s'
            '</object></resources><build/></model>' % (NS, CUBE_XML))
    root = ('<model unit="millimeter" %s xmlns:p="http://schemas.microsoft.com/3dmanufacturing/'
            'production/2015/06"><resources><object id="1" type="model"><components>'
            '<component p:path="/3D/Objects/leaf.model" objectid="7" '
            'transform="1 0 0 0 1 0 0 0 1 3 0 0"/></components></object></resources>'
            '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 4 0"/></build></model>' % NS)
    p = path('prod.3mf')
    write_zip(p, root, {'3D/Objects/leaf.model': leaf})
    s = meshio.read(p)
    check(len(s.items) == 1 and s.items[0].mesh.nt == 12, 'a component in another model file is found')
    check(np.allclose(s.items[0].matrix[:3, 3], [3, 4, 0]), 'and placed')

    # a build with nothing in it, and a reference to nothing
    write_zip(p, '<model %s><resources/><build/></model>' % NS)
    try:
        meshio.read(p)
        check(False, 'an empty build is refused')
    except meshio.ReadError:
        check(True, 'an empty build is refused')
    write_zip(p, '<model %s><resources/><build><item objectid="9"/></build></model>' % NS)
    try:
        meshio.read(p)
        check(False, 'a missing object is refused')
    except meshio.ReadError:
        check(True, 'a missing object is refused')


def test_properties_on_triangles():
    print('triangles with color properties')
    xml = CUBE_XML.replace('<triangle v1="0" v2="2" v3="1"/>',
                           '<triangle v1="0" v2="2" v3="1" pid="1" p1="0"/>')
    model = ('<model %s><resources><object id="1">%s</object></resources>'
             '<build><item objectid="1"/></build></model>' % (NS, xml))
    p = path('p.3mf')
    write_zip(p, model)
    s = meshio.read(p)
    check(s.items[0].mesh.nt == 12 and s.items[0].mesh.tris[0].tolist() == [0, 2, 1],
          'indices are read by name when properties are present')


def test_scale():
    print('a large 3MF')
    import time
    m = mk(meshes.subdivide(*meshes.uv_sphere(10.0, 16, 8), 4), 'big')
    parts = [(m, matrix(None, (i * 30, 0, 0)), 'p%d' % i) for i in range(3)]
    p = path('big.3mf')
    meshio.write_3mf(p, parts)
    t = time.perf_counter()
    s = meshio.read(p)
    dt = time.perf_counter() - t
    mb = os.path.getsize(p) / 1e6
    print('       %d triangles in %.1f MB zipped: %.2f s (%.2f s per million)'
          % (m.nt, mb, dt, dt / (m.nt / 1e6)))
    check(len(s.items) == 3 and s.items[0].mesh.nt == m.nt, 'read back')
    check(dt / (m.nt / 1e6) < 3.0, 'parse speed is a few seconds per million triangles')


if __name__ == '__main__':
    test_stl()
    test_3mf_roundtrip()
    test_units_and_transform()
    test_components()
    test_properties_on_triangles()
    test_scale()
    print()
    if FAILS:
        print('FAILED:')
        for f in FAILS:
            print('  ' + f)
        sys.exit(1)
    print('ALL OK')
