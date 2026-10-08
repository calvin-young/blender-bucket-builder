# SPDX-License-Identifier: GPL-3.0-or-later
"""Glue between Blender and the collision world.

A ``Monitor`` exists for every scene that has monitoring switched on.  It
mirrors the scene's visible mesh objects into a ``core.World`` and keeps it up
to date from three sources:

* ``depsgraph_update_post`` -- fires on every step of a modal transform, so
  moved objects are re-checked before the viewport redraws,
* a timer -- background work (first scan, exact refinement, a slow safety
  resync) in small time slices so the interface never blocks,
* undo / redo / file load -- full resync.

Taking in a mesh has an expensive part, sorting its triangles into a tree
(about half a microsecond per triangle).  For large meshes that is done by
worker threads, which only ever see NumPy arrays: Blender data is read, and
the scene is touched, on the main thread alone.

Blender data is only ever read here; the scene is never modified.
"""

import concurrent.futures
import hashlib
import os as _os
import time
from collections import deque

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import props
from .core import World, OK, CLEAR, COLLIDE, PARTIAL, OUTSIDE, bvh

_monitors = {}          # scene.session_uid -> Monitor
_timer_running = False
_pool = None            # worker threads, made when first needed
HOT_SECONDS = 0.35      # an object counts as "being edited" this long after a change
IDLE_SECONDS = 0.15     # quiet time before quick results get their exact pass
RESYNC_SECONDS = 1.5    # safety net: compare the scene with the mirror this often
LIVE_RESYNC_SECONDS = 0.25   # at most this often while something is being edited
SYNC_TRIS = 60000       # meshes up to this size are sorted at once, larger ones by a worker
MAX_INFLIGHT = 40000000  # triangles being sorted at a time (their arrays are in memory)
SCAN_SECONDS = 2.0      # the scene is searched for unchecked things at most this often
SCAN_INSTANCES = 20000  # instances looked at per search
NON_MESH = {'CURVE', 'SURFACE', 'FONT', 'META'}   # objects with faces that are not read
BIG_SKIPS = ('too many triangles', 'not enough memory', 'error')


class ObjState:
    __slots__ = ('uid', 'name', 'in_world', 'skipped', 'data_uid', 'shareable', 'ntri', 'sig',
                 'pending')

    def __init__(self, uid, name):
        self.uid = uid
        self.name = name
        self.in_world = False
        self.skipped = ''
        self.data_uid = None
        self.shareable = False
        self.ntri = 0
        self.sig = None
        self.pending = None       # key of the mesh it is waiting for (being sorted)


class _Job:
    """A mesh being sorted by a worker thread, and the objects waiting for it."""
    __slots__ = ('future', 'uids', 'ntri')

    def __init__(self, future, ntri):
        self.future = future
        self.uids = set()
        self.ntri = ntri


def _executor():
    global _pool
    if _pool is None:
        workers = max(1, min(4, (_os.cpu_count() or 2) - 1))
        _pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                                      thread_name_prefix='bucket_builder')
    return _pool


def _uid(idblock):
    try:
        return idblock.session_uid
    except AttributeError:      # very old versions
        return idblock.as_pointer()


def _matrix(obj):
    return np.array(obj.matrix_world, dtype=np.float64)


def _eligible(obj, view_layer):
    if obj.type != 'MESH' or obj.bucket_builder_ignore:
        return False
    try:
        return obj.visible_get(view_layer=view_layer)
    except Exception:
        return not obj.hide_viewport


def _signature(obj):
    """Cheap fingerprint of an object's geometry, to notice changes that did
    not come through the dependency graph (undo, scripts)."""
    me = obj.data
    bb = obj.bound_box
    return (_uid(me), len(me.vertices), len(me.polygons), len(obj.modifiers),
            round(bb[0][0], 5), round(bb[0][1], 5), round(bb[0][2], 5),
            round(bb[6][0], 5), round(bb[6][1], 5), round(bb[6][2], 5))


