# SPDX-License-Identifier: GPL-3.0-or-later
"""What a packing run is given and what it gives back (docs/AUTOPACK.md §5).

Plain data: meshes as `meshio.Mesh` (millimeters), matrices as 4 x 4 NumPy
arrays (column vectors, p_world = M @ p)."""

import math
from dataclasses import dataclass, field

import numpy as np

from . import printers

MODES = ('fixed', 'z', 'free')


@dataclass(eq=False)
class Part:
    """A part to place, `quantity` times.  `base` is its linear part as given
    (rotation and scale); the modes turn it further: `fixed` keeps it, `z`
    turns it about the vertical axis, `free` puts it on any of the 24
    axis-aligned orientations of its principal frame."""
    name: str
    mesh: object
    quantity: int = 1
    mode: str = 'fixed'
    tier: int = 1
    base: np.ndarray = field(default_factory=lambda: np.eye(3))
    close_cells: int = None           # None: 2 for an open mesh, else 0


@dataclass
class Settings:
    vlo: np.ndarray = None            # build volume corners, mm (default: the 5600, corner at 0)
    vhi: np.ndarray = None
    gap: float = 5.0                  # between parts, mm
    wall_gap: float = 5.0             # from the side walls (x and y), mm
    h: float = 1.0                    # cell size, mm
    coarse: int = 4                   # cells per coarse cell
    phases: int = 1                   # 1: coarse search at one phase; 2: also half a block over
    z_step: float = 30.0              # degrees, for mode 'z'
    max_density: float = 0.12         # 3D density limit (None: no limit)
    layer_cap: float = 0.5            # soft per-layer density limit
    overshoot_weight: float = 1.0     # greedy: mm of bottom one mm of build height raised is worth
    layer_weight: float = 100.0       # mm of height one unit of squared excess is worth
    candidates: int = 3               # coarse positions refined per orientation (8 was no better)
    time_limit: float = None          # seconds for one greedy run (None: no limit)
    time_budget: float = 60.0         # seconds for a whole job, improvement included (None: until done)
    preset: str = 'overnight'         # weights of the score (pack.score.PRESETS)
    seed: int = 0                     # of the improvement loop's choices
    compact: bool = True              # end with every part moved to the exact gap (pack.compact)
    spread_z: bool = True             # end by lifting parts where that evens out the layers
    flat_rules: bool = True           # avoid large flat faces close together or parallel to the walls
    flat_pair_weight: float = 0.001   # mm of height one mm^2 of facing faces is worth (weighted by closeness)
    flat_wall_weight: float = 0.0004  # mm of height one mm^2 of face parallel to a side wall is worth
    flat_top_weight: float = 0.0      # mm of height one mm^2 of face facing up is worth (PQ-07, meniscus)
    spread_scale: float = 1.0         # times the preset's weight of even layers
    height_far: float = 1.0           # weight of a mm of height far below the top of the volume (PK-01)
    height_top: float = 1.0           # ... and at the top; it rises over the last height_band
    height_band: float = 0.1          # of the volume's height
    near_top_extra: float = 0.0       # time added once (as a share of the time set) when parts are left
                                      # out or the build is close to the top of the volume
    stall_time: float = None          # seconds: stop once no better layout has turned up for this long
    restarts: bool = False            # the improvement loop also starts afresh now and then (Effort: Best)

    def __post_init__(self):
        if self.vlo is None or self.vhi is None:
            size = printers.PRINTERS[printers.DEFAULT_PRINTER]
            self.vlo = np.zeros(3)
            self.vhi = np.array(size, float)
        self.vlo = np.asarray(self.vlo, float)
        self.vhi = np.asarray(self.vhi, float)

    @property
    def area(self):
        return float((self.vhi[0] - self.vlo[0]) * (self.vhi[1] - self.vlo[1]))


