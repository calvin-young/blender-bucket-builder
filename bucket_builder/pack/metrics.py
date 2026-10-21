# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact volume, per-layer volume and density of a build (no `bpy`).

Part volume is the signed tetrahedron sum.  The volume of a part *below a
height h* needs no slicing or voxels either: with the field F = (0, 0, z - h)
the divergence theorem gives

    V(h) = sum over triangles of  a * integral over (the triangle below h) of (z - h) dA / A

where a = n_z A is the triangle's signed area projected on the XY plane.  The
cap at height h contributes nothing (F is zero there).  Triangles entirely
below h contribute a * (z_mean - h); the few that straddle h are clipped in
closed form.  Band volumes are differences of V at the band boundaries, so
every band is exact and the bands add up to the part's volume.

Cost is linear in triangles, in chunks (a 20 M triangle part never needs more
than a few hundred MB).  Copies of a part with the same orientation share the
per-triangle work and only differ in a height offset.

Units are whatever the meshes are in; the caller uses millimeters.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

CHUNK = 1 << 19
EPS = 1e-6                    # mm: tolerance of the inside / outside tests
OPEN_TOL = 1e-6               # |sum of vector areas| / sum of areas above which a mesh is open

INSIDE, PARTIAL, OUTSIDE = 'inside', 'partial', 'outside'


# ---------------------------------------------------------------- one mesh

def _columns(v, shift=None):
    """x, y, z of an (n, 3) array as three contiguous float64 arrays."""
    if shift is None:
        shift = np.zeros(3)
    return [np.ascontiguousarray(v[:, i] - shift[i], dtype=np.float64) for i in range(3)]


def _stats_chunk(cols, t):
    x, y, z = cols
    i0, i1, i2 = t[:, 0], t[:, 1], t[:, 2]
    x0, y0, z0 = x[i0], y[i0], z[i0]
    x1, y1, z1 = x[i1], y[i1], z[i1]
    x2, y2, z2 = x[i2], y[i2], z[i2]
    ex, ey, ez = x1 - x0, y1 - y0, z1 - z0
    fx, fy, fz = x2 - x0, y2 - y0, z2 - z0
    nx = ey * fz - ez * fy
    ny = ez * fx - ex * fz
    nz = ex * fy - ey * fx
    # six times the tetrahedron volume (origin, p0, p1, p2) = p0 . (p1 x p2)
    vol = (x0 * (y1 * z2 - z1 * y2) + y0 * (z1 * x2 - x1 * z2) + z0 * (x1 * y2 - y1 * x2)).sum()
    return (float(vol), float(nx.sum()), float(ny.sum()), float(nz.sum()),
            float(np.abs(nx).sum() + np.abs(ny).sum() + np.abs(nz).sum()))


def mesh_stats(mesh, workers=None):
    """(signed volume, openness) of a mesh in its own coordinates.

    Openness is |sum of triangle vector areas| / (sum of their absolute
    components): zero (to rounding) for a closed surface, whatever its
    orientation, and clearly positive for most open ones.  It does not find
    every defect (a mesh that is open in two places that cancel); `topology`
    does."""
    v, t = mesh.verts, mesh.tris
    if t.shape[0] == 0:
        return 0.0, 0.0
    c = v[np.unique(t[:min(t.shape[0], 4096)].ravel())].mean(axis=0, dtype=np.float64)
    cols = _columns(v, c)
    parts = _map(lambda r: _stats_chunk(cols, t[r[0]:r[1]]), t.shape[0], workers)
    tot = np.sum(parts, axis=0)
    return tot[0] / 6.0, (float(np.linalg.norm(tot[1:4])) / tot[4] if tot[4] > 0 else 0.0)


def _map(fn, n, workers=None, chunk=None):
    """Apply fn((start, stop)) to consecutive ranges covering n items, in a
    thread pool (NumPy releases the GIL in the heavy calls); results in order."""
    chunk = chunk or CHUNK
    ranges = [(s, min(s + chunk, n)) for s in range(0, n, chunk)]
    w = workers if workers is not None else default_workers()
    if w <= 1 or len(ranges) <= 1:
        return [fn(r) for r in ranges]
    with ThreadPoolExecutor(max_workers=min(w, len(ranges))) as ex:
        return list(ex.map(fn, ranges))


def default_workers():
    return max(1, min(os.cpu_count() or 1, 8))