def read_mesh(obj_eval):
    """Evaluated geometry of an object as (float32 (nv, 3), int32 (nt, 3)).

    The arrays are copied out by Blender, not by a Python loop, and where
    possible straight from the attribute arrays: that is a plain memory copy,
    a hundred times faster than going through ``vertices`` and
    ``loop_triangles`` (5 M triangles: 1.3 s that way).  A mesh that consists
    of triangles only, which imported parts do, needs no triangulation: its
    face corners are its triangles.
    """
    me = obj_eval.to_mesh()
    try:
        nv = len(me.vertices)
        nf = len(me.polygons)
        if nv == 0 or nf == 0:
            return None
        co = np.empty(nv * 3, dtype=np.float32)
        try:
            me.attributes['position'].data.foreach_get('vector', co)
        except Exception:
            me.vertices.foreach_get("co", co)
        nl = len(me.loops)
        corner = None
        try:
            corner = np.empty(nl, dtype=np.int32)
            me.attributes['.corner_vert'].data.foreach_get('value', corner)
        except Exception:
            corner = None
        if nl == 3 * nf:
            if corner is None:
                corner = np.empty(nl, dtype=np.int32)
                me.loops.foreach_get("vertex_index", corner)
            idx = corner
        else:
            me.calc_loop_triangles()
            tris = me.loop_triangles
            nt = len(tris)
            if nt == 0:
                return None
            idx = np.empty(nt * 3, dtype=np.int32)
            if corner is None:
                tris.foreach_get("vertices", idx)
            else:
                tris.foreach_get("loops", idx)
                idx = np.take(corner, idx)
    finally:
        obj_eval.to_mesh_clear()
    return co.reshape(nv, 3), idx.reshape(-1, 3)


