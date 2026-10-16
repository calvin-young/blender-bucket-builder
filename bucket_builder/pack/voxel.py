# SPDX-License-Identifier: GPL-3.0-or-later
"""Voxel grids of parts (docs/AUTOPACK.md section 8.1).

Cells are half-open: cell i along an axis covers [o + i h, o + (i + 1) h),
so every point lies in exactly one cell.  A part's grids, for one
orientation (a 3 x 3 linear map A, scale included):

* surface: every cell a point of the surface lies in.  Triangles are
  subdivided until no edge is longer than h; the cells of each piece's
  bounding box are then a superset of the cells it touches.
* solid: the surface plus everything a flood fill from outside does not
  reach (6-connected through empty cells).  A cavity open to the outside
  stays empty, so other parts may go there; a sealed void counts as solid.
  An open mesh is closed first (morphological closing), or the fill would
  run inside it.
* dilated(g): the cells a point closer than g to the solid can lie in, for
  the gap g.  Two layouts are kept apart by checking a candidate's dilated
  grid against the *solid* cells already taken: if they share no cell, no
  point of one part is within g of the other.  (The infimum distance
  between points of cells c and c + d is h * sqrt(sum max(|d_i| - 1, 0)^2),
  so the dilation is by the offsets where that is below g: a 3 x 3 x 3 cube
  followed by an integer ball of radius g / h.)

NumPy only; SciPy is used when it can be imported (Blender does not bundle
it) and gives the same grids.
"""

import math

import numpy as np

try:                                    # optional: faster flood fill and dilation
    from scipy import ndimage as _ndi
except Exception:                       # pragma: no cover - depends on the install
    _ndi = None

CHUNK = 1 << 18
EDT_FROM = 8.0      # cells: below this radius shifted copies beat SciPy's distance transform


def have_scipy():
    return _ndi is not None


# ------------------------------------------------------------------ surface

def _subdivide(P, h):
    """Triangles (n, 3, 3) cut in two across their longest edge until no edge
    is longer than h.  Yields arrays of finished triangles; the work is done
    depth first in batches, so a few huge triangles never hold more than a
    few batches in memory (a 200 mm face at h = 1 becomes some thousands of
    pieces, not millions)."""
    h2 = h * h * (1.0 - 1e-12)
    stack = [P]
    while stack:
        Q = stack.pop()
        if Q.shape[0] > CHUNK:
            stack.append(Q[CHUNK:])
            Q = Q[:CHUNK]
        e = np.stack([((Q[:, 1] - Q[:, 0]) ** 2).sum(1), ((Q[:, 2] - Q[:, 1]) ** 2).sum(1),
                      ((Q[:, 0] - Q[:, 2]) ** 2).sum(1)], 1)
        big = e.max(1) > h2
        if not big.all():
            yield Q[~big]
        if not big.any():
            continue
        Q = Q[big]
        k = e[big].argmax(1)                      # longest edge: (k, k + 1)
        n = np.arange(Q.shape[0])
        a = Q[n, k]
        b = Q[n, (k + 1) % 3]
        c = Q[n, (k + 2) % 3]
        m = 0.5 * (a + b)
        stack.append(np.concatenate([np.stack([a, m, c], 1), np.stack([m, b, c], 1)]))


def _mark(grid, P, origin, h):
    """Mark the bounding-box cells of small triangles P (edges <= h)."""
    if not P.shape[0]:
        return
    lo = np.floor((P.min(axis=1) - origin) / h).astype(np.int64)
    hi = np.floor((P.max(axis=1) - origin) / h).astype(np.int64)
    dims = np.array(grid.shape)
    lo = np.clip(lo, 0, dims - 1)
    hi = np.clip(hi, 0, dims - 1)
    flat = grid.reshape(-1)
    for dx in (0, 1):
        ix = np.minimum(lo[:, 0] + dx, hi[:, 0])
        for dy in (0, 1):
            iy = np.minimum(lo[:, 1] + dy, hi[:, 1])
            for dz in (0, 1):
                iz = np.minimum(lo[:, 2] + dz, hi[:, 2])
                flat[(ix * dims[1] + iy) * dims[2] + iz] = True


