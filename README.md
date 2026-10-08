# Blender Bucket Builder

**Bucket Builder** is a Blender add-on for preparing 3D-print builds, aimed at
dense HP Multi Jet Fusion (MJF) builds.

Its first tool is a live build check. Turn monitoring on once, then move,
rotate and scale parts as usual. While you drag, the viewport shows:

* where parts **collide** (red cross-hatching and the exact intersection curve),
* where parts are closer than your **minimum clearance** (amber hatching and the
  measured gap),
* what reaches **outside the printer's build volume** (magenta hatching on just
  the geometry that is out),
* which parts are at fault: every colliding part is **shaded light red**,
* optionally, parts that sit too close to the **side walls** of the volume,
* a large **green tick** when there are no collisions and every part is inside
  the volume.

Nothing in the scene is modified; everything is drawn on top of it.

![A part being dragged: the collision hatching follows it](docs/images/dragging.png)

| | |
| --- | --- |
| ![Colliding parts are shaded, the overlap is hatched](docs/images/collision.png) | ![A clean build](docs/images/build-ok.png) |

A large part imported into the middle of a full build, and being dragged out
of it (50 parts; the new one lands on 33 of them):

| | |
| --- | --- |
| ![Just imported](docs/images/import-into-full-build.png) | ![Being dragged out](docs/images/dragging-out.png) |

Screenshots are from the automated window tests (`tests/blender_gui_test.py`,
`tests/blender_gui_stress.py`), with test shapes sized in millimetres inside a
380 x 284 x 380 mm volume.

The user guide is in [bucket_builder/README.md](bucket_builder/README.md).

## Status

Version 1.0.0. Declared for Blender 4.2 and newer; so far it has only been run
on **Blender 5.2.2** (Linux, software OpenGL), where:

* the geometry engine agrees with brute-force references on random and
  hand-picked cases, and with Blender's own `BVHTree.overlap` on which
  triangles intersect,
* 66 end-to-end checks pass with the add-on installed from the packaged zip
  (live updates on move / rotate / scale, mesh edits, modifiers, linked
  duplicates, hiding, the build volume, printer profiles, units, undo, save
  and reload),
* the overlay has been rendered off-screen and inspected,
* in an interactive Blender window on a virtual display, a part dragged with
  simulated mouse input updates the overlay on every step of the drag
  (`tests/gui_on_xvfb.sh`).

Not yet tried: a real GPU, a person at the mouse, Blender 4.2 - 5.1, Windows
or macOS.

## Install

Download or build `bucket_builder-1.0.0.zip`, then in Blender:
Edit > Preferences > Get Extensions > the arrow in the top right >
*Install from Disk*.

To build the zip from this repository:

    blender --command extension build --source-dir bucket_builder --output-dir dist

## How it works

    scene objects
        -> broad phase: bounding boxes of the parts that changed against all others
        -> narrow phase: both parts' bounding-volume trees, traversed together
        -> exact triangle / triangle intersection and distance
        -> viewport drawing

* **One tree per unique mesh.** Triangles are sorted once into an implicit
  bounding-volume tree. Copies of a part share it.
* **Moving is free.** A part's tree is stored without its translation; the
  offset between two parts is applied while querying. Dragging a part
  therefore rebuilds nothing.
* **Rotating or scaling re-fits** the boxes of that one part (a few vectorised
  passes, no re-sorting). Only a real geometry change re-sorts a mesh.
* **Only what changed is recomputed.** Results are kept per pair of
  neighbouring parts; when a part moves, only its own pairs are looked at.
* **No Python loops over triangles.** All pairs that need work are traversed
  together, level by level, in NumPy.
* **A live step has a time budget.** Whether two parts collide is always
  decided exactly. How much more each pair gets (the complete intersection
  curve, the hatched region, the proven clearance distance) follows the
  measured cost: with a few neighbours every pair gets everything; when a part
  lands on dozens of others, most pairs are only *sketched* (one intersecting
  triangle pair proves a collision) and the detail fills in for as many as
  fit. Whatever was left out is completed exactly just after release.
* **Colliding parts are shaded from a mesh uploaded once** per unique part and
  redrawn with the object's current matrix, so the cheapest answer (collides
  or not) costs nothing to draw while a part moves.

### Why not `mathutils.bvhtree.BVHTree`

It was the starting point, and it is still used as an independent check in the
tests. Two things rule it out for the live path:

* `BVHTree.overlap()` takes no transform, so both trees must be in world
  space and a moving part's tree has to be rebuilt on every step of a drag.
* It reports overlapping triangles only. There is no tree-to-tree distance
  query, which the clearance check needs.

Measured on the same machine, one step of dragging a part into its neighbour,
collision check only (`tests/blender_bvhtree_compare.py`):

| Triangles (both parts) | BVHTree: rebuild the moving tree + overlap | This add-on |
| ---: | ---: | ---: |
| 6,000 | 1.3 ms | 2.4 ms |
| 24,000 | 4.3 ms | 3.8 ms |
| 98,000 | 19.9 ms | 4.6 ms |
| 327,000 | 39.1 ms | 6.6 ms |

The add-on's figure also covers the intersection curve and the geometry the
overlay draws. Both find the same intersecting triangles.

## Tests

The engine needs only NumPy:

    python3 tests/test_tritri.py     # triangle routines against slow references
    python3 tests/test_world.py      # the collision world against brute force
    python3 tests/bench.py --quick   # timings on build-sized scenes

With the add-on installed and enabled in Blender:

    blender --background --python tests/blender_test.py             # end to end
    blender --background --python tests/blender_ui_test.py          # panels and menus
    blender --background --python tests/blender_gpu_test.py -- out  # overlay, off-screen PNGs
    blender --background --python tests/blender_bvhtree_compare.py  # against BVHTree
    blender --enable-event-simulate --python tests/blender_gui_test.py -- out   # in a window

## Layout

    bucket_builder/       the add-on (this folder is what gets zipped)
      core/               NumPy engine, no Blender imports
        tritri.py         exact triangle / triangle intersection and distance
        bvh.py            implicit bounding-volume tree layout and build order
        arena.py          growable shared storage
        world.py          objects, cached trees, incremental pair results
        narrow.py         batched tree traversal, regions, build-volume test
      monitor.py          mirrors the Blender scene into the engine, handlers, timer
      overlay.py          viewport drawing
      props.py            settings, preferences, printer profiles
      ops.py              operators: navigation, printer profiles
      ui.py               sidebar panels
    tests/
    tools/test-blender/   building Blender from source where it cannot be downloaded
    CLAUDE.md             working notes: state, decisions, what is next

## Licence

GPL-3.0-or-later, see [LICENSE](LICENSE).