class Monitor:
    def __init__(self, scene):
        self.scene_uid = _uid(scene)
        self.scene_name = scene.name
        self.world = World()
        self.objs = {}            # uid -> ObjState
        self.queue = {}           # uid -> None: geometry to (re)read, in order
        self.queue_total = 0
        self.need_resync = True
        self.check_sigs = False
        self.params_dirty = True
        self.last_hot = 0.0
        self.last_resync = 0.0
        self.hot = {}             # world slot -> time of last live change
        self.mm_per_unit = 1.0
        self.unit_note = ''
        self.data_geom = {}       # mesh data uid -> geometry key (unmodified shared meshes)
        self.jobs = {}            # geometry key -> _Job (meshes being sorted)
        self.mem_limit = -1       # the cache limit last given to the world
        self.unchecked = {}       # what the scene holds that is not checked: kind -> count
        self.need_scan = True
        self.last_scan = 0.0
        self._faces = {}          # non-mesh object uid -> (signature, has faces)
        self.seen_version = -1
        self.move_serial = 0      # any transform change
        self.cold_serial = 0      # transform changes found by polling (not live edits)
        self.error = ''
        self.last_tick_ms = 0.0
        # recent cost of live updates and of drawing, in milliseconds (shown in
        # the Parts panel so slow scenes can be diagnosed)
        self.live_ms = deque(maxlen=240)
        self.draw_ms = deque(maxlen=240)
        self._problems = (None, [])
        self._auto_mm = None

    # ------------------------------------------------------------ parameters
    def _resolve_units(self, scene, st):
        us = scene.unit_settings
        # scale_length is single precision: 0.001 arrives as 0.0010000000475
        scale = float(f"{us.scale_length:.7g}") if us.system != 'NONE' else 1.0
        if st.unit_mode == 'MM':
            return 1.0, "1 unit = 1 mm"
        if st.unit_mode == 'SCENE':
            return scale * 1000.0, "scene units"
        if us.system != 'NONE' and abs(scale - 1.0) > 1e-9:
            return scale * 1000.0, "scene units"
        # Default scene scale: decide from the size of the parts.  Printable
        # parts measured in metres are well below 1 unit; millimetre numbers
        # are well above.
        w = self.world
        slots = list(w._slot_of.values())
        if slots and self._auto_mm is None:
            ext = (w.O_HI[slots] - w.O_LO[slots]).max(axis=1)
            self._auto_mm = 1.0 if float(np.median(ext)) > 1.5 else 1000.0
        if self._auto_mm is None:
            return 1.0, "1 unit = 1 mm"
        if self._auto_mm == 1.0:
            return 1.0, "1 unit = 1 mm (detected)"
        return 1000.0, "scene units, metres (detected)"

    def volume_box(self, st):
        """Build volume as (lo, hi) in scene units."""
        k = 1.0 / self.mm_per_unit
        size = np.array(st.volume_size, dtype=np.float64) * k
        off = np.array(st.volume_offset, dtype=np.float64) * k
        if st.volume_align == 'CORNER':
            lo = off
        elif st.volume_align == 'CENTER_XY':
            lo = off + np.array([-0.5 * size[0], -0.5 * size[1], 0.0])
        else:
            lo = off - 0.5 * size
        return lo, lo + size

    def margin_box(self, st):
        """The volume shrunk by the wall clearance in X and Y (scene units), or
        None while that check is off."""
        if not (st.use_volume and st.use_wall_clearance) or st.wall_clearance_mm <= 0.0:
            return None
        lo, hi = self.volume_box(st)
        m = np.array([st.wall_clearance_mm, st.wall_clearance_mm, 0.0]) / self.mm_per_unit
        mid = 0.5 * (lo + hi)
        return np.minimum(lo + m, mid), np.maximum(hi - m, mid)

    def apply_params(self, scene, st):
        self.params_dirty = False
        self.mm_per_unit, self.unit_note = self._resolve_units(scene, st)
        k = 1.0 / self.mm_per_unit
        w = self.world
        lo, hi = self.volume_box(st)
        size = float(np.abs(hi - lo).max())
        slots = list(w._slot_of.values())
        if slots:
            size = max(size, float((w.O_HI[slots] - w.O_LO[slots]).max()))
        w.set_scale(size, 0.5 * k)
        w.set_thresholds(st.collision_mm * k, st.clearance_mm * k if st.use_clearance else 0.0)
        w.set_detect_enclosed(st.detect_enclosed)
        if st.use_volume:
            w.set_volume(lo, hi, st.wall_clearance_mm * k if st.use_wall_clearance else 0.0)
        else:
            w.set_volume(None, None)
        limit = props.memory_limit()
        if limit != self.mem_limit:
            self.mem_limit = limit
            w.set_cache_limit(limit)

    # ----------------------------------------------------------------- sync
    def _remove(self, uid):
        os = self.objs.pop(uid, None)
        self.queue.pop(uid, None)
        if os is not None and os.in_world:
            self.hot.pop(self.world.slot(uid), None)
            self.world.remove_object(uid)

    def _waiting(self):
        """Objects whose geometry is not in the world yet."""
        return len(self.queue) + sum(len(j.uids) for j in self.jobs.values())

    def _count_waiting(self):
        n = self._waiting()
        self.queue_total = max(self.queue_total, n) if n else 0

    def resync(self, depsgraph, view_layer):
        """Compare every object of the view layer with the mirror."""
        self.need_resync = False
        self.last_resync = time.perf_counter()
        check_sigs = self.check_sigs
        self.check_sigs = False
        seen = set()
        w = self.world
        for obj in view_layer.objects:
            if not _eligible(obj, view_layer):
                continue
            uid = _uid(obj)
            seen.add(uid)
            os = self.objs.get(uid)
            if os is None:
                os = ObjState(uid, obj.name)
                self.objs[uid] = os
                self.queue[uid] = None
                continue
            os.name = obj.name
            if not os.in_world:
                if check_sigs and uid not in self.queue and not os.skipped:
                    self.queue[uid] = None
                continue
            if check_sigs and uid not in self.queue and _signature(obj) != os.sig:
                self.data_geom.pop(os.data_uid, None)
                self.queue[uid] = None
            ob_eval = obj.evaluated_get(depsgraph)
            if w.set_matrix(uid, _matrix(ob_eval), hot=False):
                self.move_serial += 1
                self.cold_serial += 1
        for uid in [u for u in self.objs if u not in seen]:
            self._remove(uid)
        self._count_waiting()

    def note_update(self, obj_eval, transform, geometry, now):
        """A dependency-graph update for one object (called from the handler)."""
        try:
            obj = obj_eval.original
        except AttributeError:
            obj = obj_eval
        uid = _uid(obj)
        os = self.objs.get(uid)
        if os is None:
            if obj.type == 'MESH':
                self.need_resync = True
            self.need_scan = True
            return
        if geometry:
            self.data_geom.pop(os.data_uid, None)
            self.queue[uid] = None
            self._count_waiting()
            self.need_scan = True
        if transform and os.in_world:
            if self.world.set_matrix(uid, _matrix(obj_eval), hot=True):
                self.hot[self.world.slot(uid)] = now
                self.last_hot = now
                self.move_serial += 1

    def _too_big(self, nv, nt, max_tris):
        """Why a mesh of this size is left out, or ''."""
        if nt > max_tris:
            return 'too many triangles'
        limit = self.mem_limit
        if limit is not None and limit > 0 and (
                World.geom_size(nv, nt) + World.pose_size(nv, nt) > 0.5 * limit):
            return 'not enough memory'
        return ''

    def _load_geometry(self, obj, depsgraph, max_tris, now):
        """Read one object's evaluated mesh and put it in the world, or hand
        it to a worker thread to be sorted first."""
        uid = _uid(obj)
        os = self.objs[uid]
        w = self.world
        me = obj.data
        os.data_uid = _uid(me)
        os.shareable = (len(obj.modifiers) == 0 and me.shape_keys is None)
        os.pending = None
        ob_eval = obj.evaluated_get(depsgraph)
        key = self.data_geom.get(os.data_uid) if os.shareable else None
        if key is not None and not w.has_geom(key) and key not in self.jobs:
            key = None
        os.skipped = ''
        if key is None:
            data = read_mesh(ob_eval)
            if data is None:
                os.skipped = 'no faces'
            else:
                co, tri = data
                os.skipped = self._too_big(co.shape[0], tri.shape[0], max_tris)
                if not os.skipped:
                    h = hashlib.sha1()
                    h.update(co)
                    h.update(tri)
                    key = h.digest()
                    os.ntri = int(tri.shape[0])
                    if os.shareable:
                        self.data_geom[os.data_uid] = key
                    if not w.has_geom(key) and key not in self.jobs:
                        if tri.shape[0] <= SYNC_TRIS:
                            w.add_geom(key, co, tri)
                        else:
                            self.jobs[key] = _Job(_executor().submit(bvh.sort_mesh, co, tri),
                                                  int(tri.shape[0]))
        if key is None:
            if os.in_world:
                self.hot.pop(w.slot(uid), None)
                w.remove_object(uid)
                os.in_world = False
            return
        job = self.jobs.get(key)
        if job is not None:
            # put in when the worker is done (see _collect_jobs)
            job.uids.add(uid)
            os.pending = key
            return
        self._install(obj, os, key, ob_eval, now)
        w.drop_unused_geoms()

    def _install(self, obj, os, key, ob_eval, now):
        """Give an object its (sorted) geometry in the world."""
        w = self.world
        os.sig = _signature(obj)
        if os.in_world:
            if w.set_geometry(os.uid, key):
                self.hot[w.slot(os.uid)] = now
                self.last_hot = now
        else:
            w.add_object(os.uid, key, _matrix(ob_eval))
            os.in_world = True

    def _collect_jobs(self, view_layer, depsgraph, now):
        """Take over the meshes the worker threads have finished sorting."""
        done = [(key, job) for key, job in self.jobs.items() if job.future.done()]
        if not done:
            return 0
        by_uid = None
        for key, job in done:
            del self.jobs[key]
            waiting = [u for u in job.uids if u in self.objs and self.objs[u].pending == key]
            try:
                verts, tris = job.future.result()
            except Exception as ex:
                for uid in waiting:
                    self.objs[uid].pending = None
                    self.objs[uid].skipped = 'error'
                self.error = f'sorting a mesh failed: {ex}'
                continue
            if not waiting:
                continue                  # nobody wants it any more
            self.world.add_sorted(key, verts, tris)
            if by_uid is None:
                by_uid = {_uid(o): o for o in view_layer.objects if o.type == 'MESH'}
            for uid in waiting:
                os = self.objs[uid]
                os.pending = None
                obj = by_uid.get(uid)
                if obj is None:
                    self.need_resync = True       # object left the view layer
                    continue
                try:
                    self._install(obj, os, key, obj.evaluated_get(depsgraph), now)
                except Exception as ex:
                    os.skipped = 'error'
                    self.error = f'{os.name}: {ex}'
        self.world.drop_unused_geoms()
        self._count_waiting()
        return len(done)

    def wait_jobs(self, timeout=None):
        """Block until the worker threads are done (for tests and scripts;
        the results are taken over by the next ``tick``)."""
        if self.jobs:
            concurrent.futures.wait([j.future for j in self.jobs.values()], timeout=timeout)

    def process_queue(self, view_layer, depsgraph, deadline, max_tris, now):
        """Read queued geometry until the deadline (always at least one)."""
        done = 0
        by_uid = {_uid(o): o for o in view_layer.objects if o.type == 'MESH'}
        for uid in list(self.queue):
            os = self.objs.get(uid)
            obj = by_uid.get(uid)
            if os is None or obj is None:
                del self.queue[uid]
                if os is not None:
                    self.need_resync = True      # object left the view layer
                continue
            if obj.data is None or obj.data.is_editmode:
                continue            # wait until edit mode is left
            if self.jobs and sum(j.ntri for j in self.jobs.values()) > MAX_INFLIGHT:
                break               # the workers have enough for now
            del self.queue[uid]
            try:
                self._load_geometry(obj, depsgraph, max_tris, now)
            except Exception as ex:      # never let one odd object stop the monitor
                os.skipped = 'error'
                self.error = f'{os.name}: {ex}'
            done += 1
            if time.perf_counter() > deadline:
                break
        self._count_waiting()
        return done

    # ------------------------------------------------------- what is not checked
    def _has_faces(self, obj, depsgraph):
        """True if an object that is not a mesh would come out as one with faces."""
        bb = obj.bound_box
        sig = (_uid(obj.data) if obj.data is not None else 0,
               round(bb[0][0], 5), round(bb[0][1], 5), round(bb[0][2], 5),
               round(bb[6][0], 5), round(bb[6][1], 5), round(bb[6][2], 5))
        uid = _uid(obj)
        hit = self._faces.get(uid)
        if hit is not None and hit[0] == sig:
            return hit[1]
        faces = False
        try:
            ob_eval = obj.evaluated_get(depsgraph)
            me = ob_eval.to_mesh()
            try:
                faces = me is not None and len(me.polygons) > 0
            finally:
                ob_eval.to_mesh_clear()
        except Exception:
            faces = False
        self._faces[uid] = (sig, faces)
        return faces

    def scan_unchecked(self, depsgraph, view_layer):
        """Count what the scene shows but the check does not cover.  Only real
        mesh data of mesh objects is read, so that leaves out instances
        (from geometry nodes that do not realize them, or collection
        instances) and objects that are not meshes.  Returns True if the
        counts changed."""
        self.need_scan = False
        self.last_scan = time.perf_counter()
        parts = set()
        collections = set()
        try:
            n = 0
            for inst in depsgraph.object_instances:
                if not inst.is_instance:
                    continue
                n += 1
                if n > SCAN_INSTANCES:
                    break
                parent = inst.parent
                if parent is None or inst.object.type != 'MESH':
                    continue
                po = parent.original
                if po.bucket_builder_ignore:
                    continue
                uid = _uid(po)
                if uid in self.objs:
                    parts.add(uid)
                elif po.type == 'EMPTY':
                    collections.add(uid)
        except Exception:
            pass
        other = 0
        seen = set()
        for obj in view_layer.objects:
            if obj.type not in NON_MESH or obj.bucket_builder_ignore:
                continue
            try:
                if not obj.visible_get(view_layer=view_layer):
                    continue
            except Exception:
                continue
            seen.add(_uid(obj))
            if self._has_faces(obj, depsgraph):
                other += 1
        for uid in [u for u in self._faces if u not in seen]:
            del self._faces[uid]
        new = {k: v for k, v in (('instances', len(parts)), ('collections', len(collections)),
                                 ('other', other)) if v}
        if new != self.unchecked:
            self.unchecked = new
            return True
        return False

    # ----------------------------------------------------------------- tick
    def tick(self, scene, depsgraph, view_layer, live):
        """One update slice.  ``live`` is True when called from the dependency
        graph handler in the middle of an edit: then only the changed objects
        are handled, quickly; background work is left to the timer."""
        st = props.settings(scene)
        p = props.prefs()
        budget = (p.budget_ms if p else 12.0) / 1000.0
        max_tris = int((p.max_tris_millions if p else 8.0) * 1e6)
        t0 = time.perf_counter()
        now = t0
        changed = False
        if self.need_resync and not (live and now - self.last_resync < LIVE_RESYNC_SECONDS):
            self.resync(depsgraph, view_layer)
        if self.params_dirty:
            self.apply_params(scene, st)
        had = len(self.world._slot_of)
        if self.jobs and self._collect_jobs(view_layer, depsgraph, now):
            changed = True
        if self.queue and not (live and now - self.last_hot < HOT_SECONDS and self._only_new()):
            self.process_queue(view_layer, depsgraph, t0 + budget, max_tris, now)
        if had == 0 and self.world._slot_of:
            self.apply_params(scene, st)        # units may now be detectable
        idle = (not live) and (time.perf_counter() - self.last_hot) > IDLE_SECONDS
        self.world.step(budget=budget, hot_budget=max(0.016, budget), idle=idle)
        if self.need_scan and idle and time.perf_counter() - self.last_scan > SCAN_SECONDS:
            if self.scan_unchecked(depsgraph, view_layer):
                changed = True
        self.last_tick_ms = (time.perf_counter() - t0) * 1000.0
        cutoff = now - HOT_SECONDS
        if self.hot:
            for slot in [s for s, t in self.hot.items() if t < cutoff]:
                del self.hot[slot]
        if self.world.version != self.seen_version:
            self.seen_version = self.world.version
            return True
        return changed

    def settle(self, scene, depsgraph, view_layer, timeout=300.0):
        """Run background slices until nothing is left to do, waiting for the
        worker threads as needed.  For tests and scripts: in the interface
        the timer does this, a slice at a time.  Returns True if it got
        there."""
        end = time.perf_counter() + timeout
        wait = self.last_hot + IDLE_SECONDS - time.perf_counter()
        if wait > 0.0:
            time.sleep(wait + 0.01)
        while self.busy and time.perf_counter() < end:
            self.tick(scene, depsgraph, view_layer, live=False)
            if self.jobs:
                self.wait_jobs(0.02)
            elif self.need_scan and not (self.queue or self.need_resync or self.params_dirty
                                         or self.world.busy):
                self.scan_unchecked(depsgraph, view_layer)
        return not self.busy

    def _only_new(self):
        """True if the geometry queue holds only objects not yet in the world
        (first scan), which can wait while something is being dragged."""
        for uid in self.queue:
            os = self.objs.get(uid)
            if os is not None and os.in_world:
                return False
        return True

    @property
    def busy(self):
        return (bool(self.queue) or bool(self.jobs) or self.need_resync or self.params_dirty
                or self.need_scan or self.world.busy)

    # -------------------------------------------------------------- reports
    def name_of(self, slot):
        uid = self.world.uid(slot)
        os = self.objs.get(uid)
        return os.name if os else '?'

    def status(self):
        """Summary for the panel and the viewport badge."""
        w = self.world
        nc, ncl, npart, nout, nwall = w.counts()
        skipped = sum(1 for o in self.objs.values() if o.skipped in BIG_SKIPS)
        waiting = self._waiting()
        return {
            'objects': w.object_count,
            'collisions': nc,
            'clearance': ncl,
            'partly_out': npart,
            'outside': nout,
            'near_wall': nwall,
            'skipped': skipped,
            'volume': w.volume is not None,
            # the verdict is final once every part is read and every pair has a
            # result; exact-distance refinement and the periodic resync that
            # may still be queued do not make it provisional
            'busy': bool(self.queue) or bool(self.jobs) or self.params_dirty or w.unsettled,
            'preparing': (max(self.queue_total - waiting, 0), self.queue_total) if waiting else None,
            'unchecked': self.unchecked,
            'pending': len(w._pend_hot) + len(w._pend_cold),
            'refining': len(w._pend_refine),
            'clearance_on': w.clear_thr > 0.0,
        }

    def problems(self):
        """Sorted list of problems (worst first) for the panel and navigation."""
        ver = (self.world.version, self.move_serial)
        if self._problems[0] == ver:
            return self._problems[1]
        w = self.world
        k = self.mm_per_unit
        out = []
        for (a, b), pr in w.viol.items():
            if pr.center is None:
                continue
            out.append({
                'kind': 'COLLIDE' if pr.state == COLLIDE else 'CLEAR',
                'a': self.name_of(a), 'b': self.name_of(b),
                'dist_mm': pr.dist * k,
                'center': np.asarray(pr.center) + w.O_T[a],
                'radius': float(pr.radius),
                'approx': pr.approx or pr.refine,
                'inside': pr.enclosed,
                'key': ('P', w.uid(a), w.uid(b)),
            })
        for slot, r in w.oob.items():
            c = 0.5 * (r.lo + r.hi)
            out.append({
                'kind': 'PARTIAL' if r.state == PARTIAL else 'OUTSIDE',
                'a': self.name_of(slot), 'b': '',
                'dist_mm': 0.0,
                'center': c,
                'radius': 0.5 * float(np.linalg.norm(r.hi - r.lo)),
                'approx': False,
                'inside': 0,
                'key': ('V', w.uid(slot)),
            })
        for slot, r in w.wall.items():
            out.append({
                'kind': 'WALL',
                'a': self.name_of(slot), 'b': '',
                'dist_mm': r.dist * k,
                'center': r.center,
                'radius': float(r.radius),
                'approx': False,
                'inside': 0,
                'key': ('W', w.uid(slot)),
            })
        rank = {'COLLIDE': 0, 'PARTIAL': 1, 'OUTSIDE': 2, 'CLEAR': 3, 'WALL': 4}
        out.sort(key=lambda d: (rank[d['kind']], d['dist_mm'], d['a'], d['b']))
        self._problems = (ver, out)
        return out


