# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings: per-scene monitor settings, the printer profile library (stored in
the add-on preferences so it is shared by all files) and display options."""

import sys

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
from bpy.types import AddonPreferences, PropertyGroup

ADDON_ID = __package__
GIB = float(1 << 30)

# Build volumes in millimetres (X, Y, Z).  These only seed the editable
# profile list; nothing else in the add-on depends on them.  The 580+ is the
# owner's 580 with custom firmware, which builds a little more than the
# 332 x 190 x 248 mm HP publishes for the 500 series; the figure is his.
DEFAULT_PROFILES = (
    ("HP MJF 5600", (380.0, 284.0, 380.0)),
    ("HP MJF 1200", (320.0, 165.0, 230.0)),
    ("HP MJF 580", (332.0, 190.0, 248.0)),
    ("HP MJF 580+", (332.6, 198.7, 267.8)),
)

# The built-in list has changed twice.  Libraries made by an earlier version
# are brought up to date: its built-in entries go (unless their size was
# edited), printers the user saved stay.  Every name the list has had, with
# its size and what that printer is called now (None: it is no longer listed):
PROFILES_VERSION = 3
_PROFILES_OLD = {
    # 1.0
    "HP Jet Fusion 5600": ((380.0, 284.0, 380.0), "HP MJF 5600"),
    "HP Jet Fusion 5200": ((380.0, 284.0, 380.0), "HP MJF 5600"),
    "HP Jet Fusion 5000": ((380.0, 284.0, 250.0), None),
    "HP Jet Fusion 4200": ((380.0, 284.0, 380.0), "HP MJF 5600"),
    "HP Multi Jet Fusion 1200": ((320.0, 165.0, 230.0), "HP MJF 1200"),
    "HP Jet Fusion 580 / 540": ((332.0, 190.0, 248.0), "HP MJF 580"),
    # 1.1
    "HP MJF 4XXX/5XXX": ((380.0, 284.0, 380.0), "HP MJF 5600"),
    "HP MJF 5XX": ((332.0, 190.0, 248.0), "HP MJF 580"),
}


def prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def settings(scene):
    return getattr(scene, "bucket_builder", None)


def active(st):
    """True if anything is being monitored in a scene with these settings."""
    return st is not None and (st.detect_collisions or st.monitor_volume)


def volume_shown(st):
    """The build volume is drawn: its own checkbox, under the main switch."""
    return st.monitor_volume and st.show_volume


def volume_checked(st):
    """Parts are checked against the build volume."""
    return st.monitor_volume and st.use_volume


def migrate_scene(scene):
    """Bring the settings of a scene saved by version 1.0 or 1.1 up to date:
    there was one switch for everything then.  Returns True if anything was
    changed."""
    st = settings(scene)
    if st is None or not st.enabled:
        return False
    st.enabled = False
    if not (st.is_property_set("detect_collisions") or st.is_property_set("monitor_volume")):
        st.detect_collisions = True
        st.monitor_volume = True
    return True


_installed = [False, None]


def installed_memory():
    """Memory installed in the computer in bytes, or None if that cannot be
    found out."""
    if _installed[0]:
        return _installed[1]
    total = None
    try:
        if sys.platform == 'win32':
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.ullTotalPhys)
        else:
            import os
            total = int(os.sysconf('SC_PHYS_PAGES')) * int(os.sysconf('SC_PAGE_SIZE'))
    except Exception:
        total = None
    if total is not None and total <= 0:
        total = None
    _installed[0] = True
    _installed[1] = total
    return total


def automatic_memory_limit():
    """What the checker may use when the preference is left on automatic: a
    fifth of the installed memory, but between 1 and 16 GiB."""
    total = installed_memory()
    if total is None:
        return int(3 * GIB)
    return int(min(max(0.2 * total, 1 * GIB), 16 * GIB))


def memory_limit(context=None):
    """Bytes the checker's cached data may take (the Memory preference)."""
    p = prefs(context)
    gb = p.memory_gb if p is not None else 0.0
    if gb <= 0.0:
        return automatic_memory_limit()
    return int(gb * GIB)


# ---------------------------------------------------------------------------
# update callbacks (kept tiny: they only poke the monitor)
# ---------------------------------------------------------------------------

def _poke(self, context):
    from . import monitor
    monitor.on_settings_changed(context.scene if context else None)


def _poke_enabled(self, context):
    from . import monitor
    monitor.on_enabled_changed(context.scene if context else None)


def _poke_redraw(self, context):
    from . import monitor
    monitor.tag_redraw_all()


def _poke_prefs(self, context):
    from . import monitor
    monitor.on_prefs_changed()