def topology(mesh):
    """Count edges that stop a mesh from being a closed, consistently oriented
    surface: boundary edges (used once), edges used by more than two triangles,
    and edges whose two triangles disagree about direction.  Needs welded
    vertices (`meshio.weld` for an STL)."""
    t = mesh.tris.astype(np.int64)
    nv = int(mesh.verts.shape[0])
    d = np.concatenate([t[:, 0] * nv + t[:, 1], t[:, 1] * nv + t[:, 2], t[:, 2] * nv + t[:, 0]])
    r = np.concatenate([t[:, 1] * nv + t[:, 0], t[:, 2] * nv + t[:, 1], t[:, 0] * nv + t[:, 2]])
    ud, cd = np.unique(d, return_counts=True)
    same_dir = int((cd > 1).sum())                   # a directed edge used twice
    have_rev = np.isin(ud, r, assume_unique=False)
    boundary = int((~have_rev).sum())                # no triangle runs the other way
    undirected = np.minimum(d, r)
    _, cu = np.unique(undirected, return_counts=True)
    return {'boundary_edges': boundary, 'nonmanifold_edges': int((cu > 2).sum()),
            'flipped_edges': same_dir}


# ---------------------------------------------------------------- per pose

def _pose_columns(mesh, A):
    """The mesh's vertices under the linear map A as x, y, z columns."""
    V = mesh.verts @ np.asarray(A, float).T
    return [np.ascontiguousarray(V[:, i]) for i in range(3)]


def _pose_chunk(cols, t):
    """Per-triangle quantities for one chunk of triangles `t`, given the
    transformed vertex columns: z of the lowest, middle and highest vertex, the
    signed projected area a = n_z * area, and the chunk's extents.  Triangles
    with a == 0 (vertical, degenerate) are left out of the arrays but not out
    of the extents."""
    x, y, z = cols
    i0, i1, i2 = t[:, 0], t[:, 1], t[:, 2]
    z0, z1, z2 = z[i0], z[i1], z[i2]
    x0, y0 = x[i0], y[i0]
    x1, y1 = x[i1], y[i1]
    x2, y2 = x[i2], y[i2]
    zlo = np.minimum(np.minimum(z0, z1), z2)
    zhi = np.maximum(np.maximum(z0, z1), z2)
    ext = (np.array([min(x0.min(), x1.min(), x2.min()), min(y0.min(), y1.min(), y2.min()), zlo.min()]),
           np.array([max(x0.max(), x1.max(), x2.max()), max(y0.max(), y1.max(), y2.max()), zhi.max()]))
    a = 0.5 * ((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0))
    keep = a != 0.0
    zlo, zhi, a = zlo[keep], zhi[keep], a[keep]
    zmid = z0[keep] + z1[keep] + z2[keep] - zlo - zhi
    return zlo, zmid, zhi, a, ext


def _accumulate(zl, zm, zh, a, off, dz, nb, acc):
    """Add one chunk, shifted up by `off`, to the accumulators of one instance.

    Boundaries are h_j = j * dz, j = 0 .. nb.  acc = [S0, S1, strad] arrays of
    length nb + 2: S0 / S1 collect a and a * z_mean of triangles entirely below
    a boundary at index m (they count for every boundary j >= m), strad the
    clipped parts of triangles that straddle a boundary."""
    if zl.size == 0:
        return
    lo = zl + off
    mid = zm + off
    hi = zh + off
    inv = 1.0 / dz
    mtop = np.ceil(hi * inv)                           # first boundary at or above the top
    m = np.clip(mtop, 0, nb + 1).astype(np.intp)
    acc[0] += np.bincount(m, weights=a, minlength=nb + 2)
    acc[1] += np.bincount(m, weights=a * ((lo + mid + hi) * (1.0 / 3.0)), minlength=nb + 2)
    # triangles that straddle boundaries j_lo .. j_hi
    j_lo = np.maximum(np.floor(lo * inv) + 1, 0)
    j_hi = np.minimum(mtop - 1, nb)
    cnt = np.maximum(j_hi - j_lo + 1, 0).astype(np.intp)
    sel = np.nonzero(cnt)[0]
    if sel.size == 0:
        return
    cnt = cnt[sel]
    total = int(cnt.sum())
    rep = np.repeat(np.arange(sel.size), cnt)
    first = np.cumsum(cnt) - cnt
    j = (j_lo[sel][rep] + (np.arange(total) - first[rep])).astype(np.intp)
    l, md, h_, aa = lo[sel][rep], mid[sel][rep], hi[sel][rep], a[sel][rep]
    h = np.clip(j * dz, l, h_)
    span = h_ - l
    span = np.where(span > 0, span, 1.0)
    d1 = np.where(md - l > 0, md - l, 1.0)
    d2 = np.where(h_ - md > 0, h_ - md, 1.0)
    below_mid = h <= md
    # h in [low, middle]: the corner at the lowest vertex is below h
    t_ac = (h - l) / span
    t_ab = (h - l) / d1
    v1 = aa * t_ac * t_ab * ((l - h) * (1.0 / 3.0))
    # h in (middle, high]: everything but the corner at the highest vertex
    top = ((h_ - h) / span) * ((h_ - h) / d2)
    v2 = aa * ((l + md + h_) * (1.0 / 3.0) - h) - aa * top * ((h_ - h) * (1.0 / 3.0))
    acc[2] += np.bincount(j, weights=np.where(below_mid, v1, v2), minlength=nb + 2)


