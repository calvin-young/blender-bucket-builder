# SPDX-License-Identifier: GPL-3.0-or-later
"""The build report of the sidebar: density, packed height, layer profile and
the warnings that go with them (docs/AUTOPACK.md section 6).

It is worked out from what the monitor already has: the meshes in the world
and the parts' matrices.  The arithmetic is `pack.metrics`, in a worker thread
(the main thread only collects the parts and copies meshes it has not seen
before).  Results are cached per mesh, rotation and height, so a part that is
moved sideways costs nothing and one that is lifted costs one pass over its
mesh; a part that is turned costs a pass too.

No `bpy` here: the monitor owns one `BuildReport` and the panel reads it.
"""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .pack import metrics, plot, quality

BAND = 1.0                  # mm, layer band of the profile
SMOOTH = 5.0                # mm, window of the spread
BATCH_TRIANGLES = 8_000_000     # meshes copied for one job
CACHE_LIMIT = 20000         # results kept


class _Mesh:
    """Stands in for a mesh: a key and a size.  The arrays are copied from the
    world only for a job that has to read them."""
    __slots__ = ('key', 'nt', 'verts', 'tris')

    def __init__(self, key, nt):
        self.key, self.nt, self.verts, self.tris = key, nt, None, None


def kind_was_final(rep):
    """Throttling only holds back a new plan after a result; the batches of one
    analysis follow each other directly."""
    return rep.kind != 'warm' or rep.future is not None


class BuildReport:
    def __init__(self):
        self.cache = {}
        self.faces = {}               # (mesh key, linear part) -> pack.quality.Faces or None
        self.result = None            # metrics.Report, in mm
        self.result_sig = None
        self.incomplete = False       # parts were still being read when it was made
        self.serial = 0               # grows whenever `result` is replaced
        self.error = ''
        self.future = None
        self.kind = None              # 'warm' or 'final'
        self.job_sig = None
        self.job_incomplete = False
        self.guard = None
        self.dirty = True
        self.last_ms = 0.0
        self.t_job = 0.0
        self.pixels = None            # the profile picture
        self._ex = None
        self.workers = max(1, metrics.default_workers() - 1)

    # ------------------------------------------------------------ state
    @property
    def working(self):
        return self.future is not None or self.dirty or self.result_sig != self.guard_sig

    guard_sig = None
    not_before = 0.0

    @property
    def pending(self):
        """Something is going on or due: the timer must come back soon."""
        return self.future is not None or self.dirty

    def close(self):
        """The monitor is being thrown away."""
        self.cache.clear()
        if self._ex is not None:
            self._ex.shutdown(wait=False, cancel_futures=True)
            self._ex = None
        self.future = None
        self.result = None
        self.pixels = None

    def invalidate(self):
        self.dirty = True

    # ------------------------------------------------------------ collecting
    @staticmethod
    def _collect(mon, st):
        w = mon.world
        s = mon.mm_per_unit
        lo, hi = mon.volume_box(st)
        vlo, vhi = np.asarray(lo, float) * s, np.asarray(hi, float) * s
        stubs, first, parts = {}, {}, []
        for slot in w._slot_of.values():
            key, nt = w.part_key(slot)
            m = stubs.get(key)
            if m is None:
                m = stubs[key] = _Mesh(key, nt)
                first[key] = slot
            M = w.part_matrix(slot)
            M[:3, :] *= s                    # scene units -> mm (the mesh stays in units)
            parts.append((mon.name_of(slot), m, M))
        return parts, vlo, vhi, first

    @staticmethod
    def _signature(parts, vlo, vhi, incomplete):
        h = hashlib.blake2b(digest_size=16)
        h.update(np.asarray([vlo, vhi]).tobytes())
        h.update(b'1' if incomplete else b'0')
        for name, m, M in parts:
            h.update(repr(m.key).encode())
            h.update(name.encode('utf-8', 'replace'))
            h.update(M.tobytes())
        return h.digest()

    # ------------------------------------------------------------ the step
    def step(self, mon, st):
        """Called from the monitor's tick.  Returns True when `result`
        changed (the panel needs a redraw)."""
        changed = False
        if self.future is not None:
            if not self.future.done():
                return False
            fut, kind, sig = self.future, self.kind, self.job_sig
            self.future = None
            self.last_ms = (time.perf_counter() - self.t_job) * 1000.0
            try:
                res = fut.result()
            except Exception as ex:             # shown in the panel; try again when something moves
                self.error = f'{type(ex).__name__}: {ex}'
                self.result_sig = None
                self.dirty = False
                self.guard = self._guard(mon, st)
                return True
            self.error = ''
            if kind == 'final':
                self.result = res
                self.result_sig = sig
                self.incomplete = self.job_incomplete
                self.serial += 1
                self.pixels = plot.profile_pixels(res)
                changed = True
            else:
                self.dirty = True                # more to read, or the final pass
        guard = self._guard(mon, st)
        if not self.dirty and guard == self.guard:
            return changed
        if mon.paused:
            return changed
        now = time.perf_counter()
        if now < self.not_before and kind_was_final(self):
            return changed                      # a drag: not more often than the last plan allows
        self.guard = guard
        self.dirty = False
        self._plan(mon, st)
        # collecting the parts is main-thread work: leave room for the user
        self.not_before = now + max(0.2, 4.0 * (time.perf_counter() - now))
        return changed

    @staticmethod
    def _guard(mon, st):
        w = mon.world
        return (w.version, mon.move_serial, w.object_count, mon.mm_per_unit, bool(mon.queue),
                tuple(st.volume_size), tuple(st.volume_offset), st.volume_align)

    def _plan(self, mon, st):
        parts, vlo, vhi, first = self._collect(mon, st)
        incomplete = bool(mon.queue)
        sig = self._signature(parts, vlo, vhi, incomplete)
        self.guard_sig = sig
        if not parts:
            self.result = metrics.measure([], vlo, vhi, BAND, SMOOTH)
            self.result_sig = sig
            self.incomplete = incomplete
            self.pixels = None
            self.serial += 1
            return
        if sig == self.result_sig:
            return
        if len(self.cache) > CACHE_LIMIT:
            self.cache.clear()
            self.faces.clear()
        nb = metrics.n_bands(vlo, vhi, BAND)
        miss, seen = [], set()
        for i, (name, m, M) in enumerate(parts):
            if m.nt == 0:
                continue
            ck = (m.key, M[:3, :3].tobytes(), float(M[2, 3]))
            if ck in seen:
                continue
            seen.add(ck)
            if metrics.missing(self.cache, m, M[:3, :3], M[2, 3], vlo[2], BAND, nb):
                miss.append(i)
        if self._ex is None:
            self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bucket_builder_report')
        w = mon.world
        self.t_job = time.perf_counter()
        self.job_sig = sig
        self.job_incomplete = incomplete
        if miss:
            by_key = {}
            for i in miss:
                by_key.setdefault(parts[i][1].key, []).append(i)
            batch, total = [], 0
            for key, idx in by_key.items():
                m = parts[idx[0]][1]
                if batch and total + m.nt > BATCH_TRIANGLES:
                    break
                _, v, t = w.part_mesh(first[key])
                m.verts, m.tris = np.array(v), np.array(t)      # copies: the world's storage may move
                total += m.nt
                batch += [parts[i] for i in idx]
            self.kind = 'warm'
            self.future = self._ex.submit(_warm, batch, vlo, vhi, self.workers, self.cache, self.faces)
        else:
            self.kind = 'final'
            self.future = self._ex.submit(_final, parts, vlo, vhi, self.workers, self.cache, self.faces)

    def wait(self, mon, st, timeout=300.0):
        """Block until the report matches the build (for tests and scripts)."""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            self.step(mon, st)
            if self.future is None and not self.dirty and self.guard == self._guard(mon, st) \
                    and self.result_sig == self.guard_sig:
                return True
            time.sleep(0.01)
        return False