def _origin(lo, h, pad, align):
    if align == 'min':
        return lo - pad * h            # the lowest corner of the part on a cell corner
    return (np.floor(lo / h) - pad) * h


def surface_grid(verts, tris, A, h, pad, align='grid'):
    """(surface grid, origin): the cells the posed surface A @ v lies in, on a
    grid with `pad` empty cells on every side; origin is the corner of cell 0:
    a multiple of h (align 'grid'), or `pad` cells below the part's lowest
    corner (align 'min': the same part turned onto itself gets the same
    cells, and its low sides waste nothing)."""
    V = np.asarray(verts, np.float64) @ np.asarray(A, np.float64).T
    lo = V.min(axis=0)
    hi = V.max(axis=0)
    origin = _origin(lo, h, pad, align)
    dims = (np.floor((hi - origin) / h).astype(np.int64) + 1 + pad)
    grid = np.zeros(tuple(int(d) for d in dims), dtype=bool)
    t = np.asarray(tris)
    for s in range(0, t.shape[0], CHUNK):
        P = V[t[s:s + CHUNK]]
        for piece in _subdivide(P, h):
            _mark(grid, piece, origin, h)
    return grid, origin


# ------------------------------------------------------------------ large meshes

class Cloud:
    """A mesh's surface as the centres of the cells (side hl, in its own
    frame) that the surface lies in.  Made once per mesh; a turned copy is
    then voxelised from these points, at a cost set by the part's size, not
    its triangle count (docs/AUTOPACK.md section 8.10)."""

    def __init__(self, verts, tris, hl):
        grid, origin = surface_grid(verts, tris, np.eye(3), hl, 0)
        self.hl = hl
        self.centers = origin + (np.argwhere(grid) + 0.5) * hl
        self.lo = np.asarray(verts).min(axis=0)
        self.hi = np.asarray(verts).max(axis=0)


def cloud_cell(h, sigma):
    """Local cell size for a cloud that a map with largest stretch sigma turns
    into boxes no wider than h (each then touches at most 2 cells per axis)."""
    return 0.999 * h / (max(sigma, 1e-12) * math.sqrt(3.0))


def surface_grid_cloud(cloud, A, h, pad, align='grid'):
    """surface_grid from a Cloud: every point of a local cell lies within
    sigma * hl * sqrt(3) / 2 of the turned centre, so the cells of that
    box around it hold the turned surface (conservative)."""
    A = np.asarray(A, np.float64)
    sigma = float(np.linalg.svd(A, compute_uv=False)[0])
    R = 0.5 * sigma * cloud.hl * math.sqrt(3.0)
    if 2 * R > h:
        raise ValueError('cloud too coarse for this cell size and scale')
    C = cloud.centers @ A.T
    lo = C.min(axis=0) - R
    hi = C.max(axis=0) + R
    origin = _origin(lo, h, pad, align)
    dims = (np.floor((hi - origin) / h).astype(np.int64) + 1 + pad)
    grid = np.zeros(tuple(int(d) for d in dims), dtype=bool)
    for s in range(0, C.shape[0], CHUNK):
        c = C[s:s + CHUNK]
        _mark(grid, np.stack([c - R, c + R], axis=1), origin, h)
    return grid, origin


# ------------------------------------------------------------------ outside

def _propagate(empty, out, axis):
    """Cells of `empty` that share an empty run along `axis` with a cell of
    `out` become `out` too."""
    E = np.moveaxis(empty, axis, -1)
    O = np.moveaxis(out, axis, -1)
    n = E.shape[-1]
    lines = E.size // n
    E2 = E.reshape(lines, n)
    O2 = O.reshape(lines, n)
    seg = np.cumsum(~E2, axis=1, dtype=np.int64)          # run number along each line
    key = seg + (np.arange(lines, dtype=np.int64) * (n + 1))[:, None]
    hit = np.zeros(lines * (n + 1), dtype=bool)
    hit[key[O2]] = True
    new = E2 & hit[key]
    return np.moveaxis(new.reshape(E.shape), -1, axis)


