# SPDX-License-Identifier: GPL-3.0-or-later
"""What a packing run is given and what it gives back (docs/AUTOPACK.md §5).

Plain data: meshes as `meshio.Mesh` (millimetres), matrices as 4 x 4 NumPy
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
    layer_weight: float = 100.0       # mm of height one unit of squared excess is worth
    candidates: int = 3               # coarse positions refined per orientation (8 was no better)
    time_limit: float = None          # seconds (None: no limit)

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


@dataclass
class Placement:
    part: int                         # index into the problem's parts
    copy: int
    matrix: np.ndarray
    orientation: int = 0


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


def principal_frame(verts):
    """Rotation whose rows are the principal axes of the vertices (largest first)."""
    v = np.asarray(verts, float)
    c = v - v.mean(axis=0)
    w, U = np.linalg.eigh(c.T @ c)
    R = U[:, ::-1].T
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
        P = principal_frame(np.asarray(part.mesh.verts) @ base.T)
        return [G @ P @ base for G in cube_group()]
    raise ValueError('unknown rotation mode: %r' % part.mode)