def _face_key(m, M):
    return (m.key, np.asarray(M, float)[:3, :3].tobytes())


def _warm(batch, vlo, vhi, workers, cache, faces):
    """A batch of meshes read for the first time: their volume per layer,
    and their large flat faces (worker thread)."""
    out = metrics.measure_parts(batch, vlo, vhi, BAND, None, True, workers, cache)
    for name, m, M in batch:
        k = _face_key(m, M)
        if k not in faces and m.verts is not None:
            f = quality.flat_faces(m.verts, m.tris, np.asarray(M, float)[:3, :3])
            faces[k] = f if len(f) else None
    return out


def _final(parts, vlo, vhi, workers, cache, faces):
    """The report of the whole build (worker thread), with the flat-face
    rules' counts for the parts whose faces are known."""
    res = metrics.measure(parts, vlo, vhi, BAND, SMOOTH, workers, cache)
    placed = []
    for n, (name, m, M) in enumerate(parts):
        f = faces.get(_face_key(m, M))
        if f is not None:
            placed.append((n, f, np.asarray(M, float)[:3, 3]))
    res.flat = quality.assess(placed) if placed else (0, 0.0, 0, 0.0)
    return res


def rows(rep):
    """What the panel shows as (label, value, tone): tone '' or 'warn'."""
    if rep is None or rep.n_parts == 0:
        return [('Parts', 'none', '')]
    out = [('Parts', '%d' % rep.n_parts, '')]
    if rep.n_partial:
        out.append(('Partly Outside', '%d' % rep.n_partial, 'warn'))
    if rep.n_outside:
        out.append(('Outside', '%d' % rep.n_outside, ''))
    out.append(('Volume', '%.1f cm\u00b3' % (rep.volume / 1e3), ''))
    if rep.bottom > 0.05:
        out.append(('Packed Height', '%.1f mm (from %.1f)' % (rep.height, rep.bottom), ''))
    else:
        out.append(('Packed Height', '%.1f mm' % rep.height, ''))
    if rep.density is None:
        out.append(('3D Density', 'n/a', ''))
    else:
        out.append(('3D Density', '%.1f %%' % (rep.density * 100.0), 'warn' if rep.n_partial else ''))
    if rep.layer_density is not None and rep.height > 0:
        out.append(('Peak Layer', '%.1f %%' % (rep.peak_layer * 100.0), ''))
        if rep.spread_cv is not None:
            out.append(('Layer Spread', '%.2f' % rep.spread_cv, ''))
    flat = getattr(rep, 'flat', None)
    if flat is not None and (flat[0] or flat[2]):
        if flat[0]:
            out.append(('Flat Faces Close', '%d pair%s' % (flat[0], '' if flat[0] == 1 else 's'), 'warn'))
        if flat[2]:
            out.append(('Flat Faces by Walls', '%d' % flat[2], ''))
    return out
