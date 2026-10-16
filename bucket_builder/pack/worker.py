# SPDX-License-Identifier: GPL-3.0-or-later
"""Packing in a separate process, so that Blender stays responsive.

Python threads inside Blender share one interpreter lock with the user
interface: a packer working in threads, mostly in short NumPy calls, starved
Blender's own Python (panels, the overlay) and the window froze.  A process
of its own has its own interpreter and all the cores it wants.

The add-on writes a job folder (`write_job`); Blender's bundled Python runs

    python -I -c "import sys; sys.path.insert(0, ADDON); from pack.worker import main; main(JOB)"

which runs a `pack.job.Job` and writes `progress.json` (atomically: written
to a temporary name, then renamed) as parts are placed and whenever the
best layout improves, and stops within a part or two when a file `stop`
appears.  No `bpy` here.
"""

import json
import os
import sys
import time
import traceback

import numpy as np

from . import job as J, safe
from .meshio import Mesh
from .problem import Part, Settings

SETTINGS = ('gap', 'wall_gap', 'h', 'coarse', 'phases', 'z_step', 'max_density', 'layer_cap',
            'layer_weight', 'candidates', 'time_limit', 'time_budget', 'preset', 'seed', 'compact')


def write_job(folder, parts, settings, locked=(), start=None):
    """Write what a worker needs: parts (meshes shared by identity),
    settings, locked (mesh, 4 x 4) pairs, and optionally the layout to start
    from ({(part, copy): 4 x 4}).  Geometry and numbers only: the worker
    needs no names, so the parts are numbered here (part-001 ...)."""
    arrays, meshes, spec = {}, {}, []
    for p in parts:
        j = meshes.get(id(p.mesh))
        if j is None:
            j = meshes[id(p.mesh)] = len(meshes)
            arrays['v%d' % j] = np.asarray(p.mesh.verts, np.float64)
            arrays['t%d' % j] = np.asarray(p.mesh.tris, np.int32)
        spec.append({'name': 'part-%03d' % (len(spec) + 1), 'mesh': j, 'quantity': int(p.quantity), 'mode': p.mode,
                     'tier': int(p.tier), 'base': np.asarray(p.base, float).tolist(),
                     'close_cells': p.close_cells})
    for n, (m, M) in enumerate(locked):
        arrays['lv%d' % n] = np.asarray(m.verts, np.float64)
        arrays['lt%d' % n] = np.asarray(m.tris, np.int32)
        arrays['lM%d' % n] = np.asarray(M, np.float64)
    st = []
    for n, ((i, c), M) in enumerate(sorted((start or {}).items())):
        arrays['sM%d' % n] = np.asarray(M, np.float64)
        st.append([int(i), int(c)])
    np.savez(os.path.join(folder, 'job.npz'), **arrays)
    job = {'parts': spec, 'locked': len(locked), 'vlo': list(map(float, settings.vlo)),
           'vhi': list(map(float, settings.vhi)), 'start': st,
           'settings': {k: getattr(settings, k) for k in SETTINGS}}
    with open(os.path.join(folder, 'job.json'), 'w') as f:
        json.dump(job, f)


def read_progress(folder):
    """The worker's latest report, or None."""
    try:
        with open(os.path.join(folder, 'progress.json')) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(folder, data):
    tmp = os.path.join(folder, 'progress.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, os.path.join(folder, 'progress.json'))


def main(folder):
    t0 = time.perf_counter()
    state = {'done': 0, 'total': 0, 'placements': [], 'unplaced': [], 'finished': False,
             'error': '', 'seconds': 0.0, 'phase': 'greedy', 'version': 0, 'height': 0.0,
             'budget': None, 'iterations': 0, 'log': []}
    try:
        with open(os.path.join(folder, 'job.json')) as f:
            job = json.load(f)
        z = np.load(os.path.join(folder, 'job.npz'))
        meshes = {}
        parts = []
        for sp in job['parts']:
            j = sp['mesh']
            if j not in meshes:
                meshes[j] = Mesh(z['v%d' % j], z['t%d' % j], sp['name'])
            parts.append(Part(sp['name'], meshes[j], sp['quantity'], sp['mode'], sp['tier'],
                              np.array(sp['base']), sp['close_cells']))
        locked = [(Mesh(z['lv%d' % n], z['lt%d' % n], 'locked'), z['lM%d' % n]) for n in range(job['locked'])]
        start = {(i, c): z['sM%d' % n] for n, (i, c) in enumerate(job.get('start', []))} or None
        s = Settings(vlo=job['vlo'], vhi=job['vhi'], **job['settings'])
        jb = J.Job(parts, s, locked, start=start)
        stop = os.path.join(folder, 'stop')
        last = [0.0, -1, -1]

        def report(final=False):
            if os.path.exists(stop):
                jb.cancel = True
            now = time.perf_counter()
            n_now = len(jb.cur.recs) if jb.cur is not None else 0
            changed = jb.version != last[1] or (jb.best is None and n_now != last[2])
            if not final and (now - last[0] < 0.2 or not changed) and now - last[0] < 1.0:
                return
            last[0] = now
            res = jb.result()
            state['done'], state['total'] = jb.progress
            state['phase'] = jb.phase
            state['version'] = jb.version
            state['budget'] = jb.budget
            state['iterations'] = jb.iterations
            state['height'] = float(res.height or 0.0)
            state['log'] = jb.log[-20:]
            if changed or final:
                last[1], last[2] = jb.version, n_now
                state['placements'] = [[p.part, p.copy, p.orientation, np.asarray(p.matrix).ravel().tolist()]
                                       for p in res.placements]
                state['unplaced'] = [[i, c, why] for i, c, why in res.unplaced]
            state['seconds'] = now - t0
            _write(folder, state)

        _write(folder, state)
        try:
            while jb.step(0.1):
                report()
        finally:
            jb.close()
        state['finished'] = True
        report(final=True)
    except Exception:
        state['error'] = safe.tidy(traceback.format_exc()[-2000:])
        state['finished'] = True
        _write(folder, state)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
