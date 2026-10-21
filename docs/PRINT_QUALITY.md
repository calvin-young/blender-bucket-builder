# Print quality and packing: what matters and why

What practitioners know about how the way a build is packed affects the
parts that come out of it, written down so that any tool (this add-on, its
packer, an exporter, a checker) can use the same rules. It is kept as a
document of its own so that it can move to a repository of its own later
and be referenced from other projects.

Scope: powder-bed fusion, written for HP Multi Jet Fusion first. Other
technologies are noted where a rule is known to differ.

## How to read a rule

Each rule has a stable ID (`PQ-` for part quality, `PK-` for packing
policy) so that code, tests and other documents can point to it. A rule
says:

* **Problem**: what goes wrong on the part, as an operator would see it.
* **Cause**: what is known or believed about why. "Not yet explained" is an
  honest answer.
* **Detect**: how a tool can see that a layout is at risk.
* **Avoid**: what a packer does about it, and what it costs.
* **Who cares**: some customers care a lot, some not at all (see
  [The quality / efficiency dial](#the-quality--efficiency-dial)).
* **Applies to**: technologies and printers.
* **Source** and **numbers**: where the rule comes from, and whether its
  numbers are measured, taken from a practitioner, or placeholders.

Sources are of three kinds: *practitioner* (experience from running
printers in production, passed on in conversation), *published* (a cited
document), and *assumed* (a reasonable default that nobody has confirmed
yet; treat its numbers as placeholders).

## Part quality rules

### PQ-01 Gap between parts

* **Problem**: parts too close fuse together, or come out with powder
  sintered onto the faces that were close.
* **Cause**: heat from one part sinters the powder between it and its
  neighbor.
* **Detect**: the smallest distance between any two parts, on the meshes.
* **Avoid**: a hard constraint: never closer than the gap.
* **Who cares**: everybody; the value differs between customers who want
  clean faces and those who accept some cleanup.
* **Applies to**: all powder-bed processes; the value depends on material
  and printer.
* **Source**: practitioner. **Numbers**: assumed (the packer's default is
  5 mm; the gaps used per material and printer have not been collected yet).

### PQ-02 Gap to the walls

* **Problem**: parts close to the bucket walls come out with worse
  surfaces or dimensions than parts in the middle.
* **Cause**: the powder near the walls cools differently.
* **Detect**: each part's distance to the four side walls.
* **Avoid**: a hard constraint, separate from PQ-01.
* **Source**: practitioner. **Numbers**: assumed (5 mm default).

### PQ-03 Packing density

* **Problem**: a build that is too dense overheats: parts warp, grow, or
  fuse powder.
* **Cause**: the total heat put into a stretch of the bucket.
* **Detect**: the volume of the parts divided by the volume of the bucket
  **over the height the parts fill, from their lowest point to their
  highest**. Raising the whole build off the floor does not change it.
* **Avoid**: a limit; the packer builds higher rather than exceed it.
* **Source**: practitioner (the definition over the filled height is a
  correction from production experience). **Numbers**: assumed (12 %
  default).

### PQ-04 Even density from layer to layer

* **Problem**: a crowded layer with little above or below it (49 parts in
  one layer and one part on top) is a bad build even when its overall
  density is fine.
* **Cause**: believed to be the heat of that layer concentrated in a short
  stretch of the bucket (an inference, not confirmed).
* **Detect**: the density of each layer (the add-on uses 1 mm layers) and
  how much it varies.
* **Avoid**: stagger parts across the height, never above the build's top.
  Some users want parts kept low instead, to fill the build up by hand
  afterwards, so this is a switch.
* **Source**: practitioner. **Numbers**: no limit known; the packer
  minimizes the variation as a soft goal.

### PQ-05 Large flat faces facing each other ("elephant skin")

* **Problem**: two large flat faces of different parts, parallel and close
  together, come out with a wrinkled, rough surface.
* **Cause**: not yet explained here.
* **Detect**: large flat regions of each part (triangles grouped by normal
  and plane); pairs of opposite normals, overlapping when projected, closer
  than a distance.
* **Avoid**: a soft goal: move or turn parts apart where that costs little
  height.
* **Who cares**: customers who care about surfaces.
* **Source**: practitioner. **Numbers**: assumed (faces from 2500 mm^2,
  pairs closer than 10 mm, within 5 degrees of parallel).

### PQ-06 Large flat faces parallel to the side walls

* **Problem**: the same kind of surface defect as PQ-05 on large faces
  that run parallel to a side wall.
* **Cause**: not yet explained here.
* **Detect**: large flat regions whose normal is within a few degrees of
  the x or y axis.
* **Avoid**: a soft goal: turn such parts about the vertical (45 degrees
  is the first thing tried).
* **Source**: practitioner. **Numbers**: assumed (within 10 degrees).

### PQ-07 Large flat faces facing up ("capillary" or "meniscus" defect)

* **Problem**: a flat face that faces up, parallel to the top of the
  bucket, comes out cupped: its edges sharp and turned up, its middle
  sagging a little.
* **Cause**: not yet explained here; the names suggest the shape of a
  liquid's surface in a narrow tube.
* **Detect**: large flat regions whose normal is within about 1 degree of
  +z. The defect needs the face to be really flat to the layers: about one
  degree off, it is no longer seen.
* **Avoid**: a soft goal: turn the part so the face does not face up. A
  small tilt also avoids it, but a face only slightly tilted shows the layer
  steps instead (PQ-09), so a tilt has to be large enough for that.
* **Who cares**: some customers a great deal, others not at all: off by
  default for the efficiency end of the dial.
* **Source**: practitioner (the 1 degree too). **Numbers**: the area from
  which a face counts is assumed (2500 mm^2).

### PQ-08 Faces facing down do not matter

* A large flat face parallel to the floor, facing down, is not a quality
  problem in itself. (Practitioner.) Tools should not penalize it.

### PQ-09 Shallow faces and stair-stepping

* **Problem**: faces at a small angle to the layers show the layer steps.
* **Detect**: large flat regions close to, but not on, an axis.
* **Avoid**: tilt parts so large faces are well away from every axis (the
  Arrange tool's Tilt Off Axis uses 20 degrees).
* **Source**: practitioner, as "a first rule to refine". **Numbers**:
  assumed. With PQ-07 it brackets what a large top face can do: flat to
  within about a degree (meniscus), tilted a little (steps), or tilted
  well away. How the tilt trades against PQ-06 (faces off the walls) has
  not been worked out.

### PQ-10 Interlocking parts

* **Problem**: parts packed so that they are linked (two rings through
  each other) cannot be taken apart after the print, although they never
  touch.
* **Cause**: topology: the parts are disjoint but not separable.
* **Detect**: whether one part's closed loop is threaded by another's.
  A ring that merely hangs on a straight shaft (an O on an I) is not
  linked: it slides off. A topological test (linking number of the loops)
  tells the two apart.
* **Avoid**: a hard constraint once detected; rare in practice, so the
  check may run in the background after every other check, with the
  verdict marked as provisional ("Build OK*") until it is done.
* **Source**: practitioner. **Numbers**: none needed.

## Packing policy

These are about what a good layout is for the business, not about the
parts' surfaces.

### PK-01 Height matters near the top of the bucket only

* Whether everything fits is what really matters. Below that, a few
  millimeters of height make little difference (157 against 164 mm: nobody
  cares); near the top of the bucket they decide whether a job fits (377
  against 384 mm in a 380 mm bucket: a deal breaker). A packer should
  weigh height lightly far below the limit and heavily near it, and try
  much harder when it is close to or over the limit. (Practitioner.)

### PK-02 Big parts first

* When not everything fits, what is left over should be small parts: they
  fit more easily into the next build, and a big part that fails is
  reprinted sooner if it was in this one. (Practitioner.)

### PK-03 Fast or thorough

* A service bureau packing a day's thousand mixed parts into several
  printers wants a good layout quickly. A maker printing the same bucket
  for weeks wants the best layout and will wait for it (one bucket instead
  of one and a half). (Practitioner.)

### PK-04 Offer choices

* Sometimes the answer is a choice: one bucket that fits everything at the
  cost of part quality, or two buckets with the quality rules kept. A tool
  should be able to show both and let the operator choose. (Practitioner.)

### PK-05 Rules depend on the printer and the technology

* Printers of one family differ in small ways (a 580 and a 5600, for
  example), and other print technologies have different rules: a tool
  should switch rules on and off, and change their numbers, by the printer
  chosen. (Practitioner.)

## The quality / efficiency dial

One way to think about all of this is a dial between part quality and
packing efficiency. At one end, a customer accepts a lower density for
better parts: more room between parts and from the walls, preferred
orientations, even layers, no large faces facing each other, the walls or
the top. At the other end: "fit in as many parts as possible, throw all the
other rules out of the window". The hard rules stay at every setting
(nothing collides, nothing is outside the bucket, the density limit as the
customer sets it); the soft rules scale with the dial.

| Rule | Quality end | Middle | Efficiency end |
|---|---|---|---|
| PQ-01, PQ-02 gaps | as set (maybe larger) | as set | as set |
| PQ-03 density limit | as set | as set | as set |
| PQ-04 even layers | strong | on | off |
| PQ-05 facing faces | strong | on | off |
| PQ-06 faces by walls | strong | on | off |
| PQ-07 faces facing up | on | off | off |
| PK-01 height | light below the limit | light | strongest |

## Open questions

* The gaps, wall gaps and density limits used per material and printer.
* Why PQ-05, PQ-06 and PQ-07 happen; whether a face's size, its distance
  or its depth in the bucket matters most.
* Whether PQ-07 depends on where in the bucket the face is.
* How PQ-09's tilt should be traded against PQ-06 and PQ-07.
* How nested parts that are too dense in one layer should be handled
  (re-nested farther apart?).

## Where the rules are used

* `bucket_builder/pack/quality.py`: PQ-05, PQ-06, PQ-07 as soft goals of
  the packer, and in the Build Report.
* `bucket_builder/pack/`: PQ-01 to PQ-04 as constraints and goals; PK-01 to
  PK-03 in the goals and the Effort choice.
* `bucket_builder/arrange.py`: PQ-09 (Tilt Off Axis).
* `docs/AUTOPACK.md`: the packer's design, with references to these IDs.
