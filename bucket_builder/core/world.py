# SPDX-License-Identifier: GPL-3.0-or-later
"""The collision world: every monitored object, its cached acceleration data
and the incrementally maintained results.

Nothing in here imports Blender; it is plain NumPy so it can be tested alone.

What is kept, from most to least expensive to rebuild
    Geom   one unique mesh: its vertices and its triangles in tree order
           (shared by all copies of a part)
    Pose   that mesh with one rotation/scale applied: posed vertices and the
           world-axis-aligned boxes of every tree node (shared by copies that
           have the same orientation)
    object a mesh, a rotation/scale and a translation

Moving an object only changes its translation, so nothing is rebuilt.  Only a
real geometry change rebuilds the triangle order.

Rotating or scaling an object needs boxes for the new orientation.  Fitting
them costs about 45 ns per triangle, too long to do on every mouse step for a
large mesh, so the object *borrows*: it keeps using the pose it had, whose
boxes are moved into the new orientation as they are looked at.  Such boxes
are looser (queries cost roughly twice as much) but nothing has to be
recomputed.  Once things are quiet the object gets a pose of its own again,
fitted in pieces of a millisecond or two, and its results are refreshed.

Poses are the bulk of the memory (about 55 bytes per triangle each) and they
are a cache: one is built when a pair involving the object is first solved,
never for a part that has no neighbours, and the least recently used ones
are dropped when ``cache_limit`` is reached.  A live edit never waits for a
fit if there is any pose of the mesh to borrow.  Exact results, on the other
hand, are always worked out with fitted poses: proving a clearance with the
looser boxes of a borrowed one can take ten times as long as fitting.  The
extents of an object do not need its pose.

A mesh can be put in before it is sorted (``add_raw``): its objects have their
extents at once, so what is far from everything else, and what is inside the
build volume, is known without waiting.  Only the pairs that involve such a
mesh wait, until ``set_sorted`` delivers its triangles in tree order.

A mesh that no object uses any more can be kept as long as there is room
(``keep_unused``): hiding a part and showing it again then costs nothing.

Results are kept per object pair and are only recomputed for pairs that involve
an object that changed.
"""

import time
import numpy as np

from .arena import Arena, grow_rows
from . import bvh, narrow

PENDING, OK, CLEAR, COLLIDE = -1, 0, 1, 2
INSIDE, PARTIAL, OUTSIDE = 0, 1, 2

MAX_BATCH = 96
OOB_TRI_CAP = 60000
LIVE_TARGET = 0.012       # seconds a live solve should take
LIVE_FULL = 0.024         # ... and may take when that gives every pair its full detail
LIVE_FULL_PAIRS = 8       # ... which is only promised for this many pairs
REGION_BUDGET = 400000    # triangles of hatched regions kept for the whole scene
VERT_CHUNK = 1 << 16      # vertices posed in one piece
FIT_BLOCK = 15            # log2 of the triangles whose boxes are fitted in one piece
FIT_NOW = 40000           # meshes up to this many triangles are re-fitted at once
BORROW_MAX = 64.0         # largest stretch between a borrowed pose and its user
MAX_WAIT = 2              # poses still to be fitted that one batch of pairs may wait for
# Two parts count as not having moved against each other while their offset
# stays within this fraction of their distance from the origin.  Positions
# arrive as single precision numbers: when several parts are moved together,
# each is rounded on its own and their offsets jitter by a few units in the
# last place (1.2e-7 of the coordinate at most).  Without the slack every
# pair among them would be solved again on every step of the move.
POS_TOL = 1e-6


class Geom:
    """One unique mesh.  ``ready``: its triangles are in tree order (until then
    only its vertices are of use).  ``users``: the slots of its objects."""
    __slots__ = ('key', 'nv', 'nt', 'lbase', 'tbase', 'H', 'nreal', 'npad', 'off',
                 'rows', 'refs', 'ext', 'poses', 'rad', 'ready', 'users')


class Pose:
    __slots__ = ('index', 'geom', 'key', 'L', 'base', 'rows', 'vbase', 'bbase', 'slots',
                 'used', 'unfit', 'job')


class _VertView:
    """The pose storage seen as rows of three floats.  A pose keeps its posed
    vertices in front of its boxes in the same block, so that there is one
    array to grow, to limit and to keep compact."""

    def __init__(self, arena):
        self._arena = arena

    @property
    def data(self):
        return self._arena.data.reshape(-1, 3)


class PairResult:
    """Outcome for one pair of objects.

    ``segs``, ``pa``, ``pb`` and ``center`` are in the frame of the pair's
    first object (add that object's translation to get world space).  The
    surface patches ``tri_a`` / ``tri_b`` are each in the frame of their own
    object, so a patch keeps sticking to its part while the result is waiting
    to be recomputed.  ``clip_a`` / ``clip_b`` are None, or a box (in the
    first object's frame) the patch should be trimmed to when drawn."""
    __slots__ = ('state', 'dist', 'pa', 'pb', 'segs', 'nseg', 'tri_a', 'tri_b', 'clip_a',
                 'clip_b', 'approx', 'refine', 'lite', 'loose', 'unproven', 'no_cut', 'enclosed',
                 'center', 'radius', 'stale', 'stamp', 'serial')

    def __init__(self):
        self.state = PENDING
        self.dist = float('inf')
        self.pa = self.pb = None
        self.segs = None
        self.nseg = 0
        self.tri_a = self.tri_b = None
        self.clip_a = self.clip_b = None
        self.approx = False
        self.refine = False
        self.lite = False         # only sketched: the state is known, the details are not
        self.loose = False        # solved while a part was borrowing a pose
        self.unproven = False     # no intersection found, but the search was cut short
        self.no_cut = False       # ... and the exhaustive one has since found none either
        self.enclosed = 0         # 1: first part inside the second, 2: the reverse
        self.center = None
        self.radius = 0.0
        self.stale = True
        self.stamp = None
        self.serial = 0


class OobResult:
    """Build-volume status of one object (geometry in world space)."""
    __slots__ = ('state', 'tris', 'lo', 'hi', 'band_only', 'serial')


class WallResult:
    """A part inside the build volume but closer to a side wall than allowed
    (geometry in world space; ``dist`` is the gap to the nearest side wall)."""
    __slots__ = ('dist', 'tris', 'lo', 'hi', 'band_only', 'center', 'radius', 'serial',
                 'axis', 'high')


