# Bucket Builder

Live collision, clearance and build-volume monitoring for 3D-print build
preparation in Blender, aimed at dense HP Multi Jet Fusion nests.

Switch on what you want checked and arrange parts as usual: every mesh object
of the build is watched, and the viewport shows where parts intersect, where
they are closer than the clearance you set, and what sticks out of the
printer's build volume. The scene itself is never modified; everything you see
is drawn on top.

Requires Blender 4.2 or newer.

## Install

Edit > Preferences > Get Extensions (or Add-ons) > the down arrow in the top
right > Install from Disk, and pick the `bucket_builder` zip file. Installing
a newer version over an older one keeps your printers and settings.

## Use

Open the sidebar in the 3D viewport (N) and the **Bucket** tab. It has six
panels; each can be folded away by a click on its title.

| Panel | What is in it |
| --- | --- |
| **Bucket Builder** | How the build stands, and the list of problems. |
| **Collision Detection** | The switch for collisions and clearance, their settings, and which parts are checked. |
| **Build Volume** | The switch for the build volume, the printer, the wall gap. |
| **Build Report** | The switch for the density report: parts, volume, packed height, 3D density, the density of every layer and a picture of it. |
| **Options** | Part colors, what is drawn, performance figures, units. |
| **Export** | Writing the build to a 3MF file (not in this version yet). |

There are two switches, and they are independent:

1. **Detect Collisions** checks every part against its neighbors.
2. **Monitor Build Volume** shows the printer's build volume and checks that
   every part is inside it.

Both are off in a new scene. Switch on one or both, then move, rotate or scale
parts: results follow while you drag.

| What you see | Meaning |
| --- | --- |
| Red cross-hatching and a red curve | Parts intersect. The curve is where the two surfaces cross; the hatching covers both surfaces around it. |
| A part shaded red | It collides with at least one other part. In a crowded build this is how you tell the parts at fault from their neighbors. |
| Red box around a part | The part is completely inside another part. |
| Amber diagonal hatching, a line and a distance | Parts are closer than the clearance. The line joins the two closest points. |
| Magenta hatching | The part of a mesh that is outside the build volume. |
| Magenta box | A part that is entirely outside the build volume. |
| A wall of the build volume in magenta | Something goes through that wall. The other walls keep their color. |
| Amber hatching next to a wall, and a distance "to wall" | A part inside the volume but closer to a side wall than the wall gap you asked for. |
| Thin inner box | The limit set by the wall gap. The side a part is too close to turns amber. |
| A small gray ring | A problem you have chosen to ignore (see below). |
| "~" in front of a distance | The gap is at most that; the search for the smallest one was cut short. This happens between parts with large faces lying side by side. Whether the pair touches, and whether it keeps the clearance, is never left at that: those two questions are searched to the end (the verdict says "Checking" meanwhile). |

### The verdict

The **Bucket Monitoring** sign at the top of the sidebar is lit while
something is being checked, and gray while both switches are off. The box
under it says how the build stands, one line for each kind of problem, the
most serious first, each with an icon in its color. The badge in the bottom
right corner of the viewport shows the same lines: the first one large, with
a large icon.

| Icon | Line | When |
| --- | --- | --- |
| Magenta square | 2 Parts Outside Build Volume | Parts reach out of the volume, or are outside it altogether. |
| Red cross | 3 Collisions Detected | Parts touch or cut through each other. |
| Amber triangle | 4 Clearance Warnings | Parts closer than the clearance. |
| Amber square | 1 Part Within Wall Gap | A part too close to a side wall. |
| Amber triangle | Not Everything Checked | The scene holds something that is left out (see "What is checked"). |
| Amber ring | 2 problems ignored / 1 part ignored | What you have left out on purpose, so that it is not forgotten when the build is finished. |
| Green tick | Build OK | Both checks are on and there is nothing to report. With only one of them on it says **No Collisions** or **Inside Build Volume**. |
| Magnifier | Local view: 2 of 6 parts shown | This viewport shows some of the parts only (see "Problems"). The lines above it are about the whole build all the same. |
| Gray | Preparing Parts / Checking | Still analyzing: parts are being read, pairs are being checked, or gaps that could still turn out to be under the clearance are being measured. A problem that is already known is shown at once. |
| Gray | Not checking collisions / Not checking the build volume | That switch is off. |

The last line, with the "i", names the printer the build is being checked
for (in the badge it is in the color of the build volume).

The badge sits at the right edge of the viewport. The sidebar lies on top of
the viewport there, but only as far down as its panels go: when they reach
the badge, the badge moves to the left of the sidebar, and back when you fold
panels away or switch to a shorter tab.

### Problems