# ---------------------------------------------------------------------------
# module-level plumbing
# ---------------------------------------------------------------------------

def get(scene, create=False):
    if scene is None:
        return None
    uid = _uid(scene)
    mon = _monitors.get(uid)
    if mon is None and create:
        mon = Monitor(scene)
        _monitors[uid] = mon
    return mon


def tag_redraw_all():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for win in wm.windows:
        screen = win.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def on_enabled_changed(scene):
    if scene is None:
        return
    st = props.settings(scene)
    if st.enabled:
        get(scene, create=True)
        _ensure_timer()
    else:
        _monitors.pop(_uid(scene), None)
    tag_redraw_all()


def on_settings_changed(scene):
    mon = get(scene)
    if mon is not None:
        mon.params_dirty = True
        mon.need_resync = True
        mon.need_scan = True
        _ensure_timer()
    tag_redraw_all()


def on_prefs_changed():
    """A preference that the monitors act on was edited."""
    for mon in _monitors.values():
        mon.params_dirty = True
    if _monitors:
        _ensure_timer()
    tag_redraw_all()


def force_recheck(scene):
    """Throw the mirror away and start again (the "Recheck" button)."""
    if scene is None:
        return
    _monitors.pop(_uid(scene), None)
    if props.settings(scene).enabled:
        get(scene, create=True)
        _ensure_timer()
    tag_redraw_all()


