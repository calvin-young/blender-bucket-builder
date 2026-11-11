# SPDX-License-Identifier: GPL-3.0-or-later
"""The compiled voxel steps (bbcore.c), when a library for this platform is
present: bbcore.so (Linux), bbcore.dll (Windows), bbcore.dylib (macOS).
Each function gives exactly what its NumPy original in pack/voxel.py gives;
`lib` is None without a library (or with BB_NATIVE=0), and voxel.py then
uses NumPy."""

import ctypes
import os
import sys

import numpy as np

lib = None

_NAME = {'win32': 'bbcore.dll', 'darwin': 'bbcore.dylib'}.get(sys.platform, 'bbcore.so')


def _load():
    global lib
    if os.environ.get('BB_NATIVE', '1') == '0':
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _NAME)
    if not os.path.exists(path):
        return
    try:
        L = ctypes.CDLL(path)
    except OSError:
        return
    P = ctypes.c_void_p
    L.bb_version.restype = ctypes.c_int
    L.bb_subdivide.argtypes = [P, ctypes.c_int64, ctypes.c_double, ctypes.c_int64, ctypes.POINTER(ctypes.c_void_p)]
    L.bb_subdivide.restype = ctypes.c_int64
    L.bb_free.argtypes = [P]
    L.bb_mark_boxes.argtypes = [P, P, P, P, P, ctypes.c_int64, P, ctypes.c_double]
    L.bb_mark_tris.argtypes = [P, P, P, ctypes.c_int64, P, ctypes.c_double]
    L.bb_mark_boxes_turned.argtypes = [P, P, P, P, P, ctypes.c_int64, P, P, P, ctypes.c_double]
    L.bb_outside.argtypes = [P, P, P]
    L.bb_outside.restype = ctypes.c_int
    if L.bb_version() == 2:
        lib = L


_load()


def _p(a):
    return a.ctypes.data_as(ctypes.c_void_p)


def _dims(grid):
    return np.array(grid.shape, dtype=np.int64)


def _f64(a):
    return np.ascontiguousarray(a, dtype=np.float64)


def subdivide(P, h, max_pieces=0):
    """Triangles (n, 3, 3) cut until no edge is longer than h, as one array;
    None if there would be more than max_pieces (0: no limit)."""
    P = _f64(P)
    out = ctypes.c_void_p()
    n = lib.bb_subdivide(_p(P), P.shape[0], float(h), int(max_pieces), ctypes.byref(out))
    if n < 0:
        return None
    if n == 0:
        lib.bb_free(out)
        return np.zeros((0, 3, 3))
    buf = (ctypes.c_double * (n * 9)).from_address(out.value)
    res = np.frombuffer(buf, dtype=np.float64).reshape(n, 3, 3).copy()
    lib.bb_free(out)
    return res


def mark_boxes(grid, pmin, pmax, origin, h, normals=None):
    """voxel._mark_boxes on a bool grid, in place."""
    g = grid.view(np.uint8)
    pmin, pmax = _f64(pmin), _f64(pmax)
    nr = _f64(normals) if normals is not None else None
    lib.bb_mark_boxes(_p(g), _p(_dims(grid)), _p(pmin), _p(pmax), _p(nr) if nr is not None else None,
                      pmin.shape[0], _p(_f64(origin)), float(h))


def mark_boxes_turned(grid, pmin, pmax, normals, axes, signs, origin, h):
    """_mark_boxes of boxes in a frame turned by a quarter turn (axes, signs)."""
    g = grid.view(np.uint8)
    lib.bb_mark_boxes_turned(_p(g), _p(_dims(grid)), _p(_f64(pmin)), _p(_f64(pmax)), _p(_f64(normals)),
                             pmin.shape[0], _p(np.asarray(axes, np.int64)), _p(_f64(signs)),
                             _p(_f64(origin)), float(h))


def mark_tris(grid, P, origin, h):
    """voxel._mark for triangles (n, 3, 3) on a bool grid, in place."""
    g = grid.view(np.uint8)
    P = _f64(P)
    lib.bb_mark_tris(_p(g), _p(_dims(grid)), _p(P), P.shape[0], _p(_f64(origin)), float(h))


def outside(blocked):
    """voxel.outside: empty cells connected to the border."""
    b = np.ascontiguousarray(blocked, dtype=bool)
    out = np.empty(b.shape, dtype=bool)
    if lib.bb_outside(_p(b.view(np.uint8)), _p(out.view(np.uint8)), _p(_dims(b))) != 0:
        raise MemoryError('flood fill')
    return out
