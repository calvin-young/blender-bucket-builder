# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for the packer's tools (no Blender needed).

    python -m pack report bucket.3mf                  # density report
    python -m pack report bucket.3mf --printer 1200 --profile
    python -m pack report part.stl --volume 380 284 380 --center

Run it from the `bucket_builder` folder (or use tools/pack_report.py).

The output never contains the file name or the part names unless `--names` is
given: parts are `part-001` ... in file order and the file is `bucket-01`.
That keeps a report of a customer's bucket safe to paste anywhere
(docs/AUTOPACK.md section 15).
"""

import argparse
import sys
import time

import numpy as np

from . import meshio, metrics, printers


def build_parser():
    ap = argparse.ArgumentParser(prog='pack', description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('report', help='density, height, layer profile and warnings of a build')
    r.add_argument('files', nargs='+', help='.3mf or .stl files; each is one bucket')
    g = r.add_mutually_exclusive_group()
    g.add_argument('--printer', default=None,
                   help='a printer name or part of it (default %s); known: %s'
                   % (printers.DEFAULT_PRINTER, ', '.join(printers.PRINTERS)))
    g.add_argument('--volume', nargs=3, type=float, metavar=('X', 'Y', 'Z'),
                   help='build volume in mm')
    r.add_argument('--center', action='store_true',
                   help='the volume is centred on x = y = 0 (default: its corner is at 0, 0, 0)')
    r.add_argument('--band', type=float, default=1.0, help='layer thickness of the profile, mm (1)')
    r.add_argument('--smooth', type=float, default=5.0, help='smoothing window of the spread, mm (5)')
    r.add_argument('--profile', action='store_true', help='print the density of every layer band')
    r.add_argument('--check', action='store_true',
                   help='also check every mesh for boundary, non-manifold and flipped edges')
    r.add_argument('--names', action='store_true',
                   help='print file and part names (do not for private buckets)')
    r.add_argument('--workers', type=int, default=None, help='threads (default: all cores)')
    return ap


def volume_of(args):
    if args.volume:
        name, size = 'custom', tuple(args.volume)
    else:
        name, size = printers.find(args.printer or printers.DEFAULT_PRINTER)
    size = np.asarray(size, float)
    lo = np.array([-size[0] / 2, -size[1] / 2, 0.0]) if args.center else np.zeros(3)
    return name, lo, lo + size


def fmt_cv(rep):
    return 'n/a' if rep.spread_cv is None else '%.2f' % rep.spread_cv


def report_one(path, label, args, out):
    t0 = time.perf_counter()
    scene = meshio.read(path)
    t_read = time.perf_counter() - t0
    pname, vlo, vhi = volume_of(args)
    parts = [(it.name if args.names else 'part-%03d' % (i + 1), it.mesh, it.matrix)
             for i, it in enumerate(scene.items)]
    t1 = time.perf_counter()
    rep = metrics.measure(parts, vlo, vhi, band=args.band, smooth_mm=args.smooth)
    t_meas = time.perf_counter() - t1

    shown = path if args.names else label
    p = lambda s='': print(s, file=out)  # noqa: E731
    p('%s' % shown)
    tri = sum(m.nt for m in scene.meshes.values())
    tri_all = sum(it.mesh.nt for it in scene.items)
    p('  printer        %s  (%.1f x %.1f x %.1f mm, %s)'
      % (pname, *(vhi - vlo), 'centred' if args.center else 'corner at 0'))
    p('  parts          %d  (%d unique meshes, %.2f M triangles stored, %.2f M printed)'
      % (rep.n_parts, len(scene.meshes), tri / 1e6, tri_all / 1e6))
    p('                 %d inside, %d partly outside, %d outside'
      % (rep.n_inside, rep.n_partial, rep.n_outside))
    p('  volume         %.1f cm3  (%.1f cm3 outside, not counted)'
      % (rep.volume / 1e3, rep.volume_outside / 1e3))
    p('  packed height  %.1f mm' % rep.height)
    if rep.density is None:
        p('  3D density     n/a')
    else:
        flag = '   <-- %d part%s partly outside' % (rep.n_partial, '' if rep.n_partial == 1 else 's') \
            if rep.n_partial else ''
        p('  3D density     %.1f %%%s' % (rep.density * 100, flag))
    if rep.layer_density is not None:
        p('  layer density  peak %.1f %%, spread (CV over %g mm) %s'
          % (rep.peak_layer * 100, rep.smooth_mm, fmt_cv(rep)))
    for w in rep.warnings:
        p('  WARNING        ' + w)
    for name, why in scene.skipped:
        p('  skipped        one %s object' % why)
    if args.check:
        bad = 0
        for key, mesh in scene.meshes.items():
            t = metrics.topology(meshio.weld(mesh))
            if t['boundary_edges'] or t['nonmanifold_edges'] or t['flipped_edges']:
                bad += 1
                p('  edges          mesh %d: %d boundary, %d non-manifold, %d flipped'
                  % (bad, t['boundary_edges'], t['nonmanifold_edges'], t['flipped_edges']))
        if not bad:
            p('  edges          every mesh is closed and consistent')
    if args.profile and rep.layer_density is not None:
        used = max(1, int(np.ceil(rep.height / args.band - 1e-9)))
        p('  profile        z (mm)   density')
        top = max(rep.peak_layer, 1e-12)
        step = max(1, used // 40)
        for i in range(0, used, step):
            d = max(0.0, float(rep.layer_density[i:i + step].mean()))
            p('                 %6.1f   %5.1f %%  %s' % (rep.z_mid[i] - args.band / 2 + 0, d * 100,
                                                      '#' * int(round(30 * d / top))))
    p('  time           read %.2f s, measure %.2f s' % (t_read, t_meas))
    return rep


def main(argv=None, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    code = 0
    if args.cmd == 'report':
        for i, path in enumerate(args.files):
            try:
                report_one(path, 'bucket-%02d' % (i + 1), args, out)
            except (meshio.ReadError, OSError, KeyError) as e:
                msg = str(e)
                if not args.names:
                    msg = 'could not read bucket-%02d (%s)' % (i + 1, type(e).__name__)
                print('error: ' + msg, file=sys.stderr)
                code = 1
    return code


if __name__ == '__main__':
    sys.exit(main())