def dial_settings(quality):
    """Settings for a place on the dial between packing density (0) and
    part quality (1) (docs/PRINT_QUALITY.md, "The quality / efficiency
    dial"): the soft rules scale with it, the hard ones (gaps, the density
    limit) do not.  The final lift stays on (it is how the density limit is
    met as low as possible); only Keep Parts Low turns it off.  Height counts less far below the top of the volume the
    more quality counts; near the top it always counts three times."""
    q = min(1.0, max(0.0, float(quality)))
    on = q > 0.05
    return dict(flat_rules=on,
                flat_pair_weight=0.002 * q,
                flat_wall_weight=0.0008 * q,
                flat_top_weight=0.0016 * max(0.0, q - 0.5),
                spread_scale=2.0 * q,
                spread_z=True,                  # (also how the density limit is met: never off here)
                height_far=1.0 - 0.8 * q,
                height_top=3.0,
                height_band=0.1,
                near_top_extra=1.0)


@dataclass
class Placement:
    part: int                         # index into the problem's parts
    copy: int
    matrix: np.ndarray
    orientation: int = 0
    cell: tuple = None                # fine cell offset in the packer's grid (None: not from it)


@dataclass
class Result:
    placements: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)      # (part, copy, reason)
    seconds: float = 0.0
    volume: float = 0.0
    height: float = 0.0
    density: float = None
    log: list = field(default_factory=list)

    def items(self, parts):
        """(name, mesh, matrix) of every placement, for metrics and the referee."""
        return [('%s#%d' % (parts[p.part].name, p.copy + 1), parts[p.part].mesh, p.matrix)
                for p in self.placements]


# ------------------------------------------------------------------ orientations

def rot_z(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def cube_group():
    """The 24 rotations that map the coordinate axes onto themselves."""
    out = []
    for perm in ((0, 1, 2), (1, 2, 0), (2, 0, 1), (1, 0, 2), (0, 2, 1), (2, 1, 0)):
        for signs in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1),
                      (-1, 1, 1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1)):
            R = np.zeros((3, 3))
            for i, (j, s) in enumerate(zip(perm, signs)):
                R[i, j] = s
            if np.linalg.det(R) > 0:
                out.append(R)
    return out


def _canonical_sign(c, e):
    """e or -e, chosen from the shape (third moment, then the largest
    component), so that every platform picks the same."""
    m3 = float(((c @ e) ** 3).sum())
    scale = float((np.abs(c @ e) ** 3).sum()) or 1.0
    if abs(m3) > 1e-9 * scale:
        return e if m3 > 0 else -e
    j = int(np.argmax(np.abs(e) + np.arange(3) * 1e-12))
    return e if e[j] > 0 else -e


def _hull2d(P):
    """Convex hull of 2-D points, counter-clockwise (monotone chain)."""
    P = np.unique(np.round(np.asarray(P, float), 9), axis=0)
    if len(P) < 3:
        return P

    def half(pts):
        out = []
        for p in pts:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out
    lo = half(P)
    hi = half(P[::-1])
    return np.array(lo[:-1] + hi[:-1])


