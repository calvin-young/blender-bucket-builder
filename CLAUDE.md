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

Last updated: 2026-10-08 (session 2, early afternoon).

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
* On `main` (commit 47fb638): poses are an on-demand cache with an optional
  byte limit (`World.set_cache_limit`), least recently used first out. Nothing
  sets a limit yet.
* Live modifiers: the evaluated mesh is what is checked (verified with Array
  and Shrinkwrap). Silent gaps the owner has been told about and wants a
  warning for: geometry-node instances that are not realized are not checked,
  and neither are non-mesh objects (text, curves).
* Not tested: a real GPU, Blender 4.2 - 5.1, Windows, macOS, a human at the
  mouse. The owner works on an HP ZBook Firefly 14 G11 with 32 GB RAM.

### Measured at the owner's size (tests/bench_scale.py parts 20 1500000)

30 M triangles in 20 parts, 2-core sandbox, commit 47fb638:
sorting 15 s (0.5 us per triangle, on the main thread, 0.8 s per mesh);
first complete check 9 s in ONE slice (interface frozen); memory 2.3 GB;
drag step 5 ms; rotating a 1.5 M triangle part 150 ms per step.
In this virtual machine the first write to a page of memory is about twenty
times slower than on real hardware, which is most of that 9 s; the benchmark
now touches the pose storage once before timing (pass `cold` to see it raw).

## In progress: branch `wip/borrowed-poses` (NOT finished, NOT verified)

Goal: no freeze and no lag with large parts. Three pieces, in this order.

1. **Borrowed poses** (engine). An object whose rotation/scale changes keeps
   the pose it had and its boxes are transformed as they are looked at
   (`O_VIRT`, `O_REL`, `O_PAD` in `World`; centre and half extent through
   `rel` and `|rel|`, padded for rounding). Triangles of a borrowing object
   are posed from the mesh's own vertices (`LVERT`) with exactly the
   expression `World._posed` uses, so they are bit-identical to what a fitted
   pose holds; only the boxes are looser (about 1.3 - 1.6 x per axis, measured).
   When idle, `World._upgrade` gives the object a fitted pose again (in place
   if nobody else uses the pose) and `_refresh` re-solves the pairs that were
   solved with borrowed boxes (`PairResult.loose`). Under a memory limit,
   copies of one mesh in different rotations borrow one pose instead of each
   having their own (`_attach`, `_borrowing`).
   Done: `core/world.py` rewritten for this (not run yet). To do:
   `core/narrow.py` must honour it everywhere boxes or posed vertices are
   read: `_test_block` (transform gathered boxes of borrowing sides),
   `corners` / `_pair_tris` / `_node_tris` (pose from `LVERT` with `O_L`),
   `_region_side` (bounds, proxy boxes), the enclosure test (move the query
   point into the pose's frame with `O_RELI`), `oob_solve` (transformed
   boxes, exact triangle boxes at the leaves). Then tests: force borrowing
   with `w.fit_now = 0`; collisions must equal a world without borrowing,
   distances within the usual 0.1 %, and after settling everything must
   equal a freshly built world. `tests/test_world.py: posed_tris` should use
   `World.part_corners`.
2. **Fits in slices** (done in `world.py`, not run yet): `_fit_steps` is a
   generator (blocks of 32768 triangles, about 1.5 ms each), `_prepare`
   fits what a batch needs until the step's deadline, pairs wait in their
   queue meanwhile, `_oob_fill` collects out-of-volume geometry the same way.
   Also there: pose storage grows straight to what the scene needs
   (`Arena.want`), new array + copy instead of `ndarray.resize` (which zero
   fills and so really allocates everything); mesh vertices in a shared array
   `LVERT`; extents 7x faster (never use `.min(axis=0)` on an (n, 3) array).
3. **Sorting off the main thread** (to do, `monitor.py`): `bvh.sort_mesh` is
   a pure function made for this; run it in a worker thread (a few in
   parallel), poll from the timer, then `World.add_sorted`.

Then: memory preference (automatic default about 20 % of installed RAM, so
about 6 GB on the owner's machine) wired to `World.set_cache_limit`, cache
use and "borrowing" count in the Parts panel, the warning for unrealized
instances and non-mesh objects, benchmarks (`tests/bench_scale.py`, both
modes), README and user guide.

## Next (after the branch is merged)

1. Measure and report: 30 M triangles in 20 parts, and 600 instanced parts
   with a memory limit (`tests/bench_scale.py instances 600 300000 12 2`).
2. Throttle viewport redraws during the first analysis of a scene.
3. If memory still matters: smaller poses (quantised boxes for the two lowest
   levels would halve them; the fit is memory bound, so it would get faster
   too).
4. Possibly: an "import beside the bucket" helper, amber shading for
   warning-only parts (offered, not requested).
