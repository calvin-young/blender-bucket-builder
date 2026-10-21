# Print quality and packing: what matters and why

What practitioners know about how the way a build is packed affects the
parts that come out of it, written down so that any tool (this add-on, its
packer, an exporter, a checker) can use the same rules. It is kept as a
document of its own so that it can move to a repository of its own later
and be referenced from other projects.

Scope: how parts are arranged in a build (spacing, position, orientation,
density), not how parts are designed: a packer does not care what is
printed, only how. Powder-bed fusion, written for HP Multi Jet Fusion first;
other technologies are noted where a rule is known to differ.

Published guidance is cited where it exists, but read it with care: much
of it is years old and deliberately conservative (it is written to be safe
for every customer), and service bureaus are often conservative too.
Experience from production counts for more here; where the two disagree,
both are given.

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
document, listed under [Sources](#sources) as [S1], [S2] ...), and
*assumed* (a reasonable default that nobody has confirmed yet; treat its
numbers as placeholders).

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
* **Source**: practitioner; published. **Numbers**: the packer's default is
  5 mm. Published: at least 2 mm "for the best printer stability" [S1];
  at least 5 mm, more than 10 mm for dense parts or walls over 15 mm [S2];
  3 mm as a training baseline [S4]; 2 - 3 mm common in service bureau
  guides [S5]. The gaps used in production per material and printer have
  not been collected yet.

### PQ-02 Gap to the walls

* **Problem**: parts close to the bucket walls come out with worse
  surfaces or dimensions than parts in the middle.
* **Cause**: the powder near the walls cools differently.
* **Detect**: each part's distance to the four side walls.
* **Avoid**: a hard constraint, separate from PQ-01.
* **Source**: practitioner; published. **Numbers**: 5 mm default.
  Published: 10 - 20 mm from the margins of the build volume [S2]; parts
  that must be accurate kept away from the sides [S1]. But a measurement
  of 45 parts in one MJF build found the parts at the edges the *most*
  accurate and those in the middle the least (about 0.12 mm against up to
  0.25 mm of deviation, 30 - 40 % more shrinkage in the middle, put down to
  a middle 5 - 10 degrees C hotter) [S6]. See PQ-11.

### PQ-03 Packing density

* **Problem**: a build that is too dense overheats: parts warp, grow, or
  fuse powder.
* **Cause**: the total heat put into a stretch of the bucket.
* **Detect**: the volume of the parts divided by the volume of the bucket
  **over the height the parts fill, from their lowest point to their
  highest**. Raising the whole build off the floor does not change it.
* **Avoid**: a limit; the packer builds higher rather than exceed it.
* **Source**: practitioner (the definition over the filled height is a
  correction from production experience); published. **Numbers**: 12 %
  default. Published: 8 - 12 % as "a good compromise between throughput
  and part quality", 8 - 10 % where mechanical properties matter [S2];
  over-packed builds also degrade the powder and its reuse [S2] and give
  more scatter between parts [S4].

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
* **Source**: practitioner; published: large changes in printed area from
  layer to layer are to be avoided (a "homogeneity" indicator) [S2], and an
  HP patent on packing counts abrupt changes of cross-section between
  successive slices as undesirable [S7]. **Numbers**: no limit known; the
  packer minimizes the variation as a soft goal.

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
* **Source**: practitioner (the 1 degree too). Possibly related, published:
  "sinks on the tops of parts", which a cosmetic print profile reduces, and
  fewer sinks in the top quarter of the bucket [S2]. **Numbers**: the area
  from which a face counts is assumed (2500 mm^2).

### PQ-08 Faces facing down do not matter

* A large flat face parallel to the floor, facing down, is not a quality
  problem in itself. (Practitioner.) Tools should not penalize it.
  Published guidance agrees that down-facing skins often look better [S4].

### PQ-09 Shallow faces and stair-stepping

* **Problem**: faces at a small angle to the layers show the layer steps.
* **Detect**: large flat regions close to, but not on, an axis.
* **Avoid**: tilt parts so large faces are well away from every axis (the
  Arrange tool's Tilt Off Axis uses 20 degrees).
* **Source**: practitioner, as "a first rule to refine"; published: keep
  up-facing cosmetic faces more than about 20 degrees from the layers to
  hide the steps [S4, S5]. **Numbers**: 20 degrees. With PQ-07 it brackets what a large top face can do: flat to
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

### PQ-11 Where in the bucket a part sits

* **Problem**: the same part comes out differently in different places of
  the bucket: in size, in warpage, in its surface.
* **Cause**: the bucket is not evenly hot; the middle of a build stays
  hotter and cools more slowly than its edges and its top.
* **Detect**: each part's position (distance to the walls, the floor, the
  top of the build).
* **Avoid**: a placement preference for parts marked as critical, and
  keeping a repeat job's parts in the same places (PK-06).
* **Source**: published, and in conflict. Critical or massive parts in the
  middle and low, for more even cooling and less warpage [S1, S2]; parts
  that must look good in the top quarter, for fewer sinks [S2]; but a
  measured build had its middle parts the least accurate [S6]. Which way
  to lean probably depends on what "quality" means for the part (size,
  flatness or surface). Not built; waits for the case studies.

### PQ-12 Large compact cross-sections

* **Problem**: where a layer has a large solid region, heat builds up:
  powder sticks to the surface ("thermal bleed"), sinks, local shrinkage.
* **Cause**: much fused area with little perimeter to lose heat through.
* **Detect**: per layer, regions whose area and area-to-perimeter ratio
  are large [S7]; parts with thick walls (over about 15 mm) [S2].
* **Avoid**: more room around massive parts (more than 10 mm [S2]);
  spreading massive parts across the height and across the bed rather than
  in one layer or one corner [S2]. The packer's layer profile (PQ-04)
  covers part of this; regions within a layer are not yet looked at.
* **Source**: published [S1, S2, S7]. **Numbers**: published.

### PQ-13 Long, thin, flat parts

* **Problem**: long thin beams and long thin flats warp first.
* **Detect**: parts with a large aspect ratio (published rule of thumb:
  flats over 10 to 1 [S5]).
* **Avoid**: lay them flat, horizontal and low in the bucket, and keep the
  build short in Z [S1, S2, S4].
* **Source**: published. Not built (the packer has no per-part placement
  preferences yet).

### PQ-14 Heavy end up?

* An HP patent on thermally aware packing prefers orientations with a
  part's center of mass high in its bounding box (denser portions above
  less dense ones), which may limit heat bleeding downward [S7]. Not
  confirmed by practice here; noted as a candidate rule.


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

