# The collision engine: design, testing and performance

How the live build check stays exact and fast. The user guide is
[bucket_builder/README.md](../bucket_builder/README.md); the packer has its
own spec in [AUTOPACK.md](AUTOPACK.md).

## How it works

    scene objects
        -> broad phase: bounding boxes of the parts that changed against all others
        -> narrow phase: both parts' bounding-volume trees, traversed together
        -> exact triangle / triangle intersection and distance
        -> viewport drawing

* **One tree per unique mesh.** Triangles are sorted once into an implicit
  bounding-volume tree. Copies of a part share it. Sorting costs about half a
  microsecond per triangle; for large meshes it is done by worker threads,
  which only ever see NumPy arrays.
* **Moving is free.** A part's tree is stored without its translation; the
  offset between two parts is applied while querying. Dragging a part
  therefore rebuilds nothing.
* **Rotating or scaling borrows.** New boxes for a rotated part cost about
  45 ns per triangle, too much per mouse step for a large mesh. So while it is
  edited the part keeps the boxes it had, and they are moved into the new
  orientation as they are looked at (looser, so queries cost about twice as
  much, but nothing is recomputed). Its triangles are posed from the mesh's
  own vertices with the same expression a fitted tree is made with, so they
  are bit-identical to it. A moment after the edit the boxes are fitted
  again, in pieces of a millisecond or two, and what was worked out with
  borrowed boxes is redone: what is on screen at rest does not depend on how
  the parts got there.
* **Only what changed is recomputed.** Results are kept per pair of
  neighboring parts; when a part moves, only its own pairs are looked at.
* **No Python loops over triangles.** All pairs that need work are traversed
  together, level by level, in NumPy.
* **A live step has a time budget.** Whether two parts collide is decided
  on every step. How much more each pair gets (the complete intersection
  curve, the hatched region, the proven clearance distance) follows the
  measured cost: with a few neighbors every pair gets everything; when a part
  lands on dozens of others, most pairs are only *sketched* (one intersecting
  triangle pair proves a collision) and the detail fills in for as many as
  fit. Whatever was left out is completed exactly just after release.
* **Whether parts intersect is never left to a search that was cut short.**
  Two fine meshes that nearly coincide (a part and its copy a fraction of a
  triangle away) have millions of triangle pairs with overlapping boxes. The
  search of one pass is bounded, and a live step has a budget for all its
  pairs together, so that a dozen parts duplicated in place and nudged do
  not stall a step for seconds. A pair that runs into a bound without a hit
  is not called clear: it stays "checking" and is settled by an exhaustive
  search that stops at the first cut and is done a slice at a time. A copy
  lying exactly on its original is in contact, which is found at once.
* **Nor is "do they touch" or "do they keep the clearance".** The proof of
  the distance between two parts is bounded as well. Where it is cut short
  and what it left out could still hold a contact, or a gap under the
  clearance (a flange lying flat on a plate, two large faces at about the
  clearance), the same exhaustive search goes through all of it.
* **Nothing blocks.** Fitted trees are built in slices between redraws, and
  pairs whose trees are ready are solved first, so the results of a new scene
  appear as the work gets done.
* **Background work paces itself by what the user is doing.** While they edit
  or turn the view, its slices are short and come with pauses. While they
  wait, slices are longer and follow each other directly, and the viewport is
  asked to show progress about ten times a second, not after every slice: a
  redraw of a heavy scene costs more than a slice, and used to be most of the
  time a result took to appear.
* **Memory is bounded.** The fitted trees (55 bytes per triangle and part) are
  a cache with a limit; the least recently used are dropped and built again
  when needed. A part without neighbors never gets one.
* **Colliding parts are shaded from a mesh uploaded once** per unique part and
  redrawn with the object's current matrix, so the cheapest answer (collides
  or not) costs nothing to draw while a part moves.


## Why not `mathutils.bvhtree.BVHTree`

It was the starting point, and it is still used as an independent check in the
tests. Two things rule it out for the live path:

* `BVHTree.overlap()` takes no transform, so both trees must be in world
  space and a moving part's tree has to be rebuilt on every step of a drag.
* It reports overlapping triangles only. There is no tree-to-tree distance
  query, which the clearance check needs.

Measured on the same machine, one step of dragging a part into its neighbor,
collision check only (`tests/blender_bvhtree_compare.py`):

