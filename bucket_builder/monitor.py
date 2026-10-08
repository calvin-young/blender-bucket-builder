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

Blender data is only ever read here; the scene is never modified.
"""

import hashlib
import time
from collections import deque

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import props
from .core import World, OK, CLEAR, COLLIDE, PARTIAL, OUTSIDE

_monitors = {}          # scene.session_uid -> Monitor
_timer_running = False
HOT_SECONDS = 0.35      # an object counts as "being edited" this long after a change
IDLE_SECONDS = 0.15     # quiet time before quick results get their exact pass
RESYNC_SECONDS = 1.5    # safety net: compare the scene with the mirror this often
LIVE_RESYNC_SECONDS = 0.25   # at most this often while something is being edited


class ObjState:
    __slots__ = ('uid', 'name', 'in_world', 'skipped', 'data_uid', 'shareable', 'ntri', 'sig')

    def __init__(self, uid, name):
        self.uid = uid
        self.name = name
        self.in_world = False
        self.skipped = ''
        self.data_uid = None
        self.shareable = False
        self.ntri = 0
        self.sig = None


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

    Uses ``foreach_get`` so the copy is done by Blender, not by a Python loop.
    """
    me = obj_eval.to_mesh()
    try:
        nv = len(me.vertices)
        if nv == 0 or len(me.polygons) == 0:
            return None
        co = np.empty(nv * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        me.calc_loop_triangles()
        tris = me.loop_triangles
        nt = len(tris)
        if nt == 0:
            return None
        idx = np.empty(nt * 3, dtype=np.int32)
        tris.foreach_get("vertices", idx)
    finally:
        obj_eval.to_mesh_clear()
    return co.reshape(nv, 3), idx.reshape(nt, 3)


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

    # ----------------------------------------------------------------- sync
    def _remove(self, uid):
        os = self.objs.pop(uid, None)
        self.queue.pop(uid, None)
        if os is not None and os.in_world:
            self.hot.pop(self.world.slot(uid), None)
            self.world.remove_object(uid)

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
        self.queue_total = max(self.queue_total, len(self.queue))
        if not self.queue:
            self.queue_total = 0

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
            return
        if geometry:
            self.data_geom.pop(os.data_uid, None)
            self.queue[uid] = None
            self.queue_total = max(self.queue_total, len(self.queue))
        if transform and os.in_world:
            if self.world.set_matrix(uid, _matrix(obj_eval), hot=True):
                self.hot[self.world.slot(uid)] = now
                self.last_hot = now
                self.move_serial += 1

    def _load_geometry(self, obj, depsgraph, max_tris, now):
        """Read one object's evaluated mesh and put it in the world."""
        uid = _uid(obj)
        os = self.objs[uid]
        w = self.world
        me = obj.data
        os.data_uid = _uid(me)
        os.shareable = (len(obj.modifiers) == 0 and me.shape_keys is None)
        ob_eval = obj.evaluated_get(depsgraph)
        key = self.data_geom.get(os.data_uid) if os.shareable else None
        if key is not None and not w.has_geom(key):
            key = None
        os.skipped = ''
        if key is None:
            data = read_mesh(ob_eval)
            if data is None:
                os.skipped = 'no faces'
            else:
                co, tri = data
                if tri.shape[0] > max_tris:
                    os.skipped = 'too many triangles'
                else:
                    h = hashlib.blake2b(digest_size=16)
                    h.update(co)
                    h.update(tri)
                    key = h.digest()
                    if not w.has_geom(key):
                        w.add_geom(key, co, tri)
                    os.ntri = int(tri.shape[0])
                    if os.shareable:
                        self.data_geom[os.data_uid] = key
        if key is None:
            if os.in_world:
                self.hot.pop(w.slot(uid), None)
                w.remove_object(uid)
                os.in_world = False
            return
        os.sig = _signature(obj)
        if os.in_world:
            if w.set_geometry(uid, key):
                self.hot[w.slot(uid)] = now
                self.last_hot = now
        else:
            w.add_object(uid, key, _matrix(ob_eval))
            os.in_world = True
        w.drop_unused_geoms()

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
            del self.queue[uid]
            try:
                self._load_geometry(obj, depsgraph, max_tris, now)
            except Exception as ex:      # never let one odd object stop the monitor
                os.skipped = 'error'
                self.error = f'{os.name}: {ex}'
            done += 1
            if time.perf_counter() > deadline:
                break
        if not self.queue:
            self.queue_total = 0
        return done

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
        if self.need_resync and not (live and now - self.last_resync < LIVE_RESYNC_SECONDS):
            self.resync(depsgraph, view_layer)
        if self.params_dirty:
            self.apply_params(scene, st)
        if self.queue and not (live and now - self.last_hot < HOT_SECONDS and self._only_new()):
            had = len(self.world._slot_of)
            self.process_queue(view_layer, depsgraph, t0 + budget, max_tris, now)
            if had == 0 and self.world._slot_of:
                self.apply_params(scene, st)        # units may now be detectable
        idle = (not live) and (time.perf_counter() - self.last_hot) > IDLE_SECONDS
        self.world.step(budget=budget, hot_budget=max(0.016, budget), idle=idle)
        self.last_tick_ms = (time.perf_counter() - t0) * 1000.0
        cutoff = now - HOT_SECONDS
        if self.hot:
            for slot in [s for s, t in self.hot.items() if t < cutoff]:
                del self.hot[slot]
        if self.world.version != self.seen_version:
            self.seen_version = self.world.version
            return True
        return False

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
        return bool(self.queue) or self.need_resync or self.params_dirty or self.world.busy

    # -------------------------------------------------------------- reports
    def name_of(self, slot):
        uid = self.world.uid(slot)
        os = self.objs.get(uid)
        return os.name if os else '?'

    def status(self):
        """Summary for the panel and the viewport badge."""
        w = self.world
        nc, ncl, npart, nout, nwall = w.counts()
        skipped = sum(1 for o in self.objs.values() if o.skipped)
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
            'busy': bool(self.queue) or self.params_dirty or w.unsettled,
            'preparing': (self.queue_total - len(self.queue), self.queue_total) if self.queue else None,
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
    global _timer_running
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer_running = False
    _monitors.clear()
