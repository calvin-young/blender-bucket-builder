# SPDX-License-Identifier: GPL-3.0-or-later
"""Timing of the packer's heavy step on this machine: FFT correlation over a
build volume, on the CPU (NumPy, SciPy) and on an NVIDIA GPU (CuPy) if one
is available.  Optionally also the greedy packer on the synthetic buckets.

    In Blender: Bucket sidebar > Pack > Advanced > Measure Packer Speed
    python3 tools/pack_timing.py              # FFT table
    python3 tools/pack_timing.py --greedy     # and the synthetic buckets (a few minutes)

For the GPU column, a normal Python with CuPy for your CUDA version, e.g.
`pip install numpy scipy cupy-cuda12x`.  Nothing here reads your files or
writes any; paste the printed table back.
"""

import os
import platform
import sys
import time

import numpy as np


SIZES = {          # the HP 5600 volume (380 x 284 x 380 mm) at a few cell sizes, plus a
    '8 mm': (50, 40, 50),             # margin, rounded to sizes with factors 2, 3, 5 only
    '4 mm': (100, 75, 100),
    '2 mm': (200, 150, 200),
    '1 mm': (400, 300, 400),
}


def backends():
    out = [('numpy', np, np.fft, {})]
    try:
        import scipy.fft as sfft
        out.append(('scipy (all cores)', np, sfft, {'workers': -1}))
    except Exception:
        pass
    try:
        import cupy as cp
        import cupy.fft as cfft
        cp.zeros(1).sum()
        out.append(('cupy (GPU)', cp, cfft, {}))
    except Exception:
        pass
    return out


def time_corr(xp, fft, kw, shape, dtype, reps=5):
    """One correlation as the packer does it: the bucket's spectrum is kept,
    the part's is kept, so a query is a product and one inverse FFT."""
    rng = np.random.default_rng(0)
    bucket = xp.asarray((rng.random(shape) < 0.1).astype(dtype))
    k = tuple(max(1, s // 6) for s in shape)
    kern = xp.zeros(shape, dtype=dtype)
    kern[:k[0], :k[1], :k[2]] = 1
    t = time.perf_counter()
    B = fft.rfftn(bucket, s=shape, axes=(0, 1, 2), **kw)
    K = xp.conj(fft.rfftn(kern, s=shape, axes=(0, 1, 2), **kw))
    if xp is not np:
        xp.cuda.Stream.null.synchronize()
    t_fwd = (time.perf_counter() - t) / 2
    best = float('inf')
    for _ in range(reps):
        t = time.perf_counter()
        c = fft.irfftn(B * K, s=shape, axes=(0, 1, 2), **kw)
        m = c < 0.5
        if xp is not np:
            int(m.sum())                    # (waits for the GPU, and copies one number back)
        best = min(best, time.perf_counter() - t)
    return t_fwd, best


def fft_table():
    print('FFT correlation over the 5600 volume (best of 5; forward = one spectrum)')
    print('%-20s %-7s %-14s %12s %12s' % ('backend', 'cells', 'grid', 'forward', 'query'))
    for name, xp, fft, kw in backends():
        for label, shape in SIZES.items():
            for dtype in ((np.float32, np.float64) if name != 'numpy' else (np.float64,)):
                n = shape[0] * shape[1] * shape[2]
                if name == 'numpy' and n > 3e7:
                    continue                 # (slow, and memory hungry; the others answer it)
                try:
                    f, q = time_corr(xp, fft, kw, shape, dtype)
                except Exception as ex:      # out of memory and the like
                    print('%-20s %-7s %-14s   failed: %s' % (name, label, 'x'.join(map(str, shape)), ex))
                    continue
                print('%-20s %-7s %-14s %9.1f ms %9.1f ms   %s'
                      % (name, label, 'x'.join(map(str, shape)), f * 1e3, q * 1e3, np.dtype(dtype).name))


def thread_table():
    """Do NumPy's FFTs run side by side in threads here (they should: the
    packer searches a part's orientations in parallel)?"""
    from concurrent.futures import ThreadPoolExecutor
    from .metrics import default_workers
    shape = SIZES['4 mm']
    rng = np.random.default_rng(1)
    B = np.fft.rfftn(rng.random(shape), s=shape, axes=(0, 1, 2))

    def q(_):
        return float(np.fft.irfftn(B * B, s=shape, axes=(0, 1, 2))[0, 0, 0])

    n = 2 * default_workers()
    t = time.perf_counter()
    for i in range(n):
        q(i)
    serial = time.perf_counter() - t
    with ThreadPoolExecutor(default_workers()) as ex:
        t = time.perf_counter()
        list(ex.map(q, range(n)))
        par = time.perf_counter() - t
    print()
    print('%d NumPy 4 mm queries: one after the other %.0f ms, in %d threads %.0f ms (%.1f x)'
          % (n, serial * 1e3, default_workers(), par * 1e3, serial / max(par, 1e-9)))


def greedy_table():
    from . import greedy, metrics, problem, synthetic
    from .problem import Settings
    try:
        from . import referee
    except Exception as ex:              # (the exact engine is needed for the check)
        referee = None
        print('referee unavailable:', ex)
    print()
    print('Greedy packer on the synthetic buckets (CPU, %d threads)' % metrics.default_workers())
    m = synthetic.pringle()
    print('pringle principal frame (rows): %s' % np.round(problem.principal_frame(m.verts), 3).tolist())
    print('%-11s %8s %9s %9s %8s  %s' % ('bucket', 'placed', 'height', 'density', 'time', 'exact check'))
    for name, (desc, parts, kw) in synthetic.buckets().items():
        s = Settings(**kw)
        t = time.perf_counter()
        res = greedy.solve(parts, s)
        dt = time.perf_counter() - t
        items = res.items(parts)
        rep = metrics.measure(items, s.vlo, s.vhi)
        n = sum(p.quantity for p in parts)
        verdict = '-'
        if referee is not None:
            v = referee.check(items, s.vlo, s.vhi, s.gap, s.wall_gap)
            verdict = 'clean' if v.ok else ('%d collisions, %d too close, %d outside, %d at walls'
                                            % (v.collisions, v.too_close, v.outside, v.near_wall))
        print('%-11s %4d/%-3d %6.1f mm %7.2f %% %6.1f s  %s'
              % (name, len(res.placements), n, rep.height, 100 * (rep.density or 0), dt, verdict), flush=True)


def main(args=None):
    if args is None:
        args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    print('machine: %s, %s, %d logical CPUs, Python %s, NumPy %s'
          % (platform.platform(), platform.processor() or platform.machine(), os.cpu_count() or 0,
             platform.python_version(), np.__version__))
    try:
        import scipy
        print('SciPy', scipy.__version__)
    except Exception:
        print('SciPy: not installed')
    try:
        import cupy as cp
        dev = cp.cuda.runtime.getDeviceProperties(0)
        print('GPU:', dev['name'].decode(), '%.1f GB' % (dev['totalGlobalMem'] / 2 ** 30), 'CuPy', cp.__version__)
    except Exception as ex:
        print('GPU: none usable (%s)' % type(ex).__name__)
    print()
    fft_table()
    thread_table()
    if '--greedy' in args:
        greedy_table()


if __name__ == '__main__':
    main()

