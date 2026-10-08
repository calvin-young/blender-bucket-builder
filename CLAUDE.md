# Bucket Builder: working notes

Notes for whoever (Claude or human) picks this project up in a new session.
Keep the "State" and "Next" sections current; push after every verified step.

## What it is

A Blender add-on (`bucket_builder/`, extension id `bucket_builder`) for
preparing HP Multi Jet Fusion builds. Its one feature so far is a live build
check: while parts are moved, rotated or scaled it shows collisions, clearance
violations and geometry outside the printer's build volume, and a green tick
when the build is clean. It replaces the collision / clearance check the
owner used in Autodesk Netfabb. Read `README.md` for the design and
`bucket_builder/README.md` for the user guide.

## The owner's requirements (github.com/calvin-young)

* **Speed first.** Results must follow the mouse during a drag. Translation
  must cost almost nothing; a short pause on rotate or scale is acceptable.
  Never recompute more of the scene than changed.
* Production tool, not a prototype. It should feel native to Blender.
* Printers: mostly the Jet Fusion 5600 (380 x 284 x 380 mm); also the new
  Multi Jet Fusion 1200 (320 x 165 x 230) and a 580 running custom firmware
  with a slightly larger bucket (the owner enters that size himself).
* Builds: usually 2 to 50 parts, sometimes up to 600 small parts nested
  densely, "generally instanced". Tens of millions of triangles are common
  and must stay snappy; hundreds of millions happen and may be slower.
* Wants the work pushed to GitHub regularly and notes kept here, because a
  session can stop at any time when a usage limit is hit.
* Out of scope for now: automatic packing, physics, booleans, repair,
  supports, orientation optimisation.

## How to work on it

* Engine tests need only NumPy: `python3 tests/test_tritri.py`,
  `python3 tests/test_world.py` (about a minute), `python3 tests/bench.py --quick`.
* Blender tests need the add-on installed and enabled in the profile that
  `BLENDER_USER_RESOURCES` points to. See `README.md` (Tests) for the commands
  and `tools/test-blender/README.md` for building Blender in a sandbox that
  cannot download it, including everything that went wrong the first time.
* After editing the add-on, copy it over the installed copy before running a
  Blender test (the tests import the installed extension, not the repo).
* The off-screen overlay test draws with `srgb_target=False` and does not look
  like the real viewport, which blends in linear light. Judge appearance from
  the window tests' screenshots only.
* Commit style: imperative subject, a body that says why. Push to `main`.

## Decisions already made, and why

* **Not `mathutils.bvhtree.BVHTree`** for the live path: `overlap()` takes no
  transform (the moving tree would be rebuilt every step) and there is no
  tree-to-tree distance query. It is used as an independent check in
  `tests/blender_bvhtree_compare.py`. Numbers are in `README.md`.
* **Implicit tree in sorted triangle order** (`core/bvh.py`): node `i` on level
  `h` covers triangles `[i << h, (i + 1) << h)`. No pointers, so a whole
  frontier of node pairs is expanded with a few NumPy calls.
* **Boxes are fitted per "pose"** (unique mesh + rotation/scale), in world
  axes, without the translation. Translation is added at query time. Copies
  with the same rotation share a pose. This is what makes dragging free, and
  it is also the memory cost: about 55 bytes per triangle per pose.
* **Quick vs exact**: during a live edit intersections are complete but the
  clearance search is capped; pairs cut short are redone exactly when idle.
* **The hatched collision region** is the surface of both parts within a few
  millimetres of the other (from the coarse traversal), not the triangles the
  intersection curve crosses, which are an invisible sliver on dense meshes.
* **Status badge "busy"** only waits for work that can change the verdict
  (unread parts, unsolved pairs), not for refinement or the periodic resync.
* Live updates come from `depsgraph_update_post` (during a modal transform it
  reports only `Object` with the transform flag), background work from a timer.

## State

Last updated: 2026-10-08 (session 2, mid-morning).

* Engine, monitor, overlay, panels, printer profiles: working and tested on
  Blender 5.2.2 (Linux, software OpenGL), headless and in a real window on a
  virtual display with simulated mouse input.
* Added this session at the owner's request: wall gap (side walls only,
  warning), light red shading of every colliding part, printers 5600 / 1200 /
  580, fewer labels in crowded builds.
* Load control: a live step has a time budget. Pairs are *sketched* (collision
  yes/no, exact) or fully treated depending on measured cost; see
  `World._choose_detail`, `World._learn_cost`, `narrow.solve(detail=...)`.
  `tests/bench.py --storm` and `tests/blender_gui_stress.py ... storm` are the
  owner's "import a part into the middle of 50" scenario: 159 -> 24 ms per
  step with 20+ parts in contact.
* Window test numbers on the 2-core, no-GPU sandbox: 60 parts / 2 M triangles,
  about 5 ms of checking per drag step; overlay data preparation about 2 ms
  per redraw (the rest of the overlay time there is the software rasteriser).
* Not tested: a real GPU, Blender 4.2 - 5.1, Windows, macOS, a human at the
  mouse. The owner works on an HP ZBook Firefly 14 G11 with 32 GB RAM.

## Next

1. Memory. Each pose (unique mesh + rotation) costs about 55 bytes per
   triangle; fine for tens of millions of triangles, too much for the
   600-part builds. Plan: poses become a cache with a byte budget (default
   about 6 GB on the owner's machine) and least-recently-used eviction; a
   part's extents are computed without building its pose, so poses are only
   built for parts that actually have neighbours. Then, if needed, shrink the
   pose itself (do not store the two lowest box levels, 16-bit boxes).
2. Measure at the owner's sizes: 30 M triangles in 2 - 50 parts, and 600
   instanced parts (Alt+D linked duplicates; that is what "instanced" means
   in his files). Sorting very large meshes off the main thread if the
   first analysis stalls the interface.
3. Throttle viewport redraws during the first analysis of a scene.
4. Possibly: an "import beside the bucket" helper, amber shading for
   warning-only parts (offered, not requested).
