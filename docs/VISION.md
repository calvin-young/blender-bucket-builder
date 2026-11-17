# Where Bucket Builder goes

2026-11-16. **Claude's proposal**, written to match the toolset's stages
(the owner's private planning notes define stages 0 - 4 across all his
add-ons; they are summarized here, not linked). Short on purpose: the
designs live in the documents this one points to.

## What it is for

**The place a powder-bed build is finished.** An operator brings parts
in, and leaves with a build they trust: nothing collides, nothing is
closer than it may be, everything is inside the printer, the bucket is as
full as it sensibly can be, and the file that goes to the printer is the
build that was checked.

Three things it does, in the order they matter:

1. **Check.** Live, exact, on builds of millions of triangles. This is
   the reason to install it, and the one part that must never be wrong.
2. **Arrange.** Tools that act on what is selected: place a part, cage a
   handful, rack a few hundred, make and number copies.
3. **Pack.** Fill the build, fix a hand pack, add a late part.

What it is not: a repair tool (it detects, and sends the user to
Blender's 3D Print Toolbox), a slicer, or a section viewer (that is
Crosscut; a problem row will open the spot there).

## What "good" looks like

* A wrong verdict is the worst bug there is. Speed and features wait
  behind it.
* A new user understands every control without a tooltip.
* It feels like part of Blender: parenting, the Outliner, undo, the
  sidebar, Blender's own words.
* The engines (`buildcore`: the check and the packer) work without
  Blender, so builds can be checked and packed from a script.
* Claims are measured: against brute force for the check, against other
  packers for packing, with numbers that can be repeated.

## Milestones

Each stage has one test that says it is done, and is not started in
earnest before the one before it passes. Version numbers are a guide,
not a promise.

| Stage | Theme | Bucket Builder ships | Done when |
| --- | --- | --- | --- |
| **0** | **Trust** (1.5.x) | #19 settled by a run in a window; a slow checker that shares no code with the engine, run over packed layouts; a test that the mirror equals the scene; tests on every push | No known wrong answer in the live check ([AUDIT.md](AUDIT.md)) |
| **1** | **Finish a job** (1.6) | Export with a gate (#9), through the 3MF add-on; two clearances (#35) and the result box (#32); the four Pack actions with Add (#33, #34); "Show in Crosscut" on problem rows; the printer written on the Scene; the engines as a package; drawing order (#28); quiet outside parts until export (#31) | The owner takes a real build from import to an exported print file without leaving Blender, and prints it |
| **2** | **Someone else can use it** (1.7) | Windows tested for real; the panel's place as a preference (#38); hidden parts by default (#30); a user guide that matches the panels; install from the one shared address; repeatable packing runs | A colleague on Windows installs it and exports a build without help |
| **3** | **Operator features** (1.8 - 1.x) | Units, cages, kits, numbered copies, sprue racks ([SPEC_PACK.md](SPEC_PACK.md), in its order); part checks (#29); quantities across several builds and a build record; adding a part from the command line; packer work chosen by the Magics and Netfabb comparisons (#24 - #27) | The lab prepares its routine builds with it for two weeks |
| **4** | **Public** | A listing on Blender's extensions site; demo videos; the Pack panel loses "Experimental" | Strangers file issues |

## What decides the order

* **Stage 0 before everything.** The audit found no wrong answer it could
  reproduce, and also found that the referee of the packer is the same
  engine as the live check. Until an independent checker exists, "the
  build passed" rests on one implementation.
* **The daily friction comes next,** not the biggest feature: two
  clearances and four clear actions change every session the owner has,
  and need no new concept.
* **Units before cages, kits and sprues.** They are one idea; built once,
  the three features are mostly tools that make and tag objects.
* **The packer follows evidence.** No more search work until the Magics
  and Netfabb layouts are judged with our own checker on the same
  builds. If the loss is the 1 mm grid at small gaps, the answer is exact
  refinement after the grid, not a finer grid.

## Deliberately late, or not at all

* A compiled narrow phase for the live check: prototype and measure
  after stage 0 ([AUDIT.md](AUDIT.md), A4). It could remove most of the
  engine's bookkeeping, and it could also be a month spent on something
  already fast enough.
* The packer on the GPU (#25), macOS and Vulkan testing (#3), Blender
  older than 5.2 (#2: declared, untested).
* Repair and hollowing (#37): not built here.
* Hull-shaped cages, gate points proposed from marked faces, collection
  instances as units.

## Open with the owner

* The Pack panel's direction, the cage numbers and the polisher's
  dimensions ([SPEC_PACK.md](SPEC_PACK.md), end).
* The license of the engine package: the audit proposed Apache-2.0; the
  toolset plan keeps GPL-3.0-or-later for now and accepts contributions
  under both, which leaves the choice open. This document follows the
  toolset plan.
* Whether the Magics kit and its results may ever be public.
