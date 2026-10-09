"""Installing a new version over an older one that is in use, in the same
Blender session: what "Install from Disk" does for someone who upgrades.

    blender --background --python this_file -- <old version>.zip <new version>.zip

Run it with BLENDER_USER_RESOURCES pointing at an empty directory: it installs
into that profile.  The old package is not in the repository; build it from
its commit, for example version 1.1.0:

    git worktree add /tmp/bb-1.1.0 b9e2464
    blender --command extension build --source-dir /tmp/bb-1.1.0/bucket_builder --output-dir /tmp/bb-old

Blender unregisters the old code, replaces the files, reloads the modules
and registers the new code, while the scene keeps the settings and the
preferences keep the printer library the old version made.  All of that has
to come through: nothing left registered from the old version, the settings
and the user's own printers kept, the built-in printers brought up to date,
and the monitor going again.
"""

import sys
import time

import bpy

OLD_ZIP, NEW_ZIP = sys.argv[-2:]
PKG = 'bl_ext.user_default.bucket_builder'


def addon():
    return sys.modules[PKG]


def settle():
    m = addon()
    sc = bpy.context.scene
    mon = m.monitor.get(sc, create=True)
    vl = bpy.context.view_layer
    time.sleep(m.monitor.IDLE_SECONDS + 0.05)
    assert mon.settle(sc, vl.depsgraph, vl), 'the monitor never settled'
    return mon


def main():
    assert PKG not in bpy.context.preferences.addons.keys(), 'run this with an empty profile'
    r = bpy.ops.extensions.package_install_files(filepath=OLD_ZIP, repo='user_default', enable_on_install=True)
    assert r == {'FINISHED'} and PKG in bpy.context.preferences.addons.keys(), r
    m = addon()
    classes_before = {c.__name__ for c in m.ui.CLASSES + m.ops.CLASSES}

    # ---- the old version in use
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, location=(100, 100, 100))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=20, location=(130, 100, 100))      # into the first
    bpy.ops.mesh.primitive_cube_add(size=30, location=(100, 100, 137))             # 2 mm above it
    st = bpy.context.scene.bucket_builder
    old_switch = not hasattr(st, 'detect_collisions')      # versions 1.0 and 1.1: one switch for everything
    if old_switch:
        st.enabled = True
    else:
        st.detect_collisions = True
        st.monitor_volume = True
    st.clearance_mm = 4.0
    p = m.props.prefs()
    m.props.seed_profiles(p)                  # (the add-on does this from a timer after enabling)
    old_names = [x.name for x in p.profiles]
    st.volume_size = (300.0, 200.0, 250.0)
    assert bpy.ops.bucketbuilder.profile_add('EXEC_DEFAULT', name='Shop Printer') == {'FINISHED'}
    first = old_names[0]
    bpy.ops.bucketbuilder.profile_apply(index=0)
    volume = tuple(st.volume_size)
    p.tint_strength = 0.8                     # a preference the user changed
    mon = settle()
    s = mon.status()
    before = (s['objects'], s['collisions'], s['clearance'])
    assert before == (3, 1, 1), before
    print(f'  old version in use: {before[0]} parts, {before[1]} collision, {before[2]} clearance warning; '
          f'printer {st.printer!r}; library {old_names + ["Shop Printer"]}')

    # ---- the new version over it
    r = bpy.ops.extensions.package_install_files(filepath=NEW_ZIP, repo='user_default', enable_on_install=True)
    assert r == {'FINISHED'}, r
    m = addon()
    version = m.ops.addon_version()
    st = bpy.context.scene.bucket_builder
    p = m.props.prefs()
    print(f'  new version {version} installed over it')
    assert abs(st.clearance_mm - 4.0) < 1e-6, 'the scene lost its settings'
    if old_switch:
        assert st.enabled and not st.detect_collisions, 'the old switch should still be as it was saved'
        m.monitor.migrate_scenes()            # (a timer does this a moment after the new version is in)
    assert st.detect_collisions and st.monitor_volume and not st.enabled, \
        'monitoring was on before the upgrade: both of the new switches must be on after it'
    assert abs(p.tint_strength - 0.8) < 1e-6, 'the preferences were reset'
    assert tuple(st.volume_size) == volume, 'the build volume changed'
    for cls in m.ui.CLASSES + m.ops.CLASSES:
        assert cls.is_registered, f'{cls.__name__} is not registered'
    left = [n for n in classes_before - {c.__name__ for c in m.ui.CLASSES + m.ops.CLASSES}
            if hasattr(bpy.types, n)]
    assert not left, f'classes of the old version are still registered: {left}'

    m.props.seed_profiles(p)                  # (the timer again)
    names = [x.name for x in p.profiles]
    builtin = [n for n, _ in m.props.DEFAULT_PROFILES]
    assert names == builtin + ['Shop Printer'], names
    assert tuple(p.profiles[len(builtin)].size) == (300.0, 200.0, 250.0)
    shown = m.props.current_printer_name(st)
    assert shown in builtin, (st.printer, shown)
    print(f'  library now {names}; the scene\'s printer {first!r} shows as {shown!r}')

    mon = settle()
    s = mon.status()
    after = (s['objects'], s['collisions'], s['clearance'])
    assert after == before and not mon.error, (after, mon.error)
    head = m.overlay.badge_text(s)[:2]
    assert head == ('FAIL', '1 Collision Detected'), head
    assert bpy.ops.bucketbuilder.count_all_problems() == {'FINISHED'}    # an operator only the new version has
    print(f'  the monitor goes on: {after[0]} parts, {after[1]} collision, {after[2]} clearance warning')
    print('UPGRADE TEST OK')


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    print('UPGRADE TEST FAILED')
    sys.exit(1)
