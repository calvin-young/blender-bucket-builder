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

Moving an object only changes its translation, so nothing is rebuilt.  Rotating
or scaling re-fits the boxes of that one object (a few vectorised passes, no
re-sorting).  Only a real geometry change rebuilds the triangle order.

Poses are the bulk of the memory (about 55 bytes per triangle each) and they
are a cache: one is built when a pair involving the object is first solved,
never for a part that has no neighbours, and the least recently used ones
are dropped when ``cache_limit`` is reached.  A dropped pose is simply re-fitted
when it is needed again.  The extents of an object do not need its pose.

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
REGION_BUDGET = 400000    # triangles of hatched regions kept for the whole scene


class Geom:
    __slots__ = ('key', 'nv', 'nt', 'verts', 'tbase', 'H', 'nreal', 'npad', 'off',
                 'rows', 'refs', 'ext')


class Pose:
    __slots__ = ('index', 'geom', 'key', 'L', 'base', 'rows', 'vbase', 'bbase', 'slots',
                 'used', 'unfit')


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
                 'clip_b', 'approx', 'refine', 'lite', 'enclosed', 'center', 'radius', 'stale',
                 'stamp', 'serial')

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
    __slots__ = ('dist', 'tris', 'lo', 'hi', 'band_only', 'center', 'radius', 'serial')


