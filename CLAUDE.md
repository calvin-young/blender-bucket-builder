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
  `python3 tests/test_world.py` (about 3 minutes), `python3 tests/bench.py --quick`,
  `--storm`, and `tests/bench_scale.py` for builds of the owner's size.
* Blender tests need the add-on installed and enabled in the profile that
  `BLENDER_USER_RESOURCES` points to. See `README.md` (Tests) for the commands
  and `tools/test-blender/README.md` for building Blender in a sandbox that
  cannot download it, including everything that went wrong the first time.
* After editing the add-on, copy it over the installed copy before running a
  Blender test (the tests import the installed extension, not the repo).
* The off-screen overlay test draws with `srgb_target=False` and does not look
  like the real viewport, which blends in linear light. Judge appearance from
  the window tests' screenshots only.
* Commit style: imperative subject, a body that says why. Commit and push
  straight to `main` (the owner asked for that and lets Claude manage the
  repository); push after every verified step.
* Timing in the sandbox: the first write to a page of memory is about twenty
  times slower than on real hardware and run-to-run noise is around 20 %.
  Compare old and new in the same session (`git worktree add` the old one).

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
* **Poses are a cache** (`World.set_cache_limit`), built on demand, least
  recently used out first, each one a single block (vertices + boxes) in one
  array, in size classes. A part without neighbours never has one.
* **Borrowed poses, for live answers only.** An object whose rotation/scale
  changes keeps its pose; boxes read from it are moved at query time
  (`narrow._move_boxes`, padded for rounding), its triangles are posed from
  the mesh's own vertices with exactly `World._posed`'s expression, so they
  are bit-identical to a fitted pose. Exact work never uses borrowed boxes:
  proving a clearance with them was measured 10 - 50 x slower than fitting
  (16 ms per 300 k triangles). `_prepare(borrow=False)` gives every object
  its own pose; `_upgrade` queues what was solved with borrowed boxes
  (`PairResult.loose`) to be redone.
* **Fits are generators** (`World._fit_steps`), a block of 32768 triangles per
  piece; a step fits what its batch needs until its deadline. `_batch` takes
  pairs whose poses are ready first, and otherwise waits for two poses at
  most, so results appear as fits get done.
* **Live load control**: a solve of n sketched pairs costs c0 + c1 n, a fully
  treated pair `_detail_cost` (rises fast, falls slowly). All pending pairs
  are taken at once if that fits twice the budget; full detail for all only
  up to 8 pairs and 24 ms (`_choose_detail`).
* **Clearance proof in two passes** (`narrow.solve`, `prove`): moderate caps
  first, with a proven lower bound for what was left out; large caps only if
  that bound could change the verdict. Else the value stands, flagged
  `approx` when not proven to 1 % (shown as "~").
* **Quick vs exact**: during a live edit intersections are complete but the
  clearance search is capped; pairs cut short are redone exactly when idle.
* **The hatched collision region** is the surface of both parts within a few
  millimetres of the other (from the coarse traversal), not the triangles the
  intersection curve crosses, which are an invisible sliver on dense meshes.
* **Status badge "busy"** only waits for work that can change the verdict
  (unread parts, unsolved pairs), not for refinement or the periodic resync.
* Live updates come from `depsgraph_update_post` (during a modal transform it
  reports only `Object` with the transform flag), background work from a timer.
* **Mesh data is read from attribute arrays** (`position`, `.corner_vert`): a
  memory copy, 100 x faster than `vertices` / `loop_triangles` (which remain
  as fallbacks). A triangles-only mesh needs no triangulation.
* **Sorting runs in worker threads** (`monitor._executor`, `bvh.sort_mesh`),
  which see NumPy arrays only. Meshes up to 60000 triangles are sorted inline.
* **What is not checked is reported, not hidden**: unrealized geometry-node
  instances, collection instances, non-mesh objects with faces
  (`Monitor.scan_unchecked`); the badge turns to a warning.
* Never use `.min(axis=0)` on an (n, 3) array in a hot path (7 x slower than
  per-column), nor `ndarray.resize` (it zero-fills, which really allocates).

## State

Last updated: 2026-10-08 (session 2, afternoon).

