# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounding-volume hierarchy layout shared by all meshes.

The tree is *implicit*: triangles are sorted once so that every aligned block
of 2**h consecutive triangles is spatially compact, and node ``i`` on level
``h`` simply covers triangles ``[i << h, (i + 1) << h)``.  No child pointers
are stored, which is what lets a whole frontier of node pairs be expanded and
tested with a handful of NumPy calls.

Level 0 holds the triangles themselves; the root is the single node on the
top level ``H``.

Sorting is the expensive part of taking in a mesh (about a quarter of a
microsecond per triangle on one processor core).  Nothing in here touches
anything but its arguments, so it can run in worker threads, and one large
mesh can be shared out between several: NumPy releases the interpreter lock
for nearly all of the work.
"""

import threading

import numpy as np

# Each level is padded to a multiple of this, so a node can be expanded up to
# three levels at once without ever indexing outside its level.
PAD = 8
MAX_LEVELS = 48

BLOCK = 1 << 16      # a subtree of this many triangles is finished in one go (it fits the cache)
SMALL = 256          # rows shorter than this have their extents taken column by column
CHUNK = 1 << 18      # triangles per piece of the work that is simply cut into ranges


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


class Cancelled(Exception):
    """A sort was told to stop (the add-on is being switched off)."""


def _run(tasks):
    """Run the callables, the first here and the others in threads of their
    own; returns when all are done.  The first exception is raised again."""
    if len(tasks) == 1:
        tasks[0]()
        return
    errors = []

    def guarded(fn):
        try:
            fn()
        except BaseException as ex:      # handed to the caller below
            errors.append(ex)

    threads = [threading.Thread(target=guarded, args=(fn,), name='bucket_builder_sort')
               for fn in tasks[1:]]
    for t in threads:
        t.start()
    guarded(tasks[0])
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


def _ranges(n, pieces, step=1):
    """``[0, n)`` cut into at most ``pieces`` ranges of about the same size
    whose boundaries are multiples of ``step``."""
    units = -(-n // step)
    pieces = max(1, min(int(pieces), units))
    out = []
    for i in range(pieces):
        s = (units * i // pieces) * step
        e = min(n, (units * (i + 1) // pieces) * step)
        if e > s:
            out.append((s, e))
    return out


def _row_minmax(X):
    """(min, max) of every row of a contiguous (m, size) array, ``size`` a
    power of two.  A reduction along short rows spends its time on the
    bookkeeping per row; halving the columns instead costs the same for any
    row length."""
    if X.shape[1] >= SMALL:
        return X.min(axis=1), X.max(axis=1)
    a = X[:, 0::2]
    b = X[:, 1::2]
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    while lo.shape[1] > 1:
        lo = np.minimum(lo[:, 0::2], lo[:, 1::2])
        hi = np.maximum(hi[:, 0::2], hi[:, 1::2])
    return lo[:, 0], hi[:, 0]


def _levels(cx, cy, cz, order, size, stop=2, cancel=None):
    """Median splits of the aligned blocks of ``size`` elements, then of their
    halves, and so on while the blocks are larger than ``stop``.

    All blocks of a level are split in one vectorised pass (they are the rows
    of a 2-D array), so the cost is a few NumPy calls per level, never a
    Python loop over nodes.  The arrays are permuted in place.
    """
    n = cx.shape[0]
    while size > stop and size >= 4:
        if cancel is not None and cancel():
            raise Cancelled()
        half = size >> 1
        m = n // size
        end = m * size
        if m:
            X = cx[:end].reshape(m, size)
            Y = cy[:end].reshape(m, size)
            Z = cz[:end].reshape(m, size)
            lo, hi = _row_minmax(X)
            ex = hi - lo
            lo, hi = _row_minmax(Y)
            ey = hi - lo
            lo, hi = _row_minmax(Z)
            ez = hi - lo
            use_y = (ey > ex) & (ey >= ez)
            use_z = (ez > ex) & (ez > ey)
            if size >= SMALL:
                key = np.where(use_z[:, None], Z, np.where(use_y[:, None], Y, X))
                part = np.argpartition(key, half, axis=1)
                part += (np.arange(m, dtype=np.int64) * size)[:, None]
            else:
                # (broadcasting a value per row over short rows is as slow as
                # reducing them: spell the rows out instead)
                uy = np.repeat(use_y, size).reshape(m, size)
                uz = np.repeat(use_z, size).reshape(m, size)
                key = np.where(uz, Z, np.where(uy, Y, X))
                part = np.argpartition(key, half, axis=1)
                part += np.repeat(np.arange(0, end, size, dtype=np.int64), size).reshape(m, size)
            del key
            part = part.ravel()
            order[:end] = np.take(order[:end], part)
            cx[:end] = np.take(cx[:end], part)
            cy[:end] = np.take(cy[:end], part)
            cz[:end] = np.take(cz[:end], part)
            del part
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


def _finish(cx, cy, cz, order, size, cancel=None):
    """Every level from blocks of ``size`` down, for arrays that start at a
    block boundary.  Once the blocks fit the cache each one is finished
    before the next is touched."""
    if size > BLOCK:
        _levels(cx, cy, cz, order, size, BLOCK, cancel)
        size = BLOCK
    n = cx.shape[0]
    if n <= BLOCK:
        _levels(cx, cy, cz, order, size, 2, cancel)
        return
    for s in range(0, n, BLOCK):
        e = min(n, s + BLOCK)
        _levels(cx[s:e], cy[s:e], cz[s:e], order[s:e], BLOCK, 2, cancel)


def build_order(cx, cy, cz, cancel=None, threads=1):
    """Triangle order for the implicit tree, by recursive median split along
    the longest side of each node.

    ``cx, cy, cz`` are contiguous float32 arrays with the triangle bounding-box
    centres; they are used as scratch space.  ``cancel``, if given, is called
    now and then; when it returns true the sort is abandoned with
    ``Cancelled``.  With ``threads`` > 1 the subtrees are shared out between
    that many threads (the result does not depend on it).
    """
    n = cx.shape[0]
    order = np.arange(n, dtype=np.int32)
    if n <= 2:
        return order
    size = 1 << (n - 1).bit_length()
    threads = max(1, int(threads))

    def piece(s, e, size, stop):
        if stop:
            return lambda: _levels(cx[s:e], cy[s:e], cz[s:e], order[s:e], size, stop, cancel)
        return lambda: _finish(cx[s:e], cy[s:e], cz[s:e], order[s:e], size, cancel)

    # near the root there are too few nodes to share out evenly: one level at
    # a time, the nodes of a level side by side
    while threads > 1 and size > BLOCK and -(-n // size) < 4 * threads:
        _run([piece(s, e, size, size >> 1) for s, e in _ranges(n, threads, size)])
        size >>= 1
    # from here on every thread has subtrees of its own to finish
    _run([piece(s, e, size, 0) for s, e in _ranges(n, threads, size)])
    return order


def clean_mesh(verts, tris):
    """A triangle mesh as the collision world stores it: float32 vertices
    (nv, 3) and int32 triangles (nt, 3).  Vertices no triangle uses are
    dropped, so that the extents of the mesh can be taken from its vertices
    alone; non-finite coordinates become zero.  The arguments are returned
    as they are when there is nothing to do."""
    verts = np.asarray(verts, dtype=np.float32).reshape(-1, 3)
    tris = np.ascontiguousarray(tris, dtype=np.int32).reshape(-1, 3)
    if tris.shape[0] == 0:
        raise ValueError('mesh has no triangles')
    if not np.isfinite(verts).all():
        verts = np.nan_to_num(verts, nan=0.0, posinf=0.0, neginf=0.0)
    used = np.zeros(verts.shape[0], dtype=bool)
    used[tris.ravel()] = True
    if not used.all():
        remap = np.cumsum(used, dtype=np.int64) - 1
        verts = np.ascontiguousarray(verts[used])
        tris = np.ascontiguousarray(remap[tris], dtype=np.int32)
    return verts, tris


def order_mesh(verts, tris, cancel=None, threads=1):
    """The triangles (int32 (nt, 3)) of a mesh in tree order, as a new array.

    ``verts`` and ``tris`` are only read.  See ``build_order`` for ``cancel``
    and ``threads``.
    """
    nt = tris.shape[0]
    threads = max(1, int(threads))
    cols = [np.ascontiguousarray(verts[:, c]) for c in range(3)]
    cen = [np.empty(nt, dtype=np.float32) for _ in range(3)]

    def centres(s0, e0):
        def work():
            for s in range(s0, e0, CHUNK):
                if cancel is not None and cancel():
                    raise Cancelled()
                e = min(e0, s + CHUNK)
                idx = [np.ascontiguousarray(tris[s:e, k]) for k in range(3)]
                for c in range(3):
                    a = np.take(cols[c], idx[0])
                    b = np.take(cols[c], idx[1])
                    d = np.take(cols[c], idx[2])
                    lo = np.minimum(a, b)
                    np.minimum(lo, d, out=lo)
                    np.maximum(a, b, out=a)
                    np.maximum(a, d, out=a)
                    lo += a
                    lo *= 0.5
                    cen[c][s:e] = lo
        return work

    _run([centres(s, e) for s, e in _ranges(nt, threads, CHUNK)])
    del cols
    order = build_order(cen[0], cen[1], cen[2], cancel, threads)
    del cen
    out = np.empty((nt, 3), dtype=np.int32)

    def gather(s0, e0):
        def work():
            for s in range(s0, e0, CHUNK):
                e = min(e0, s + CHUNK)
                out[s:e] = np.take(tris, order[s:e], axis=0)
        return work

    _run([gather(s, e) for s, e in _ranges(nt, threads, CHUNK)])
    return out


def sort_mesh(verts, tris, cancel=None, threads=1):
    """Bring a triangle mesh into the form the collision world stores.

    Returns (float32 vertices (nv, 3), int32 triangles (nt, 3) in tree order):
    ``clean_mesh`` followed by ``order_mesh``.
    """
    verts, tris = clean_mesh(verts, tris)
    return verts, order_mesh(verts, tris, cancel, threads)
