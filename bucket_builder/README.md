# Bucket Builder

Live collision, clearance and build-volume monitoring for 3D-print build
preparation in Blender, aimed at dense HP Multi Jet Fusion nests.

Turn it on once and arrange parts as usual: every mesh object of the build is
watched, and the viewport shows where parts intersect, where they are closer
than the clearance you set, and what sticks out of the printer's build volume.
The scene itself is never modified; everything you see is drawn on top.

Requires Blender 4.2 or newer.

## Install

Edit > Preferences > Get Extensions (or Add-ons) > the down arrow in the top
right > Install from Disk, and pick the `bucket_builder` zip file. Installing
a newer version over an older one keeps your printers and settings.

## Use

Open the sidebar in the 3D viewport (N) and the **Bucket** tab.

1. Press **Enable Monitoring**.
2. Move, rotate or scale parts. Results follow while you drag.

| What you see | Meaning |
| --- | --- |
| Red cross-hatching and a red curve | Parts intersect. The curve is where the two surfaces cross; the hatching covers both surfaces around it. |
| A part shaded red | It collides with at least one other part. In a crowded build this is how you tell the parts at fault from their neighbours. |
| Red box around a part | The part is completely inside another part. |
| Amber diagonal hatching, a line and a distance | Parts are closer than the clearance. The line joins the two closest points. |
| Magenta hatching | The part of a mesh that is outside the build volume. |
| Magenta box | A part that is entirely outside the build volume. |
| A wall of the build volume in magenta | Something goes through that wall. The other walls keep their colour. |
| Amber hatching next to a wall, and a distance "to wall" | A part inside the volume but closer to a side wall than the wall gap you asked for. |
| Thin inner box | The limit set by the wall gap. The side a part is too close to turns amber. |
| "~" in front of a distance | The gap is at most that; the search for a smaller one was cut short. This happens between parts with large faces lying side by side. The search still goes on for as long as it could change whether the pair counts as too close. |

### The verdict

The badge in the bottom right corner of the viewport says how the build stands,
and the same text is at the top of the sidebar.

| Badge | Says | When |
| --- | --- | --- |
| Green tick | Build OK | No collisions, everything inside the volume, no warnings. |
| Amber triangle | Clearance Warning | Parts closer than the clearance, nothing worse. |
| Amber triangle | Wall Gap Warning | A part too close to a side wall, nothing worse. |
| Amber triangle | Not Everything Checked | The scene holds something that is left out (see "What is checked"). |
| Red cross | Collision Detected | Parts touch or cut through each other. |
| Red cross | Outside Build Volume | A part reaches out of the volume (and nothing collides). |
| Three dots | Preparing Parts / Checking | Still analysing. A problem that is already known is shown at once. |

The lines under it give the counts, and the last one, in the colour of the
build volume, names the printer the build is being checked for.

### Problems

**Problems** lists everything that needs attention, worst first. Click an
entry, or use **Previous** / **Next**, to frame it in the viewport and select
the parts involved.

**Isolate** shows the parts of the problem you go to on their own, in Blender's
local view. Leave it on and step through the list to see one problem after
the other without the rest of the build; switch it off to see everything
again. The view stays where it is, so switching it on and off shows the same
spot with and without its surroundings. While a viewport is in local view
(also one you entered yourself with numpad `/`) it shows only the problems
among the parts it shows; the badge always speaks for the whole build.
Numpad `.` frames the selected parts as a whole.

### Part Collisions

* **Clearance (mm)**: the minimum gap you want between parts. Closer pairs get
  a warning; untick it to check collisions only. Parts collide when they
  touch or cut through each other.
* **Ignore Hidden Parts**: off by default, because a part you hid to see past
  it is still in the build. Hidden parts are then checked like the others.
  They are not shaded or hatched, but a problem with one is drawn (curve,
  line, marker), listed with "(hidden)" after the name, and counted in the
  verdict. Tick it and anything hidden is left out. Only the eye in the
  Outliner (or `H`) hides a part in this sense. A part that is *disabled in
  viewports* (the screen icon) is not worked out by Blender at all and cannot
  be checked; the Parts section says how many there are.
* **Advanced > Parts Inside Parts**: also report a part that lies completely
  inside another one although their surfaces do not touch. This needs closed
  meshes; switch it off if open shells cause false alarms.
* **Advanced > Units**: *Auto* follows the scene's unit scale, and treats a
  default scene holding millimetre-sized numbers (the usual STL import) as
  1 unit = 1 mm. The line underneath shows what was decided; override it if
  it is wrong.

### Build Volume

* **View Build Volume** frames the whole volume.
* The **printer** menu sets the volume. The plus button saves the current
  size as a new printer, the disk button stores it in the selected printer,
  the minus button deletes the printer. Printers are kept in the add-on
  preferences, so they are there in every file.
* **Show Build Volume** draws it; **Check Build Volume** warns about parts
  that reach out of it.
* **Wall Gap (mm)** is optional. When ticked, a part that sits closer than
  this to a side wall (X and Y; the floor and the top are not counted) gets a
  warning. A part that actually crosses a wall is an error.
* **Advanced**: type a **Size** of your own, place the volume with a corner on
  the scene origin or centred on it (**Origin**), shift it (**Offset**).