**Problems** lists everything that needs attention, in order of severity:
outside the build volume, collisions, clearance, wall gap. Clearance warnings
are sorted by their gap, the closest pair first, and so are the wall-gap
warnings; the gap is what their line starts with: `[3.0 mm] Block | Pin`.
Each kind has its icon, and a slim tab at the left end of the line in the
color that kind has in the viewport: magenta, red, amber.

Click a line, or use **Previous** / **Next**, to frame the problem in the
viewport and select the parts involved; the status bar then names them in
full. The list scrolls when it is long, and its lower edge can be dragged to
make it taller.

Every entry has three buttons:

* **Magnifier: isolate.** Shows the parts of that problem on their own, in
  Blender's local view. Click it again to see the whole build. The view stays
  where it is, so switching it on and off shows the same spot with and
  without its surroundings. The magnifier between Previous and Next is the
  same switch for whatever entry you are at: leave it on and step through
  the list to see one problem after the other.
* **Eye: ignore.** The problem stays in the list, grayed, but it is no longer
  drawn and no longer counts: the verdict is about what is left, and says
  "1 problem ignored". It counts again by itself as soon as it changes, for
  instance when one of the parts is moved. Moving both parts together does
  not change it. Click the eye again to stop ignoring it.
* **Lock: ignore for good.** However the parts are moved, the problem does not
  count. Use it for parts that are meant to touch, or for a part that is
  meant to stand outside. One thing a lock does not cover: two parts locked
  as *too close* count again when they *collide*, because that is another
  problem and a worse one. (A plain "ignore" of a clearance warning does not
  cover a collision either.) Click the lock again to go back to ignoring the
  problem only as it is now.

**Ignore None**, under the list, forgets everything that is ignored. The
line beside it counts what is ignored, also what is "not in the list": a
lock on two parts that are apart just now is still there, and takes effect
when they meet again. Ignored problems are saved with the scene.

