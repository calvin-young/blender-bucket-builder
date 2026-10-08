# Bucket Builder

Live collision, clearance and build-volume monitoring for 3D-print build
preparation in Blender, aimed at dense HP Multi Jet Fusion nests.

Turn it on once and arrange parts as usual: every visible mesh object is
watched, and the viewport shows where parts intersect, where they are closer
than the clearance you set, and what sticks out of the printer's build volume.
The scene itself is never modified; everything you see is drawn on top.

Requires Blender 4.2 or newer.

## Install

Edit > Preferences > Get Extensions (or Add-ons) > the down arrow in the top
right > Install from Disk, and pick `bucket_builder-1.0.0.zip`.

## Use

Open the sidebar in the 3D viewport (N) and the **Bucket** tab.

1. Press **Enable Monitoring**.
2. Move, rotate or scale parts. Results follow while you drag.

| What you see | Meaning |
| --- | --- |
| Red cross-hatching and a red curve | Parts intersect. The curve is where the two surfaces cross; the hatching covers both surfaces around it. |
| A part shaded light red | It collides with at least one other part. In a crowded build this is how you tell the parts at fault from their neighbours. |
| Red box around a part | The part is completely inside another part. |
| Amber diagonal hatching, a line and a distance | Parts are closer than the clearance. The line joins the two closest points. |
| Magenta hatching | The part of a mesh that is outside the build volume. |
| Magenta box | A part that is entirely outside the build volume. |
| Amber hatching next to a wall, and a distance "to wall" | A part inside the volume but closer to a side wall than the wall gap you asked for. |
| Thin inner box | The limit set by the wall gap. It turns amber when a part crosses it. |
| Badge at the bottom of the viewport | Green tick: no collisions and every part inside the volume. A small amber mark on it: warnings only, or something in the scene that is not being checked (the lines next to it say what). Red cross: something to fix. Three dots: still analysing. |
| "~" in front of a distance | The gap is at most that; the search for a smaller one was cut short. This happens between parts with large faces lying side by side. The search still goes on for as long as it could change whether the pair counts as too close. |

**Problems** lists everything that needs attention, worst first. Click an entry,
or use Previous / Next, to frame it in the viewport and select the parts
involved.

### Thresholds

* **Collision (mm)**: parts closer than this count as colliding. `0` means
  touching or intersecting.
* **Clearance (mm)**: the minimum gap you want between parts. Closer pairs get
  a warning; untick it to check collisions only.
* **Units**: *Auto* follows the scene's unit scale, and treats a default scene
  holding millimetre-sized numbers (the usual STL import) as 1 unit = 1 mm.
  The line underneath shows what was decided; override it if it is wrong.

### Printer / build volume

Pick a printer from the menu, or type a size. The plus button saves the current
size as a new printer, the tick button stores it in the selected printer, the
minus button deletes the printer. Printers are stored in the add-on
preferences, so they are available in every file. **Origin** places the volume
with a corner on the scene origin or centred on it; **Offset** shifts it.

**Wall Gap (mm)** is optional. When ticked, a part that sits closer than this
to a side wall (X and Y; the floor and the top are not counted) gets a
warning. Like the part-to-part clearance it does not fail the build: the
badge stays green. A part that actually crosses a wall is still an error.

### Leaving objects out

Fixtures, a build-plate model or reference geometry: select them and use
**Parts > Ignore Selected**. Hidden objects are never checked.

### What is checked

Each mesh object is checked as the viewport shows it, after its modifiers.
A Subdivision modifier is therefore checked at its viewport level, not its
render level: keep that at what you will export.

* A part with simple modifiers (Subdivision, Array, Mirror ...) is as cheap
  to move as a plain mesh.
* A modifier whose result depends on where the part is (Shrinkwrap, a Boolean
  with another object) changes the mesh on every step of a drag. The check
  follows, but a large part will lag. Apply such modifiers before nesting.
* Only real mesh data is read. The badge and the Parts panel tell you when
  the scene holds something that is left out: instances that geometry nodes
  do not realize (add a Realize Instances node), collection instances, and
  text, curve or metaball objects with faces (convert them to meshes). If
  such an object is not part of the build, ignore it and the warning goes.
* While a part is in Edit Mode it is checked with the shape it had when you
  entered; it updates when you leave Edit Mode.

### Large parts

Nothing needs switching on for these, but it helps to know what happens.

* A part of more than about 60 000 triangles is prepared in the background
  when it first appears (about half a second per million triangles, several
  parts at a time). The badge shows "Preparing parts" with a count, and
  Blender stays usable meanwhile.
* Rotating or scaling a large part gives answers at once, from looser data,
  and the exact picture a moment after you let go.
* **Preferences > Add-ons > Bucket Builder > Memory (GB)** limits what the
  checker keeps in memory. Left at 0 it uses a fifth of the installed memory.
  As a guide: 20 bytes per triangle for each different mesh, plus 55 bytes
  per triangle for each part that has neighbours. When the limit is reached
  the checker keeps working with what it has, more slowly, and says so in the
  Parts panel. A single part too large for the limit is listed there as not
  checked, and the badge shows a warning instead of a plain tick.

## How it stays fast

* Each unique mesh is sorted once into a bounding-volume tree. Copies of a part
  share it, whether they are linked duplicates or separate identical meshes.
* Moving a part changes nothing in its tree: the translation is applied while
  querying. While a part is rotated or scaled, the boxes it had are turned as
  they are looked at; a moment after you stop they are fitted again, in
  pieces of a millisecond or two. Only an actual geometry change re-sorts a
  mesh, and that happens in the background.
* Results are kept per pair of neighbouring parts. When a part changes, only
  its own pairs are looked at again.
* All pairs that need work are traversed together in vectorised NumPy passes;
  nothing loops over triangles in Python.
* While you drag, every step has a time budget. Whether parts collide is
  always decided, and the colliding parts are shaded at once. The curve, the
  hatching and the exact clearance are worked out for as many neighbours as
  fit in the budget and for all of them a moment after you stop. So a part
  dropped into the middle of a full build can still be dragged out smoothly.
* The first analysis of a large scene, and anything else that is not a live
  edit, runs in small slices in the background. The badge shows progress.

## Limits to know about

* "Inside another part" needs the outer part to be a closed surface; it is
  simply not reported for open shells.
* The amber near-contact region is drawn at a resolution of about a third of
  the clearance; the reported distance and the red/amber/green state are exact
  (see "~" above for the one exception).
* Parts above the size limit in the preferences (50 million triangles unless
  you change it) are skipped and listed in the Parts panel.
