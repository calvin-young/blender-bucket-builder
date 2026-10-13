# SPDX-License-Identifier: GPL-3.0-or-later
"""Read STL and 3MF files with NumPy, `zipfile` and `re` only (no `bpy`).

Everything comes back in millimetres:

    scene = read('bucket.3mf')
    scene.meshes   {key: Mesh}          unique meshes (copies share one)
    scene.items    [Item]               one per printed instance
    scene.skipped  [(name, why)]        objects that are not printed

3MF specifics handled: units; build items and nested components with their
transforms (the matrix is 12 numbers, row-vector convention); the production
extension's `p:path` (components and items in other model files); object types
(supports, surfaces and "other" objects are not printed and are skipped).
Copies of one object share a `Mesh`, so a 600-part build of 12 meshes costs 12
meshes in memory.

Parsing is regular expressions and `np.fromstring`, not an XML tree: a
3MF of 20 million triangles is a gigabyte of text.
"""

import os
import re
import struct
import zipfile
from dataclasses import dataclass, field

import numpy as np

UNITS_MM = {'micron': 1e-3, 'millimeter': 1.0, 'centimeter': 10.0,
            'inch': 25.4, 'foot': 304.8, 'meter': 1000.0}
# types that are not part of the printed build
NOT_PRINTED = ('support', 'surface', 'other')


@dataclass(eq=False)
class Mesh:
    """Triangles of one unique mesh, in the mesh's own coordinates (mm)."""
    verts: np.ndarray          # (nv, 3) float64
    tris: np.ndarray           # (nt, 3) int32 (int64 above 2**31 vertices)
    name: str = ''

    @property
    def nt(self):
        return int(self.tris.shape[0])


@dataclass(eq=False)
class Item:
    """One printed instance: a mesh and its 4 x 4 matrix (column vectors,
    p_world = M @ p), translation in mm."""
    name: str
    mesh: Mesh
    matrix: np.ndarray
    source: str = ''           # the object's id in the file, for messages


@dataclass(eq=False)
class Scene:
    meshes: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    unit_scale: float = 1.0
    path: str = ''


class ReadError(Exception):
    pass


# --------------------------------------------------------------------------- STL

def read_stl(path):
    """Binary or ASCII STL as a Mesh of unwelded triangles (every triangle has
    its own three vertices; `weld` joins them)."""
    with open(path, 'rb') as f:
        data = f.read()
    n_bin = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else -1
    if len(data) == 84 + 50 * n_bin and n_bin >= 0:
        rec = np.frombuffer(data, dtype=np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)),
                                                  ('a', '<u2')]), count=n_bin, offset=84)
        verts = rec['v'].reshape(-1, 3).astype(np.float64)
    else:
        text = data.decode('latin-1')
        if 'vertex' not in text:
            raise ReadError('not an STL file: ' + os.path.basename(path))
        nums = re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', text)
        verts = np.array(nums, dtype=np.float64).reshape(-1, 3)
        if verts.shape[0] % 3:
            raise ReadError('ASCII STL with a broken facet: ' + os.path.basename(path))
    tris = np.arange(verts.shape[0], dtype=np.int32).reshape(-1, 3)
    return Mesh(verts, tris, os.path.splitext(os.path.basename(path))[0])