class World:
    def __init__(self):
        # pose storage: node boxes (lo xyz, hi xyz) and, through VERT, posed vertices
        self.BOX = Arena(6, np.float32, 1 << 14, align=bvh.PAD)
        self.VERT = _VertView(self.BOX)
        self.TIDX = Arena(3, np.int32, 1 << 13)      # sorted triangles (vertex ids)
        self.LVERT = Arena(3, np.float32, 1 << 13)   # vertices in mesh coordinates

        n = 16
        self.LVL = np.zeros((n, bvh.MAX_LEVELS), dtype=np.int64)   # row of each level
        self.SZ = np.zeros((n, bvh.MAX_LEVELS), dtype=np.float32)  # typical box size
        self.PH = np.zeros(n, dtype=np.int64)      # height of the root
        self.PTB = np.zeros(n, dtype=np.int64)     # first row in TIDX
        self.PVB = np.zeros(n, dtype=np.int64)     # first row in VERT
        self.PNT = np.zeros(n, dtype=np.int64)     # triangle count
        self._pose_slots = []
        self._pose_free = []
        self._poses = {}
        self._geoms = {}
        self._idle = {}           # keys of meshes no object uses, least recently used first
        # Whether a sorted mesh is kept when its last object goes (see
        # ``_geom_unused``).  Whoever switches this on must be sure that a
        # key still stands for the same geometry when the mesh is used again.
        self.keep_unused = False
        self.cache_limit = None   # bytes the cached data may take (None: no limit)
        self.pose_bytes = 0       # bytes of the poses that exist right now
        self.geom_bytes = 0       # bytes of the unique meshes
        self.evictions = 0        # poses dropped to stay within the limit
        self.flushes = 0          # times every pose had to go (fragmented storage)
        self.fit_now = FIT_NOW    # meshes up to this size never borrow a pose
        self._clock = 0           # advances with every use of poses (for eviction)
        self._demand = 0          # rows the poses of all objects would take together
        # What is on its way but not here yet (meshes being sorted): triangle
        # rows, vertex rows, pose rows.  The shared arrays are then sized for
        # it in one go instead of being grown and copied as each one arrives.
        self.incoming = (0, 0, 0)

        m = 64
        self.O_T = np.zeros((m, 3), dtype=np.float64)
        self.O_LO = np.zeros((m, 3), dtype=np.float64)
        self.O_HI = np.zeros((m, 3), dtype=np.float64)
        self.O_POSE = np.full(m, -1, dtype=np.int64)
        self.O_ALIVE = np.zeros(m, dtype=bool)
        self.O_VER = np.zeros(m, dtype=np.int64)
        # for objects that borrow a pose fitted for another rotation/scale
        self.O_VIRT = np.zeros(m, dtype=bool)
        self.O_REL = np.zeros((m, 3, 3), dtype=np.float32)    # pose frame -> object frame
        self.O_RELI = np.zeros((m, 3, 3), dtype=np.float64)   # and back
        self.O_PAD = np.zeros(m, dtype=np.float32)            # slack for rounding
        self.O_L = np.zeros((m, 3, 3), dtype=np.float32)      # rotation/scale of the object
        self.O_LB = np.zeros(m, dtype=np.int64)               # its mesh's first row in LVERT
        self._obj = []            # slot -> [uid, Geom, L (3x3 float32), Pose or None] or None
        self._slot_of = {}
        self._slot_free = []
        self._virt_todo = set()   # borrowing slots whose results are still to be redone
        self._borrowing = set()   # the other borrowing slots

        self.coll_thr = 0.0
        self.clear_thr = 0.0      # 0 disables the clearance check
        self.detect_enclosed = True   # report parts completely inside other parts
        self.volume = None        # (lo, hi) in world space
        self.wall_margin = 0.0    # wanted distance to the side walls (X and Y), 0 = off
        self.set_scale(100.0, 0.5)

        self.pairs = {}           # (slot_a, slot_b), a < b -> PairResult
        self.adj = {}             # slot -> set of slots it has a pair with
        self.viol = {}            # pairs currently in CLEAR / COLLIDE state
        self.oob = {}             # slot -> OobResult (only PARTIAL / OUTSIDE)
        self.wall = {}            # slot -> WallResult (inside, but too close to a side wall)
        self._dirty = {}          # slot -> hot flag
        self._pend_hot = {}       # pairs touched by a live edit: solved first, quickly
        self._pend_cold = {}      # background work: solved exactly
        self._pend_refine = {}    # quick results waiting for their exact pass
        self._pend_scan = {}      # pairs whose search for an intersection was cut short -> state of
        #                           the exhaustive one (None: not begun), see narrow.scan
        self.scans = 0            # how many of those were needed, and how many found a collision
        self.scan_hits = 0
        self._pend_wait = {}      # pairs waiting for a mesh to be sorted -> came from the hot queue
        self._oob_dirty = set()
        self._oob_all = False
        self._oob_geom = {}       # slots whose out-of-volume geometry is still to be collected
        self._oob_wait = {}       # ... and those that wait for their mesh to be sorted for it
        self._param_stamp = 0
        self.version = 0          # bumps whenever any result changes
        self._serial = 0
        # Load control, following the measured cost.  A live solve that only
        # sketches its n pairs takes about _lite_c0 + n * _lite_c1 seconds,
        # and one that treats them fully _detail_cost each (see
        # ``_choose_detail``).  ``live_detail`` overrides the choice with a
        # fixed number of fully treated pairs.  ``_bg_batch`` is how many
        # pairs a background solve takes at once.
        self.live_detail = None
        self._lite_c0 = 0.003
        self._lite_c1 = 0.0005
        self._detail_cost = 0.0
        self._bg_batch = 2
        self.tri_cap = narrow.TRI_CAP     # region triangles per part and pair
        self.last_step_ms = 0.0
        self.last_pairs = 0
        self.proofs_repeated = 0  # clearance searches redone with large caps (see narrow)

    # ------------------------------------------------------------------ setup
    def set_scale(self, scene_size, viz_pad):
        """Tolerances follow the size of the scene so behaviour is the same
        whether one unit is a millimetre or a metre."""
        s = max(float(scene_size), 1e-9)
        self.scene_size = s
        self.eps_len = 1e-6 * s       # boxes closer than this count as touching
        self.eps_touch = 2e-6 * s     # surfaces closer than this are in contact
        self.eps_exact = 1e-9 * s
        self.viz_pad = float(viz_pad)

    def set_thresholds(self, coll, clear):
        coll = max(0.0, float(coll))
        clear = max(0.0, float(clear))
        if coll == self.coll_thr and clear == self.clear_thr:
            return
        self.coll_thr = coll
        self.clear_thr = clear
        self.invalidate_all()

    def set_detect_enclosed(self, flag):
        flag = bool(flag)
        if flag != self.detect_enclosed:
            self.detect_enclosed = flag
            self.invalidate_all()

    def set_volume(self, lo, hi, wall_margin=0.0):
        """Build volume, and optionally the distance parts should keep from
        its side walls (X and Y; the top and bottom are not included)."""
        if lo is None:
            new = None
        else:
            new = (np.array(lo, dtype=np.float64), np.array(hi, dtype=np.float64))
        margin = max(0.0, float(wall_margin)) if new is not None else 0.0
        old = self.volume
        if margin == self.wall_margin and (new is None) == (old is None) and (
                new is None or (np.array_equal(new[0], old[0]) and np.array_equal(new[1], old[1]))):
            return
        self.volume = new
        self.wall_margin = margin
        self._oob_all = True

    def inner_box(self):
        """The build volume shrunk by the wall margin in X and Y, or None."""
        if self.volume is None or self.wall_margin <= 0.0:
            return None
        lo, hi = self.volume
        m = np.array([self.wall_margin, self.wall_margin, 0.0])
        mid = 0.5 * (lo + hi)
        return np.minimum(lo + m, mid), np.maximum(hi - m, mid)

    def invalidate_all(self):
        self._param_stamp += 1
        for slot in self._slot_of.values():
            self._dirty.setdefault(slot, False)

    def _dmax(self):
        return max(self.coll_thr, self.clear_thr, self.eps_touch)

    # --------------------------------------------------------------- geometry
    def has_geom(self, key):
        return key in self._geoms

    def add_geom(self, key, verts, tris):
        """Register a triangle mesh (local coordinates) under ``key``."""
        g = self._geoms.get(key)
        if g is None:
            return self.add_sorted(key, *bvh.sort_mesh(verts, tris))
        if not g.ready:
            self.set_sorted(key, bvh.order_mesh(*self.geom_views(key)))
        return g

    def add_sorted(self, key, verts, tris):
        """Register a mesh that ``bvh.sort_mesh`` has prepared.  If it is
        there already but unsorted, this sorts it."""
        g = self._geoms.get(key)
        if g is not None:
            if not g.ready:
                self.set_sorted(key, tris)
            return g
        return self._geom_new(key, verts, tris, True)

    def add_raw(self, key, verts, tris):
        """Register a mesh whose triangles are not in tree order yet, as
        ``bvh.clean_mesh`` returns it.  Objects can use it at once; what
        needs its tree waits for ``set_sorted``."""
        g = self._geoms.get(key)
        if g is not None:
            return g
        return self._geom_new(key, verts, tris, False)

    def _geom_new(self, key, verts, tris, ready):
        nt = tris.shape[0]
        g = Geom()
        g.key = key
        g.nv = verts.shape[0]
        g.nt = nt
        g.H, g.nreal, g.npad, g.off, g.rows = bvh.level_layout(nt)
        self._make_way(self._geom_size(g))
        self.TIDX.want = self.TIDX.top + max(g.npad[0], self.incoming[0])
        self.LVERT.want = self.LVERT.top + max(g.nv, self.incoming[1])
        g.tbase = self.TIDX.alloc(g.npad[0])
        t = self.TIDX.data[g.tbase:g.tbase + g.npad[0]]
        t[:nt] = tris
        t[nt:] = 0
        g.lbase = self.LVERT.alloc(g.nv)
        self.LVERT.data[g.lbase:g.lbase + g.nv] = verts
        g.rad = float(np.abs(verts).max()) if g.nv else 0.0
        g.refs = 0                # objects using this mesh
        g.users = set()           # ... and their slots
        g.ext = {}                # rotation/scale bytes -> extents
        g.poses = []
        g.ready = bool(ready)
        self._geoms[key] = g
        self.geom_bytes += self._geom_size(g)
        return g

    def geom_ready(self, key):
        """True if the mesh is there and sorted."""
        g = self._geoms.get(key)
        return g is not None and g.ready

    def geom_views(self, key):
        """(vertices, triangles) of a mesh as views of the shared storage, for
        handing an unsorted mesh to ``bvh.order_mesh``.  They stay valid for
        reading (the storage is replaced, never rewritten, when it grows),
        but only describe the mesh for as long as it is registered."""
        g = self._geoms[key]
        return (self.LVERT.data[g.lbase:g.lbase + g.nv],
                self.TIDX.data[g.tbase:g.tbase + g.nt])

    def set_sorted(self, key, tris):
        """The triangles of an unsorted mesh in tree order (what
        ``bvh.order_mesh`` made of ``geom_views``).  Everything that was
        waiting for the mesh is taken up again.  Returns False if there is
        no such mesh waiting."""
        g = self._geoms.get(key)
        if g is None or g.ready:
            return False
        if tris.shape[0] != g.nt:
            raise ValueError('not the triangles of this mesh')
        self.TIDX.data[g.tbase:g.tbase + g.nt] = tris
        g.ready = True
        obj = self._obj
        wait = self._pend_wait
        for slot in g.users:
            if slot in self._oob_wait:
                del self._oob_wait[slot]
                self._oob_geom[slot] = None
            if not wait:
                continue
            for o in self.adj[slot]:
                pair = (slot, o) if slot < o else (o, slot)
                hot = wait.get(pair)
                if hot is not None and obj[o][1].ready:
                    del wait[pair]
                    (self._pend_hot if hot else self._pend_cold)[pair] = None
        return True

    def wanted(self):
        """The unsorted meshes that results are waiting for: key -> how many
        pairs and out-of-volume pictures each holds up."""
        out = {}
        obj = self._obj
        for pair in self._pend_wait:
            for slot in pair:
                g = obj[slot][1]
                if not g.ready:
                    out[g.key] = out.get(g.key, 0) + 1
        for slot in self._oob_wait:
            g = obj[slot][1]
            out[g.key] = out.get(g.key, 0) + 1
        return out

    @staticmethod
    def _geom_size(g):
        return g.npad[0] * 12 + g.nv * 12

    @staticmethod
    def geom_size(nv, nt):
        """Bytes a mesh with ``nv`` vertices and ``nt`` triangles takes."""
        return (-(-int(nt) // bvh.PAD) * bvh.PAD + int(nv)) * 12

    def _geom_drop(self, g):
        self.TIDX.release(g.tbase, g.npad[0])
        self.LVERT.release(g.lbase, g.nv)
        self._idle.pop(g.key, None)
        if self._geoms.pop(g.key, None) is not None:
            self.geom_bytes -= self._geom_size(g)

    def _geom_unused(self, g):
        """The last object using a mesh is gone.  A sorted mesh is kept for
        as long as there is room, in case the part comes back (hidden and
        shown again, say); sorting is what takes the time."""
        if g.ready and self.keep_unused:
            self._idle[g.key] = None
            self._trim_idle()
        else:
            self._geom_drop(g)

    def _trim_idle(self, need=0):
        """Drop meshes that no object uses, the longest unused first, until
        ``need`` more bytes fit the limit (counting the pose storage as it
        is, used or not: giving that back means copying it)."""
        limit = self.cache_limit
        if limit is None:
            return
        while self._idle and (self.geom_bytes + max(self.pose_bytes, self.BOX.nbytes)
                              + need > limit):
            self._geom_drop(self._geoms[next(iter(self._idle))])

    def _make_way(self, need):
        """Room for ``need`` more bytes of mesh data within the limit.  Meshes
        nobody uses go first, then poses, the least recently used first (they
        are fitted again when they are needed).  A mesh in use never goes: if
        those alone are more than the limit, the limit gives way."""
        limit = self.cache_limit
        if limit is None:
            return
        self._trim_idle(need)
        fixed = self.geom_bytes + need
        if fixed + max(self.pose_bytes, self.BOX.nbytes) <= limit:
            return
        for p in sorted(self._poses.values(), key=lambda q: q.used):
            if fixed + self.pose_bytes <= limit:
                break
            self._pose_drop(p)
            self.evictions += 1
        # the storage itself: hand back its unused end, or start afresh
        self.BOX.shrink(max(self.BOX.top, 1 << 14))
        if self._poses and fixed + self.BOX.nbytes > limit:
            self.evictions += len(self._poses)
            self.flushes += 1
            self.flush_poses()

    def drop_unused_geoms(self):
        for g in [g for g in self._geoms.values() if g.refs == 0]:
            self._geom_drop(g)

    # ------------------------------------------------------------------ poses
    @staticmethod
    def _pose_rows(nv, box_rows):
        """(rows the posed vertices take, rows of the whole block).  Blocks
        come in size classes an eighth of a power of two apart, so that the
        block a dropped pose leaves behind fits the pose of any mesh of about
        that size, not only of the very same one."""
        vrows = -(-((int(nv) + 1) // 2) // bvh.PAD) * bvh.PAD
        rows = vrows + int(box_rows)
        step = max(bvh.PAD, 1 << max(rows.bit_length() - 4, 0))
        return vrows, -(-rows // step) * step

    @staticmethod
    def pose_size(nv, nt):
        """Bytes one pose of a mesh with ``nv`` vertices and ``nt`` triangles takes."""
        return World._pose_rows(nv, bvh.level_layout(nt)[4])[1] * 24

    @staticmethod
    def rows_for(nv, nt):
        """(triangle rows, vertex rows, pose rows) a mesh of this size takes,
        for ``incoming``."""
        return (-(-int(nt) // bvh.PAD) * bvh.PAD, int(nv),
                World._pose_rows(nv, bvh.level_layout(nt)[4])[1])

    def pose_room(self):
        """Bytes available for poses, or None when there is no limit."""
        if self.cache_limit is None:
            return None
        return max(0, self.cache_limit - self.geom_bytes)

    def _posed(self, g, L32, s, e):
        """Vertices ``s`` to ``e`` of a mesh under a rotation/scale, as three
        contiguous float32 columns.  Every posed coordinate in the engine comes
        from this one expression (``narrow`` repeats it for borrowed poses),
        so the extents of an object, the boxes of its pose and its triangles
        agree to the last bit."""
        V = self.LVERT.data
        b = g.lbase
        x = np.ascontiguousarray(V[b + s:b + e, 0])
        y = np.ascontiguousarray(V[b + s:b + e, 1])
        z = np.ascontiguousarray(V[b + s:b + e, 2])
        out = []
        for c in range(3):
            t = x * L32[c, 0]
            t += y * L32[c, 1]
            t += z * L32[c, 2]
            out.append(t)
        return out

    def _extents(self, g, L32):
        """Exact extents of a mesh under a rotation/scale, without its pose."""
        key = L32.tobytes()
        ext = g.ext.get(key)
        if ext is None:
            lo = np.full(3, np.inf)
            hi = np.full(3, -np.inf)
            for s in range(0, g.nv, VERT_CHUNK):
                cols = self._posed(g, L32, s, min(g.nv, s + VERT_CHUNK))
                for c in range(3):
                    lo[c] = min(lo[c], float(cols[c].min()))
                    hi[c] = max(hi[c], float(cols[c].max()))
            ext = (lo, hi)
            if len(g.ext) > 4096:
                g.ext.clear()
            g.ext[key] = ext
        return ext

    def _room_for(self, g):
        """True if one more pose of ``g`` fits without dropping anything."""
        room = self.pose_room()
        if room is None:
            return True
        rows = self._pose_rows(g.nv, g.rows)[1]
        if self.pose_bytes + rows * 24 > room:
            return False
        # ... and if the storage has to grow for it, that must fit too
        grow = self.BOX.growth(rows)
        return grow == 0 or self.BOX.nbytes + grow * 24 <= room

    def _make_room(self, g):
        """Drop least recently used poses until one more of ``g`` fits the limit.

        Poses in use by the work at hand (same clock) are kept, unless the
        storage is so fragmented that the new one fits nowhere: then every
        pose is dropped (which leaves the storage empty, hence compact) and the
        caller builds the ones it needs again."""
        if self._room_for(g):
            return
        if self._idle:
            # meshes nobody uses go before any pose does
            while self._idle and not self._room_for(g):
                self._geom_drop(self._geoms[next(iter(self._idle))])
            if self._room_for(g):
                return
        # least recently used first; a pose that others borrow, or the only
        # one of its mesh, is worth more than its age says
        virt = self.O_VIRT
        clock = self._clock

        def rank(q):
            lent = len(q.geom.poses) == 1 or any(virt[s] for s in q.slots)
            return (lent, q.used)

        for p in sorted((q for q in self._poses.values() if q.used != clock), key=rank):
            self._pose_drop(p)
            self.evictions += 1
            if self._room_for(g):
                return
        need = self._pose_rows(g.nv, g.rows)[1] * 24
        if self._poses and self.pose_bytes + need <= self.pose_room():
            for p in list(self._poses.values()):
                self._pose_drop(p)
                self.evictions += 1
            self.flushes += 1

    def _pose_new(self, g, L32, key):
        """Storage for a pose of ``g`` under ``L32``; it is fitted later."""
        self._make_room(g)
        p = Pose()
        p.geom = g
        p.key = key
        p.L = L32.copy()
        p.slots = set()
        p.used = self._clock
        p.unfit = True
        p.job = None
        if self._pose_free:
            p.index = self._pose_free.pop()
            self._pose_slots[p.index] = p
        else:
            p.index = len(self._pose_slots)
            self._pose_slots.append(p)
            need = p.index + 1
            self.LVL = grow_rows(self.LVL, need, 0)
            self.SZ = grow_rows(self.SZ, need, 0)
            self.PH = grow_rows(self.PH, need, 0)
            self.PTB = grow_rows(self.PTB, need, 0)
            self.PVB = grow_rows(self.PVB, need, 0)
            self.PNT = grow_rows(self.PNT, need, 0)
        vrows, p.rows = self._pose_rows(g.nv, g.rows)
        room = self.pose_room()
        # When the storage has to grow it goes straight to what the scene is
        # going to need (the memory is only really taken as it is written
        # to), but never beyond the limit.
        want = self._demand + self.incoming[2]
        self.BOX.want = want + (want >> 3) + 4096
        p.base = self.BOX.alloc(p.rows, None if room is None else room - self.BOX.nbytes)
        p.vbase = 2 * p.base
        p.bbase = p.base + vrows
        # valid tables from the start, so that a pose under construction can
        # be addressed (it is not used before it is fitted)
        i = p.index
        self.LVL[i, :g.H + 1] = p.bbase + g.off
        self.PH[i] = g.H
        self.PTB[i] = g.tbase
        self.PVB[i] = p.vbase
        self.PNT[i] = g.nt
        self._poses[key] = p
        g.poses.append(p)
        self.pose_bytes += p.rows * 24
        return p

    def _pose_drop(self, p):
        """Free a pose.  Objects that were using it get one again when they
        next need it."""
        for slot in p.slots:
            self._obj[slot][3] = None
            self.O_POSE[slot] = -1
            self._unborrow(slot)
        p.slots.clear()
        p.job = None
        p.geom.poses.remove(p)
        del self._poses[p.key]
        self.BOX.release(p.base, p.rows)
        self._pose_slots[p.index] = None
        self._pose_free.append(p.index)
        self.pose_bytes -= p.rows * 24

    def _rekey(self, p, L32, key):
        """Reuse the storage of a pose for another rotation/scale of its mesh."""
        del self._poses[p.key]
        p.key = key
        p.L = L32.copy()
        p.unfit = True
        p.job = None
        self._poses[key] = p

    # -- borrowing ----------------------------------------------------------
    def _rel_to(self, p, g, L32):
        """How to move the boxes of pose ``p`` into the orientation ``L32``:
        (matrix, its inverse, slack for rounding), or None if that is not
        possible or would stretch them too far."""
        PL = p.L.astype(np.float64)
        L = L32.astype(np.float64)
        try:
            rel = L @ np.linalg.inv(PL)
            reli = PL @ np.linalg.inv(L)
        except np.linalg.LinAlgError:
            return None
        n1 = float(np.abs(rel).sum(axis=1).max())
        n2 = float(np.abs(reli).sum(axis=1).max())
        if not (np.isfinite(n1) and np.isfinite(n2)) or max(n1, n2) > BORROW_MAX:
            return None
        # posed coordinates are rounded to float32 (a few parts in 1e7 of
        # their size) in the pose, in the object and in the box transform
        pad = 2e-6 * g.rad * (float(np.abs(L).sum(axis=1).max())
                              + n1 * float(np.abs(PL).sum(axis=1).max()))
        return rel.astype(np.float32), reli, np.float32(pad)

    def _base_for(self, g, L32):
        """A pose of ``g`` the orientation ``L32`` can borrow, with the
        transform: (pose, rel, reli, pad), or None."""
        best = None
        best_cost = None
        poses = g.poses
        if len(poses) > 6:
            poses = sorted(poses, key=lambda q: q.used)[-6:]
        for p in poses:
            x = self._rel_to(p, g, L32)
            if x is None:
                continue
            # the less the boxes are turned, the less they grow; a fitted pose
            # is better than one that still has to be
            a = np.abs(x[0])
            cost = float(a.sum()) / max(float(a.max(axis=1).sum()), 1e-30)
            cost += 10.0 if p.unfit else 0.0
            if best is None or cost < best_cost:
                best = (p,) + x
                best_cost = cost
        return best

    def _borrow(self, slot, rel, reli, pad):
        self.O_VIRT[slot] = True
        self.O_REL[slot] = rel
        self.O_RELI[slot] = reli
        self.O_PAD[slot] = pad
        self._borrowing.discard(slot)
        self._virt_todo.add(slot)

    def _unborrow(self, slot):
        if self.O_VIRT[slot]:
            self.O_VIRT[slot] = False
            self._virt_todo.discard(slot)
            self._borrowing.discard(slot)

    def _link(self, slot, p):
        p.slots.add(slot)
        self._obj[slot][3] = p
        self.O_POSE[slot] = p.index

    def _attach(self, slot, borrow):
        """Give the object in ``slot`` a pose: the one fitted for it or, if
        ``borrow`` is allowed and there is one to borrow, another one of its
        mesh (no fit to wait for, no memory taken)."""
        o = self._obj[slot]
        g, L32 = o[1], o[2]
        key = (g.key, L32.tobytes())
        p = self._poses.get(key)
        if p is None:
            if borrow and g.nt > self.fit_now and g.poses:
                base = self._base_for(g, L32)
                if base is not None:
                    self._link(slot, base[0])
                    base[0].used = self._clock
                    self._borrow(slot, base[1], base[2], base[3])
                    return base[0]
            p = self._pose_new(g, L32, key)
        self._link(slot, p)
        p.used = self._clock
        return p

    def _own(self, slot):
        """Give a borrowing object the pose fitted for it (fitted later)."""
        o = self._obj[slot]
        p = o[3]
        g, L32 = o[1], o[2]
        key = (g.key, L32.tobytes())
        q = self._poses.get(key)
        self._unborrow(slot)
        if q is None and len(p.slots) == 1:
            self._rekey(p, L32, key)          # nobody else uses it: fit again in place
            p.used = self._clock
            return
        p.slots.discard(slot)
        o[3] = None
        self.O_POSE[slot] = -1
        if not p.slots:
            self._pose_drop(p)
        if q is None:
            q = self._pose_new(g, L32, key)
        self._link(slot, q)
        q.used = self._clock

    def _detach(self, slot):
        o = self._obj[slot]
        p = o[3]
        if p is None:
            return
        o[3] = None
        self.O_POSE[slot] = -1
        self._unborrow(slot)
        p.slots.discard(slot)
        if not p.slots:
            self._pose_drop(p)

    def _pose_cost(self, slot):
        g = self._obj[slot][1]
        return self._pose_rows(g.nv, g.rows)[1] * 24

    def _pose_ident(self, slot):
        """Something that is equal for objects that share the pose fitted for
        them (an object that borrows one may need its own)."""
        o = self._obj[slot]
        if o[3] is not None and not self.O_VIRT[slot]:
            return o[3]
        return (o[1].key, o[2].tobytes())

    def _advance(self, p, deadline):
        """Continue fitting a pose until it is done (True) or time is up."""
        if not p.unfit:
            return True
        job = p.job
        if job is None:
            job = p.job = self._fit_steps(p)
        while True:
            try:
                next(job)
            except StopIteration:
                p.job = None
                p.unfit = False
                return True
            if deadline is not None and time.perf_counter() >= deadline:
                return False

    def _prepare(self, slots, deadline, borrow):
        """The poses of the given objects are about to be used.  Whatever is
        missing is attached (keeping the others of the same request alive)
        and fitted until ``deadline``; returns True once all are usable.
        With ``borrow`` false every object ends up with the pose fitted for
        it, as exact work needs."""
        self._clock += 1
        clock = self._clock
        slots = list(slots)
        obj = self._obj
        virt = self.O_VIRT
        for slot in slots:
            p = obj[slot][3]
            if p is not None and (borrow or not virt[slot]):
                p.used = clock
        again = 0
        while True:
            flushes = self.flushes
            for slot in slots:
                if obj[slot][3] is None:
                    self._attach(slot, borrow)
                elif virt[slot] and not borrow:
                    self._own(slot)
            if flushes == self.flushes:
                break
            again += 1
            if again > 1:
                # cannot happen while a request fits the limit; if it does not,
                # the limit gives way rather than the request
                limit, self.cache_limit = self.cache_limit, None
                for slot in slots:
                    if obj[slot][3] is None:
                        self._attach(slot, borrow)
                    elif virt[slot] and not borrow:
                        self._own(slot)
                self.cache_limit = limit
                break
        for slot in slots:
            p = obj[slot][3]
            if p.unfit and not self._advance(p, deadline):
                return False
        return True

    def need_poses(self, slots):
        """Build the fitted poses of the given objects now, however long it
        takes."""
        self._prepare(slots, None, False)

    def reserve(self):
        """Make the pose storage as large as the scene is going to need (within
        the limit) in one go.  Optional: it happens anyway with the first pose."""
        want = self._demand + self.incoming[2]
        want += (want >> 3) + 4096
        room = self.pose_room()
        if room is not None:
            want = min(want, room // 24)
        want = want // self.BOX.align * self.BOX.align
        if want > self.BOX.data.shape[0]:
            self.BOX._resize(want)

    def set_cache_limit(self, nbytes):
        """Most memory the cached data may take, in bytes (None: no limit).
        Lowering it below what is in use drops poses; they are fitted again
        on demand."""
        self.cache_limit = None if nbytes is None else max(0, int(nbytes))
        self._trim_idle()
        room = self.pose_room()
        if room is not None and self.BOX.nbytes > room:
            self.flush_poses()

    def flush_poses(self):
        """Drop every pose and give their memory back (the monitor is being
        paused, or the limit was lowered).  Results stay; poses are fitted
        again when they are next needed."""
        for p in list(self._poses.values()):
            self._pose_drop(p)
        self.BOX.shrink(1 << 14)

    def _fit(self, p):
        """(Re)compute posed vertices and every node box of a pose."""
        for _ in self._fit_steps(p):
            pass
        p.unfit = False
        p.job = None

    def _fit_steps(self, p):
        """The fit of a pose as a generator that yields after every piece of
        work of a millisecond or two, so that a large mesh can be fitted in
        the gaps between redraws.

        Triangles are handled in blocks that fit the processor's cache: the
        boxes of a block's triangles and of all the tree levels inside the
        block are computed while the data is at hand.  (Most of the time goes
        into writing the 48 bytes of boxes per triangle to memory.)

        No view of the shared arrays is kept across a yield: they may be
        reallocated in between.
        """
        g = p.geom
        nt = g.nt
        nv = g.nv
        L = p.L
        # posed vertices
        for s in range(0, nv, VERT_CHUNK):
            e = min(nv, s + VERT_CHUNK)
            cols = self._posed(g, L, s, e)
            dst = self.VERT.data[p.vbase + s:p.vbase + e]
            for c in range(3):
                dst[:, c] = cols[c]
            del dst
            yield

        H = g.H
        K = min(FIT_BLOCK, H)
        B = 1 << K
        off = g.off
        cur_buf = np.empty((6, B), dtype=np.float32)
        nxt_buf = np.empty((6, B), dtype=np.float32)
        for s in range(0, nt, B):
            e = min(nt, s + B)
            n = e - s
            P = self.VERT.data
            t = self.TIDX.data[g.tbase + s:g.tbase + e]
            a = np.take(P, p.vbase + t[:, 0], axis=0)
            b = np.take(P, p.vbase + t[:, 1], axis=0)
            d = np.take(P, p.vbase + t[:, 2], axis=0)
            lo = np.minimum(a, b)
            np.minimum(lo, d, out=lo)
            np.maximum(a, b, out=a)
            np.maximum(a, d, out=a)
            cur = cur_buf[:, :n]
            cur[:3] = lo.T
            cur[3:] = a.T
            del P, t, a, b, d, lo
            box = self.BOX.data
            b0 = p.bbase
            other = nxt_buf
            nxt = None
            h = 0
            while True:
                r0 = b0 + off[h] + (s >> h)
                box[r0:r0 + n] = cur.T
                if h == K:
                    break
                half = n // 2
                n2 = (n + 1) // 2
                nxt = other[:, :n2]
                np.minimum(cur[:3, 0:2 * half:2], cur[:3, 1:2 * half:2], out=nxt[:3, :half])
                np.maximum(cur[3:, 0:2 * half:2], cur[3:, 1:2 * half:2], out=nxt[3:, :half])
                if n & 1:
                    nxt[:, half] = cur[:, n - 1]
                other = cur_buf if other is nxt_buf else nxt_buf
                cur = nxt
                n = n2
                h += 1
            del box, cur, nxt
            yield

        # the levels above the blocks, the padding rows and the tables
        box = self.BOX.data
        b0 = p.bbase
        n = g.nreal[K]
        cur = np.ascontiguousarray(box[b0 + off[K]:b0 + off[K] + n].T)
        for h in range(K + 1, H + 1):
            half = n // 2
            n2 = (n + 1) // 2
            nxt = np.empty((6, n2), dtype=np.float32)
            np.minimum(cur[:3, 0:2 * half:2], cur[:3, 1:2 * half:2], out=nxt[:3, :half])
            np.maximum(cur[3:, 0:2 * half:2], cur[3:, 1:2 * half:2], out=nxt[3:, :half])
            if n & 1:
                nxt[:, half] = cur[:, n - 1]
            cur = nxt
            n = n2
            box[b0 + off[h]:b0 + off[h] + n] = cur.T
        i = p.index
        self.LVL[i, :H + 1] = b0 + off
        sz = self.SZ[i]
        sz[:] = 0.0
        for h in range(H + 1):
            n = g.nreal[h]
            r0 = b0 + off[h]
            box[r0 + n:r0 + g.npad[h], :3] = np.inf
            box[r0 + n:r0 + g.npad[h], 3:] = -np.inf
            smp = box[r0:r0 + n:max(1, n // 512)]
            sz[h] = float((smp[:, 3:] - smp[:, :3]).max(axis=1).mean())
        self.PH[i] = H
        self.PTB[i] = g.tbase
        self.PVB[i] = p.vbase
        self.PNT[i] = nt

    # ---------------------------------------------------------------- objects
    def _grow_objects(self, need):
        self.O_T = grow_rows(self.O_T, need, 0.0)
        self.O_LO = grow_rows(self.O_LO, need, 0.0)
        self.O_HI = grow_rows(self.O_HI, need, 0.0)
        self.O_POSE = grow_rows(self.O_POSE, need, -1)
        self.O_ALIVE = grow_rows(self.O_ALIVE, need, False)
        self.O_VER = grow_rows(self.O_VER, need, 0)
        self.O_VIRT = grow_rows(self.O_VIRT, need, False)
        self.O_REL = grow_rows(self.O_REL, need, 0.0)
        self.O_RELI = grow_rows(self.O_RELI, need, 0.0)
        self.O_PAD = grow_rows(self.O_PAD, need, 0.0)
        self.O_L = grow_rows(self.O_L, need, 0.0)
        self.O_LB = grow_rows(self.O_LB, need, 0)

    def _set_extents(self, slot):
        o = self._obj[slot]
        lo, hi = self._extents(o[1], o[2])
        self.O_LO[slot] = lo
        self.O_HI[slot] = hi
        self.O_VER[slot] += 1

    def _mark(self, slot, hot):
        self._dirty[slot] = self._dirty.get(slot, False) or hot
        self._oob_dirty.add(slot)

    def has_object(self, uid):
        return uid in self._slot_of

    def slot(self, uid):
        return self._slot_of.get(uid)

    def uid(self, slot):
        o = self._obj[slot]
        return o[0] if o else None

    def add_object(self, uid, geom_key, matrix):
        if uid in self._slot_of:
            raise KeyError('object already present')
        g = self._geoms[geom_key]
        M = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
        L32 = np.ascontiguousarray(M[:3, :3], dtype=np.float32)
        if self._slot_free:
            slot = self._slot_free.pop()
            self._obj[slot] = [uid, g, L32, None]
        else:
            slot = len(self._obj)
            self._obj.append([uid, g, L32, None])
            self._grow_objects(slot + 1)
        g.refs += 1
        g.users.add(slot)
        self._idle.pop(g.key, None)
        self._demand += self._pose_rows(g.nv, g.rows)[1]
        self._slot_of[uid] = slot
        self.O_T[slot] = M[:3, 3]
        self.O_ALIVE[slot] = True
        self.O_POSE[slot] = -1
        self.O_VIRT[slot] = False
        self.O_L[slot] = L32
        self.O_LB[slot] = g.lbase
        self.adj[slot] = set()
        self._set_extents(slot)
        # like a live edit: every neighbour gets a quick answer first (does it
        # collide?), the details follow.  A part dropped into a full build is
        # then judged within a frame or two instead of after a long first pass.
        self._mark(slot, True)
        return slot

    def set_matrix(self, uid, matrix, hot=True):
        """Update an object's world matrix.  Returns True if anything changed."""
        slot = self._slot_of[uid]
        M = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
        o = self._obj[slot]
        changed = False
        L32 = np.ascontiguousarray(M[:3, :3], dtype=np.float32)
        lb = L32.tobytes()
        if lb != o[2].tobytes():
            g = o[1]
            p = o[3]
            o[2] = L32
            self.O_L[slot] = L32
            if p is not None:
                key = (g.key, lb)
                q = self._poses.get(key)
                if q is p:
                    self._unborrow(slot)          # back in the pose's own orientation
                elif q is not None and not q.unfit:
                    self._detach(slot)            # there is a pose for exactly this
                    self._link(slot, q)
                else:
                    x = self._rel_to(p, g, L32) if g.nt > self.fit_now else None
                    if x is not None:
                        # keep the pose and move its boxes as they are looked
                        # at; a fitted one follows when things are quiet
                        self._borrow(slot, *x)
                    elif q is None and len(p.slots) == 1:
                        # nobody else uses the pose: fit it again in place
                        self._unborrow(slot)
                        self._rekey(p, L32, key)
                    else:
                        self._detach(slot)
            self._set_extents(slot)
            changed = True
        t = M[:3, 3]
        if not np.array_equal(t, self.O_T[slot]):
            self.O_T[slot] = t
            changed = True
        if changed:
            self._mark(slot, hot)
        return changed

    def set_geometry(self, uid, geom_key, hot=True):
        slot = self._slot_of[uid]
        g = self._geoms[geom_key]
        o = self._obj[slot]
        old = o[1]
        if old is g:
            return False
        self._detach(slot)
        o[1] = g
        self.O_LB[slot] = g.lbase
        g.refs += 1
        g.users.add(slot)
        self._idle.pop(g.key, None)
        old.refs -= 1
        old.users.discard(slot)
        self._demand += self._pose_rows(g.nv, g.rows)[1] - self._pose_rows(old.nv, old.rows)[1]
        if old.refs == 0:
            self._geom_unused(old)
        self._oob_wait.pop(slot, None)
        self._set_extents(slot)
        self._mark(slot, hot)
        return True

    def remove_object(self, uid):
        slot = self._slot_of.pop(uid)
        for o in list(self.adj[slot]):
            self._drop_pair(slot, o)
        del self.adj[slot]
        self._detach(slot)
        g = self._obj[slot][1]
        g.refs -= 1
        g.users.discard(slot)
        self._demand -= self._pose_rows(g.nv, g.rows)[1]
        if g.refs == 0:
            self._geom_unused(g)
        self._obj[slot] = None
        self.O_ALIVE[slot] = False
        self.O_POSE[slot] = -1
        self._slot_free.append(slot)
        self._dirty.pop(slot, None)
        self._oob_dirty.discard(slot)
        self._oob_geom.pop(slot, None)
        self._oob_wait.pop(slot, None)
        self.oob.pop(slot, None)
        self.wall.pop(slot, None)
        self.version += 1

    @property
    def object_count(self):
        return len(self._slot_of)

    def colliding_slots(self):
        """Slots of the parts that take part in at least one collision."""
        out = set()
        for key, pr in self.viol.items():
            if pr.state == COLLIDE:
                out.update(key)
        return out

    def part_mesh(self, slot):
        """(geometry key, local vertices (nv, 3), triangles (nt, 3)) of a part.
        The arrays are views: copy them if they are kept."""
        g = self._obj[slot][1]
        return (g.key, self.LVERT.data[g.lbase:g.lbase + g.nv],
                self.TIDX.data[g.tbase:g.tbase + g.nt])

    def part_triangles(self, slot):
        return self._obj[slot][1].nt

    def part_matrix(self, slot):
        """World matrix of a part as float64 (4, 4)."""
        M = np.eye(4)
        M[:3, :3] = self._obj[slot][2]
        M[:3, 3] = self.O_T[slot]
        return M

    def part_bounds(self, slot):
        """Exact world-space extents of a part: (lo, hi)."""
        return self.O_LO[slot] + self.O_T[slot], self.O_HI[slot] + self.O_T[slot]

    def part_corners(self, slot):
        """Every triangle of a part as float32 (nt, 3, 3), in the part's frame
        (no translation), exactly as the engine sees it."""
        o = self._obj[slot]
        g = o[1]
        P = np.stack(self._posed(g, o[2], 0, g.nv), axis=1)
        return P[self.TIDX.data[g.tbase:g.tbase + g.nt]]

    # ------------------------------------------------------------ broad phase
    def _stamp(self, key):
        """What the result of a pair was worked out for: the versions of the
        two objects (geometry, rotation, scale), the settings, and where the
        second object is as seen from the first."""
        a, b = key
        d = (self.O_T[b] - self.O_T[a]).tolist()
        return (int(self.O_VER[a]), int(self.O_VER[b]), self._param_stamp, d[0], d[1], d[2])

    def solved_for(self, key):
        """The offset of the second object of a pair from the first that its
        result was worked out for, or None if it has none yet."""
        st = self.pairs[key].stamp
        return None if st is None else np.array(st[3:6])

    def _drop_pair(self, a, b):
        key = (a, b) if a < b else (b, a)
        self.adj[a].discard(b)
        self.adj[b].discard(a)
        self._pend_hot.pop(key, None)
        self._pend_cold.pop(key, None)
        self._pend_refine.pop(key, None)
        self._pend_scan.pop(key, None)
        self._pend_wait.pop(key, None)
        if self.pairs.pop(key, None) is not None and self.viol.pop(key, None) is not None:
            self.version += 1

    def _broad(self):
        dirty = self._dirty
        self._dirty = {}
        slots = np.array([s for s in dirty if self.O_ALIVE[s]], dtype=np.int64)
        if slots.shape[0] == 0:
            return
        n = len(self._obj)
        lo = self.O_LO[:n] + self.O_T[:n]
        hi = self.O_HI[:n] + self.O_T[:n]
        alive = self.O_ALIVE[:n]
        d = self._dmax() + self.eps_len
        d2max = d * d
        block = max(1, min(256, (1 << 21) // max(n, 1)))
        # for comparing stamps, as plain numbers: positions, versions and
        # how far an offset may be out (see POS_TOL)
        at = np.abs(self.O_T[:n])
        known = (self.O_T[:n].tolist(), self.O_VER[:n].tolist(),
                 (POS_TOL * np.maximum(np.maximum(at[:, 0], at[:, 1]), at[:, 2])).tolist())
        for s in range(0, slots.shape[0], block):
            sl = slots[s:s + block]
            g = np.maximum(lo[sl][:, None, :] - hi[None, :, :],
                           lo[None, :, :] - hi[sl][:, None, :])
            np.maximum(g, 0.0, out=g)
            near = (np.einsum('ijk,ijk->ij', g, g) <= d2max) & alive[None, :]
            near[np.arange(sl.shape[0]), sl] = False
            rows, cols = np.nonzero(near)
            bounds = np.searchsorted(rows, np.arange(sl.shape[0] + 1))
            for i in range(sl.shape[0]):
                a = int(sl[i])
                self._update_pairs(a, cols[bounds[i]:bounds[i + 1]].tolist(), dirty[a], known)

    def _update_pairs(self, a, cand, hot, known):
        adj = self.adj[a]
        gone = adj.difference(cand)
        for o in gone:
            self._drop_pair(a, o)
        pairs = self.pairs
        T, ver, tol = known
        ta = T[a]
        va = ver[a]
        params = self._param_stamp
        for o in cand:
            key = (a, o) if a < o else (o, a)
            pr = pairs.get(key)
            if pr is None:
                pr = PairResult()
                pairs[key] = pr
                adj.add(o)
                self.adj[o].add(a)
            else:
                # Nothing to do if the pair is as it was when it was solved:
                # the same objects, the same settings, the same offset.  (The
                # stamp is that of the solve, so offsets cannot creep.)
                st = pr.stamp
                if st is not None and st[2] == params:
                    to = T[o]
                    if a < o:
                        same = st[0] == va and st[1] == ver[o]
                        dx, dy, dz = to[0] - ta[0], to[1] - ta[1], to[2] - ta[2]
                    else:
                        same = st[0] == ver[o] and st[1] == va
                        dx, dy, dz = ta[0] - to[0], ta[1] - to[1], ta[2] - to[2]
                    if same:
                        e = tol[a] if tol[a] > tol[o] else tol[o]
                        if abs(dx - st[3]) <= e and abs(dy - st[4]) <= e and abs(dz - st[5]) <= e:
                            continue
            pr.no_cut = False
            if not pr.stale:
                pr.stale = True
                # A live edit is answered within a frame or two; until then
                # the old result stays on screen instead of blinking off.
                if not hot and self.viol.pop(key, None) is not None:
                    self.version += 1
            self._pend_refine.pop(key, None)
            self._pend_scan.pop(key, None)
            was_hot = self._pend_wait.pop(key, False)
            if hot or was_hot:
                self._pend_cold.pop(key, None)
                self._pend_hot[key] = None
            elif key not in self._pend_hot:
                self._pend_cold[key] = None

    # ------------------------------------------------------------------- step
    @property
    def busy(self):
        """True while ``step`` has something to do.  (What waits for a mesh
        to be sorted does not count: see ``waiting``.)"""
        return bool(self._dirty or self._pend_hot or self._pend_cold or self._pend_refine
                    or self._pend_scan or self._virt_todo or self._oob_geom)

    @property
    def waiting(self):
        """True while something waits for a mesh to be sorted."""
        return bool(self._pend_wait or self._oob_wait)

    @property
    def unsettled(self):
        """True while a pair has no result yet, or one that does not say for
        certain whether the parts collide.  (Pairs waiting only for their
        exact distance already have a complete collision answer.)"""
        return bool(self._dirty or self._pend_hot or self._pend_cold or self._pend_wait
                    or self._pend_scan)

    @property
    def pending(self):
        return (len(self._pend_hot) + len(self._pend_cold) + len(self._pend_refine)
                + len(self._pend_wait) + len(self._pend_scan))

    def _batch(self, src, size, borrow):
        """The next pairs of a queue to solve together (they stay queued).

        Pairs whose poses are fitted go first.  Only when there are none does
        a batch wait, and then for at most ``MAX_WAIT`` poses, so that the
        results of a new scene appear as the fits get done and not after all
        of them."""
        room = self.pose_room()
        pairs = self.pairs
        obj = self._obj
        virt = self.O_VIRT
        dead = []
        ready = []
        late = []                     # (key, poses it still needs fitted)
        waiting = set()
        closed = False
        held = set()
        held_bytes = 0
        for key in src:
            if key not in pairs:
                dead.append(key)
                continue
            if not (obj[key[0]][1].ready and obj[key[1]][1].ready):
                # A mesh is still to be sorted: out of the queue until it is.
                # That can take a while, so what is on screen for the pair
                # (worked out for another shape or place) goes now.
                dead.append(key)
                self._pend_wait[key] = src is self._pend_hot
                if self.viol.pop(key, None) is not None:
                    self.version += 1
                continue
            wait = None
            for slot in key:
                p = obj[slot][3]
                if p is None or p.unfit or (virt[slot] and not borrow):
                    ident = self._pose_ident(slot)
                    wait = [ident] if wait is None else wait + [ident]
            if wait is not None:
                if not closed and len(late) < size:
                    more = [i for i in wait if i not in waiting]
                    if late and len(waiting) + len(more) > MAX_WAIT:
                        closed = True
                    else:
                        waiting.update(more)
                        late.append(key)
                continue
            if room is not None:
                # the poses of one batch must fit the cache together
                extra = 0
                idents = []
                for slot in key:
                    ident = self._pose_ident(slot)
                    if ident not in held and ident not in idents:
                        idents.append(ident)
                        extra += self._pose_cost(slot)
                if ready and held_bytes + extra > room:
                    break
                held.update(idents)
                held_bytes += extra
            ready.append(key)
            if len(ready) >= size:
                break
        for key in dead:
            del src[key]
        if ready or not late or room is None:
            return ready or late
        # the same limit for a batch that waits
        keys = []
        held = set()
        held_bytes = 0
        for key in late:
            extra = 0
            idents = []
            for slot in key:
                ident = self._pose_ident(slot)
                if ident not in held and ident not in idents:
                    idents.append(ident)
                    extra += self._pose_cost(slot)
            if keys and held_bytes + extra > room:
                break
            held.update(idents)
            held_bytes += extra
            keys.append(key)
        return keys

    def step(self, budget=0.010, hot_budget=0.016, idle=True):
        """Bring results up to date.  Returns True while work remains.

        In order:
          hot     pairs touched by a live edit, handled with the quick solve
                  until ``hot_budget`` is used up.  Whether parts collide is
                  always decided; how much detail each pair gets follows the
                  measured cost (see ``_choose_detail``).  Pairs that do not
                  fit are taken up again in the next step.
          cold    background work such as the first scan; solved exactly
                  within what is left of ``budget``.
          poses   objects that borrow a pose get their own, when ``idle``.
          refine  quick results waiting for their exact pass; only handled
                  when ``idle`` is true, i.e. nothing is being dragged.

        Poses that are missing are fitted within the same budgets, a piece
        at a time; the pairs that need them wait.
        """
        t0 = time.perf_counter()
        solved = 0
        if self._dirty:
            self._broad()
        while True:
            if self._pend_hot:
                src, exact, limit = self._pend_hot, False, hot_budget
            elif self._pend_cold:
                src, exact, limit = self._pend_cold, True, budget
            elif idle and self._virt_todo:
                self._upgrade()
                continue
            elif idle and self._pend_refine:
                src, exact, limit = self._pend_refine, True, budget
            elif idle and self._pend_scan:
                if (solved and time.perf_counter() - t0 > budget) or not self._scan_step(t0 + budget):
                    break
                solved += 1
                continue
            else:
                break
            if solved and time.perf_counter() - t0 > limit:
                break
            if exact:
                size = self._bg_batch
            else:
                # A live solve takes every pair that is waiting if a sketch of
                # each fits twice the budget: complete answers a little late
                # are better than partial ones on time, and a solve has a
                # fixed cost that is then paid once.  Otherwise it takes as
                # many as fit what is left of the budget; the rest wait for
                # the next step (their previous result stays on screen).
                c0, c1 = self._lite_c0, max(self._lite_c1, 1e-5)
                elapsed = time.perf_counter() - t0
                if elapsed + c0 + c1 * len(src) <= 2.0 * limit:
                    size = len(src)
                else:
                    size = max(8.0, (max(limit - elapsed, 0.5 * limit) - c0) / c1)
                size = int(min(MAX_BATCH, size))
            keys = self._batch(src, size, not exact)
            if not keys:
                continue
            # (a live solve borrows poses rather than wait for fits; exact
            # work always gets fitted ones)
            if not self._prepare({slot for key in keys for slot in key}, t0 + limit, not exact):
                break                 # still fitting: the pairs stay where they are
            for key in keys:
                del src[key]
                self.pairs[key].stamp = self._stamp(key)
            detail = None if exact else self._choose_detail(keys)
            if exact:
                n = len(self.viol)
                self.tri_cap = int(min(narrow.TRI_CAP, max(2000, REGION_BUDGET // (2 * max(n, 1)))))
            t1 = time.perf_counter()
            narrow.solve(self, keys, exact, detail)
            dt = time.perf_counter() - t1
            if exact:
                # background slices should fit the budget they are given
                fit = budget * len(keys) / max(dt, 1e-6)
                self._bg_batch = int(min(MAX_BATCH, max(1, 0.5 * self._bg_batch + 0.5 * fit)))
            else:
                self._learn_cost(len(keys), detail, dt)
            self._serial += 1
            virt = self.O_VIRT
            for key in keys:
                pr = self.pairs[key]
                pr.serial = self._serial
                pr.loose = bool(virt[key[0]] or virt[key[1]])
                if pr.state > OK:
                    self.viol[key] = pr
                else:
                    self.viol.pop(key, None)
                if pr.refine:
                    self._pend_refine[key] = None
                if pr.unproven:
                    self._pend_scan[key] = None
                    self.scans += 1
            solved += len(keys)
            self.version += 1
        if self._oob_dirty or self._oob_all:
            self._update_oob()
        if self._oob_geom:
            self._oob_fill(t0 + max(budget, hot_budget))
        self.last_step_ms = (time.perf_counter() - t0) * 1000.0
        self.last_pairs = solved
        return self.busy

    def _scan_step(self, deadline):
        """Go on with the exhaustive search for an intersection in the pairs
        whose regular search ran into its caps (``narrow.scan``).  Returns
        False when time is up or a pose is still being fitted."""
        while self._pend_scan:
            key = next(iter(self._pend_scan))
            pr = self.pairs.get(key)
            if pr is None or pr.stale or not pr.unproven:
                del self._pend_scan[key]
                continue
            if not self._prepare(set(key), deadline, False):
                return False
            state = self._pend_scan[key]
            if state is None:
                state = self._pend_scan[key] = narrow.scan_start(self, key)
            found = narrow.scan(self, key, state, deadline)
            if found is None:
                return False
            del self._pend_scan[key]
            pr.unproven = False
            if found:
                narrow.scan_report(self, key, pr, found)
                self._serial += 1
                pr.serial = self._serial
                self.viol[key] = pr
                self.scan_hits += 1
                self.version += 1
            else:
                # They do not cut or touch.  How close they are was left
                # unproven until this was known (see narrow.solve).
                pr.no_cut = True
                if self.clear_thr > 0.0:
                    self._pend_refine[key] = None
        return True

    def _upgrade(self):
        """Things are quiet: whatever was worked out with borrowed boxes is
        queued to be redone with fitted poses, so that what is on screen at
        rest does not depend on how the parts got where they are.  (An object
        without such results simply keeps borrowing; that costs nothing.)"""
        virt = self.O_VIRT
        for slot in self._virt_todo:
            for o in self.adj[slot]:
                key = (slot, o) if slot < o else (o, slot)
                pr = self.pairs.get(key)
                if (pr is None or pr.stale or not pr.loose
                        or key in self._pend_hot or key in self._pend_cold):
                    continue
                self._pend_refine[key] = None
        self._borrowing |= self._virt_todo
        self._virt_todo.clear()

    def _choose_detail(self, keys):
        """Which pairs of a live solve get the full treatment (the complete
        intersection curve, the hatched region, the proven distance): None
        for all, else a boolean per pair.

        All of them when they are few and that fits ``LIVE_FULL`` (the cost of
        a pair varies too much to promise that for dozens: a part dropped onto
        thirty others would stall for a moment); otherwise as many as fit
        ``LIVE_TARGET`` next to a sketch of the others, and one at least if
        that still fits ``LIVE_FULL``.  Pairs that already show detail keep
        it, so the picture does not flicker; then known problems, then the
        smaller ones."""
        n = len(keys)
        cost = self._detail_cost
        if self.live_detail is not None:
            nd = self.live_detail                    # set from outside (tests)
        elif cost <= 0.0:
            nd = 4                                   # nothing measured yet
        elif n <= LIVE_FULL_PAIRS and n * cost <= LIVE_FULL:
            return None
        else:
            sketch = self._lite_c0 + self._lite_c1 * n
            nd = int((LIVE_TARGET - sketch) / cost)
            if nd < 1 and sketch + cost <= LIVE_FULL:
                nd = 1
        if nd >= n:
            return None
        detail = np.zeros(n, dtype=bool)
        if nd > 0:
            rank = np.empty(n, dtype=np.int64)
            size = np.empty(n, dtype=np.int64)
            for i, key in enumerate(keys):
                pr = self.pairs[key]
                rank[i] = 2 if pr.state <= OK else (1 if pr.lite else 0)
                size[i] = self._obj[key[0]][1].nt + self._obj[key[1]][1].nt
            detail[np.lexsort((size, rank))[:nd]] = True
        return detail

    def _learn_cost(self, n, detail, dt):
        """Update the cost estimates of a live solve from one that was timed."""
        nd = n if detail is None else int(detail.sum())
        if nd == n:
            per = dt / n
        elif nd:
            per = max(dt - self._lite_c0 - self._lite_c1 * (n - nd), 0.25 * dt) / nd
        if nd:
            # quick to rise, slow to fall: the cost climbs as a part goes
            # deeper into its neighbours, and an estimate that lags behind
            # shows as a stutter
            old = self._detail_cost
            self._detail_cost = per if per > old else 0.85 * old + 0.15 * per
        elif n <= 3:
            # a small solve is mostly its fixed cost
            c0 = max(dt - self._lite_c1 * n, 0.5 * dt)
            self._lite_c0 = 0.7 * self._lite_c0 + 0.3 * c0
        else:
            c1 = max(dt - self._lite_c0, 0.2 * dt) / n
            self._lite_c1 = 0.6 * self._lite_c1 + 0.4 * c1
        if not nd:
            self._detail_cost *= 0.99        # so that detail is tried again now and then

    # ----------------------------------------------------------- build volume
    def _update_oob(self):
        """Classify the parts that changed against the build volume.  This
        needs their extents only; the geometry the overlay draws for them is
        collected by ``_oob_fill``."""
        if self.volume is None:
            if self.oob or self.wall:
                self.oob.clear()
                self.wall.clear()
                self.version += 1
            self._oob_dirty.clear()
            self._oob_geom.clear()
            self._oob_wait.clear()
            self._oob_all = False
            return
        if self._oob_all:
            cand = list(self._slot_of.values())
        else:
            cand = [s for s in self._oob_dirty if self.O_ALIVE[s]]
        self._oob_dirty.clear()
        self._oob_all = False
        if not cand:
            return
        slots = np.array(cand, dtype=np.int64)
        vlo, vhi = self.volume
        e = self.eps_len
        # These are the exact extents of each part: translation alone decides
        # most cases.
        lo = self.O_LO[slots] + self.O_T[slots]
        hi = self.O_HI[slots] + self.O_T[slots]
        inside = ((lo >= vlo - e) & (hi <= vhi + e)).all(axis=1)
        outside = ((lo > vhi + e) | (hi < vlo - e)).any(axis=1)
        if self.inner_box() is not None:
            # gaps to the four side walls (x low, y low, x high, y high); the
            # floor and the top do not count
            gaps = np.concatenate([lo[:, :2] - vlo[:2], vhi[:2] - hi[:, :2]], axis=1)
            gap = gaps.min(axis=1)
            which = gaps.argmin(axis=1)
            near = inside & (gap < self.wall_margin - e)
        else:
            gap = which = None
            near = np.zeros(slots.shape[0], dtype=bool)
        changed = False
        self._serial += 1
        todo = self._oob_geom
        for i, slot in enumerate(cand):
            todo.pop(slot, None)
            self._oob_wait.pop(slot, None)
            if near[i]:
                r = WallResult()
                r.dist = max(0.0, float(gap[i]))
                r.lo = lo[i]
                r.hi = hi[i]
                r.tris = None
                r.band_only = False
                # until the geometry is known: the middle of the side of the
                # part's bounding box that faces the nearest wall
                r.axis, r.high = int(which[i]) % 2, int(which[i]) >= 2
                r.center = 0.5 * (lo[i] + hi[i])
                r.center[r.axis] = hi[i][r.axis] if r.high else lo[i][r.axis]
                r.radius = 0.25 * float(np.linalg.norm(hi[i] - lo[i]))
                r.serial = self._serial
                self.wall[slot] = r
                todo[slot] = None
                changed = True
            elif self.wall.pop(slot, None) is not None:
                changed = True
            if inside[i]:
                if self.oob.pop(slot, None) is not None:
                    changed = True
                continue
            r = OobResult()
            r.lo = lo[i]
            r.hi = hi[i]
            r.tris = None
            r.band_only = False
            r.serial = self._serial
            r.state = OUTSIDE if outside[i] else PARTIAL
            if r.state == PARTIAL:
                todo[slot] = None
            self.oob[slot] = r
            changed = True
        if changed:
            self.version += 1

    def _oob_fill(self, deadline):
        """Collect what the overlay draws for parts that reach out of the
        volume or into the wall margin.  This needs their poses, so it can
        take several calls; returns True when nothing is left."""
        todo = self._oob_geom
        vlo, vhi = self.volume
        inner = self.inner_box()
        for slot in [s for s in todo if not self._obj[s][1].ready]:
            del todo[slot]                  # its mesh is still to be sorted
            self._oob_wait[slot] = None
        while todo:
            room = self.pose_room()
            group = []
            held = set()
            held_bytes = 0
            for slot in todo:
                if len(group) >= 64:
                    break
                if room is not None:
                    ident = self._pose_ident(slot)
                    extra = 0 if ident in held else self._pose_cost(slot)
                    if group and held_bytes + extra > room:
                        break
                    held.add(ident)
                    held_bytes += extra
                group.append(slot)
            if not self._prepare(group, deadline, True):
                return False
            for slot in group:
                del todo[slot]
            partial = [s for s in group if s in self.oob and self.oob[s].state == PARTIAL]
            close = [s for s in group if s in self.wall] if inner is not None else []
            if partial:
                tris, band = narrow.oob_solve(self, np.array(partial, dtype=np.int64),
                                              vlo, vhi, OOB_TRI_CAP)
                for slot, t, b in zip(partial, tris, band):
                    r = self.oob[slot]
                    r.tris = t
                    r.band_only = b
            if close:
                # the triangles that reach into the margin: everything that is
                # not completely inside the inner box
                tris, band = narrow.oob_solve(self, np.array(close, dtype=np.int64),
                                              inner[0], inner[1], OOB_TRI_CAP)
                for slot, t, b in zip(close, tris, band):
                    r = self.wall[slot]
                    r.tris = t
                    r.band_only = b
                    if len(t):
                        # mark the geometry that is inside the margin of the nearest wall
                        pts = t.reshape(-1, 3)
                        c = pts[:, r.axis]
                        sel = pts[c > inner[1][r.axis]] if r.high else pts[c < inner[0][r.axis]]
                        if len(sel):
                            r.center = sel.mean(axis=0, dtype=np.float64)
                            r.radius = max(
                                0.5 * float(np.linalg.norm(sel.max(axis=0) - sel.min(axis=0))),
                                self.wall_margin)
            self.version += 1
        return True

    # ---------------------------------------------------------------- reports
    def counts(self):
        """(collisions, clearance warnings, partly outside, fully outside,
        too close to a side wall)."""
        nc = ncl = 0
        for pr in self.viol.values():
            if pr.state == COLLIDE:
                nc += 1
            else:
                ncl += 1
        npart = nout = 0
        for r in self.oob.values():
            if r.state == PARTIAL:
                npart += 1
            else:
                nout += 1
        return nc, ncl, npart, nout, len(self.wall)

    def stats(self):
        return {
            'objects': len(self._slot_of),
            'geoms': len(self._geoms),
            'poses': len(self._poses),
            'pairs': len(self.pairs),
            'triangles': int(sum(self._obj[s][1].nt for s in self._slot_of.values())),
            'unique_triangles': int(sum(g.nt for g in self._geoms.values())),
            'bytes': self.pose_bytes + self.geom_bytes,
            'reserved': self.BOX.nbytes + self.TIDX.nbytes + self.LVERT.nbytes,
            'pose_bytes': self.pose_bytes,
            'geom_bytes': self.geom_bytes,
            'cache_limit': self.cache_limit,
            'evictions': self.evictions,
            'flushes': self.flushes,
            'borrowing': int(len(self._virt_todo) + len(self._borrowing)),
            'unsorted': sum(1 for g in self._geoms.values() if not g.ready),
            'scans': self.scans,
            'scan_hits': self.scan_hits,
            'unused_bytes': sum(self._geom_size(self._geoms[k]) for k in self._idle),
        }