def _window_for(scene):
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return None
    for win in wm.windows:
        if win.scene == scene:
            return win
    return None


@persistent
def _on_depsgraph_update(scene, depsgraph):
    st = props.settings(scene)
    if st is None or not st.enabled:
        return
    try:
        if depsgraph.mode != 'VIEWPORT':
            return
    except AttributeError:
        pass
    mon = get(scene, create=True)
    now = time.perf_counter()
    try:
        for upd in depsgraph.updates:
            idd = upd.id
            if isinstance(idd, bpy.types.Object):
                tr = upd.is_updated_transform
                ge = upd.is_updated_geometry
                if tr or ge:
                    mon.note_update(idd, tr, ge, now)
            elif isinstance(idd, (bpy.types.Scene, bpy.types.Collection)):
                mon.need_resync = True
                mon.need_scan = True
        view_layer = depsgraph.view_layer
        if mon.tick(scene, depsgraph, view_layer, live=True):
            tag_redraw_all()
        if mon.busy:
            _ensure_timer()
        if mon.hot:
            mon.live_ms.append((time.perf_counter() - now) * 1000.0)
    except Exception as ex:          # a handler must never raise into Blender
        mon.error = str(ex)
        import traceback
        traceback.print_exc()


@persistent
def _on_undo_redo(scene, *_args):
    for mon in _monitors.values():
        mon.need_resync = True
        mon.need_scan = True
        mon.check_sigs = True
        mon.data_geom.clear()
    _ensure_timer()