def _boundary_volumes(acc, dz, nb):
    """V(h_j) for j = 0 .. nb from the accumulators."""
    h = np.arange(nb + 1) * dz
    S0 = np.cumsum(acc[0])[:nb + 1]
    S1 = np.cumsum(acc[1])[:nb + 1]
    return S1 - h * S0 + acc[2][:nb + 1]


def _group_pass(mesh, A, tz, z0, dz, nb, want_bands, workers=None):
    """One orientation of one mesh, for the instances whose heights are `tz`:
    (total signed volumes, bounding box lo, hi, accumulators per instance).
    The per-triangle work is done once; each instance only shifts it."""
    cols = _pose_columns(mesh, A)
    t = mesh.tris
    pivot = float(cols[2][t[0, 0]])
    k = len(tz)

    def one(r):
        zl, zm, zh, a, ext = _pose_chunk(cols, t[r[0]:r[1]])
        tot = float((a * ((zl + zm + zh) * (1.0 / 3.0) - pivot)).sum()) if zl.size else 0.0
        acc = None
        if want_bands and zl.size:
            acc = [[np.zeros(nb + 2) for _ in range(3)] for _ in range(k)]
            for q, h in enumerate(tz):
                _accumulate(zl, zm, zh, a, h - z0, dz, nb, acc[q])
        return tot, ext, acc

    tot = 0.0
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    acc = [[np.zeros(nb + 2) for _ in range(3)] for _ in range(k)] if want_bands else None
    for ctot, (elo, ehi), cacc in _map(one, t.shape[0], workers):
        tot += ctot
        lo = np.minimum(lo, elo)
        hi = np.maximum(hi, ehi)
        if cacc is not None:
            for q in range(k):
                for w in range(3):
                    acc[q][w] += cacc[q][w]
    return np.full(k, tot), lo, hi, acc


@dataclass
class PartResult:
    name: str
    volume: float = 0.0               # physical volume, >= 0
    lo: np.ndarray = None             # world bounding box
    hi: np.ndarray = None
    status: str = INSIDE
    openness: float = 0.0
    inside_out: bool = False          # winding is reversed (volume came out negative)
    bounds: np.ndarray = None         # V(h_j): volume below each band boundary
    below: float = 0.0                # volume under the first boundary
    above: float = 0.0                # volume over the last boundary

    @property
    def is_open(self):
        return self.openness > OPEN_TOL


def classify(lo, hi, vlo, vhi, eps=EPS):
    if np.any(hi <= vlo + eps) or np.any(lo >= vhi - eps):
        return OUTSIDE
    if np.all(lo >= vlo - eps) and np.all(hi <= vhi + eps):
        return INSIDE
    return PARTIAL


def n_bands(vlo, vhi, band):
    """How many bands of `band` thickness reach the volume's ceiling."""
    return max(1, int(np.ceil((float(vhi[2]) - float(vlo[2])) / band - 1e-9)))


def cache_key(mesh, A, z, z0, band, nb):
    """Key of one part's result in a cache, or None for a mesh without a `key`.
    Moving a part in X or Y does not change its volume per band, so only the
    mesh, its linear part, its height and the band grid count."""
    k = getattr(mesh, 'key', None)
    if k is None:
        return None
    return (k, np.asarray(A, float).tobytes(), float(z), float(z0), float(band), int(nb))


def missing(cache, mesh, A, z, z0, band, nb):
    """Whether measuring this part would have to read the mesh: its result or
    its mesh statistics are not in `cache`."""
    ck = cache_key(mesh, A, z, z0, band, nb)
    return ck is None or ck not in cache or ('stats', mesh.key) not in cache