def outside(blocked):
    """Empty cells connected (6-neighbours) to the grid's border through empty
    cells.  The border must be empty."""
    empty = ~blocked
    if _ndi is not None:
        lab, _ = _ndi.label(empty)
        border = np.unique(np.concatenate([
            lab[0].ravel(), lab[-1].ravel(), lab[:, 0].ravel(), lab[:, -1].ravel(),
            lab[:, :, 0].ravel(), lab[:, :, -1].ravel()]))
        border = border[border > 0]
        return np.isin(lab, border)
    out = np.zeros_like(empty)
    for sl in ((0,), (-1,)):
        out[sl] = empty[sl]
        out[:, sl[0]] = empty[:, sl[0]]
        out[:, :, sl[0]] = empty[:, :, sl[0]]
    count = int(out.sum())
    while True:
        for ax in (0, 1, 2):
            out = _propagate(empty, out, ax)
        c = int(out.sum())
        if c == count:
            return out
        count = c


# ------------------------------------------------------------------ dilation

def _shift_or(dst, src, d):
    """dst |= src shifted by the integer vector d (cells pushed out are lost)."""
    sd, ss = [], []
    for k, n in zip(d, src.shape):
        k = int(k)
        if k >= 0:
            sd.append(slice(k, n))
            ss.append(slice(0, n - k))
        else:
            sd.append(slice(0, n + k))
            ss.append(slice(-k, n))
    dst[tuple(sd)] |= src[tuple(ss)]


def dilate_cube(grid, r=1):
    """Dilation by the cube of offsets |d_i| <= r (separable)."""
    g = grid
    for ax in range(3):
        out = g.copy()
        for k in range(1, r + 1):
            d = [0, 0, 0]
            d[ax] = k
            _shift_or(out, g, d)
            d[ax] = -k
            _shift_or(out, g, d)
        g = out
    return g


def _run_or(g, w, axis):
    """OR of g shifted by -w .. +w along axis (doubling, O(log w) passes)."""
    if w <= 0:
        return g.copy()
    n = g.shape[axis]
    widths = [(0, 0)] * 3
    widths[axis] = (0, 2 * w)               # room for what the forward shifts push past the end
    g = np.pad(g, widths)
    length = 2 * w + 1
    acc = g.copy()          # covers offsets [0, 1)
    span = 1
    while span * 2 <= length:
        d = [0, 0, 0]
        d[axis] = span
        nxt = acc.copy()
        _shift_or(nxt, acc, d)
        acc = nxt
        span *= 2
    if span < length:       # top up to the full length
        d = [0, 0, 0]
        d[axis] = length - span
        nxt = acc.copy()
        _shift_or(nxt, acc, d)
        acc = nxt
    sl = [slice(None)] * 3
    sl[axis] = slice(w, w + n)
    return acc[tuple(sl)].copy()


def ball_offsets(r):
    """Integer offsets e with |e| < r."""
    k = int(math.ceil(r))
    ax = np.arange(-k, k + 1)
    e = np.stack(np.meshgrid(ax, ax, ax, indexing='ij'), -1).reshape(-1, 3)
    return e[(e * e).sum(1) < r * r - 1e-9]


def dilate_ball(grid, r):
    """Dilation by the integer ball |e| < r (cells)."""
    if r <= 1e-12:
        return grid.copy()
    if _ndi is not None and r > EDT_FROM:
        d = _ndi.distance_transform_edt(~grid)
        return np.rint(d * d) < r * r - 1e-9
    k = int(math.ceil(r))
    out = np.zeros_like(grid)
    rows = {}
    for ez in range(-k, k + 1):
        for ey in range(-k, k + 1):
            rr = r * r - 1e-9 - ez * ez - ey * ey
            if rr <= 0:
                continue
            w = int(math.ceil(math.sqrt(rr))) - 1        # largest ex with ex^2 < rr
            while (w + 1) ** 2 < rr:
                w += 1
            while w > 0 and w * w >= rr:
                w -= 1
            if w not in rows:
                rows[w] = _run_or(grid, w, 0)
            _shift_or(out, rows[w], (0, ey, ez))
    return out


