# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings: per-scene monitor settings, the printer profile library (stored in
the add-on preferences so it is shared by all files) and display options."""

import sys

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy.types import AddonPreferences, PropertyGroup

ADDON_ID = __package__
GIB = float(1 << 30)

# Build volumes in millimetres (X, Y, Z) as published by HP.  These only seed
# the editable profile list; nothing else in the add-on depends on them.
DEFAULT_PROFILES = (
    ("HP Jet Fusion 5600", (380.0, 284.0, 380.0)),
    ("HP Jet Fusion 5200", (380.0, 284.0, 380.0)),
    ("HP Jet Fusion 5000", (380.0, 284.0, 250.0)),
    ("HP Jet Fusion 4200", (380.0, 284.0, 380.0)),
    ("HP Multi Jet Fusion 1200", (320.0, 165.0, 230.0)),
    ("HP Jet Fusion 580 / 540", (332.0, 190.0, 248.0)),
)


def prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def settings(scene):
    return getattr(scene, "bucket_builder", None)


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
            for prof in p.profiles:
                if prof.name == st.printer and all(
                        abs(a - b) < 1e-4 for a, b in zip(prof.size, st.volume_size)):
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


class BucketBuilderSettings(PropertyGroup):
    """Per-scene settings of the build monitor"""

    enabled: BoolProperty(
        name="Monitor Build",
        description="Continuously check all visible mesh objects for collisions, "
                    "insufficient clearance and parts outside the build volume",
        default=False, update=_poke_enabled)

    collision_mm: FloatProperty(
        name="Collision", description="Parts closer than this are reported as colliding. "
        "0 means only touching or intersecting parts",
        default=0.0, min=0.0, soft_max=5.0, precision=2, step=10, update=_poke)
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
        name="Wall Clearance",
        description="Warn when a part inside the build volume is closer than this to a side "
                    "wall (X and Y). The top and the bottom are not checked",
        default=False, update=_poke)
    wall_clearance_mm: FloatProperty(
        name="Wall Clearance", description="Distance parts should keep from the side walls",
        default=5.0, min=0.0, soft_max=50.0, precision=2, step=50, update=_poke)
    show_volume: BoolProperty(
        name="Show Build Volume", description="Draw the build volume in the viewport",
        default=True, update=_poke_redraw)
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
    show_hud: BoolProperty(
        name="Status Badge", description="Show the large pass / fail badge in the viewport",
        default=True, update=_poke_redraw)
    show_labels: BoolProperty(
        name="Markers and Distances", description="Mark each problem and label clearance gaps",
        default=True, update=_poke_redraw)
    problem_index: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})


class BucketBuilderPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    profiles: CollectionProperty(type=BucketBuilderProfile)
    profiles_seeded: BoolProperty(default=False)

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
    hatch_spacing: FloatProperty(
        name="Hatch Spacing", description="Distance between hatch lines in pixels",
        default=9.0, min=4.0, max=40.0, update=_poke_redraw)
    xray: FloatProperty(
        name="See-Through", description="How strongly problem regions hidden behind "
        "geometry still show through (0 hides them)",
        default=0.45, min=0.0, max=1.0, subtype='FACTOR', update=_poke_redraw)
    tint_strength: FloatProperty(
        name="Tint Strength", description="How strongly colliding parts are shaded",
        default=0.3, min=0.0, max=1.0, subtype='FACTOR', update=_poke_redraw)
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
        col.prop(self, "hatch_spacing")
        col.prop(self, "xray")
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


def seed_profiles(p):
    """Fill an empty profile library with the defaults (once)."""
    if p is None or p.profiles_seeded:
        return
    if len(p.profiles) == 0:
        for name, size in DEFAULT_PROFILES:
            item = p.profiles.add()
            item.name = name
            item.size = size
    p.profiles_seeded = True


CLASSES = (BucketBuilderProfile, BucketBuilderSettings, BucketBuilderPreferences)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bucket_builder = bpy.props.PointerProperty(type=BucketBuilderSettings)
    bpy.types.Object.bucket_builder_ignore = BoolProperty(
        name="Ignore in Bucket Builder",
        description="Leave this object out of collision, clearance and build-volume checks",
        default=False, update=_poke)


def unregister():
    del bpy.types.Object.bucket_builder_ignore
    del bpy.types.Scene.bucket_builder
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