While a viewport is in local view (through the magnifier, or with numpad `/`)
it shows the problems of the parts it shows. If you move one of them into a
part that is not in the view, that part appears shaded red, as a ghost:
nothing new goes unnoticed while you work on two parts. If you would rather
see every problem of the build in such a view, set **Options > Display > In
Local View** to *Whole Build*. The verdict always speaks for the whole build;
in the badge of such a viewport, and in the box at the top of its sidebar, a
line says how many of the parts are to be seen ("Local view: 2 of 6 parts
shown"). Numpad `.` frames the selected parts as a whole.

### Collision Detection

* **Detect Collisions**: the switch. While it is off nothing about pairs of
  parts is worked out; what was known is kept, and switching it on again
  only looks at what has moved since.
* **Clearance Between Parts**: the minimum gap you want between parts, in
  millimeters. Closer pairs get a warning; untick it to check collisions
  only. Parts collide when they touch or cut through each other.
* **Ignore Hidden Parts**: off by default, because a part you hid to see past
  it is still in the build. Hidden parts are then checked like the others.
  They are not shaded or hatched, but a problem with one is drawn (curve,
  line, marker), listed with "(hidden)" after the name, and counted in the
  verdict. Tick it and anything hidden is left out, of the build volume check
  as well; the verdict then says how many hidden parts are not checked. Only
  the eye in the Outliner (or `H`) hides a part in this sense. A part that is
  *disabled in viewports* (the screen icon) is not worked out by Blender at
  all and cannot be checked; **Parts** says how many there are.
* **Parts Inside Parts**: also report a part that lies completely inside
  another one although their surfaces do not touch. This needs closed
  meshes; switch it off if open shells cause false alarms.
* **Parts** (fold it open): leaving objects out of the checks, see below.

### Build Volume

* **Monitor Build Volume**: the switch. While it is off the volume is neither
  drawn nor checked, whatever the two checkboxes say.
* The **printer** menu sets the volume. The plus button saves the current
  size as a new printer, the disk button stores it in the selected printer,
  the minus button deletes the printer. Printers are kept in the add-on
  preferences, so they are there in every file.
* **View Build Volume** frames the whole volume. (A new Blender viewport
  does not see further than 1000 units; if the volume would be cut off, the
  view's Clip End is raised.)
* **Show Build Volume** draws it; **Check Build Volume** warns about parts
  that reach out of it. Under the switch above, each works on its own.
* **Wall Gap** (in millimeters) is optional. When ticked, a part that sits closer than
  this to a side wall (X and Y; the floor and the top are not counted) gets a
  warning. A part that actually crosses a wall is an error.
* **Advanced**: type a **Size** of your own, place the volume with a corner on
  the scene origin or centered on it (**Origin**), shift it (**Offset**).

### Options

* **Part Colors**: two buttons that switch the viewport between *Custom* (one
  color for every part, which lets the problems stand out; the swatch
  underneath picks it) and *Random* (a color per part, which tells the parts
  apart). These are Blender's own settings of solid shading; if the viewport
  is in another shading mode, a button to go back to solid shading appears.
* **Display**: a switch for each thing that is drawn. First the badge and the
  whole overlay; under *Collision* the shading of colliding parts, the
  outline of an intersection and the collision markers; under *Clearance*
  the line between the closest points, the ring that marks a warning and the
  distance written next to it (the last two are separate: you can have the
  distance without the ring). Then what a viewport in local view shows, how
  strong the shading is, the distance between hatch lines in pixels, whether
  hatching that is hidden behind geometry shows through (**Hatch X-Ray**)
  and how strongly, how much the walls of the volume are tinted, the size of
  the badge, and the colors.
* **Performance**: see "When it feels slow".
* **Units**: *Auto* follows the scene's unit scale, and treats a default
  scene holding millimeter-sized numbers (the usual STL import) as
  1 unit = 1 mm. The line underneath shows what was decided; override it if
  it is wrong.

### Build Report

**Density Report** works out, from the parts as they are now, what a printer
operator asks first of a build:

* **Volume**, the total of the parts' exact volumes (a closed mesh; the
  evaluated mesh, so modifiers count), and the **packed height**, the
  highest point above the floor of the volume.
* **3D Density**, volume divided by build area times packed height. A part
  that is only partly inside the volume is counted in full, and the density
  line carries a warning saying so; a part wholly outside is listed and left
  out.
* **Peak Layer** and **Layer Spread**: the volume is cut into 1 mm layers;
  the picture shows the density of every layer with the floor at the bottom,
  and the spread is how much that varies (over a 5 mm window; 0 is perfectly
  even).
* Warnings for meshes that are not closed (their volume cannot be trusted)
  and for parts that are inside out.

It needs one of the two checks above to be on, because those are what read
the parts. It runs in a worker thread and keeps what it has learned: moving a
part sideways costs nothing, lifting or turning it costs one pass over its
mesh (about 0.2 s per million triangles on one core).

Without Blender, `python3 tools/pack_report.py bucket.3mf --printer 5600`
prints the same figures for a 3MF or STL file. Its output has no file or
part names unless `--names` is given.

### Arrange

Tools for placing parts by hand. Each acts on every selected part on its
own, changes only where the part is and how it is turned (never its mesh),
and can be undone.

* **Drop to Floor** (also **Ctrl+Down Arrow** in Object Mode): moves each
  part straight down until it rests on the floor of the build volume.
* **Minimize Box Volume**: turns the part so that its bounding box is as
  small as it can be, with the shortest side upright.
* **Minimize Height**: turns the part so that it is as low as it can be,
  lying on a face of its outer shape, and turns its footprint to the
  smallest rectangle.
* **Tilt Off Axis**: turns the part so that its large flat faces are at
  least 20 degrees away from every axis (so from the layers and from the
  walls), which avoids stair-stepping on them; the larger a face, the more
  it counts. The angle can be changed in the Adjust Last Operation panel. A
  part without large flat faces is left as it is.
* **Snap to Build Volume** (a switch): while it is on, parts snap to the
  edges and corners of the build volume with Blender's own snapping (hold
  Ctrl while moving). It adds a wireframe of the volume called "Build Volume
  (snap)" that cannot be selected, is not rendered and is not checked; it
  follows the volume when you change printer, and goes when you switch the
  snapping off. If you have ticked "Snap onto Selectable Only" in Blender's
  snapping options, untick it: the wireframe is not selectable. Leave it out
  when you export the scene as a whole.

### Export

**Export 3MF** is a placeholder: writing the build to a 3MF file comes in a
later version. It already says what an export must not let through without
a word: parts outside the build volume, a build volume that is not being
checked, a check that is still running.

### Leaving objects out

Fixtures, a build-plate model or reference geometry are left out with the
**Ignore** switch of an object. It is in three places:

* **Collision Detection > Parts > Ignore Selected / Include Selected** in the
  sidebar, with **Include All** and a list of everything that is ignored,
  each with a button to take it back in,
* the right-click menu of the 3D viewport and of the Outliner,
* Object Properties > Visibility.

An ignored part is out of both checks. The verdict says "3 parts ignored" for
as long as there are any, in the sidebar and next to the badge.

With **Ignore Hidden Parts** ticked, the eye in the Outliner does the same job
for whatever you hide, a whole collection included.

To leave out one problem instead of a whole part, use the eye in its row of
the list of problems.

### What is checked

Each mesh object is checked as the viewport shows it, after its modifiers.
A Subdivision modifier is therefore checked at its viewport level, not its
render level: keep that at what you will export.

* A part with simple modifiers (Subdivision, Array, Mirror ...) is as cheap
  to move as a plain mesh.
* A modifier whose result depends on where the part is (Shrinkwrap, a Boolean
  with another object) changes the mesh on every step of a drag. The check
  follows, but a large part will lag. Apply such modifiers before nesting.