def gap_cells(gap, h):
    """How many cells the dilation for gap g reaches beyond the solid."""
    if gap <= 0:
        return 0
    return 1 + int(math.ceil(gap / h))


def dilate_gap(solid, gap, h):
    """The candidate grid for gap `gap`: every cell a point closer than `gap`
    to the solid can lie in.  gap <= 0: the solid itself (parts may then
    touch, which the exact engine calls a collision; use a small gap)."""
    if gap <= 0:
        return solid.copy()
    return dilate_ball(dilate_cube(solid, 1), gap / h)


# ------------------------------------------------------------------ parts

class PartGrid:
    """One part in one orientation, voxelised.

    ``origin`` is the corner of cell 0 in the posed part's frame (mm): the
    part placed with translation t puts cell 0 at origin + t.  ``solid`` and
    ``kernel`` (solid dilated by the gap) have the same shape; ``s_lo`` and
    ``s_hi`` bound the solid's cells (half-open)."""

    def __init__(self, solid, kernel, origin, h, gap, closed):
        self.solid = solid
        self.kernel = kernel
        self.origin = origin
        self.h = h
        self.gap = gap
        self.closed = closed            # the mesh had to be closed first
        idx = [np.nonzero(solid.any(axis=tuple(a for a in range(3) if a != ax)))[0] for ax in range(3)]
        self.s_lo = np.array([i[0] for i in idx])
        self.s_hi = np.array([i[-1] + 1 for i in idx])
        self.n_solid = int(solid.sum())
        self.n_kernel = int(kernel.sum())
        self._coarse = {}

    @property
    def shape(self):
        return self.solid.shape

    def coarse(self, f, phase=(0, 0, 0)):
        """The kernel max-pooled by f with the part's cell 0 at fine offset
        `phase` inside block 0 (a conservative stand-in at coarse level)."""
        key = (f, tuple(int(p) for p in phase))
        g = self._coarse.get(key)
        if g is None:
            g = pool(self.kernel, f, phase)
            self._coarse[key] = g
        return g


def pool(grid, f, phase=(0, 0, 0)):
    """Max-pool by f, with the grid's cell 0 at offset `phase` in block 0."""
    phase = [int(p) for p in phase]
    dims = [int(math.ceil((n + p) / f)) for n, p in zip(grid.shape, phase)]
    big = np.zeros([d * f for d in dims], dtype=bool)
    big[phase[0]:phase[0] + grid.shape[0], phase[1]:phase[1] + grid.shape[1],
        phase[2]:phase[2] + grid.shape[2]] = grid
    return big.reshape(dims[0], f, dims[1], f, dims[2], f).any(axis=(1, 3, 5))


def voxelize(verts, tris, A, h, gap, close_cells=0, cloud=None, align='grid'):
    """PartGrid of a mesh posed by A, at cell size h, for gap `gap`.

    close_cells > 0 closes holes up to about twice that many cells wide
    before the flood fill (for open meshes; costs concavities that narrow).
    With a `Cloud` the surface comes from it instead of the triangles."""
    pad = gap_cells(gap, h) + close_cells + 1
    if cloud is not None:
        surf, origin = surface_grid_cloud(cloud, A, h, pad, align)
    else:
        surf, origin = surface_grid(verts, tris, A, h, pad, align)
    if close_cells > 0:
        thick = dilate_cube(surf, close_cells)
        out = outside(thick)
        solid = ~dilate_cube(out, close_cells) | surf
    else:
        solid = ~outside(surf)
    kernel = dilate_gap(solid, gap, h)
    return PartGrid(solid, kernel, origin, h, gap, close_cells > 0)
