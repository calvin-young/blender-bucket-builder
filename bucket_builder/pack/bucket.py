# SPDX-License-Identifier: GPL-3.0-or-later
"""The bucket as a voxel grid: what is taken, where a part fits
(docs/AUTOPACK.md section 8.1).

The fine grid holds the *solid* cells of the parts already placed (and of
no-build zones).  A candidate fits at a cell offset b when its *dilated*
grid, shifted by b, meets no taken cell and its solid lies inside the volume
less the wall gap.  The fine grid has an empty margin wide enough for any
dilation, so windows never leave it.

All offsets at once are found on a coarse grid (blocks of f x f x f cells,
max-pooled, so coarse-feasible implies fine-feasible) by FFT correlation:
corr[c] = sum_u taken[c + u] kernel[u], computed circularly at the coarse
grid's own size, which is exact for every offset where the kernel lies
inside the grid.  Candidates are then refined at full resolution with
direct window checks.
"""

import math
import threading
from collections import OrderedDict

import numpy as np

try:
    from scipy import fft as _fft
    _FFT_KW = {'workers': 1}          # (orientations are searched in parallel threads instead)
except Exception:                    # pragma: no cover
    _fft = np.fft
    _FFT_KW = {}


def rfftn(a, s):
    return _fft.rfftn(a, s=s, axes=(0, 1, 2), **_FFT_KW)


def irfftn(a, s):
    return _fft.irfftn(a, s=s, axes=(0, 1, 2), **_FFT_KW)


def fast_size(n):
    """The smallest n' >= n whose prime factors are 2, 3 and 5 (FFTs of a
    prime length such as 71 are several times slower)."""
    n = max(1, int(n))
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


class _SpecCache:
    """Kernel spectra, least recently used out first, within a byte limit."""

    def __init__(self, limit=512 << 20):
        self.limit = limit
        self.bytes = 0
        self.d = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, make):
        with self.lock:
            v = self.d.get(key)
            if v is not None:
                self.d.move_to_end(key)
                return v
        v = make()                           # (outside the lock: two threads may both make it)
        with self.lock:
            if key not in self.d:
                self.d[key] = v
                self.bytes += v.nbytes
            while self.bytes > self.limit and len(self.d) > 1:
                _, old = self.d.popitem(last=False)
                self.bytes -= old.nbytes
        return v


SPECTRA = _SpecCache()


def pool_sum(grid, f, phase):
    """Block sums (int32) of a grid whose cell 0 is at `phase` in block 0."""
    dims = [int(math.ceil((n + p) / f)) for n, p in zip(grid.shape, phase)]
    big = np.zeros([d * f for d in dims], dtype=np.int32)
    big[phase[0]:phase[0] + grid.shape[0], phase[1]:phase[1] + grid.shape[1],
        phase[2]:phase[2] + grid.shape[2]] = grid
    return big.reshape(dims[0], f, dims[1], f, dims[2], f).sum(axis=(1, 3, 5), dtype=np.int32)