def principal_frame(verts, tol=1e-6):
    """Rotation whose rows are the principal axes of the vertices (largest
    first), made definite where the moments leave it open.

    Two equal moments (a disc, a saddle like the pringle) leave the axes in
    their plane arbitrary, and LAPACK picks them differently on different
    machines: the same packing came out 66 mm high on one and 109 mm on
    another.  So: in such a plane the first axis is the one of the smallest
    bounding rectangle (a square part square to the axes), or for a round
    outline where the shape bends most toward the third axis (the
    eigenvector of sum (v.n) v v^T in the plane: a saddle's rising
    direction), else the given x axis projected into the plane; signs by
    the third moment.  Three equal moments (a cube, a sphere) keep the
    given axes."""
    v = np.asarray(verts, float)
    c = v - v.mean(axis=0)
    w, U = np.linalg.eigh(c.T @ c)
    w, U = w[::-1], U[:, ::-1]                      # largest first
    big = max(float(w[0]), 1e-300)
    same01 = (w[0] - w[1]) <= tol * big
    same12 = (w[1] - w[2]) <= tol * big
    if same01 and same12:
        return np.eye(3)
    if same01 or same12:
        # the lone axis n and an orthonormal basis (a, b) of the degenerate plane
        n = U[:, 2] if same01 else U[:, 0]
        n = _canonical_sign(c, n)
        a = np.cross(n, [1.0, 0.0, 0.0])
        if np.linalg.norm(a) < 1e-6:
            a = np.cross(n, [0.0, 1.0, 0.0])
        a /= np.linalg.norm(a)
        b = np.cross(n, a)
        P = c @ np.stack([a, b], 1)                 # coordinates in the plane
        d = None
        # the turn in the plane with the smallest bounding rectangle (what
        # boxes and grids want: a square part square to the axes) ...
        H = _hull2d(P[::max(1, len(P) // 20000)])

        def rect(th):
            D = np.stack([np.cos(th), np.sin(th)])
            u = H @ D
            w2 = H @ np.stack([-np.sin(th), np.cos(th)])
            return D, u.max(0) - u.min(0), w2.max(0) - w2.min(0)
        _, lu, lw = rect(np.radians(np.arange(0.0, 90.0, 1.0)))
        area = lu * lw
        if area.max() - area.min() > 0.05 * area.max():         # (a polygon of many sides is round)
            # one side of the smallest rectangle lies along an edge of the hull
            e = np.roll(H, -1, axis=0) - H
            th = np.unique(np.round(np.mod(np.arctan2(e[:, 1], e[:, 0]), np.pi / 2), 12))
            D, lu, lw = rect(th)
            area = lu * lw
            j = int(np.argmin(area + np.arange(len(th)) * 1e-12 * area.max()))
            d = D[:, j]
            if lw[j] > lu[j] * (1 + 1e-9):
                d = np.array([-d[1], d[0]])          # (the longer side first)
        else:
            # ... and for a round outline, where it bends most toward the
            # third axis (a saddle's rising direction)
            wt = c @ n
            q, V = np.linalg.eigh((P * wt[:, None]).T @ P)
            scale = float((np.abs(wt) * (P * P).sum(1)).sum())
            if q[1] - q[0] > 1e-6 * scale:
                d = V[:, 1]
        if d is None:
            d = np.array([1.0, 0.0])
        e = a * d[0] + b * d[1]
        e = _canonical_sign(c, e)
        f = np.cross(n, e)
        R = np.stack([e, f, n]) if same01 else np.stack([n, e, f])
    else:
        R = np.stack([_canonical_sign(c, U[:, j]) for j in range(3)])
    if np.linalg.det(R) < 0:
        R[2] *= -1
    return R


def group_key(L, mode):
    """What two copies of one mesh must share to be one Part (one set of
    orientations to try).  Free rotation tries every axis-aligned turn of the
    mesh's principal frame, which does not depend on how a copy happens to be
    turned: copies that differ only by a rotation (and a uniform scale, the
    same) are one part.  Otherwise the linear part itself."""
    L = np.asarray(L, float)
    if mode == 'free':
        sv = np.linalg.svd(L, compute_uv=False)
        # (files keep a few decimals: a scale of 2 comes back as 1.9998 .. 2.0002)
        if sv[0] - sv[-1] <= 1e-3 * max(sv[0], 1e-12) and np.linalg.det(L) > 0:
            return ('free', round(float(np.exp(np.log(sv).mean())), 3))
    return (mode, np.round(L, 9).tobytes())


def orientations(part, z_step=30.0):
    """The linear parts to try for a part, as 3 x 3 arrays."""
    base = np.asarray(part.base, float)
    if part.mode == 'fixed':
        return [base]
    if part.mode == 'z':
        n = max(1, int(round(360.0 / z_step)))
        return [rot_z(k * 360.0 / n) @ base for k in range(n)]
    if part.mode == 'free':
        V = np.asarray(part.mesh.verts, float) @ base.T
        P = principal_frame(V)
        out = [G @ P @ base for G in cube_group()]
        # in an order that depends on the shape only, so that ties go the
        # same way everywhere: lowest first, then by extent and center, then
        # by the posed vertices themselves
        V0 = np.asarray(part.mesh.verts, float)

        def key(A):
            W = V0 @ A.T
            lo, hi = W.min(0), W.max(0)
            cen = W.mean(0) - lo
            k = tuple(np.round(np.concatenate([(hi - lo)[::-1], cen[::-1]]), 6))
            if len(W) <= 200_000:
                n = np.rint((W - lo) * 1000.0).astype(np.int64)      # (micrometres, >= 0)
                k += (n[np.lexsort(n.T[::-1])].astype('>i8').tobytes(),)   # (bytes in numeric order)
            return k
        return sorted(out, key=key)
    raise ValueError('unknown rotation mode: %r' % part.mode)