* Only real mesh data is read. The verdict and **Parts** tell you when the
  scene holds something that is left out: instances that geometry nodes do
  not realize (add a Realize Instances node), collection instances, and
  text, curve or metaball objects with faces (convert them to meshes). If
  such an object is not part of the build, ignore it and the warning goes.
* While a part is in Edit Mode it is checked with the shape it had when you
  entered; it updates when you leave Edit Mode.

### Large parts

Nothing needs switching on for these, but it helps to know what happens.

* A part of more than about 60 000 triangles is prepared in the background
  when it first appears (a quarter to half a second per million triangles and
  processor thread; several parts at a time). Where the part is, is known at
  once: whether it is inside the build volume, and that it is nowhere near
  most of the others, is said without waiting. Only the parts that are close
  to each other wait, and their meshes are prepared first. The verdict shows
  "Preparing Parts" with a count meanwhile, and Blender stays usable.
* Rotating or scaling a large part gives answers at once, from looser data,
  and the exact picture a moment after you let go.
* Switching both checks off does not throw the prepared parts away, and
  neither does hiding a part while Ignore Hidden Parts is ticked: switching
  on again, or showing the part again, does not prepare anything a second
  time. **Options > Performance > Recheck Everything** does throw it all away.
* **Preferences > Add-ons > Bucket Builder > Memory (GB)** limits what the
  checker keeps in memory. Left at 0 it uses a fifth of the installed memory.
  As a guide: 20 bytes per triangle for each different mesh, plus 55 bytes
  per triangle for each part that has neighbors. When the limit is reached
  the checker keeps working with what it has, more slowly, and says so under
  Performance. A single part too large for the limit is listed under Parts
  as not checked, and the verdict shows a warning instead of a plain tick.

### When it feels slow

**Options > Performance** shows how long a live update takes, how long the
overlay takes to draw, and the time from one redraw of the viewport to the
next while you move something, which is the frame rate you see. **Copy
Report** puts these and a description of the build (number of parts and
triangles, the graphics card, the settings) on the clipboard as a few lines of
text. Paste that into a message when you report something slow: it says where
the time goes. **Recheck Everything** throws all cached data away and starts
again.

## How it stays fast

* Each unique mesh is sorted once into a bounding-volume tree. Copies of a part
  share it, whether they are linked duplicates or separate identical meshes.
* Moving a part changes nothing in its tree: the translation is applied while
  querying. While a part is rotated or scaled, the boxes it had are turned as
  they are looked at; a moment after you stop they are fitted again, in
  pieces of a millisecond or two. Only an actual geometry change re-sorts a
  mesh, and that happens in the background.
* Results are kept per pair of neighboring parts. When a part changes, only
  its own pairs are looked at again; parts that are moved together keep the
  results among themselves.
* All pairs that need work are traversed together in vectorised NumPy passes;
  nothing loops over triangles in Python.
* While you drag, every step has a time budget. Whether parts collide is
  decided on every step, and the colliding parts are shaded at once. The
  curve, the hatching and the exact clearance are worked out for as many
  neighbors as fit in the budget and for all of them a moment after you
  stop. So a part dropped into the middle of a full build can still be
  dragged out smoothly.
* One case takes longer than a step: two finely meshed surfaces that lie
  almost exactly on each other, or flat on each other over a large area (a
  part and a copy of it moved by a fraction of a millimeter, a flange
  resting on a plate, two large faces at about the clearance). While you
  move them the pair may show as "too close" and the verdict says
  "Checking"; the answer follows a moment after you stop, or, for the
  largest of such areas, some seconds after. A copy that lies exactly on its
  original is reported as a collision at once.
* The first analysis of a large scene, and anything else that is not a live
  edit, runs in slices in the background. The verdict shows progress. While
  you edit or turn the view the slices are short, so Blender stays smooth;
  while you wait they are longer and follow each other directly, so the
  result comes sooner.
* With only the build volume monitored there is next to nothing to do: a
  part's place is known from its corner points, without its tree.

## Limits to know about

* "Inside another part" needs the outer part to be a closed surface; it is
  simply not reported for open shells.
* The amber near-contact region is drawn at a resolution of about a third of
  the clearance; the reported distance and the red/amber/green state are exact
  (see "~" above for the one exception).
* Parts above the size limit in the preferences (50 million triangles unless
  you change it) are skipped and listed under Parts.
* Two surfaces closer than about a micron (two millionths of the size of the
  build) count as touching.
* The colored icons of the box at the top of the sidebar keep their colors
  when you change the overlay's colors in Options > Display > Colors; the
  viewport and the tabs in the list of problems follow.
* A row of a Blender panel cannot be given a background color by an add-on,
  and its text can only be made red. That is why the list of problems carries
  its colors as tabs, and the box at the top as icons beside plain text.