* Everything is on `main`. Engine, monitor, overlay, panels, printer profiles:
  working and tested on Blender 5.2.2 (Linux, software OpenGL), headless and
  in a real window on a virtual display with simulated mouse input.
* Added this session at the owner's request: wall gap (side walls only,
  warning), light red shading of every colliding part, printers 5600 / 1200 /
  580, fewer labels in crowded builds, warnings for geometry that is not
  checked.
* Large builds (the afternoon's work): pose cache with a Memory preference
  (automatic: a fifth of installed RAM, 6.4 GB on the owner's 32 GB machine),
  borrowed poses, sliced fits, worker threads, fast mesh reading. Figures are
  in `README.md` ("Large builds"): 30 M triangles in 20 parts in Blender: all
  checked after 15 s on 2 cores, longest main-thread block 43 ms, drag 5 ms,
  rotate 8 ms per step, 2.2 GB. At the start of the session the same scene
  froze Blender for about 25 s and rotating took 150 ms per step.
* Live modifiers: the evaluated mesh is what is checked (verified with Array
  and Shrinkwrap; Subdivision is not in the test build).
* Not tested: a real GPU, Blender 4.2 - 5.1, Windows, macOS, a human at the
  mouse, worker threads in an interactive session on a many-core machine.
  The owner works on an HP ZBook Firefly 14 G11 with 32 GB RAM.

## Next

The owner read the "Large builds" table (2026-10-08, 16:02) and objected to
the cold start: 15 s for 30 M triangles where Netfabb takes 2 - 3 s. His
habit is to arrange first and tick the box to check. Answered; the plan below
was promised to him, in this order. Tick items off here as they land.

Measured now (2-core VM, one worker thread): 3.6 M triangles 2.0 s, 10 M
5.1 s, 30 M 15 s. About 90 % is `bvh.sort_mesh` (480 ns per triangle; the
four lowest levels are 260 of that, mostly row extents of tiny rows; see
`scratchpad/sort_prof.py` for the per-level profile). Two sorts in two
threads take the time of one, so the owner's laptop (4 workers) should be
about 4 x quicker already. Checking itself is 1.6 s for 30 M.

1. [ ] Verdict first: objects enter the world with unsorted geometry (extents
   known), pairs wait only for the meshes they need, worker jobs are ordered
   by need. A rough arrangement then gets its answer at once.
2. [ ] Faster preparation: cheaper row extents for small rows, cache blocking,
   several threads for one big mesh, more workers than 4.
3. [ ] Keep prepared meshes when Monitor Build is unticked (pause instead of
   discard) and when parts are hidden (unused meshes stay cached within the
   memory limit).
4. [ ] Background slices back to back while there is work (today 12 ms of
   work, 20 ms of pause), redraws at a limited rate during analysis.
5. [ ] Group moves (found in review): the pair stamp rounds the relative
   translation to 1e-6 units, Blender's float32 positions jitter by more, so
   moving parts together re-solves all pairs among them on every step
   (`scratchpad/group_move.py`: 28 ms per step for 120 parts instead of 4).
   Compare with a tolerance of about 1e-6 x the larger coordinate.
6. [ ] Independent review of the integration code and an audit of the Blender
   API calls against the 4.2 source in `/home/claude/build/blender-42-src`.
7. [ ] Package: rebuild `dist/bucket_builder-1.0.0.zip`, install it into a
   clean profile, run the tests against that, send the zip to the owner
   (last verified build: commit 0e33db7, 80 + 38 checks in a clean profile).

Offered to the owner, no answer yet: a disk cache of sorted meshes so that
reopening a saved build is quick; an explicit fast / accurate switch; wall
gap on by default; amber shading for warning-only parts; a built-in profile
for his 580 with custom firmware (needs the dimensions).

Later, if wanted:
* Smaller poses (quantised boxes for the two lowest levels would halve them;
  the fit is memory bound, so it would get faster).
* Extents of a rotating part are an O(vertices) pass per step (5 ms for
  1.5 M triangles); a support query on the borrowed tree would make it
  logarithmic. The fixed cost of a live solve is 3 - 6 ms of Python overhead
  over about 20 tree levels.
* Rotating a group of parts together re-solves the pairs among them (their
  relation is unchanged; results would have to be rotated instead).
* Non-mesh objects could be checked instead of warned about (`to_mesh`
  works on them).
