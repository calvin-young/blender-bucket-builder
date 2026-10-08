import os, sys, time, cProfile, pstats
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'build_check')); sys.path.insert(0, HERE)
from core import World, narrow
import bench, meshes
rng = np.random.default_rng(5)
n_obj, tpp, spacing = (int(sys.argv[1]), int(sys.argv[2]), 46.0) if len(sys.argv) > 2 else (120, 100000, 46.0)
w, objs = bench.build_scene(rng, n_obj, tpp, spacing)
uid = n_obj // 2
steps = 120
tt = np.linspace(0, 1, steps)
path = np.stack([55 * np.sin(2 * np.pi * tt), 40 * np.sin(4 * np.pi * tt), 30 * np.sin(6 * np.pi * tt)], axis=1)
# stage timers
T = {}
def wrap(name):
    f = getattr(narrow, name)
    def g(*a, **k):
        t = time.perf_counter(); r = f(*a, **k); T[name] = T.get(name, 0) + time.perf_counter() - t; return r
    setattr(narrow, name, g)
for nm in ('_coarse', '_dive', '_fine', '_eval_rows', '_region_side', '_node_tris', '_pair_tris'):
    wrap(nm)
import core.tritri as tt_
for nm in ('tri_tri_intersect', 'tri_tri_distance', 'plane_gap2'):
    f = getattr(tt_, nm)
    def mk(f, nm):
        def g(*a, **k):
            t = time.perf_counter(); r = f(*a, **k); T[nm] = T.get(nm, 0) + time.perf_counter() - t
            T[nm + '_rows'] = T.get(nm + '_rows', 0) + a[0].shape[2]; return r
        return g
    setattr(tt_, nm, mk(f, nm))
g, M0 = objs[uid]
t0 = time.perf_counter()
pr = cProfile.Profile()
for d in path:
    M = M0.copy(); M[:3, 3] += d
    w.set_matrix(uid, M)
    pr.enable(); w.step(idle=False); pr.disable()
tot = time.perf_counter() - t0
print(f'total {tot*1000:.0f} ms for {steps} frames = {tot/steps*1000:.1f} ms/frame')
for k, v in sorted(T.items(), key=lambda kv: -kv[1] if not kv[0].endswith('_rows') else 0):
    if k.endswith('_rows'): print(f'   {k:28s} {v/steps:10.0f} rows/frame')
    else: print(f'   {k:28s} {v/steps*1000:8.2f} ms/frame')
pstats.Stats(pr).sort_stats('tottime').print_stats(18)