def _poke_volume(self, context):
    """Editing the size by hand turns the selection into a custom volume."""
    scene = context.scene if context else None
    st = settings(scene) if scene else None
    if st is not None and not st.lock_printer_name:
        p = prefs(context)
        match = False
        if p is not None:
            name = current_printer_name(st)
            for prof in p.profiles:
                if prof.name == name and _same_size(prof.size, st.volume_size):
                    match = True
                    break
        if not match and st.printer != "Custom":
            st.lock_printer_name = True
            st.printer = "Custom"
            st.lock_printer_name = False
    _poke(self, context)


# ---------------------------------------------------------------------------
# property groups
# ---------------------------------------------------------------------------

class BucketBuilderProfile(PropertyGroup):
    """One printer build volume in the user's library"""
    size: FloatVectorProperty(
        name="Build Volume", description="Usable build volume in millimetres",
        size=3, subtype='XYZ', min=1.0, soft_max=1000.0, precision=1, step=100,
        default=(380.0, 284.0, 380.0))


class BucketBuilderIgnoredProblem(PropertyGroup):
    """A problem the user has chosen to ignore (see ``Monitor.ignored``)"""
    kind: StringProperty()        # 'PAIR' (two parts), 'VOLUME' or 'WALL' (one part)
    a: PointerProperty(type=bpy.types.Object)
    b: PointerProperty(type=bpy.types.Object)
    lock: BoolProperty()          # stays ignored whatever happens to the parts
    # What the problem looked like when it was ignored.  It counts as the same
    # problem, and stays ignored, for as long as this still describes it.
    sig: FloatVectorProperty(size=12)
    aux: FloatVectorProperty(size=8)
    geo: StringProperty()


class BucketBuilderSettings(PropertyGroup):
    """Per-scene settings of the build monitor"""

    # (versions 1.0 and 1.1 had one switch for everything; see migrate_scene)
    enabled: BoolProperty(default=False, options={'HIDDEN'})
    detect_collisions: BoolProperty(
        name="Detect Collisions",
        description="Check every part against its neighbours while you arrange the build: "
                    "parts that touch or cut through each other, and parts that are closer "
                    "than the clearance",
        default=False, update=_poke_enabled)
    monitor_volume: BoolProperty(
        name="Monitor Build Volume",
        description="Show the printer's build volume and check that every part is inside it. "
                    "While this is off the volume is neither drawn nor checked, whatever the "
                    "two checkboxes below say",
        default=False, update=_poke_enabled)

    use_clearance: BoolProperty(
        name="Clearance Warning", description="Warn when parts are closer than the clearance",
        default=True, update=_poke)
    clearance_mm: FloatProperty(
        name="Clearance", description="Minimum gap between parts; closer pairs get a warning",
        default=5.0, min=0.0, soft_max=20.0, precision=2, step=50, update=_poke)

    detect_enclosed: BoolProperty(
        name="Parts Inside Parts",
        description="Also report a part that lies completely inside another one although "
                    "their surfaces do not touch. This needs closed meshes: switch it off if "
                    "open shells cause false alarms",
        default=True, update=_poke)
    ignore_hidden: BoolProperty(
        name="Ignore Hidden Parts",
        description="Leave parts that are hidden in the viewport out of the checks. When this "
                    "is off a hidden part is still checked, because it is still in the build. "
                    "(A part that is disabled in viewports, or whose collection is excluded "
                    "from the view layer, is never checked)",
        default=False, update=_poke)

    unit_mode: EnumProperty(
        name="Units", description="How scene coordinates map to millimetres",
        items=(('AUTO', "Auto", "Use the scene's unit scale; a default scene holding "
                "millimetre-sized numbers (the usual STL import) is treated as 1 unit = 1 mm"),
               ('MM', "1 unit = 1 mm", "Coordinates are millimetres, whatever the scene "
                "unit settings say"),
               ('SCENE', "Scene Units", "Use the scene's unit scale as it is")),
        default='AUTO', update=_poke)

    use_volume: BoolProperty(
        name="Check Build Volume", description="Warn when parts reach outside the build volume",
        default=True, update=_poke)
    use_wall_clearance: BoolProperty(
        name="Wall Gap",
        description="Warn when a part inside the build volume is closer than this to a side "
                    "wall (X and Y). The top and the bottom are not checked",
        default=False, update=_poke)
    wall_clearance_mm: FloatProperty(
        name="Wall Gap", description="Distance parts should keep from the side walls",
        default=5.0, min=0.0, soft_max=50.0, precision=2, step=50, update=_poke)
    show_volume: BoolProperty(
        name="Show Build Volume", description="Draw the build volume in the viewport",
        default=True, update=_poke)
    printer: StringProperty(
        name="Printer", description="Name of the printer profile the volume came from",
        default=DEFAULT_PROFILES[0][0])
    lock_printer_name: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    volume_size: FloatVectorProperty(
        name="Size", description="Build volume in millimetres",
        size=3, subtype='XYZ', min=1.0, soft_max=1000.0, precision=1, step=100,
        default=DEFAULT_PROFILES[0][1], update=_poke_volume)
    volume_align: EnumProperty(
        name="Origin", description="Where the build volume sits relative to the scene origin",
        items=(('CORNER', "Corner", "The scene origin is the lower front left corner"),
               ('CENTER_XY', "Centred", "Centred on the origin in X and Y, standing on Z = 0"),
               ('CENTER', "Centred XYZ", "Centred on the origin in all three axes")),
        default='CORNER', update=_poke)
    volume_offset: FloatVectorProperty(
        name="Offset", description="Extra shift of the build volume in millimetres",
        size=3, subtype='XYZ', precision=1, step=100, default=(0.0, 0.0, 0.0), update=_poke)

    show_overlay: BoolProperty(
        name="Problem Overlay", description="Draw collision, clearance and out-of-volume regions",
        default=True, update=_poke_redraw)
    show_tint: BoolProperty(
        name="Tint Colliding Parts",
        description="Shade every part that collides with another one, so the parts at fault "
                    "stand out in a crowded build",
        default=True, update=_poke_redraw)
    show_curves: BoolProperty(
        name="Intersection Outlines",
        description="Draw the curve along which two colliding parts cut through each other",
        default=True, update=_poke_redraw)
    show_gap_lines: BoolProperty(
        name="Closest-Distance Lines",
        description="Draw a line between the two closest points of parts that are too close",
        default=True, update=_poke_redraw)
    show_markers_collision: BoolProperty(
        name="Collision Markers",
        description="Mark every collision, and every part outside the build volume, with a ring",
        default=True, update=_poke_redraw)
    show_markers_clearance: BoolProperty(
        name="Clearance Markers",
        description="Mark every clearance and wall-gap warning with a ring",
        default=True, update=_poke_redraw)
    show_labels_clearance: BoolProperty(
        name="Clearance Distance Labels",
        description="Write the distance next to every clearance and wall-gap warning",
        default=True, update=_poke_redraw)
    local_view_mode: EnumProperty(
        name="In Local View",
        description="What a viewport shows while it is in local view (isolating a problem "
                    "puts its parts in local view)",
        items=(('SHOWN', "Parts in View",
                "The problems of the parts in the view. A part outside the view that one of "
                "them runs into appears as a ghost, so nothing new goes unnoticed"),
               ('ALL', "Whole Build",
                "Every problem of the build, also those among parts that are not in the view")),
        default='SHOWN', update=_poke_redraw)
    show_hud: BoolProperty(
        name="Status Badge", description="Show the large pass / fail badge in the viewport",
        default=True, update=_poke_redraw)
    # the problem the user last went to: which one it is, and where it was in the list
    problem_key: StringProperty(default="", options={'HIDDEN', 'SKIP_SAVE'})
    problem_index: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    isolate: BoolProperty(
        name="Isolate",
        description="Show the parts of the problem you go to on their own (local view)",
        default=False, options={'SKIP_SAVE'})
    ignored_problems: CollectionProperty(type=BucketBuilderIgnoredProblem)


class BucketBuilderPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    profiles: CollectionProperty(type=BucketBuilderProfile)
    profiles_seeded: BoolProperty(default=False)
    profiles_version: IntProperty(default=0)

    color_collision: FloatVectorProperty(
        name="Collision", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.08, 0.05, 1.0), update=_poke_redraw)
    color_clearance: FloatVectorProperty(
        name="Clearance", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.72, 0.0, 1.0), update=_poke_redraw)
    color_outside: FloatVectorProperty(
        name="Outside Volume", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.95, 0.1, 0.85, 1.0), update=_poke_redraw)
    color_volume: FloatVectorProperty(
        name="Build Volume", subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.35, 0.75, 1.0, 1.0), update=_poke_redraw)
    hatch_pixels: IntProperty(
        name="Hatch Spacing", description="Distance between hatch lines in pixels (whole "
        "pixels: anything else shimmers)",
        default=4, min=2, max=10, update=_poke_redraw)
    use_xray: BoolProperty(
        name="Hatch X-Ray", description="Let hatched regions that are hidden behind geometry "
        "show through",
        default=True, update=_poke_redraw)
    xray: FloatProperty(
        name="Strength", description="How strongly hatched regions hidden behind geometry "
        "show through",
        default=0.45, min=0.05, max=1.0, subtype='FACTOR', update=_poke_redraw)
    tint_strength: FloatProperty(
        name="Tint Strength", description="How strongly colliding parts are shaded",
        default=0.5, min=0.0, max=1.0, subtype='FACTOR', update=_poke_redraw)
    tint_budget: FloatProperty(
        name="Tint Detail", description="Colliding parts are shaded until this many million "
        "triangles are being drawn; parts beyond that get an outline box instead, so the "
        "shading never slows the viewport down. 0 uses outline boxes only",
        default=4.0, min=0.0, max=200.0, update=_poke_redraw)
    volume_fill: FloatProperty(
        name="Volume Fill", description="How strongly the faces of the build volume are tinted "
        "(0 draws the edges only)",
        default=0.25, min=0.0, max=1.0, subtype='FACTOR', update=_poke_redraw)
    badge_scale: FloatProperty(
        name="Badge Size", description="Size of the pass / fail badge in the viewport",
        default=1.0, min=0.5, max=3.0, subtype='FACTOR', update=_poke_redraw)
    budget_ms: FloatProperty(
        name="Background Time", description="Milliseconds per update spent on background "
        "analysis (first scan of a scene, exact refinement). Live edits are always processed",
        default=12.0, min=2.0, max=100.0)
    max_tris_millions: FloatProperty(
        name="Largest Part", description="Parts with more triangles than this (in millions) "
        "are skipped and listed as not checked",
        default=50.0, min=0.1, max=1000.0)
    memory_gb: FloatProperty(
        name="Memory (GB)", description="Most memory the checker may use for the data that "
        "makes it fast. 0 is automatic: a fifth of the installed memory. When it runs short, "
        "the data of the parts that were not looked at for longest is dropped and built again "
        "when needed, which makes checking slower but not less exact; a part too large for it "
        "is listed as not checked",
        default=0.0, min=0.0, soft_max=64.0, step=100, precision=1, update=_poke_prefs)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        col = layout.column(heading="Colors")
        col.prop(self, "color_collision")
        col.prop(self, "color_clearance")
        col.prop(self, "color_outside")
        col.prop(self, "color_volume")
        col = layout.column()
        col.prop(self, "tint_strength")
        col.prop(self, "tint_budget")
        col.prop(self, "volume_fill")
        col.prop(self, "hatch_pixels")
        col.prop(self, "use_xray")
        sub = col.column()
        sub.active = self.use_xray
        sub.prop(self, "xray")
        col.prop(self, "badge_scale")
        col = layout.column()
        col.prop(self, "budget_ms")
        col.prop(self, "max_tris_millions")
        col.prop(self, "memory_gb")
        total = installed_memory()
        auto = automatic_memory_limit() / GIB
        if self.memory_gb <= 0.0:
            note = f"Automatic: {auto:.1f} GB"
            if total is not None:
                note += f" of the {total / GIB:.0f} GB installed"
            col.label(text=note)
        layout.separator()
        layout.label(text="Printer profiles are edited in the Bucket tab of the 3D viewport sidebar.")