### PK-06 Repeat jobs keep their layout

* For a job printed again and again, keep each part's orientation and
  position from run to run: size changes with orientation and place in the
  bucket, so a layout that was tuned once should not move [S4]. A tool
  should save a layout and reuse it, and offer to improve it only on
  request (the add-on's Improve Current Layout starts from the current
  one). (Published; agrees with the practitioner's profile of the maker who
  prints one bucket for weeks.)

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

## Managing different practices

Practices differ between sources, between customers and between printers.
The plan: every rule keeps its numbers per source here; the add-on starts
with one dial (Part Quality) over the soft rules and plain numbers for the
hard ones (gaps, density), and later offers the rules' own numbers to
users who want to tune them, and named profiles (per printer, per
customer: "service bureau", "medical", or one's own) that set them all at
once.

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

## Published guidance at a glance

| Topic | Published | Practice here |
|---|---|---|
| Gap between parts | 2 mm [S1]; 5 mm, over 10 mm for massive parts [S2]; 3 mm [S4]; 2 - 3 mm [S5] | 5 mm default (assumed); to be collected |
| Gap to the walls | 10 - 20 mm [S2]; accurate parts away from the sides [S1] | 5 mm default (assumed); edges measured more accurate [S6] |
| Density | 8 - 12 %, 8 - 10 % for strength [S2]; 8 - 10 % [S4] | 12 % default, over the filled height |
| Layers | avoid large changes in printed area between layers [S2, S7] | even layers (PQ-04) |
| Height | keep the build short; parts low [S1, S2, S4] | matters near the top only (PK-01) |
| Faces | up-facing cosmetic faces over 20 degrees [S4, S5]; down-facing look better [S4] | PQ-05 - PQ-09 |
| Repeat jobs | lock orientation and position [S4] | PK-06 |

## Sources

* [S1] HP, *Design and printing guidelines*, 4AA7-0402ENW, June 2017
  (hosted by a reseller: https://xometry.eu/wp-content/uploads/2020/06/Design-and-Printing-guidelines-3D-Printer-Series.pdf).
* [S2] HP, *MJF Handbook*: Process for accuracy (4AA7-4236ENW), Process
  for aesthetic (4AA7-4237ENW), Process for mechanical properties
  (4AA7-4238ENW), General recommendations (4AA7-4235ENW), March 2019
  (https://3dprint.com/wp-content/uploads/2021/10/ASSET-4-Process-Tuning-your-MJF-to-the-Design.pdf).
* [S3] J. C. Catana, J. Zeng (HP Labs), *Thermal-Pack: a packing engine
  tailored to MJF thermal physics*, 2021
  (https://www.nekomath.com/static/dcg2021/JuanCatana.pdf). No numbers;
  shows that thermally aware packing (density, height, yield) is being
  pursued.
* [S4] RapidMade, *MJF best practices and common mistakes*
  (https://rapidmade.com/mjf-best-practices-and-common-mistakes/) and
  *How to choose the best orientation for MJF parts*
  (https://rapidmade.com/how-to-choose-the-best-orientation-for-mjf-parts-accuracy-efficiency-and-surface-finish/):
  a service bureau's practice, citing HP training.
* [S5] RapidMade, *Engineering guidelines for geometrically stable,
  highly accurate PA12 components via HP MJF*
  (https://rapidmade.com/engineering-guidelines-for-geometrically-stable-highly-accurate-pa12-components-via-hp-mjf/).
* [S6] K. Raz, Z. Chval, P. Faitova, *Accuracy and powder removal limits
  in Multi Jet Fusion 3D printing*, Polymers 17 (20), 2804, 2025
  (https://www.mdpi.com/2073-4360/17/20/2804).
* [S7] HP patent, *Parts packing for a build volume*, US 11,338,521
  (https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11338521).

Not found yet: first-hand operator discussion in forums (searches of
public forums turned up little on packing as opposed to part design).