def measure_parts(parts, vlo, vhi, band=1.0, nbands=None, want_bands=True, workers=None,
                  cache=None):
    """Per-part exact results for `parts` = [(name, mesh, matrix 4x4), ...].

    Bands start at the volume's floor vlo[2], `band` thick, `nbands` of them
    (default: enough to reach the volume's ceiling).  Returns a list of
    PartResult in the order of `parts`.

    `cache` (a dict, kept by the caller) remembers results of meshes that have
    a `key` attribute (anything hashable that identifies their content).  A
    part that moved sideways, or a copy at a height already seen, costs
    nothing, and a mesh whose results are all cached is never read: it may be
    a stand-in with only `key` and `nt`."""
    vlo = np.asarray(vlo, float)
    vhi = np.asarray(vhi, float)
    z0 = float(vlo[2])
    if nbands is None:
        nbands = n_bands(vlo, vhi, band)
    nb = int(nbands)
    if cache is not None and not want_bands:
        cache = None
    stats = {}
    groups = {}
    for i, (name, mesh, M) in enumerate(parts):
        M = np.asarray(M, float)
        key = (id(mesh), M[:3, :3].tobytes())
        groups.setdefault(key, (mesh, M[:3, :3].copy(), []))[2].append(i)
    out = [None] * len(parts)
    for mesh, A, idxs in groups.values():
        mkey = getattr(mesh, 'key', None)
        use = cache is not None and mkey is not None
        if mesh.nt == 0:
            for i in idxs:
                p = np.asarray(parts[i][2], float)[:3, 3]
                r = PartResult(parts[i][0], 0.0, p.copy(), p.copy())
                r.status = classify(r.lo, r.hi, vlo, vhi)
                if want_bands:
                    r.bounds = np.zeros(nb + 1)
                out[i] = r
            continue
        # statistics of the mesh (volume sign, openness)
        if use and ('stats', mkey) in cache:
            local_vol, openness = cache[('stats', mkey)]
        else:
            if id(mesh) not in stats:
                stats[id(mesh)] = mesh_stats(mesh, workers)
            local_vol, openness = stats[id(mesh)]
            if use:
                cache[('stats', mkey)] = stats[id(mesh)]
        # per-instance results: (signed total, lo, hi, raw boundary volumes)
        tzs = [float(parts[i][2][2, 3]) for i in idxs]
        found = {}
        todo = []
        for tz in dict.fromkeys(tzs):
            ck = cache_key(mesh, A, tz, z0, band, nb) if use else None
            if ck is not None and ck in cache:
                found[tz] = cache[ck]
            else:
                todo.append(tz)
        if todo:
            tot, lo, hi, acc = _group_pass(mesh, A, todo, z0, band, nb, want_bands, workers)
            for k, tz in enumerate(todo):
                Vb = _boundary_volumes(acc[k], band, nb) if want_bands else None
                found[tz] = (float(tot[k]), lo, hi, Vb)
                if use:
                    cache[cache_key(mesh, A, tz, z0, band, nb)] = found[tz]
        for k, i in enumerate(idxs):
            total, lo, hi, Vb = found[tzs[k]]
            name = parts[i][0]
            r = PartResult(name, abs(total), lo + parts[i][2][:3, 3], hi + parts[i][2][:3, 3])
            r.openness = openness
            r.inside_out = local_vol < 0
            r.status = classify(r.lo, r.hi, vlo, vhi)
            if want_bands:
                # `Vb` holds a * (z - z0) sums, whose total differs from `total`
                # (measured from the pivot) only by rounding for a closed mesh
                sgn = -1.0 if total < 0 else 1.0
                r.bounds = Vb * sgn
                r.below = float(r.bounds[0])
                r.above = float(r.volume - r.bounds[-1])
            out[i] = r
    return out


# ---------------------------------------------------------------- the build

