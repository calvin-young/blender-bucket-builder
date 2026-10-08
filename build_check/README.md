# Build Check

Live collision, clearance and build-volume monitoring for 3D-print build
preparation in Blender, aimed at dense HP Multi Jet Fusion nests.

Turn it on once and arrange parts as usual: every visible mesh object is
watched, and the viewport shows where parts intersect, where they are closer
than the clearance you set, and what sticks out of the printer's build volume.
The scene itself is never modified; everything you see is drawn on top.

Requires Blender 4.2 or newer.

## Install

Edit > Preferences > Get Extensions (or Add-ons) > the down arrow in the top
right > Install from Disk, and pick `build_check-1.0.0.zip`.

## Use

Open the sidebar in the 3D viewport (N) and the **Build** tab.

1. Press **Enable Monitoring**.
2. Move, rotate or scale parts. Results follow while you drag.

| What you see | Meaning |
| --- | --- |
| Red cross-hatching and a red curve | Parts intersect. The curve is where the two surfaces cross. |
| Red box around a part | The part is completely inside another part. |
| Amber diagonal hatching, a line and a distance | Parts are closer than the clearance. The line joins the two closest points. |
| Magenta hatching | The part of a mesh that is outside the build volume. |
| Magenta box | A part that is entirely outside the build volume. |
| Badge at the bottom of the viewport | Green tick: no collisions and every part inside the volume. Red cross: something to fix. Three dots: still analysing. |

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

### Leaving objects out

Fixtures, a build-plate model or reference geometry: select them and use
**Parts > Ignore Selected**. Hidden objects are never checked.

## How it stays fast

* Each unique mesh is sorted once into a bounding-volume tree. Copies of a part
  share it, whether they are linked duplicates or separate identical meshes.
* Moving a part changes nothing in its tree: the translation is applied while
  querying. Rotating or scaling re-fits the boxes of that one part. Only an
  actual geometry change re-sorts a mesh.
* Results are kept per pair of neighbouring parts. When a part changes, only
  its own pairs are looked at again.
* All pairs that need work are traversed together in vectorised NumPy passes;
  nothing loops over triangles in Python.
* While you drag, intersections are computed completely on every step. The
  clearance search is capped so a frame never stalls; a pair whose search was
  cut short is finished exactly a moment after you stop.
* The first analysis of a large scene, and anything else that is not a live
  edit, runs in small slices in the background. The badge shows progress.

## Limits to know about

* Only mesh objects are checked (after modifiers). Curves, text, collection
  instances and geometry-nodes instances are not.
* Objects in Edit Mode are checked with the shape they had when you entered it;
  they update when you leave Edit Mode.
* "Inside another part" needs the outer part to be a closed surface. Switch off
  nothing for that: it is simply not reported for open shells.
* The amber near-contact region is drawn at a resolution of about a third of
  the clearance; the reported distance and the red/amber/green state are exact.
* Memory use is roughly 70 bytes per triangle and object. The Parts panel
  shows the current figure. Meshes above the size limit in the preferences are
  skipped and listed there.