class World:
    def __init__(self):
        # pose storage: node boxes (lo xyz, hi xyz) and, through VERT, posed vertices
        self.BOX = Arena(6, np.float32, 1 << 14, align=bvh.PAD)
        self.VERT = _VertView(self.BOX)
        self.TIDX = Arena(3, np.int32, 1 << 13)      # sorted triangles (vertex ids)

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
        self.cache_limit = None   # bytes the cached data may take (None: no limit)
        self.pose_bytes = 0       # bytes of the poses that exist right now
        self.geom_bytes = 0       # bytes of the unique meshes
        self.evictions = 0        # poses dropped to stay within the limit
        self.flushes = 0          # times every pose had to go (fragmented storage)
        self._clock = 0           # advances with every use of poses (for eviction)

        m = 64
        self.O_T = np.zeros((m, 3), dtype=np.float64)
        self.O_LO = np.zeros((m, 3), dtype=np.float64)
        self.O_HI = np.zeros((m, 3), dtype=np.float64)
        self.O_POSE = np.full(m, -1, dtype=np.int64)
        self.O_ALIVE = np.zeros(m, dtype=bool)
        self.O_VER = np.zeros(m, dtype=np.int64)
        self._obj = []            # slot -> [uid, Geom, L (3x3 float32), Pose or None] or None
        self._slot_of = {}
        self._slot_free = []

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
        self._oob_dirty = set()
        self._oob_all = False
        self._param_stamp = 0
        self.version = 0          # bumps whenever any result changes
        self._serial = 0
        # Load control, following the measured cost.  ``live_detail`` is how
        # many pairs get the full treatment in one live solve; the rest are
        # sketched and completed when idle.  ``_bg_batch`` is how many pairs a
        # background solve takes at once.
        self.live_detail = 8
        self._detail_cost = 0.0   # seconds one fully treated pair costs (running estimate)
        self._lite_cost = 0.0     # seconds one sketched pair costs
        self._bg_batch = 8
        self.tri_cap = narrow.TRI_CAP     # region triangles per part and pair
        self.last_step_ms = 0.0
        self.last_pairs = 0

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
        if g is not None:
            return g
        verts = np.array(verts, dtype=np.float32).reshape(-1, 3)
        tris = np.ascontiguousarray(tris, dtype=np.int32).reshape(-1, 3)
        nt = tris.shape[0]
        if nt == 0:
            raise ValueError('mesh has no triangles')
        if not np.isfinite(verts).all():
            verts = np.nan_to_num(verts, nan=0.0, posinf=0.0, neginf=0.0)
        # Loose vertices are dropped, so that the extents of the mesh can be
        # taken from its vertices alone.
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
        order = bvh.build_order(cen[0], cen[1], cen[2])
        del cen, idx

        g = Geom()
        g.key = key
        g.nv = verts.shape[0]
        g.nt = nt
        g.verts = verts
        g.H, g.nreal, g.npad, g.off, g.rows = bvh.level_layout(nt)
        g.tbase = self.TIDX.alloc(g.npad[0])
        t = self.TIDX.data[g.tbase:g.tbase + g.npad[0]]
        t[:nt] = tris[order]
        t[nt:] = 0
        g.refs = 0                # objects using this mesh
        g.ext = {}                # rotation/scale bytes -> extents
        self._geoms[key] = g
        self.geom_bytes += self._geom_size(g)
        return g

    @staticmethod
    def _geom_size(g):
        return g.npad[0] * 12 + g.nv * 12

    def _geom_drop(self, g):
        self.TIDX.release(g.tbase, g.npad[0])
        if self._geoms.pop(g.key, None) is not None:
            self.geom_bytes -= self._geom_size(g)

    def drop_unused_geoms(self):
        for g in [g for g in self._geoms.values() if g.refs == 0]:
            self._geom_drop(g)

    # ------------------------------------------------------------------ poses
    @staticmethod
    def _pose_rows(nv, box_rows):
        """(rows the posed vertices take, rows of the whole block)."""
        vrows = -(-((int(nv) + 1) // 2) // bvh.PAD) * bvh.PAD
        return vrows, vrows + int(box_rows)

    @staticmethod
    def pose_size(nv, nt):
        """Bytes one pose of a mesh with ``nv`` vertices and ``nt`` triangles takes."""
        return World._pose_rows(nv, bvh.level_layout(nt)[4])[1] * 24

    def _extents(self, g, L32):
        """Exact extents of a mesh under a rotation/scale, without its pose."""
        key = L32.tobytes()
        ext = g.ext.get(key)
        if ext is None:
            vp = g.verts @ L32.T
            ext = (vp.min(axis=0).astype(np.float64), vp.max(axis=0).astype(np.float64))
            if len(g.ext) > 4096:
                g.ext.clear()
            g.ext[key] = ext
        return ext

    def _pose_new(self, g, L32, key):
        self._make_room(g)
        p = Pose()
        p.geom = g
        p.key = key
        p.L = L32.copy()
        p.slots = set()
        p.used = self._clock
        p.unfit = True
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
        # (the array may over-allocate, but not beyond the limit)
        p.base = self.BOX.alloc(p.rows, None if room is None else room - self.BOX.nbytes)
        p.vbase = 2 * p.base
        p.bbase = p.base + vrows
        self._poses[key] = p
        self.pose_bytes += p.rows * 24
        return p

    def _pose_drop(self, p):
        """Free a pose.  Objects that were using it get it rebuilt when they
        next need it."""
        for slot in p.slots:
            self._obj[slot][3] = None
            self.O_POSE[slot] = -1
        p.slots.clear()
        del self._poses[p.key]
        self.BOX.release(p.base, p.rows)
        self._pose_slots[p.index] = None
        self._pose_free.append(p.index)
        self.pose_bytes -= p.rows * 24

    def _make_room(self, g):
        """Drop least recently used poses until one more of ``g`` fits the limit.

        Poses in use by the work at hand (same clock) are kept, unless the
        storage is so fragmented that the new one fits nowhere: then every
        pose is dropped (which leaves the storage empty, hence compact) and the
        caller builds the ones it needs again."""
        room = self.pose_room()
        if room is None:
            return
        rows = self._pose_rows(g.nv, g.rows)[1]
        need = rows * 24

        def fits():
            # in what is in use, and in what the storage would have to grow to
            return (self.pose_bytes + need <= room
                    and self.BOX.nbytes + self.BOX.growth(rows) * 24 <= room)

        if fits():
            return
        for p in sorted((q for q in self._poses.values() if q.used != self._clock),
                        key=lambda q: q.used):
            self._pose_drop(p)
            self.evictions += 1
            if fits():
                return
        if self._poses and self.pose_bytes + need <= room:
            for p in list(self._poses.values()):
                self._pose_drop(p)
                self.evictions += 1
            self.flushes += 1

    def _attach(self, slot):
        """Make sure the object in ``slot`` has its pose, fitted, and return it."""
        o = self._obj[slot]
        p = o[3]
        if p is None:
            g, L32 = o[1], o[2]
            key = (g.key, L32.tobytes())
            p = self._poses.get(key)
            if p is None:
                p = self._pose_new(g, L32, key)
            p.slots.add(slot)
            o[3] = p
            self.O_POSE[slot] = p.index
        p.used = self._clock
        if p.unfit:
            self._fit(p)
            p.unfit = False
        return p

    def _detach(self, slot):
        o = self._obj[slot]
        p = o[3]
        if p is None:
            return
        o[3] = None
        self.O_POSE[slot] = -1
        p.slots.discard(slot)
        if not p.slots:
            self._pose_drop(p)

    def pose_room(self):
        """Bytes available for poses, or None when there is no limit."""
        if self.cache_limit is None:
            return None
        return max(0, self.cache_limit - self.geom_bytes)

    def _pose_cost(self, slot):
        g = self._obj[slot][1]
        return self._pose_rows(g.nv, g.rows)[1] * 24

    def _pose_ident(self, slot):
        """Something that is equal for objects that share a pose."""
        o = self._obj[slot]
        return o[3] if o[3] is not None else (o[1].key, o[2].tobytes())

    def need_poses(self, slots):
        """Poses of the given objects are about to be used: build what is
        missing, keeping the others of the same request alive."""
        self._clock += 1
        slots = list(slots)
        for slot in slots:
            p = self._obj[slot][3]
            if p is not None:
                p.used = self._clock
        while True:
            flushes = self.flushes
            for slot in slots:
                self._attach(slot)
            if flushes == self.flushes:
                break
            if self.flushes - flushes > 1:
                # cannot happen while a request fits the limit; if it does not,
                # the limit gives way rather than the request
                limit, self.cache_limit = self.cache_limit, None
                for slot in slots:
                    self._attach(slot)
                self.cache_limit = limit
                break

    def set_cache_limit(self, nbytes):
        """Most memory the cached data may take, in bytes (None: no limit).
        Lowering it below what is in use drops poses; they are re-fitted on
        demand."""
        self.cache_limit = None if nbytes is None else max(0, int(nbytes))
        room = self.pose_room()
        if room is not None and self.BOX.nbytes > room:
            for p in list(self._poses.values()):
                self._pose_drop(p)
            self.BOX.shrink(1 << 14)

    def _fit(self, p):
        """(Re)compute posed vertices and every node box of a pose.

        Work is done one coordinate at a time on contiguous 1-D arrays, which
        is several times faster in NumPy than operating on (n, 3) blocks.
        """
        g = p.geom
        nt = g.nt
        vp = g.verts @ p.L.T
        self.VERT.data[p.vbase:p.vbase + g.nv] = vp
        box = self.BOX.data
        b0 = p.bbase
        tri = self.TIDX.data[g.tbase:g.tbase + nt]
        idx = [np.ascontiguousarray(tri[:, k]) for k in range(3)]
        lv = box[b0:b0 + g.npad[0]]
        lo = []
        hi = []
        for c in range(3):
            col = np.ascontiguousarray(vp[:, c])
            a = np.take(col, idx[0])
            b = np.take(col, idx[1])
            d = np.take(col, idx[2])
            l = np.minimum(a, b)
            np.minimum(l, d, out=l)
            np.maximum(a, b, out=a)
            np.maximum(a, d, out=a)
            lv[:nt, c] = l
            lv[:nt, 3 + c] = a
            lo.append(l)
            hi.append(a)
        del idx
        lv[nt:, :3] = np.inf
        lv[nt:, 3:] = -np.inf

        i = p.index
        H = g.H
        self.LVL[i, :H + 1] = b0 + g.off
        sz = self.SZ[i]
        sz[:] = 0.0
        for h in range(H + 1):
            n = g.nreal[h]
            if h:
                m = g.nreal[h - 1]
                half = m // 2
                dst = box[b0 + g.off[h]:b0 + g.off[h] + g.npad[h]]
                for c in range(3):
                    l = lo[c][0::2].copy()
                    np.minimum(l[:half], lo[c][1::2], out=l[:half])
                    u = hi[c][0::2].copy()
                    np.maximum(u[:half], hi[c][1::2], out=u[:half])
                    dst[:n, c] = l
                    dst[:n, 3 + c] = u
                    lo[c] = l
                    hi[c] = u
                dst[n:, :3] = np.inf
                dst[n:, 3:] = -np.inf
            st = max(1, n // 512)
            ext = np.maximum(np.maximum(hi[0][::st] - lo[0][::st], hi[1][::st] - lo[1][::st]),
                             hi[2][::st] - lo[2][::st])
            sz[h] = float(ext.mean())
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
        self._slot_of[uid] = slot
        self.O_T[slot] = M[:3, 3]
        self.O_ALIVE[slot] = True
        self.O_POSE[slot] = -1
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
            p = o[3]
            if p is not None:
                key = (o[1].key, lb)
                if len(p.slots) == 1 and key not in self._poses:
                    # nobody else uses the old pose: keep its storage and
                    # re-fit it when it is next needed
                    del self._poses[p.key]
                    p.key = key
                    p.L = L32.copy()
                    p.unfit = True
                    self._poses[key] = p
                else:
                    self._detach(slot)
            o[2] = L32
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
        g.refs += 1
        old.refs -= 1
        if old.refs == 0:
            self._geom_drop(old)
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
        if g.refs == 0:
            self._geom_drop(g)
        self._obj[slot] = None
        self.O_ALIVE[slot] = False
        self.O_POSE[slot] = -1
        self._slot_free.append(slot)
        self._dirty.pop(slot, None)
        self._oob_dirty.discard(slot)
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
        return g.key, g.verts, self.TIDX.data[g.tbase:g.tbase + g.nt]

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

    # ------------------------------------------------------------ broad phase
    def _stamp(self, key):
        a, b = key
        r = np.rint((self.O_T[b] - self.O_T[a]) * 1e6)
        return (int(self.O_VER[a]), int(self.O_VER[b]),
                float(r[0]), float(r[1]), float(r[2]), self._param_stamp)

    def _drop_pair(self, a, b):
        key = (a, b) if a < b else (b, a)
        self.adj[a].discard(b)
        self.adj[b].discard(a)
        self._pend_hot.pop(key, None)
        self._pend_cold.pop(key, None)
        self._pend_refine.pop(key, None)
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
                self._update_pairs(a, cols[bounds[i]:bounds[i + 1]].tolist(), dirty[a])

    def _update_pairs(self, a, cand, hot):
        adj = self.adj[a]
        gone = adj.difference(cand)
        for o in gone:
            self._drop_pair(a, o)
        pairs = self.pairs
        for o in cand:
            key = (a, o) if a < o else (o, a)
            pr = pairs.get(key)
            if pr is None:
                pr = PairResult()
                pairs[key] = pr
                adj.add(o)
                self.adj[o].add(a)
            elif pr.stamp == self._stamp(key):
                continue
            if not pr.stale:
                pr.stale = True
                # A live edit is answered within a frame or two; until then
                # the old result stays on screen instead of blinking off.
                if not hot and self.viol.pop(key, None) is not None:
                    self.version += 1
            self._pend_refine.pop(key, None)
            if hot:
                self._pend_cold.pop(key, None)
                self._pend_hot[key] = None
            elif key not in self._pend_hot:
                self._pend_cold[key] = None

    # ------------------------------------------------------------------- step
    @property
    def busy(self):
        return bool(self._dirty or self._pend_hot or self._pend_cold or self._pend_refine)

    @property
    def unsettled(self):
        """True while a pair has no result yet.  (Pairs waiting only for their
        exact distance already have a complete collision answer.)"""
        return bool(self._dirty or self._pend_hot or self._pend_cold)

    @property
    def pending(self):
        return len(self._pend_hot) + len(self._pend_cold) + len(self._pend_refine)

    def step(self, budget=0.010, hot_budget=0.016, idle=True):
        """Bring results up to date.  Returns True while work remains.

        Three queues, in order:
          hot     pairs touched by a live edit, handled with the quick solve
                  until ``hot_budget`` is used up.  Whether parts collide is
                  always decided; how much detail each pair gets follows the
                  measured cost (see ``_choose_detail``).  Pairs that do not
                  fit are taken up again in the next step.
          cold    background work such as the first scan; solved exactly
                  within what is left of ``budget``.
          refine  quick results waiting for their exact pass; only handled
                  when ``idle`` is true, i.e. nothing is being dragged.
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
            elif idle and self._pend_refine:
                src, exact, limit = self._pend_refine, True, budget
            else:
                break
            if solved and time.perf_counter() - t0 > limit:
                break
            keys = []
            if exact:
                size = self._bg_batch
            else:
                # as many pairs as a sketch of each fits into one live solve;
                # with more than that pending, the rest wait for the next step
                # (their previous result stays on screen meanwhile)
                size = int(min(MAX_BATCH, max(8, LIVE_TARGET / max(self._lite_cost, 1e-5))))
            room = self.pose_room()
            held = set()
            held_bytes = 0
            while src and len(keys) < size:
                key = next(iter(src))
                if self.pairs.get(key) is None:
                    del src[key]
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
                    if keys and held_bytes + extra > room:
                        break
                    held.update(idents)
                    held_bytes += extra
                del src[key]
                self.pairs[key].stamp = self._stamp(key)
                keys.append(key)
            if not keys:
                continue
            self.need_poses({slot for key in keys for slot in key})
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
                self._bg_batch = int(min(MAX_BATCH, max(2, 0.5 * self._bg_batch + 0.5 * fit)))
            else:
                self._learn_cost(len(keys), detail, dt)
            self._serial += 1
            for key in keys:
                pr = self.pairs[key]
                pr.serial = self._serial
                if pr.state > OK:
                    self.viol[key] = pr
                else:
                    self.viol.pop(key, None)
                if pr.refine:
                    self._pend_refine[key] = None
            solved += len(keys)
            self.version += 1
        if self._oob_dirty or self._oob_all:
            self._update_oob()
        self.last_step_ms = (time.perf_counter() - t0) * 1000.0
        self.last_pairs = solved
        return self.busy

    def _choose_detail(self, keys):
        """Which pairs of a live solve get the full treatment: None for all,
        else a boolean per pair.  Pairs that already show detail keep it, so
        the picture does not flicker; then known problems, then the rest."""
        n = len(keys)
        if n <= self.live_detail:
            return None
        detail = np.zeros(n, dtype=bool)
        if self.live_detail > 0:
            rank = np.empty(n, dtype=np.int64)
            size = np.empty(n, dtype=np.int64)
            for i, key in enumerate(keys):
                pr = self.pairs[key]
                rank[i] = 2 if pr.state <= OK else (1 if pr.lite else 0)
                size[i] = self._obj[key[0]][1].nt + self._obj[key[1]][1].nt
            detail[np.lexsort((size, rank))[:self.live_detail]] = True
        return detail

    def _learn_cost(self, n, detail, dt):
        """Update how many pairs a live solve can afford to treat fully."""
        nd = n if detail is None else int(detail.sum())
        if nd:
            per = max(dt - self._lite_cost * (n - nd), 0.25 * dt) / nd
            self._detail_cost = per if self._detail_cost <= 0.0 else (
                0.6 * self._detail_cost + 0.4 * per)
        else:
            per = dt / n
            self._lite_cost = per if self._lite_cost <= 0.0 else 0.6 * self._lite_cost + 0.4 * per
            self._detail_cost *= 0.99        # so that detail is tried again now and then
        spare = LIVE_TARGET - self._lite_cost * n
        self.live_detail = int(min(64, max(0, spare / max(self._detail_cost, 1e-5))))

    # ----------------------------------------------------------- build volume
    def _pose_groups(self, items):
        """Split ``items`` (tuples starting with a slot) into runs whose poses
        fit the cache together."""
        room = self.pose_room()
        if room is None or not items:
            return [items] if items else []
        groups = []
        cur = []
        held = set()
        held_bytes = 0
        for item in items:
            ident = self._pose_ident(item[0])
            extra = 0 if ident in held else self._pose_cost(item[0])
            if cur and held_bytes + extra > room:
                groups.append(cur)
                cur = []
                held = set()
                held_bytes = 0
                extra = self._pose_cost(item[0])
            cur.append(item)
            held.add(ident)
            held_bytes += extra
        groups.append(cur)
        return groups

    def _update_oob(self):
        if self.volume is None:
            if self.oob or self.wall:
                self.oob.clear()
                self.wall.clear()
                self.version += 1
            self._oob_dirty.clear()
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
        # The root box of a pose is fitted to its vertices, so these are the
        # exact extents of each part: translation alone decides most cases.
        lo = self.O_LO[slots] + self.O_T[slots]
        hi = self.O_HI[slots] + self.O_T[slots]
        inside = ((lo >= vlo - e) & (hi <= vhi + e)).all(axis=1)
        outside = ((lo > vhi + e) | (hi < vlo - e)).any(axis=1)
        inner = self.inner_box()
        if inner is not None:
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
        partial = []
        close = []
        for i, slot in enumerate(cand):
            if near[i]:
                r = WallResult()
                r.dist = max(0.0, float(gap[i]))
                r.lo = lo[i]
                r.hi = hi[i]
                r.tris = None
                r.band_only = False
                # until the geometry is known: the middle of the side of the
                # part's bounding box that faces the nearest wall
                axis, high = int(which[i]) % 2, int(which[i]) >= 2
                r.center = 0.5 * (lo[i] + hi[i])
                r.center[axis] = hi[i][axis] if high else lo[i][axis]
                r.radius = 0.25 * float(np.linalg.norm(hi[i] - lo[i]))
                r.serial = self._serial
                self.wall[slot] = r
                close.append((slot, r, axis, high))
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
                partial.append((slot, r))
            self.oob[slot] = r
            changed = True
        for group in self._pose_groups(partial):
            self.need_poses([s for s, _ in group])
            ps = np.array([s for s, _ in group], dtype=np.int64)
            tris, band = narrow.oob_solve(self, ps, vlo, vhi, OOB_TRI_CAP)
            for (slot, r), t, b in zip(group, tris, band):
                r.tris = t
                r.band_only = b
        for group in self._pose_groups(close):
            # the triangles that reach into the margin: everything that is not
            # completely inside the inner box
            self.need_poses([c[0] for c in group])
            ps = np.array([c[0] for c in group], dtype=np.int64)
            tris, band = narrow.oob_solve(self, ps, inner[0], inner[1], OOB_TRI_CAP)
            for (slot, r, axis, high), t, b in zip(group, tris, band):
                r.tris = t
                r.band_only = b
                if len(t):
                    # mark the geometry that is inside the margin of the nearest wall
                    pts = t.reshape(-1, 3)
                    c = pts[:, axis]
                    sel = pts[c > inner[1][axis]] if high else pts[c < inner[0][axis]]
                    if len(sel):
                        r.center = sel.mean(axis=0, dtype=np.float64)
                        r.radius = max(0.5 * float(np.linalg.norm(sel.max(axis=0) - sel.min(axis=0))),
                                       self.wall_margin)
        if changed:
            self.version += 1

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
            'bytes': self.BOX.nbytes + self.TIDX.nbytes
            + sum(g.verts.nbytes for g in self._geoms.values()),
            'pose_bytes': self.pose_bytes,
            'cache_limit': self.cache_limit,
            'evictions': self.evictions,
            'flushes': self.flushes,
        }