### Display

Each thing the overlay draws has its own switch: the badge, the whole overlay,
the shading of colliding parts, the outline of an intersection, the line
between the closest points of two parts, and the markers of collisions and of
clearance warnings (the rings and distances). Below them: how strong the
shading is, the distance between hatch lines in pixels, whether hatching that
is hidden behind geometry shows through (**Hatch X-Ray**) and how strongly,
how much the walls of the volume are tinted, the size of the badge, and the
colours.

### Leaving objects out

Fixtures, a build-plate model or reference geometry are left out with the
**Ignore** switch of an object. It is in three places:

* **Parts > Ignore Selected / Include Selected** in the sidebar, with
  **Include All** and a list of everything that is ignored, each with a
  button to take it back in,
* the right-click menu of the 3D viewport and of the Outliner,
* Object Properties > Visibility.

With **Ignore Hidden Parts** ticked, the eye in the Outliner does the same job
for whatever you hide, a whole collection included.

### What is checked

Each mesh object is checked as the viewport shows it, after its modifiers.
A Subdivision modifier is therefore checked at its viewport level, not its
render level: keep that at what you will export.

* A part with simple modifiers (Subdivision, Array, Mirror ...) is as cheap
  to move as a plain mesh.
* A modifier whose result depends on where the part is (Shrinkwrap, a Boolean
  with another object) changes the mesh on every step of a drag. The check
  follows, but a large part will lag. Apply such modifiers before nesting.
* Only real mesh data is read. The badge and the Parts section tell you when
  the scene holds something that is left out: instances that geometry nodes
  do not realize (add a Realize Instances node), collection instances, and
  text, curve or metaball objects with faces (convert them to meshes). If
  such an object is not part of the build, ignore it and the warning goes.
* While a part is in Edit Mode it is checked with the shape it had when you
  entered; it updates when you leave Edit Mode.

### Large parts

Nothing needs switching on for these, but it helps to know what happens.

* A part of more than about 60 000 triangles is prepared in the background
  when it first appears (a quarter to half a second per million triangles,
  depending on the processor; several parts at a time). The badge shows
  "Preparing Parts" with a count, and Blender stays usable meanwhile.
* Rotating or scaling a large part gives answers at once, from looser data,
  and the exact picture a moment after you let go.
* **Preferences > Add-ons > Bucket Builder > Memory (GB)** limits what the
  checker keeps in memory. Left at 0 it uses a fifth of the installed memory.
  As a guide: 20 bytes per triangle for each different mesh, plus 55 bytes
  per triangle for each part that has neighbours. When the limit is reached
  the checker keeps working with what it has, more slowly, and says so in the
  Performance section. A single part too large for the limit is listed in
  the Parts section as not checked, and the badge shows a warning instead of
  a plain tick.

### When it feels slow

**Performance** shows how long a live update takes, how long the overlay
takes to draw, and the time from one redraw of the viewport to the next while
you move something, which is the frame rate you see. **Copy Report** puts
these and a description of the build (number of parts and triangles, the
graphics card, the settings) on the clipboard as a few lines of text. Paste
that into a message when you report something slow: it says where the time
goes. **Recheck Everything** throws all cached data away and starts again.

## How it stays fast

* Each unique mesh is sorted once into a bounding-volume tree. Copies of a part
  share it, whether they are linked duplicates or separate identical meshes.
* Moving a part changes nothing in its tree: the translation is applied while
  querying. While a part is rotated or scaled, the boxes it had are turned as
  they are looked at; a moment after you stop they are fitted again, in
  pieces of a millisecond or two. Only an actual geometry change re-sorts a
  mesh, and that happens in the background.
* Results are kept per pair of neighbouring parts. When a part changes, only
  its own pairs are looked at again; parts that are moved together keep the
  results among themselves.
* All pairs that need work are traversed together in vectorised NumPy passes;
  nothing loops over triangles in Python.
* While you drag, every step has a time budget. Whether parts collide is
  decided on every step, and the colliding parts are shaded at once. The
  curve, the hatching and the exact clearance are worked out for as many
  neighbours as fit in the budget and for all of them a moment after you
  stop. So a part dropped into the middle of a full build can still be
  dragged out smoothly.
* One case takes longer than a step: two finely meshed surfaces that lie
  almost exactly on each other, such as a part and a copy of it moved by a
  fraction of a millimetre. While you move them the pair may show as "too
  close" and the badge says "Checking"; the answer follows a moment after
  you stop. A copy that lies exactly on its original is reported as a
  collision at once.
* The first analysis of a large scene, and anything else that is not a live
  edit, runs in slices in the background. The badge shows progress. While
  you edit or turn the view the slices are short, so Blender stays smooth;
  while you wait they are longer and follow each other directly, so the
  result comes sooner.

## Limits to know about

* "Inside another part" needs the outer part to be a closed surface; it is
  simply not reported for open shells.
* The amber near-contact region is drawn at a resolution of about a third of
  the clearance; the reported distance and the red/amber/green state are exact
  (see "~" above for the one exception).
* Parts above the size limit in the preferences (50 million triangles unless
  you change it) are skipped and listed in the Parts section.
* Two surfaces closer than about a micron (two millionths of the size of the
  build) count as touching.
