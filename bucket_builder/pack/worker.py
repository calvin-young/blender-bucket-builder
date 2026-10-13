# SPDX-License-Identifier: GPL-3.0-or-later
"""Packing in a separate process, so that Blender stays responsive.

Python threads inside Blender share one interpreter lock with the user
interface: a packer working in threads, mostly in short NumPy calls, starved
Blender's own Python (panels, the overlay) and the window froze.  A process
of its own has its own interpreter and all the cores it wants.

The add-on writes a job folder (`write_job`); Blender's bundled Python runs

    python -I -c "import sys; sys.path.insert(0, ADDON); from pack.worker import main; main(JOB)"

which writes `progress.json` as parts are placed (atomically: written to a
temporary name, then renamed), and stops at the next part when a file
`stop` appears.  No `bpy` here.
"""

import json
import os
import sys
import time
import traceback

import numpy as np

from . import greedy
from .meshio import Mesh
from .problem import Part, Settings

SETTINGS = ('gap', 'wall_gap', 'h', 'coarse', 'phases', 'z_step', 'max_density', 'layer_cap',
            'layer_weight', 'candidates', 'time_limit')


def write_job(folder, parts, settings, locked=()):
    """Write what a worker needs: parts (meshes shared by identity),
    settings, locked (mesh, 4 x 4) pairs."""
    arrays, meshes, spec = {}, {}, []
    for p in parts:
        j = meshes.get(id(p.mesh))
        if j is None:
            j = meshes[id(p.mesh)] = len(meshes)
            arrays['v%d' % j] = np.asarray(p.mesh.verts, np.float64)
            arrays['t%d' % j] = np.asarray(p.mesh.tris, np.int32)
        spec.append({'name': p.name, 'mesh': j, 'quantity': int(p.quantity), 'mode': p.mode,
                     'tier': int(p.tier), 'base': np.asarray(p.base, float).tolist(),
                     'close_cells': p.close_cells})
    for n, (m, M) in enumerate(locked):
        arrays['lv%d' % n] = np.asarray(m.verts, np.float64)
        arrays['lt%d' % n] = np.asarray(m.tris, np.int32)
        arrays['lM%d' % n] = np.asarray(M, np.float64)
    np.savez(os.path.join(folder, 'job.npz'), **arrays)
    job = {'parts': spec, 'locked': len(locked), 'vlo': list(map(float, settings.vlo)),
           'vhi': list(map(float, settings.vhi)),
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
             'error': '', 'seconds': 0.0}
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
        s = Settings(vlo=job['vlo'], vhi=job['vhi'], **job['settings'])
        g = greedy.Greedy(parts, s)
        stop = os.path.join(folder, 'stop')
        last = [0.0]

        def report(res, final=False):
            if os.path.exists(stop):
                g.cancel = True
            now = time.perf_counter()
            if not final and now - last[0] < 0.2:
                return
            last[0] = now
            state['done'], state['total'] = g.progress
            state['done'] += 0 if final else 1
            state['placements'] = [[p.part, p.copy, p.orientation, np.asarray(p.matrix).ravel().tolist()]
                                   for p in res.placements]
            state['unplaced'] = [[i, c, why] for i, c, why in res.unplaced]
            state['seconds'] = now - t0
            _write(folder, state)

        g.on_place = report
        _write(folder, state)
        res = g.run(locked=locked)
        state['finished'] = True
        report(res, final=True)
    except Exception:
        state['error'] = traceback.format_exc()[-2000:]
        state['finished'] = True
        _write(folder, state)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
