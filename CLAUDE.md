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

Last updated: 2026-10-09, shortly after midnight (session 2).

* `main` is version **1.0.1**, sent to the owner as `bucket_builder-1.0.1.zip`
  (verified in a clean profile: 88 + 39 checks, the UI test, the window undo
  test). 1.0.0 was his first trial build.
* The owner tried 1.0.0 on **Blender 5.2.0, Windows 11, NVIDIA GPU, OpenGL
  backend** (from his crash log). Verdict: delighted; rendering works; snappy
  on small builds, "a bit laggy for real world large complicated buckets".
* **The crash he found, and the rule that came out of it** (see the top of
  `monitor.py`): Blender runs timers at the start of a pass of its main loop
  and refreshes the dependency graph at its end. After an operator that
  freed objects (undo, delete, a change in Adjust Last Operation) the graph
  still lists them, and reading it there, `object_instances` above all,
  crashes Blender. The timer's slice (`_tick_scene`) calls
  `view_layer.update()` first. `bpy.ops` called from Python updates the view
  layer itself afterwards, so scripted tests never see that gap by accident:
  use `bpy.data` removals (blender_test.py) or simulated key presses in a
  window (`tests/blender_gui_undo.py`). Both segfault without the fix.
* Since 1.0.0: meshes are sorted in about 60 % of the time and can be sorted
  on several threads (`bvh.order_mesh`, identical order); parts moved together
  keep their results (`POS_TOL`).
* **In the stash** (`git stash list`): engine work for "verdict first":
  unsorted meshes (`World.add_raw`, `set_sorted`, `wanted`, pairs waiting in
  `_pend_wait`), meshes no object uses kept as a cache (`_idle`,
  `_make_way`), `flush_poses`, and tests for all of it. State: every test
  passed except the last assertion of `unused_mesh_tests` (storage limit at
  small scale; the storage has a minimum of 393 KB). The monitor does not use
  any of it yet. When it goes in, the monitor must forget `data_geom[mesh]`
  when a mesh changes while its object is not tracked (hidden / ignored),
  or a kept mesh would be reused for changed geometry.
* Not tested: Blender 4.2 - 5.1, macOS, Vulkan / Metal.

## Next

### A. The owner's feedback on 1.0.0 (2026-10-08, 23:56), to ship as 1.1.0

He asked for one build with a point-by-point reply. Open questions put to
him: the axis order of his 580+ (he wrote 198.7 x 332.6 x 267.8, the stock
entry is 332 x 190 x 248); whether "Ignore Hidden Parts, off by default"
means hidden parts are checked unless ticked (assumed yes); what he is doing
when it lags and what the two timing lines in the Parts panel say.

Sidebar
- [ ] "Enable Monitoring" is easy to miss; the closed-eye icon looks odd: use
      the open eye as in the Outliner.
- [ ] Two main sections: part collisions / clearance, and build volume.
- [ ] "Ignore Hidden Parts" checkbox, prominent, off by default (so hidden
      parts are checked: they are still in the build).
- [ ] Remove the collision distance (told him the one argument for it: a red
      tier for gaps that fuse; removing unless he objects).
- [ ] Printers: "HP MJF 4XXX/5XXX", "HP MJF 5XX", "HP MJF 580+" (198.7 x
      332.6 x 267.8, right below 5XX), "HP MJF 1200". Stored libraries need a
      migration.
- [ ] Volume section order: View Build Volume, printer menu, checkboxes,
      then Advanced (size, origin, offset) collapsed.
- [ ] Parts: "Include All" below Ignore / Include Selected; below that a list
      of ignored parts, each with a button to include it again.
- [ ] Performance figures and Recheck Everything in their own sub-panel.
- [ ] Problems: a clean way to isolate the parts of a problem in local view.

Viewport
- [ ] Badge at the bottom right, text right-aligned to the left of the icon.
- [ ] Clearance warning: amber triangle with exclamation mark, "Clearance
      Warning" instead of "Build OK". Red cross: "Collision Detected" instead
      of "Build has problems". Free to rethink the other wording.
- [ ] The printer's name somewhere visible with the sidebar closed (a light
      blue line under the status text, or on the wireframe).
- [ ] Out of bounds: colour only the side(s) of the bucket that are exceeded;
      the same for the wall gap.

Display settings
- [ ] Tint strength default 0.5.
- [ ] Hatch spacing default 4, whole numbers only, range 2 to 10 (other
      values give moire).
- [ ] "See-Through" becomes a checkbox plus a slider shown when ticked,
      named "Hatch X-Ray".
- [ ] Checkboxes to hide the closest-distance lines and the intersection
      outlines.
- [ ] Separate checkboxes for collision markers and clearance markers.

Elsewhere
- [ ] Outliner restriction column: not possible (fixed in Blender). Instead:
      context menu entries in the Outliner and the viewport, and a checkbox
      in Object Properties > Visibility.
- [ ] Application template "3D Printing" (File > New): he wants to talk
      about it after this list. Not an extension type; the add-on could
      install one.

### B. Performance ("laggy for real world large complicated buckets")

Waiting for his numbers. Known and planned, in this order:
1. Pop the stash, finish "verdict first" in the monitor (objects enter the
   world unsorted, worker jobs ordered by need, more workers, several
   threads for one large mesh).
2. Pause instead of discard when monitoring is switched off; keep meshes of
   hidden parts.
3. Background slices back to back (today 12 ms of work, 20 ms of pause);
   redraws at a limited rate during analysis.
4. Large selections: 63 ms per step when all of 600 parts move
   (`scratchpad/bl_group_move.py`): the out-of-volume pictures of parts that
   stick out (42 ms, no budget), the box tests of all against all (15 ms),
   the per-pair bookkeeping (16 ms), one `set_matrix` per object (10 ms).
   A rigid-group shortcut by per-part reference positions is NOT sound (two
   worked counter-examples in the session log); compare per pair, vectorised
   over a pair table.
5. Overlay cost on a real GPU is unknown: the tint pass redraws every
   colliding part (up to 4 M triangles).

### C. Before a wider release

* Independent review of the integration code; audit of the Blender API calls
  against the 4.2 source in `/home/claude/build/blender-42-src`.
* Offered, no answer yet: a disk cache of sorted meshes so that reopening a
  saved build is quick; wall gap on by default; amber shading for
  warning-only parts.

Later, if wanted: smaller poses (quantised low levels); logarithmic extents
for a rotating part; results rotated instead of re-solved when a group is
rotated; non-mesh objects checked instead of warned about.
