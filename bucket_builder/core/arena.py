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
        self.max_step = max(1 << 16, (256 << 20) // (width * self.dtype.itemsize))
        self.want = 0        # rows the owner expects to need in all (a hint for growing)

    def alloc(self, n, room=None):
        """Reserve ``n`` rows and return the first.  ``room`` (bytes, optional)
        is the most the array may grow by beyond what this request needs."""
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
            self._grow(self.top + n, room)
        base = self.top
        self.top += n
        self.live += n
        return base

    def growth(self, n):
        """Rows the array would have to grow by to take ``n`` more rows (0 if
        they fit in what is allocated already)."""
        n = -(-int(n) // self.align) * self.align
        for _, size in self._free:
            if size >= n:
                return 0
        return max(0, self.top + n - self.data.shape[0])

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

    def _grow(self, need, room=None):
        # Go straight to what the owner expects to need, else grow
        # geometrically while small and by at most ``max_step`` rows once
        # large; never by more than ``room`` bytes beyond what is needed.
        have = int(self.data.shape[0])
        cap = max(have + min(int(have * 0.6) + 1024, self.max_step), int(self.want))
        cap = -(-cap // self.align) * self.align
        if room is not None:
            top = have + max(0, int(room)) // (self.width * self.dtype.itemsize)
            cap = min(cap, top // self.align * self.align)
        cap = max(-(-int(need) // self.align) * self.align, cap)
        self._resize(cap)

    def _resize(self, cap):
        # A new array and a copy of the rows in use.  (``ndarray.resize`` would
        # also zero-fill the rest, which touches, and so really allocates,
        # memory that may never be used.)  Nothing keeps a view of ``data``
        # across an alloc.
        new = np.empty((cap, self.width), dtype=self.dtype)
        keep = min(self.top, cap)
        new[:keep] = self.data[:keep]
        self.data = new

    def shrink(self, cap=0):
        """Give memory back: cut the array down to what is in use (at least
        ``cap`` rows).  Only the unused tail can be returned."""
        cap = max(int(cap), self.top, self.align)
        cap = -(-cap // self.align) * self.align
        if cap < self.data.shape[0]:
            self._resize(cap)

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