@persistent
def _on_frame_change(scene, *_args):
    mon = get(scene)
    if mon is not None:
        mon.need_resync = True
        _ensure_timer()


@persistent
def _on_load_post(*_args):
    _monitors.clear()
    _ensure_timer()


def _timer():
    """Background slice: runs often while there is work, slowly otherwise."""
    global _timer_running
    try:
        interval = 0.5
        any_enabled = False
        now = time.perf_counter()
        for scene in bpy.data.scenes:
            st = props.settings(scene)
            if st is None or not st.enabled:
                _monitors.pop(_uid(scene), None)
                continue
            any_enabled = True
            win = _window_for(scene)
            if win is None:
                continue
            mon = get(scene, create=True)
            view_layer = win.view_layer
            depsgraph = view_layer.depsgraph
            quiet = now - mon.last_hot > HOT_SECONDS
            if quiet and now - mon.last_resync > max(RESYNC_SECONDS, 2e-5 * 50 * len(mon.objs)):
                mon.need_resync = True
            had_hot = bool(mon.hot)
            changed = mon.tick(scene, depsgraph, view_layer, live=False)
            if changed or (had_hot and not mon.hot):
                tag_redraw_all()
            if mon.busy:
                interval = min(interval, 0.02)
            elif mon.hot:
                interval = min(interval, 0.1)
        # monitors of scenes that no longer exist
        alive = {_uid(s) for s in bpy.data.scenes}
        for uid in [u for u in _monitors if u not in alive]:
            del _monitors[uid]
        if not any_enabled:
            _timer_running = False
            return None
        return interval
    except Exception:
        import traceback
        traceback.print_exc()
        return 1.0


def _ensure_timer():
    global _timer_running
    if _timer_running and bpy.app.timers.is_registered(_timer):
        return
    _timer_running = True
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.01, persistent=True)


_HANDLERS = (
    ('depsgraph_update_post', _on_depsgraph_update),
    ('undo_post', _on_undo_redo),
    ('redo_post', _on_undo_redo),
    ('frame_change_post', _on_frame_change),
    ('load_post', _on_load_post),
)


def register():
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)
    _ensure_timer()


def unregister():
    global _timer_running, _pool
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer_running = False
    _monitors.clear()
    if _pool is not None:
        # a sort that is running finishes on its own; nothing waits for it
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