| Triangles (both parts) | BVHTree: rebuild the moving tree + overlap | This add-on |
| ---: | ---: | ---: |
| 6,000 | 1.4 ms | 3.6 ms |
| 24,000 | 5.5 ms | 6.3 ms |
| 98,000 | 26.9 ms | 8.0 ms |
| 327,000 | 44.5 ms | 8.0 ms |

The add-on's figure also covers the complete intersection curve and the
geometry the overlay draws. Both find the same intersecting triangle pairs,
with one exception in the 64 steps of that test: a pair that touches along a
millionth of a millimeter, which one counts and the other does not.

## How it's tested

As of version 1.4.0, on Blender 5.2.2 (Linux, software OpenGL):

* the geometry engine agrees with brute-force references on random and
  hand-picked cases (also with every bound of its searches set absurdly
  low), and with Blender's own `BVHTree.overlap` on which triangles
  intersect,
* 245 end-to-end checks pass inside Blender (live updates on move / rotate /
  scale, moving a selection, mesh edits, modifiers, linked duplicates, hidden
  parts, the two switches, the build volume and which of its walls are
  exceeded, ignoring problems, printer profiles and libraries made by
  versions 1.0 and 1.1, units, the wording of the verdict, undo, save and
  reload, an export in between; and for large parts: worker threads, the
  memory limit, the warnings about unchecked geometry),
* every panel, the list of problems and every menu is drawn against a
  stand-in for Blender's layout that rejects what a real one would,
* the overlay is rendered off-screen and its colors sampled where the walls
  and the badge are,
* in an interactive Blender window on a virtual display (`tests/gui_on_xvfb.sh`)
  a part dragged with simulated mouse input updates the overlay on every step
  of the drag; the badge is found in the corner the sidebar leaves free; a
  row of the list is clicked with the mouse; problems are isolated one after
  the other with Blender's real local view; and undo, redo and delete,
  triggered with simulated key presses, leave Blender standing
  (`tests/blender_gui_undo.py`, see below),
* version 1.2.0 installed over a 1.1.0 that is in use, in the same session,
  keeps the settings and the user's printers and carries on
  (`tests/blender_upgrade_test.py`).

The owner runs it on Blender 5.2.0 on Windows with an NVIDIA GPU (OpenGL).
Version 1.0.0 crashed Blender there: the add-on's timer read the dependency
graph in the moment between an operator freeing objects and Blender
rebuilding the graph. Since 1.0.1 the graph is brought up to date before the
timer reads it; the crash is reproduced by the tests without that line.
Version 1.1.0 was the interface as he asked for it after his first trial;
1.2.0 is his second and third lists, and what an independent review of the
code found (among it two cases in which a contact between parts was not
reported). 1.3.0 adds the Build Report: density, packed height and the
density of every layer, the first piece of the auto packer
(`docs/AUTOPACK.md`); its figures agree with Blender's own volume
calculation, and a command line tool gives the same report for a 3MF or
STL file without Blender.

Not yet tried: Blender 4.2 - 5.1 (the code compiles with Python 3.11, and
every API it uses was looked up in the 4.2 source), macOS, the Vulkan and
Metal backends.

## Large builds

Measured on a 2-core virtual machine without a GPU, where a single worker
thread does the sorting (there is one for every two processor threads, up
to eight). Version 1.1.0 and 1.2.0 in the same session:

| 30 M triangles, 20 parts of 1.5 M (in Blender, memory limit 1.7 GB) | 1.1.0 | 1.2.0 |
| --- | --- | --- |
| Every part placed (the build volume judged) | 21.4 s | 2.4 s |
| First collision results | 5.1 s | 2.8 s |
| Verdict final | 23.4 s | 19.4 s |
| Longest freeze while taking it in | 1.4 s | 0.16 s |
| Dragging a part of 1.5 M triangles, per step | 10 ms | 6 ms |
| Rotating it, per step | 16 ms | 27 ms |

With 12 M triangles (8 parts): every part placed after 1.0 s instead of
8.1 s, first results after 2.8 s instead of 4.6 s, verdict final after 8.4 s
in both. The time to the final verdict is the sorting, which one thread does
here. The engine alone, 180 M triangles (600 copies of 12 meshes, each in its
own rotation, 2 GB): every collision known after 9 s, all distances after
56 s, 12 ms per drag step (`tests/bench_scale.py`, measured with 1.0).

"Longest freeze" is the longest the main thread was busy in one go, so the
longest Blender would not respond. `tests/blender_bench_large.py` produces
these figures.
