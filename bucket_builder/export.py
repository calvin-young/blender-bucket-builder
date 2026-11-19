# SPDX-License-Identifier: GPL-3.0-or-later
"""Export Print File: the build as a 3MF (#9).

Only through the 3MF add-on (the maintained one, or its fork), so names,
colors and whatever else it writes come along; without it the panel offers
to install it (the owner, 2026-11-19: no writer of Bucket Builder's own in
the add-on; `pack.meshio.write_3mf` is the headless engine's, for the
command line, benchmarks and tests). The build's facts go in as model-level
metadata in the `bb:` namespace (blender-3mf-io/docs/METADATA.md): the printer, the
packing settings, the tool and version, the last check and the time of
export. No operator field (the owner, 2026-11-18). A part resting a rounding
error below the floor is written exactly on it.
"""

import datetime
import json

import bpy
import numpy as np

from . import monitor, props

BB_NS = "urn:bucket-builder"


def build_objects(context):
    """The parts of the build: the mesh objects the check looks at (shown,
    not ignored, not the snap wireframe)."""
    from .arrange import is_helper
    vl = context.view_layer
    return [o for o in vl.objects
            if o is not None and o.type == 'MESH' and not is_helper(o) and not o.bucket_builder_ignore
            and o.visible_get(view_layer=vl)]


def metadata(context):
    """{name: value} of the build's `bb:` entries (values are JSON strings)."""
    from .ops import addon_version
    from .packing import pack_gaps
    scene = context.scene
    st = props.settings(scene)
    gap, wall = pack_gaps(st)
    out = {
        'bb:printer': {'model': props.current_printer_name(st), 'volume_mm': [round(float(v), 3) for v in st.volume_size]},
        'bb:packing': {'clearance_mm': gap, 'wall_clearance_mm': wall, 'aim_mm': float(st.clearance_aim_mm),
                       'rotation': st.pack_mode, 'goal': st.pack_preset, 'effort': st.pack_effort,
                       'part_quality': float(st.pack_quality), 'leave_room': bool(st.pack_leave_room)},
        'bb:tool': 'Bucket Builder %s' % addon_version(),
        'bb:exported': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    mon = monitor.get(scene)
    if mon is not None:
        s = mon.status()
        out['bb:checks'] = {'collisions': s['collisions'], 'clearance_warnings': s['clearance'],
                            'partly_outside': s['partly_out'], 'outside': s['outside'] + s.get('staged', 0),
                            'clearance_mm': float(st.clearance_mm) if st.use_clearance else None}
    return {k: v if isinstance(v, str) else json.dumps(v, sort_keys=True) for k, v in out.items()}


def problems(context):
    """Lines to warn about before writing (not a reason to refuse)."""
    from .ops import export_warnings
    lines = list(export_warnings(context.scene))
    mon = monitor.get(context.scene)
    if mon is not None:
        s = mon.status()
        if s['collisions']:
            lines.append('%d collision%s' % (s['collisions'], 's' if s['collisions'] != 1 else ''))
        if s['clearance']:
            lines.append('%d pair%s closer than the clearance' % (s['clearance'], 's' if s['clearance'] != 1 else ''))
    return lines


def threemf_api():
    """The 3MF add-on's programming interface, or None."""
    api = bpy.app.driver_namespace.get('io_mesh_3mf')
    return api if api is not None and hasattr(api, 'export_3mf') else None


def threemf_operator():
    return hasattr(bpy.ops.export_mesh, 'threemf')


def _set_scene_metadata(scene, entries, api):
    for name, value in entries.items():
        scene[name] = {'datatype': 'xs:string', 'preserve': True, 'value': value}
    declare = getattr(api, 'declare_metadata_namespace', None) if api is not None else None
    if declare is not None:
        try:
            declare('bb', BB_NS, scene, replace=False)
        except TypeError:
            declare('bb', BB_NS)


ADDON_ID = "ThreeMF_io"          # the 3MF add-on's id on the Blender extensions platform
ADDON_URL = "https://extensions.blender.org/search/?q=3mf"


class NoAddon(RuntimeError):
    pass


def write(context, path):
    """Write the build to `path` through the 3MF add-on. Returns the number of parts."""
    objs = build_objects(context)
    api = threemf_api()
    if api is None:
        raise NoAddon("the 3MF add-on is not installed")
    _set_scene_metadata(context.scene, metadata(context), api)
    # (the add-on scales by the scene's unit, metres for a default scene; Bucket Builder knows how many
    # millimeters a unit is, which for a scene modeled in mm is not what the unit setting says)
    from .packing import mm_per_unit
    us = context.scene.unit_settings
    add_on_mm = 1000.0 * (us.scale_length or 1.0)
    res = api.export_3mf(path, objects=objs, export_location='KEEP', global_scale=mm_per_unit(context.scene, objs) / add_on_mm)
    if getattr(res, 'status', 'FINISHED') != 'FINISHED':
        raise RuntimeError('the 3MF add-on could not write the file')
    return getattr(res, 'num_written', len(objs))


def install_addon():
    """Install and enable the 3MF add-on from the extensions platform. True if it worked."""
    try:
        repos = bpy.context.preferences.extensions.repos
        idx = next(i for i, r in enumerate(repos) if 'extensions.blender.org' in (r.remote_url or ''))
        bpy.ops.extensions.package_install(repo_index=idx, pkg_id=ADDON_ID, enable_on_install=True)
    except Exception:                                    # noqa: BLE001  (offline, online access off, no such repo)
        return False
    return threemf_api() is not None