def weld(mesh):
    """Join identical vertices (exact equality), so that edges can be matched."""
    v = np.ascontiguousarray(mesh.verts)
    key = v.view(np.dtype((np.void, v.dtype.itemsize * 3))).ravel()
    _, first, inv = np.unique(key, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    return Mesh(v[first], inv[mesh.tris].astype(np.int32), mesh.name)


# --------------------------------------------------------------------------- 3MF

_ATTR = re.compile(rb'([A-Za-z_:][\w:.-]*)\s*=\s*"([^"]*)"')
_CHUNK = 1 << 24


def _numbers(block, per_row, dtype=np.float64):
    """All attribute values in a run of `<tag a="1" b="2"/>` elements as
    (n, per_row).  Only valid when every element has exactly `per_row`
    attributes, all numeric.  Splitting on the quotes is 3 x faster than
    regular expressions; chunks keep the temporary lists small."""
    out, pos, n = [], 0, len(block)
    while pos < n:
        end = n if pos + _CHUNK >= n else block.find(b'/>', pos + _CHUNK) + 2
        if end < 2:
            end = n
        vals = block[pos:end].split(b'"')[1::2]
        if vals:
            out.append(np.array(vals).astype(dtype))
        pos = end
    a = np.concatenate(out) if out else np.zeros(0, dtype)
    if a.size % per_row:
        raise ReadError('unexpected attributes in a mesh element')
    return a.reshape(-1, per_row)


def _triangles(block):
    if b'p1=' in block or b'pid=' in block or b'p2=' in block or b'p3=' in block:
        # properties on triangles: take v1, v2, v3 by name (slower)
        rows = [(int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in
                re.finditer(rb'v1\s*=\s*"(\d+)"[^>]*?v2\s*=\s*"(\d+)"[^>]*?v3\s*=\s*"(\d+)"', block)]
        return np.array(rows, dtype=np.int64).reshape(-1, 3)
    return _numbers(block, 3, np.int64)


def _parse_transform(s, scale=1.0):
    """3MF `transform`: 12 numbers m00 m01 m02 m10 .. m22 m30 m31 m32, row
    vectors: p' = p @ R + t.  Returned as a 4 x 4 for column vectors, with the
    translation multiplied by `scale` (the file's unit to mm; the linear part
    is unitless)."""
    M = np.eye(4)
    if s:
        v = [float(x) for x in s.split()]
        if len(v) != 12:
            raise ReadError('bad transform: ' + s)
        R = np.array(v[:9]).reshape(3, 3)
        M[:3, :3] = R.T
        M[:3, 3] = np.array(v[9:]) * scale
    return M


@dataclass
class _Obj:
    id: str
    name: str
    type: str
    mesh: object = None                       # (verts, tris) arrays
    comps: list = field(default_factory=list)  # (path | None, objectid, 4x4)


def _elements(xml, tag):
    """(attribute text, body or None) of every `<tag ...>` element, found with
    `bytes.find`: regular expressions are 10 x slower on a 100 MB model."""
    open_, close = b'<' + tag, b'</' + tag + b'>'
    pos, n = 0, len(xml)
    while True:
        i = xml.find(open_, pos)
        if i < 0:
            return
        j = i + len(open_)
        if j < n and xml[j:j + 1] not in b' \t\r\n/>':
            pos = j                                  # <objects>, not <object>
            continue
        gt = xml.find(b'>', j)
        if gt < 0:
            return
        attrs = xml[j:gt]
        if attrs.endswith(b'/'):
            yield attrs[:-1], None
            pos = gt + 1
        else:
            end = xml.find(close, gt)
            if end < 0:
                return
            yield attrs, xml[gt + 1:end]
            pos = end + len(close)


def _inside(body, tag):
    """The text between `<tag ...>` and `</tag>`, or None."""
    i = body.find(b'<' + tag)
    if i < 0:
        return None
    gt = body.find(b'>', i)
    end = body.find(b'</' + tag + b'>', gt)
    if gt < 0 or end < 0:
        return None
    return body[gt + 1:end]


def _parse_model(xml):
    """Objects and build items of one model file."""
    m = re.search(rb'<model\b[^>]*>', xml)
    head = dict((k.decode(), v.decode()) for k, v in _ATTR.findall(m.group(0))) if m else {}
    scale = UNITS_MM.get(head.get('unit', 'millimeter'), 1.0)
    objs = {}
    for attrs, body in _elements(xml, b'object'):
        a = dict((k.decode(), v.decode()) for k, v in _ATTR.findall(attrs))
        body = body or b''
        o = _Obj(a.get('id', ''), a.get('name') or a.get('partnumber') or '',
                 a.get('type', 'model'))
        vb = _inside(body, b'vertices')
        tb = _inside(body, b'triangles')
        if vb is not None and tb is not None:
            o.mesh = (_numbers(vb, 3) * scale, _triangles(tb))
        cb = _inside(body, b'components')
        for cm in re.finditer(rb'<component\b([^>]*)/?>', cb or b''):
            c = dict((k.decode(), v.decode()) for k, v in _ATTR.findall(cm.group(1)))
            o.comps.append((c.get('p:path') or c.get('path'), c.get('objectid', ''),
                            _parse_transform(c.get('transform', ''), scale)))
        objs[o.id] = o
    items = []
    bm = _inside(xml, b'build')
    if bm:
        for im in re.finditer(rb'<item\b([^>]*)/?>', bm):
            a = dict((k.decode(), v.decode()) for k, v in _ATTR.findall(im.group(1)))
            items.append((a.get('p:path') or a.get('path'), a.get('objectid', ''),
                          _parse_transform(a.get('transform', ''), scale), a.get('partnumber', '')))
    return scale, objs, items


def _root_model_path(z):
    try:
        rels = z.read('_rels/.rels')
    except KeyError:
        rels = b''
    for m in re.finditer(rb'<Relationship\b([^>]*)/?>', rels):
        a = dict((k.decode(), v.decode()) for k, v in _ATTR.findall(m.group(1)))
        if a.get('Type', '').endswith('/3dmodel') and 'Target' in a:
            return a['Target'].lstrip('/')
    return '3D/3dmodel.model'


def read_3mf(path):
    scene = Scene(path=path)
    with zipfile.ZipFile(path) as z:
        root = _root_model_path(z)
        files = {}                     # model path -> (scale, objs)

        def load(p):
            p = p.lstrip('/')
            if p not in files:
                scale, objs, items = _parse_model(z.read(p))
                files[p] = (scale, objs, items)
            return files[p]

        scale, objs, items = load(root)
        scene.unit_scale = scale
        if not items:
            raise ReadError('the 3MF has no build items: ' + os.path.basename(path))
        mesh_cache = {}

        def mesh_of(p, o):
            k = (p, o.id)
            if k not in mesh_cache:
                v, t = o.mesh
                nt = t.shape[0]
                if nt and (t.min() < 0 or t.max() >= v.shape[0]):
                    raise ReadError('object %s has triangle indices out of range' % o.id)
                mesh = Mesh(v, t.astype(np.int32 if v.shape[0] < 2 ** 31 else np.int64),
                            o.name or ('object ' + o.id))
                mesh_cache[k] = mesh
                scene.meshes[k] = mesh
            return mesh_cache[k]

        def expand(p, oid, M, name, depth=0):
            """Yield (mesh, matrix, name) for an object and its components."""
            file_scale, fobjs, _ = load(p)
            o = fobjs.get(oid)
            if o is None:
                raise ReadError('missing object %s in %s' % (oid, p))
            if depth > 32:
                raise ReadError('components nested too deep (a loop?)')
            if o.type in NOT_PRINTED:
                scene.skipped.append((o.name or ('object ' + oid), o.type))
                return
            nm = name or o.name or ('object ' + oid)
            if o.mesh is not None:
                yield mesh_of(p, o), M, nm, oid
            for cp, cid, CM in o.comps:
                yield from expand(cp.lstrip('/') if cp else p, cid, M @ CM, nm, depth + 1)

        for ip, oid, M, partnumber in items:
            p = (ip or root).lstrip('/')
            for mesh, mat, nm, src in expand(p, oid, M, partnumber):
                scene.items.append(Item(nm, mesh, mat, src))
    return scene


def read(path):
    """Read a .3mf or .stl file."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.3mf':
        return read_3mf(path)
    if ext == '.stl':
        mesh = read_stl(path)
        s = Scene(path=path)
        s.meshes[mesh.name] = mesh
        s.items.append(Item(mesh.name, mesh, np.eye(4), mesh.name))
        return s
    raise ReadError('unsupported file type: ' + ext)


# ------------------------------------------------------------------------ writing
# Small writers, for tests and for making synthetic benchmark buckets.

def write_stl(path, mesh):
    tri = mesh.verts[mesh.tris].astype('<f4')
    rec = np.zeros(tri.shape[0], dtype=np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)),
                                                 ('a', '<u2')]))
    rec['v'] = tri
    with open(path, 'wb') as f:
        f.write(b'\0' * 80 + struct.pack('<I', tri.shape[0]))
        f.write(rec.tobytes())


def write_3mf(path, parts, unit='millimeter'):
    """parts: list of (mesh, 4x4 matrix, name); meshes used more than once are
    written once and referenced (instancing)."""
    ids, objs = {}, []
    for mesh, _, _ in parts:
        if id(mesh) not in ids:
            ids[id(mesh)] = len(ids) + 1
            v = '\n'.join('<vertex x="%r" y="%r" z="%r"/>' % tuple(r) for r in mesh.verts.tolist())
            t = '\n'.join('<triangle v1="%d" v2="%d" v3="%d"/>' % tuple(r) for r in mesh.tris.tolist())
            objs.append('<object id="%d" type="model" name="%s"><mesh><vertices>\n%s\n</vertices>'
                        '<triangles>\n%s\n</triangles></mesh></object>'
                        % (ids[id(mesh)], mesh.name, v, t))
    items = []
    for mesh, M, name in parts:
        R = np.asarray(M)[:3, :3].T.ravel().tolist() + np.asarray(M)[:3, 3].tolist()
        items.append('<item objectid="%d" transform="%s" partnumber="%s"/>'
                     % (ids[id(mesh)], ' '.join(repr(float(x)) for x in R), name))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="%s" '
           'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n<resources>\n%s\n'
           '</resources>\n<build>\n%s\n</build>\n</model>' % (unit, '\n'.join(objs), '\n'.join(items)))
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="rels" ContentType="application/'
                   'vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" '
                   'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr('_rels/.rels',
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" '
                   'Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                   '</Relationships>')
        z.writestr('3D/3dmodel.model', xml)
