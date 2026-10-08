# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounding-volume hierarchy layout shared by all meshes.

The tree is *implicit*: triangles are sorted once so that every aligned block
of 2**h consecutive triangles is spatially compact, and node ``i`` on level
``h`` simply covers triangles ``[i << h, (i + 1) << h)``.  No child pointers
are stored, which is what lets a whole frontier of node pairs be expanded and
tested with a handful of NumPy calls.

Level 0 holds the triangles themselves; the root is the single node on the
top level ``H``.
"""

import numpy as np

# Each level is padded to a multiple of this, so a node can be expanded up to
# three levels at once without ever indexing outside its level.
PAD = 8
MAX_LEVELS = 48


def level_layout(nt):
    """Sizes and offsets of the box levels for a mesh of ``nt`` triangles.

    Returns (H, nreal, npad, off, total_rows).
    """
    nreal = [int(nt)]
    while nreal[-1] > 1:
        nreal.append((nreal[-1] + 1) // 2)
    npad = [((x + PAD - 1) // PAD) * PAD for x in nreal]
    off = np.zeros(len(nreal), dtype=np.int64)
    acc = 0
    for h, n in enumerate(npad):
        off[h] = acc
        acc += n
    return len(nreal) - 1, nreal, npad, off, acc


def build_order(cx, cy, cz):
    """Triangle order for the implicit tree, by recursive median split.

    ``cx, cy, cz`` are contiguous float32 arrays with the triangle bounding-box
    centres; they are used as scratch space.  Every level of the tree is split
    in one vectorised pass (all nodes of a level are the rows of a 2-D array),
    so the cost is a few NumPy calls per level, never a Python loop over nodes.
    """
    n = cx.shape[0]
    order = np.arange(n, dtype=np.int32)
    if n <= 2:
        return order
    size = 1 << (n - 1).bit_length()
    while size >= 4:
        half = size >> 1
        m = n // size
        end = m * size
        if m:
            X = cx[:end].reshape(m, size)
            Y = cy[:end].reshape(m, size)
            Z = cz[:end].reshape(m, size)
            ex = X.max(axis=1) - X.min(axis=1)
            ey = Y.max(axis=1) - Y.min(axis=1)
            ez = Z.max(axis=1) - Z.min(axis=1)
            use_y = (ey > ex) & (ey >= ez)
            use_z = (ez > ex) & (ez > ey)
            key = np.where(use_z[:, None], Z, np.where(use_y[:, None], Y, X))
            part = np.argpartition(key, half, axis=1)
            part += (np.arange(m, dtype=np.int64) * size)[:, None]
            part = part.ravel()
            order[:end] = np.take(order[:end], part)
            cx[:end] = np.take(cx[:end], part)
            cy[:end] = np.take(cy[:end], part)
            cz[:end] = np.take(cz[:end], part)
        if n - end > half:
            tx = cx[end:]
            ty = cy[end:]
            tz = cz[end:]
            ext = (float(tx.max() - tx.min()), float(ty.max() - ty.min()),
                   float(tz.max() - tz.min()))
            key = (tx, ty, tz)[ext.index(max(ext))]
            part = np.argpartition(key, half)
            order[end:] = np.take(order[end:], part)
            cx[end:] = np.take(tx, part)
            cy[end:] = np.take(ty, part)
            cz[end:] = np.take(tz, part)
        size = half
    return order


def sort_mesh(verts, tris):
    """Bring a triangle mesh into the form the collision world stores.

    Returns (float32 vertices (nv, 3), int32 triangles (nt, 3) in tree order).
    Vertices no triangle uses are dropped, so that the extents of the mesh can
    be taken from its vertices alone; non-finite coordinates become zero.

    This is the expensive part of taking in a mesh (a few hundred nanoseconds
    per triangle).  It touches nothing but its arguments, so it can run in a
    worker thread; NumPy releases the interpreter lock for most of it.
    """
    verts = np.array(verts, dtype=np.float32).reshape(-1, 3)
    tris = np.ascontiguousarray(tris, dtype=np.int32).reshape(-1, 3)
    nt = tris.shape[0]
    if nt == 0:
        raise ValueError('mesh has no triangles')
    if not np.isfinite(verts).all():
        verts = np.nan_to_num(verts, nan=0.0, posinf=0.0, neginf=0.0)
    used = np.zeros(verts.shape[0], dtype=bool)
    used[tris.ravel()] = True
    if not used.all():
        remap = np.cumsum(used, dtype=np.int64) - 1
        verts = np.ascontiguousarray(verts[used])
        tris = np.ascontiguousarray(remap[tris], dtype=np.int32)
    cen = []
    idx = [np.ascontiguousarray(tris[:, k]) for k in range(3)]
    for c in range(3):
        col = np.ascontiguousarray(verts[:, c])
        a = np.take(col, idx[0])
        b = np.take(col, idx[1])
        d = np.take(col, idx[2])
        lo = np.minimum(a, b)
        np.minimum(lo, d, out=lo)
        np.maximum(a, b, out=a)
        np.maximum(a, d, out=a)
        lo += a
        lo *= 0.5
        cen.append(lo)
    del idx
    order = build_order(cen[0], cen[1], cen[2])
    del cen
    return verts, np.take(tris, order, axis=0)
