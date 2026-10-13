# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for the packer's tools (no Blender needed).

    python -m pack report bucket.3mf                  # density report
    python -m pack report bucket.3mf --printer 1200 --profile
    python -m pack report part.stl --volume 380 284 380 --center
    python -m pack pack parts.3mf --mode z --gap 3 --out packed.3mf --referee

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

    k = sub.add_parser('pack', help='pack the parts of a 3MF or STL into one bucket (greedy, Stage 2)')
    k.add_argument('file', help='.3mf (each build item a part; copies of one mesh are grouped) or .stl')
    k.add_argument('--out', help='write the packed build to this .3mf')
    g = k.add_mutually_exclusive_group()
    g.add_argument('--printer', default=None, help='printer name or part of it (default %s)'
                   % printers.DEFAULT_PRINTER)
    g.add_argument('--volume', nargs=3, type=float, metavar=('X', 'Y', 'Z'), help='build volume in mm')
    k.add_argument('--center', action='store_true', help='volume centred on x = y = 0')
    k.add_argument('--mode', choices=('fixed', 'z', 'free'), default='z', help='rotation of every part (z)')
    k.add_argument('--copies', type=int, default=1, help='make this many of every part (1)')
    k.add_argument('--gap', type=float, default=5.0, help='between parts, mm (5)')
    k.add_argument('--wall-gap', type=float, default=5.0, help='from the side walls, mm (5)')
    k.add_argument('--max-density', type=float, default=12.0, help='3D density limit in %% (12; 0: none)')
    k.add_argument('--cell', type=float, default=1.0, help='voxel size, mm (1)')
    k.add_argument('--z-step', type=float, default=30.0, help='degrees between turns about z (30)')
    k.add_argument('--referee', action='store_true', help='check the result with the exact engine')
    k.add_argument('--names', action='store_true', help='print file and part names')
    return ap


def parts_of(scene, mode, copies):
    """Build items grouped into parts: copies of one mesh in one orientation
    are one part with a quantity.  Each part keeps its orientation as given
    (its scale too) as the base its mode turns."""
    from .problem import Part, group_key
    groups = {}
    for it in scene.items:
        L = np.asarray(it.matrix, float)[:3, :3]
        key = (id(it.mesh), group_key(L, mode))
        if key not in groups:
            groups[key] = Part('part-%03d' % (len(groups) + 1), it.mesh, 0, mode, base=L.copy())
        groups[key].quantity += copies
    return list(groups.values())


def pack_one(args, out):
    from . import greedy, referee
    from .problem import Settings
    p = lambda s='': print(s, file=out)  # noqa: E731
    t0 = time.perf_counter()
    scene = meshio.read(args.file)
    pname, vlo, vhi = volume_of(args)
    parts = parts_of(scene, args.mode, args.copies)
    s = Settings(vlo=vlo, vhi=vhi, gap=args.gap, wall_gap=args.wall_gap, h=args.cell,
                 z_step=args.z_step, max_density=(args.max_density / 100.0) or None)
    t_read = time.perf_counter() - t0
    res = greedy.solve(parts, s)
    items = res.items(parts)
    rep = metrics.measure(items, vlo, vhi)
    n = sum(q.quantity for q in parts)
    p(args.file if args.names else 'bucket-01')
    p('  printer        %s  (%.1f x %.1f x %.1f mm), gap %.1f mm, wall gap %.1f mm, rotation %s'
      % (pname, *(vhi - vlo), args.gap, args.wall_gap, args.mode))
    p('  placed         %d of %d  (%d unique parts)' % (len(res.placements), n, len(parts)))
    if res.unplaced:
        why = {}
        for _, _, r in res.unplaced:
            why[r] = why.get(r, 0) + 1
        for r, c in why.items():
            p('  not placed     %d: %s' % (c, r))
    if rep.density is not None:
        p('  packed height  %.1f mm' % rep.height)
        p('  3D density     %.2f %%   (limit %s)' % (100 * rep.density,
                                                     '%.1f %%' % args.max_density if args.max_density else 'none'))
        p('  layer density  peak %.1f %%, spread %s' % (100 * rep.peak_layer, fmt_cv(rep)))
    p('  time           read %.1f s, pack %.1f s' % (t_read, res.seconds))
    if args.referee:
        v = referee.check(items, vlo, vhi, args.gap, args.wall_gap)
        p('  referee        %s (%.1f s)' % ('clean' if v.ok else 'VIOLATIONS: %s' % v, v.seconds))
    if args.out:
        meshio.write_3mf(args.out, [(m, M, name if args.names else 'part-%03d' % (i + 1))
                                    for i, (name, m, M) in enumerate(items)])
        p('  written        %s' % (args.out if args.names else 'the packed 3MF'))
    return 0


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
    if args.cmd == 'pack':
        try:
            return pack_one(args, out)
        except (meshio.ReadError, OSError, KeyError) as e:
            print('error: ' + (str(e) if args.names else 'could not read the file (%s)' % type(e).__name__),
                  file=sys.stderr)
            return 1
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
