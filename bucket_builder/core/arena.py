# SPDX-License-Identifier: GPL-3.0-or-later
"""Growable NumPy storage shared by every monitored object.

All bounding boxes, posed vertices and triangle index tables live in a few big
arrays so that one vectorised gather can serve many object pairs at once.
"""

import bisect
import numpy as np


class Arena:
    """A row allocator on top of one NumPy array (first fit, with coalescing)."""

    def __init__(self, width, dtype, cap=4096, align=1):
        self.width = width
        self.dtype = np.dtype(dtype)
        self.align = int(align)
        cap = -(-int(cap) // self.align) * self.align
        self.data = np.empty((cap, width), dtype=self.dtype)
        self.top = 0
        self._free = []      # sorted list of (base, size)
        self.live = 0

    def alloc(self, n):
        n = -(-int(n) // self.align) * self.align
        free = self._free
        for i, (base, size) in enumerate(free):
            if size >= n:
                if size == n:
                    del free[i]
                else:
                    free[i] = (base + n, size - n)
                self.live += n
                return base
        if self.top + n > self.data.shape[0]:
            self._grow(self.top + n)
        base = self.top
        self.top += n
        self.live += n
        return base

    def release(self, base, n):
        base = int(base)
        n = -(-int(n) // self.align) * self.align
        if n <= 0:
            return
        self.live -= n
        free = self._free
        i = bisect.bisect_left(free, (base, 0))
        # merge with the block after
        if i < len(free) and free[i][0] == base + n:
            n += free[i][1]
            del free[i]
        # merge with the block before
        if i > 0 and free[i - 1][0] + free[i - 1][1] == base:
            base = free[i - 1][0]
            n += free[i - 1][1]
            del free[i - 1]
            i -= 1
        if base + n == self.top:
            self.top = base
        else:
            free.insert(i, (base, n))

    def _grow(self, need):
        cap = max(int(need), int(self.data.shape[0] * 1.6) + 1024)
        cap = -(-cap // self.align) * self.align
        try:
            # realloc: for large blocks the OS remaps the pages instead of
            # copying them.  Nothing keeps a view of ``data`` across an alloc.
            self.data.resize((cap, self.width), refcheck=False)
        except Exception:
            new = np.empty((cap, self.width), dtype=self.dtype)
            new[:self.top] = self.data[:self.top]
            self.data = new

    @property
    def nbytes(self):
        return self.data.nbytes


def grow_rows(arr, need, fill=None):
    """Return ``arr`` enlarged along axis 0 to hold at least ``need`` rows."""
    if need <= arr.shape[0]:
        return arr
    cap = max(int(need), int(arr.shape[0] * 1.6) + 16)
    new = np.empty((cap,) + arr.shape[1:], dtype=arr.dtype)
    new[:arr.shape[0]] = arr
    if fill is not None:
        new[arr.shape[0]:] = fill
    return new
