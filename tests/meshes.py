"""Procedural test meshes (vertices float64 (n,3), triangles int (m,3))."""

import numpy as np


def box(size=(1.0, 1.0, 1.0)):
    sx, sy, sz = (0.5 * s for s in size)
    v = np.array([[x, y, z] for z in (-sz, sz) for y in (-sy, sy) for x in (-sx, sx)], float)
    f = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4],
                  [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]])
    return v, f


def grid_box(size=(1.0, 1.0, 1.0), n=8):
    """Box whose faces are n x n grids (many small triangles on flat faces)."""
    vs, fs = [], []
    lin = np.linspace(-0.5, 0.5, n + 1)
    a, b = np.meshgrid(lin, lin, indexing='ij')
    a = a.ravel()
    b = b.ravel()
    quads = []
    for i in range(n):
        for j in range(n):
            p = i * (n + 1) + j
            quads.append((p, p + n + 1, p + n + 2, p + 1))
    quads = np.array(quads)
    base = 0
    for axis in range(3):
        for sign in (-0.5, 0.5):
            pts = np.zeros((a.size, 3))
            pts[:, axis] = sign
            pts[:, (axis + 1) % 3] = a
            pts[:, (axis + 2) % 3] = b
            vs.append(pts)
            q = quads + base
            if sign < 0:
                q = q[:, ::-1]
            fs.append(q[:, [0, 1, 2]])
            fs.append(q[:, [0, 2, 3]])
            base += a.size
    v = np.concatenate(vs) * np.array(size)
    return v, np.concatenate(fs)


def uv_sphere(radius=1.0, segs=16, rings=8):
    v = [[0, 0, radius]]
    for r in range(1, rings):
        th = np.pi * r / rings
        for s in range(segs):
            ph = 2 * np.pi * s / segs
            v.append([radius * np.sin(th) * np.cos(ph), radius * np.sin(th) * np.sin(ph),
                      radius * np.cos(th)])
    v.append([0, 0, -radius])
    f = []
    for s in range(segs):
        f.append([0, 1 + s, 1 + (s + 1) % segs])
    for r in range(rings - 2):
        for s in range(segs):
            a = 1 + r * segs + s
            b = 1 + r * segs + (s + 1) % segs
            c = a + segs
            d = b + segs
            f.append([a, c, d])
            f.append([a, d, b])
    last = len(v) - 1
    off = 1 + (rings - 2) * segs
    for s in range(segs):
        f.append([last, off + (s + 1) % segs, off + s])
    return np.array(v, float), np.array(f)


def torus(R=1.0, r=0.3, segs=24, sides=12):
    v = []
    for i in range(segs):
        a = 2 * np.pi * i / segs
        for j in range(sides):
            b = 2 * np.pi * j / sides
            v.append([(R + r * np.cos(b)) * np.cos(a), (R + r * np.cos(b)) * np.sin(a),
                      r * np.sin(b)])
    f = []
    for i in range(segs):
        for j in range(sides):
            a = i * sides + j
            b = ((i + 1) % segs) * sides + j
            c = ((i + 1) % segs) * sides + (j + 1) % sides
            d = i * sides + (j + 1) % sides
            f.append([a, b, c])
            f.append([a, c, d])
    return np.array(v, float), np.array(f)


def blob(rng, radius=1.0, segs=20, rings=10, noise=0.25):
    """Bumpy, non-convex closed surface."""
    v, f = uv_sphere(radius, segs, rings)
    d = v / np.linalg.norm(v, axis=1, keepdims=True)
    k = rng.normal(size=(4, 3))
    bump = sum(np.sin(3.0 * d @ k[i] + i) for i in range(4)) / 4.0
    return v * (1.0 + noise * bump)[:, None], f


def cylinder(radius=0.5, height=2.0, segs=24):
    """Capped cylinder with long thin side triangles, like CAD exports."""
    v = []
    for z in (-0.5 * height, 0.5 * height):
        for s in range(segs):
            a = 2 * np.pi * s / segs
            v.append([radius * np.cos(a), radius * np.sin(a), z])
    v.append([0, 0, -0.5 * height])
    v.append([0, 0, 0.5 * height])
    f = []
    for s in range(segs):
        n = (s + 1) % segs
        f.append([s, n, segs + n])
        f.append([s, segs + n, segs + s])
        f.append([2 * segs, n, s])
        f.append([2 * segs + 1, segs + s, segs + n])
    return np.array(v, float), np.array(f)


def subdivide(v, f, times=1):
    """Split every triangle in four."""
    for _ in range(times):
        edges = {}
        v = list(map(tuple, v))
        nf = []

        def mid(a, b):
            key = (a, b) if a < b else (b, a)
            i = edges.get(key)
            if i is None:
                i = len(v)
                v.append(tuple(0.5 * (np.array(v[a]) + np.array(v[b]))))
                edges[key] = i
            return i

        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [[a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]]
        v = np.array(v, float)
        f = np.array(nf)
    return v, f


def rot(rng):
    """Uniform random rotation matrix."""
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def matrix(L=None, t=(0, 0, 0)):
    M = np.eye(4)
    if L is not None:
        M[:3, :3] = L
    M[:3, 3] = t
    return M