def _same_size(a, b):
    return all(abs(x - y) < 1e-3 for x, y in zip(a, b))


def seed_profiles(p):
    """Give the profile library the built-in printers: once for a new
    library, and once more for one made by an earlier version, whose built-in
    entries are replaced while the printers the user saved are kept.  A
    built-in printer whose size the user has changed keeps that size."""
    if p is None or (p.profiles_seeded and p.profiles_version >= PROFILES_VERSION):
        return
    builtin = {name for name, _ in DEFAULT_PROFILES}
    keep = []
    sizes = {}
    for prof in p.profiles:
        old = _PROFILES_OLD.get(prof.name)
        if prof.name in builtin:
            sizes[prof.name] = tuple(prof.size)
        elif old is None or not _same_size(prof.size, old[0]):
            keep.append((prof.name, tuple(prof.size)))
    p.profiles.clear()
    for name, size in [(n, sizes.get(n, sz)) for n, sz in DEFAULT_PROFILES] + keep:
        item = p.profiles.add()
        item.name = name
        item.size = size
    p.profiles_seeded = True
    p.profiles_version = PROFILES_VERSION


def current_printer_name(st):
    """The name to show for a scene's printer.  Scenes saved with an earlier
    version carry its printer names; where the volume is still that printer's,
    the present name is shown instead."""
    old = _PROFILES_OLD.get(st.printer)
    if old is not None and old[1] is not None and _same_size(st.volume_size, old[0]):
        return old[1]
    return st.printer or "Custom"


CLASSES = (BucketBuilderProfile, BucketBuilderIgnoredProblem, BucketBuilderSettings,
           BucketBuilderPreferences)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bucket_builder = PointerProperty(type=BucketBuilderSettings)
    bpy.types.Object.bucket_builder_ignore = BoolProperty(
        name="Ignore in Bucket Builder",
        description="Leave this object out of collision, clearance and build-volume checks",
        default=False, update=_poke)


def unregister():
    del bpy.types.Object.bucket_builder_ignore
    del bpy.types.Scene.bucket_builder
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