class Bucket:
    def __init__(self, vlo, vhi, h, wall_gap, margin, f):
        self.vlo = np.asarray(vlo, float)
        self.vhi = np.asarray(vhi, float)
        self.h = float(h)
        self.f = int(f)
        self.M = int(margin)
        n = np.ceil((self.vhi - self.vlo) / h - 1e-9).astype(int)
        dims = n + 2 * self.M
        # whole blocks, and a coarse grid of FFT-friendly size (the extra cells are margin)
        dims = np.array([fast_size(int(np.ceil(d / self.f))) * self.f for d in dims])
        self.n = n
        self.fine = np.zeros(tuple(dims), dtype=bool)
        self.count = np.zeros(tuple(dims // self.f), dtype=np.int32)
        # where solid cells may be (inclusive index ranges)
        lo_in = self.vlo + np.array([wall_gap, wall_gap, 0.0])
        hi_in = self.vhi - np.array([wall_gap, wall_gap, 0.0])
        self.ilo = self.M + np.ceil((lo_in - self.vlo) / h - 1e-9).astype(int)
        self.ihi = self.M + np.floor((hi_in - self.vlo) / h + 1e-9).astype(int) - 1
        self.area = float((self.vhi[0] - self.vlo[0]) * (self.vhi[1] - self.vlo[1]))
        self.profile = np.zeros(int(n[2]) + 1)     # exact volume per band of h, from the floor
        self.volume = 0.0
        self.height = 0.0                           # highest point above the floor
        self._spec = None
        self.version = 0

    # ------------------------------------------------------------ geometry
    def translation(self, pg, b):
        """The part's translation (mm) that puts its cell 0 on bucket cell b."""
        return self.vlo + (np.asarray(b) - self.M) * self.h - pg.origin

    def in_range(self, pg, b):
        b = np.asarray(b)
        return bool(np.all(b + pg.s_lo >= self.ilo) and np.all(b + pg.s_hi - 1 <= self.ihi))

    def fits(self, pg, b):
        """No taken cell under the part's dilated grid at fine offset b."""
        k = pg.kernel
        b = [int(x) for x in b]
        src, dst = [], []
        for x, n, N in zip(b, k.shape, self.fine.shape):
            lo, hi = max(x, 0), min(x + n, N)
            if lo >= hi:
                return True
            dst.append(slice(lo, hi))
            src.append(slice(lo - x, hi - x))
        win = self.fine[tuple(dst)]
        return not np.logical_and(win, k[tuple(src)]).any()

    def _faces(self, pg, axis):
        """Flat offsets (in this grid) of the kernel cells that lead when the
        part moves towards -axis: set, with the cell before them not set."""
        key = ('faces', axis, self.fine.shape)
        off = pg._coarse.get(key)
        if off is None:
            k = pg.kernel
            prev = np.zeros_like(k)
            sl_dst = [slice(None)] * 3
            sl_src = [slice(None)] * 3
            sl_dst[axis] = slice(1, None)
            sl_src[axis] = slice(0, -1)
            prev[tuple(sl_dst)] = k[tuple(sl_src)]
            u = np.argwhere(k & ~prev)
            st = np.array(self.fine.strides) // self.fine.itemsize
            off = (u * st).sum(1).astype(np.int64)
            pg._coarse[key] = off
        return off

    def slide(self, pg, b, axis, limit):
        """How many cells (0 .. limit) a part that fits at b can move towards
        -axis and still fit.  Only the leading cells of its kernel can meet
        something new at each step."""
        if limit <= 0:
            return 0
        st = np.array(self.fine.strides) // self.fine.itemsize
        base = int(np.dot(np.asarray(b, np.int64), st))
        off = self._faces(pg, axis)
        flat = self.fine.reshape(-1)
        step = int(st[axis])
        done = 0
        while done < limit:
            n = min(8, limit - done)
            js = np.arange(done + 1, done + n + 1, dtype=np.int64)
            hit = flat[base - js[:, None] * step + off[None, :]].any(axis=1)
            if hit.any():
                return done + int(np.argmax(hit))
            done += n
        return done

    # ------------------------------------------------------------ coarse search
    def spectrum(self, dtype=np.float64):
        if self._spec is None:
            self._spec = {}
        sp = self._spec.get(dtype)
        if sp is None:
            occ = (self.count > 0).astype(dtype)
            sp = self._spec[dtype] = rfftn(occ, occ.shape)
        return sp

    def _dtype(self, n_kernel):
        """Single precision where its rounding cannot matter: the error of an
        FFT correlation is below about eps * log2(N) * |taken| * |kernel|
        (L2 norms), and it has to stay well under the 0.5 that separates
        "no overlap" from one cell of overlap.  NumPy's FFT is double only."""
        if _fft is np.fft:
            return np.float64
        n_taken = int(np.count_nonzero(self.count))
        bound = 1.2e-7 * math.log2(self.count.size) * math.sqrt(max(n_taken, 1) * max(n_kernel, 1))
        return np.float32 if bound < 0.05 else np.float64

    def coarse_feasible(self, pg, key, phase):
        """(mask, c0): mask[i] is True where the part fits at coarse offset
        c0 + i, inside the volume; fine offset = f * c + phase."""
        f = self.f
        C = self.count.shape
        kc = pg.coarse(f, phase)
        if any(a > b for a, b in zip(kc.shape, C)):
            return None, None
        phase = np.asarray(phase)
        # offsets that keep the solid inside the volume
        cmin = np.ceil((self.ilo - pg.s_lo - phase) / f).astype(int)
        cmax = np.floor((self.ihi - (pg.s_hi - 1) - phase) / f).astype(int)
        cmin = np.maximum(cmin, 0)
        cmax = np.minimum(cmax, np.array(C) - np.array(kc.shape))
        if np.any(cmax < cmin):
            return None, None
        if not self.count.any():
            mask = np.ones(tuple(cmax - cmin + 1), dtype=bool)
            return mask, cmin
        dt = self._dtype(int(kc.sum()))
        ks = SPECTRA.get((key, f, tuple(phase), C, dt),
                         lambda: np.conj(rfftn(kc.astype(dt), C)))
        corr = irfftn(self.spectrum(dt) * ks, C)
        sl = tuple(slice(a, b + 1) for a, b in zip(cmin, cmax))
        return corr[sl] < 0.5, cmin

    # ------------------------------------------------------------ changes
    def place(self, pg, b, profile, volume, top):
        """Mark the part's solid as taken.  profile: its exact volume per band
        of h from its cell 0; volume: its exact volume; top: its highest
        point above the floor once placed."""
        b = [int(x) for x in b]
        self.mark(pg.solid, b)
        z0 = b[2] - self.M
        lo, hi = max(z0, 0), min(z0 + len(profile), len(self.profile))
        if hi > lo:
            self.profile[lo:hi] += profile[lo - z0:hi - z0]
        self.volume += volume
        self.height = max(self.height, top)
        self._spec = None
        self.version += 1

    def mark(self, solid, b):
        """OR a solid grid into the taken cells at fine offset b (clipped to
        the grid) and bring the block counts of that region up to date."""
        dst, src = [], []
        for x, n, N in zip(b, solid.shape, self.fine.shape):
            lo, hi = max(x, 0), min(x + n, N)
            if lo >= hi:
                return
            dst.append(slice(lo, hi))
            src.append(slice(lo - x, hi - x))
        self.fine[tuple(dst)] |= solid[tuple(src)]
        f = self.f
        blk = tuple(slice(d.start // f, -(-d.stop // f)) for d in dst)
        fine = self.fine[tuple(slice(c.start * f, c.stop * f) for c in blk)]
        self.count[blk] = pool_sum(fine, f, (0, 0, 0))
        self._spec = None
        self.version += 1

    def block(self, lo, hi):
        """Mark a no-build box (mm) as taken."""
        a = np.floor((np.asarray(lo) - self.vlo) / self.h).astype(int) + self.M
        b = np.ceil((np.asarray(hi) - self.vlo) / self.h).astype(int) + self.M
        a = np.clip(a, 0, self.fine.shape)
        b = np.clip(b, 0, self.fine.shape)
        self.fine[a[0]:b[0], a[1]:b[1], a[2]:b[2]] = True
        self.count[...] = pool_sum(self.fine, self.f, (0, 0, 0))[:self.count.shape[0],
                                                                 :self.count.shape[1],
                                                                 :self.count.shape[2]]
        self._spec = None
        self.version += 1