@dataclass
class Report:
    n_parts: int = 0
    n_inside: int = 0
    n_partial: int = 0
    n_outside: int = 0
    volume: float = 0.0               # of the parts counted (inside and partial)
    volume_outside: float = 0.0       # of parts wholly outside, not counted
    height: float = 0.0               # highest point of the counted parts above the floor
    bottom: float = 0.0               # lowest point of the counted parts above the floor (0 if below)
    area: float = 0.0                 # build area
    density: float = None             # volume / (area * (height - bottom)): only the height the parts fill
    band: float = 1.0
    z_mid: np.ndarray = None          # band centers, relative to the floor
    layer_volume: np.ndarray = None   # per band
    layer_density: np.ndarray = None  # per band: volume / (area * band)
    peak_layer: float = 0.0
    spread_cv: float = None           # coefficient of variation of the smoothed profile
    smooth_mm: float = 5.0
    open_parts: list = field(default_factory=list)
    inside_out_parts: list = field(default_factory=list)
    partial_parts: list = field(default_factory=list)
    outside_parts: list = field(default_factory=list)
    parts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def smooth(values, width_bands):
    """Moving average over `width_bands` bands (edges use the bands there are)."""
    w = max(1, int(round(width_bands)))
    if w <= 1 or values.size == 0:
        return values.copy()
    c = np.concatenate([[0.0], np.cumsum(values)])
    idx = np.arange(values.size)
    lo = np.clip(idx - w // 2, 0, values.size)
    hi = np.clip(idx - w // 2 + w, 0, values.size)
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def measure(parts, vlo, vhi, band=1.0, smooth_mm=5.0, workers=None, cache=None):
    """The report of a build: `parts` = [(name, mesh, matrix), ...] in mm and
    the build volume's corners `vlo`, `vhi`."""
    vlo = np.asarray(vlo, float)
    vhi = np.asarray(vhi, float)
    res = measure_parts(parts, vlo, vhi, band, workers=workers, cache=cache)
    rep = Report(band=band, smooth_mm=smooth_mm, parts=res)
    rep.n_parts = len(res)
    rep.area = float((vhi[0] - vlo[0]) * (vhi[1] - vlo[1]))
    nb = None
    low = None
    for r in res:
        if r.status == OUTSIDE:
            rep.n_outside += 1
            rep.volume_outside += r.volume
            rep.outside_parts.append(r.name)
            continue
        rep.n_inside += r.status == INSIDE
        if r.status == PARTIAL:
            rep.n_partial += 1
            rep.partial_parts.append(r.name)
        rep.volume += r.volume
        rep.height = max(rep.height, float(r.hi[2] - vlo[2]))
        low = float(r.lo[2] - vlo[2]) if low is None else min(low, float(r.lo[2] - vlo[2]))
        nb = r.bounds.size - 1 if nb is None else nb
    rep.bottom = max(0.0, low) if low is not None else 0.0
    for r in res:
        if r.is_open:
            rep.open_parts.append(r.name)
        if r.inside_out and not r.is_open:
            rep.inside_out_parts.append(r.name)
    if nb is not None:
        lay = np.zeros(nb)
        for r in res:
            if r.status != OUTSIDE:
                lay += np.diff(r.bounds)
        rep.layer_volume = lay
        rep.z_mid = (np.arange(nb) + 0.5) * band
        rep.layer_density = lay / (rep.area * band) if rep.area > 0 else lay * 0
        rep.peak_layer = float(rep.layer_density.max()) if nb else 0.0
        if rep.height > rep.bottom:
            used = max(1, int(np.ceil(rep.height / band - 1e-9)))
            first = min(int(np.floor(rep.bottom / band + 1e-9)), used - 1)
            sm = smooth(rep.layer_density[first:used], smooth_mm / band)
            m = float(sm.mean())
            rep.spread_cv = float(sm.std() / m) if m > 0 else None
    # (the density of the height the parts fill: a cube in the middle of the
    # bucket is as dense as on the floor, whatever is empty above or below)
    if rep.height > rep.bottom and rep.area > 0:
        rep.density = rep.volume / (rep.area * (rep.height - rep.bottom))
    # warnings, in the order the reader needs them
    if rep.n_partial:
        rep.warnings.append(
            '%d part%s partly outside the build volume: the density counts %s in full'
            % (rep.n_partial, '' if rep.n_partial == 1 else 's',
               'it' if rep.n_partial == 1 else 'them'))
    if rep.n_outside:
        rep.warnings.append('%d part%s wholly outside the build volume (not counted)'
                            % (rep.n_outside, '' if rep.n_outside == 1 else 's'))
    if rep.open_parts:
        rep.warnings.append('%d mesh%s not closed: volume is unreliable (%s)'
                            % (len(rep.open_parts), '' if len(rep.open_parts) == 1 else 'es',
                               _names(rep.open_parts)))
    if rep.inside_out_parts:
        rep.warnings.append('%d part%s inside out (normals reversed); volume taken as positive (%s)'
                            % (len(rep.inside_out_parts),
                               '' if len(rep.inside_out_parts) == 1 else 's',
                               _names(rep.inside_out_parts)))
    return rep


def _names(names, n=3):
    s = ', '.join(names[:n])
    return s + (', ...' if len(names) > n else '')
