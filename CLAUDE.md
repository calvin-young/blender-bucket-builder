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
* Thinks of part collisions and the build volume as two separate things.
  Wants it simple enough for lay users; raised a "3D Printing" application
  template (File > New) as the next conversation. Says he may change his mind
  about display defaults as he uses it.
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
* Window tests: `bash tests/gui_on_xvfb.sh <gui blender> <out dir> [script]`
  with `SDL_VIDEO_FORCE_EGL=1`; the stress test needs about 3 minutes, so
  give the command a long timeout. Do not clean up with `pkill -f` on a
  pattern that is also in your own command line (it kills the shell).
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

Interface (1.1.0, from the owner's list after his first trial):

* **No collision distance.** Parts collide when they touch (closer than
  `eps_touch`, 2e-6 of the scene size) or cut through each other. The engine
  still has `coll_thr`; the one use for it is a red tier for gaps that fuse.
* **A hidden part is checked** unless Ignore Hidden Parts is ticked (his
  wording: the checkbox is off by default). Hidden means `visible_get()` is
  false but the dependency graph evaluates the object (the eye, H, a hidden
  layer collection). *Disabled in viewports* is not evaluated: its matrix is
  stale or identity after a reload, so it cannot be checked; it is counted
  (`Monitor.disabled`). Local view does not hide in this sense.
* Hidden parts get no shading and no hatching, but their problems are drawn
  (curve, line, marker, box) and named "(hidden)". The dependency graph does
  not list the instances of a hidden instancer, so `scan_unchecked` finds
  those from the objects (`evaluated_geometry()`, Blender 4.4+; before that,
  what was seen while the part showed).
* **A viewport in local view draws only the problems among the parts it
  shows** (`only`; a problem with a part hidden everywhere counts if the
  other one is shown). The badge always speaks for the whole build.
* **Isolate is a mode** (`st.isolate`): going to a problem then puts its
  parts in Blender's local view (`ops.local_view`, with
  `frame_selected=False`, so the view does not jump) and switching it off
  leaves local view. The view frames the problem, not the parts.
* **Only the walls that are exceeded change colour** (`Monitor.walls`, exact:
  the extents in `OobResult` are the part's own).
* **The verdict's wording** is in one place, `overlay.badge_rows` (lines with
  a tone); the sidebar shows the same text. Tests compare heads and lines.
* **Printer names changed once** (`props.PROFILES_VERSION`, `_PROFILES_V1`):
  a 1.0 library is migrated, a scene saved with a 1.0 name shows the new one
  (`current_printer_name`) and never has its volume changed.
* Collapsible parts of a panel use `layout.panel(idname, default_closed=True)`;
  their state cannot be set from Python (the scratch script
  `scratchpad/panel_shots.py` opens them with simulated clicks for
  screenshots).
* Blender takes `bl_info` away from an extension's module: read the version
  with `addon_utils.module_bl_info` (`ops.addon_version`).
* The manifest's `schema_version` is the format's version, "1.0.0". Do not
  touch it when bumping the add-on's version.

## State

Last updated: 2026-10-09, about 01:30 (session 2, the night after the owner's
first trial).

* `main` is version **1.1.0**: the owner's list of 2026-10-08 23:56 (thirty-odd
  points on the interface) is done except the two items that are
  conversations (below). 1.0.0 was his first trial build, 1.0.1 fixed the
  crash he found.
* Tests at 1.1.0: 128 checks in `blender_test.py`, 47 in
  `blender_large_test.py`, the UI test (panels with a checking stand-in,
  ignore list, report), the off-screen overlay test with sampled colours, and
  in a window: `blender_gui_test.py` (drag, badge position beside the open
  sidebar and in the corner, isolate through the real local view, hidden
  part), `blender_gui_undo.py`, `blender_gui_stress.py` (now fails if the
  overlay raises: a failed draw is noted in `mon.error`), and
  `blender_upgrade_test.py` (1.1.0 installed over a 1.0.1 in use, in one
  session; needs an empty profile and the old zip, kept in
  `scratchpad/old_zips` or rebuilt from commit bb79c38).
* The owner runs **Blender 5.2.0, Windows 11, NVIDIA GPU, OpenGL backend** on
  an HP ZBook Firefly 14 G11 with 32 GB. Verdict on 1.0.0: delighted; snappy
  on small builds, "a bit laggy for real world large complicated buckets".
  Performance > **Copy Report** (new in 1.1.0) is how to find out where: it
  gives the live update time, the overlay time, the time from redraw to
  redraw while editing, and the build's size.
* **The crash of 1.0.0, and the rule that came out of it** (see the top of
  `monitor.py`): Blender runs timers at the start of a pass of its main loop
  and refreshes the dependency graph at its end. After an operator that
  freed objects (undo, delete, a change in Adjust Last Operation) the graph
  still lists them, and reading it there, `object_instances` above all,
  crashes Blender. The timer's slice (`_tick_scene`) calls
  `view_layer.update()` first. `bpy.ops` called from Python updates the view
  layer itself afterwards, so scripted tests never see that gap by accident:
  use `bpy.data` removals (blender_test.py) or simulated key presses in a
  window (`tests/blender_gui_undo.py`). Both segfault without the fix.
* Engine since 1.0.0: meshes are sorted in about 60 % of the time and can be
  sorted on several threads (`bvh.order_mesh`, identical order); parts moved
  together keep their results (`POS_TOL`); meshes can enter the world
  unsorted (`World.add_raw`, `set_sorted`, `wanted`, pairs waiting in
  `_pend_wait`) and meshes no object uses can be kept as a cache
  (`keep_unused`, `_idle`, `_make_way`). **The monitor does not use the last
  two yet**: that is item B1 below. When it does, it must forget
  `data_geom[mesh]` when a mesh changes while its object is not tracked
  (ignored, or hidden with Ignore Hidden Parts), or a kept mesh would be
  reused for changed geometry.
* The sandbox VM got slower overnight (now a 2.8 GHz Xeon that runs this code
  1.7 - 1.9 x slower than the machine of the first day). Figures from before
  and after are not comparable: measure old and new in the same session.
* Not tested: Blender 4.2 - 5.1, macOS, Vulkan / Metal, a real GPU.

## Next

### A. Open with the owner

Answered on 2026-10-09 01:27:

* His 580+ is 332.6 x 198.7 x 267.8 (he had the order wrong), so the stock
  5XX is HP's 332 x 190 x 248 again.
* Hidden parts: yes, hiding does not ignore. He hides parts to see into the
  middle of the bucket while resolving a collision, and wonders whether local
  view is the better habit. Parts of a collection that is "disabled" should
  be ignored: that is what happens for a collection excluded from the view
  layer (its checkbox) and for one disabled in viewports (the screen icon);
  a collection hidden with the eye is hidden, so still checked. He regrets
  that the Outliner cannot get a toggle of its own.
* Lag: Blender as a whole felt laggy when orbiting (he does not blame the
  add-on; not measured yet), and "dragging the huge monkey around the
  keycaps, it would take a few seconds for all the collisions to populate".
  He can live with it and will try real, heavy buckets and send the report.

Still open:

* Whether the build volume should show, and be checked, without the
  collision check (he thinks of them as two things; today one switch turns
  the monitor on and each section has its own checkboxes).
* The "3D Printing" application template (File > New). Not an extension
  type; a template is a folder with a `startup.blend` installed through the
  Blender menu, and the add-on could install one it carries
  (`bpy.ops.preferences.app_template_install`). He wants to talk about what
  it should contain: who the "lay folks" are and what they must be able to do.
* Offered, no answer: a per-collection ignore switch (for things that should
  stay visible but out of the check); a disk cache of sorted meshes so that
  reopening a saved build is quick; an explicit fast / accurate switch; wall
  gap on by default; amber shading for warning-only parts; the collision
  distance back as a "must not be closer than" red tier.

### B. Performance ("laggy for real world large complicated buckets")

Known and planned, in this order:

1. [ ] "Verdict first" in the monitor: objects enter the world unsorted
   (extents known, so the volume verdict is immediate), worker jobs ordered
   by `World.wanted()`, more workers, several threads for one large mesh,
   hashing and cleaning off the main thread.
2. [ ] Pause instead of discard when monitoring is switched off; keep the
   meshes of parts that leave the check (`keep_unused`).
3. [ ] Background slices back to back while there is work (today 12 ms of
   work, 20 ms of pause); redraws at a limited rate during analysis.
4. [ ] Large selections: 63 ms per step when all of 600 parts move
   (`scratchpad/bl_group_move.py`): the out-of-volume pictures of parts that
   stick out (42 ms, no budget), the box tests of all against all (15 ms),
   the per-pair bookkeeping (16 ms), one `set_matrix` per object (10 ms).
   A rigid-group shortcut by per-part reference positions is NOT sound (two
   worked counter-examples in the session log); compare per pair, vectorised
   over a pair table.
5. [ ] Per redraw: `status()` and `World.counts()` walk every violation;
   the tint pass redraws every colliding part (up to 4 M triangles); cost on
   a real GPU unknown.

### C. Before a wider release

* Independent review of the integration code; audit of the Blender API calls
  against the 4.2 source in `/home/claude/build/blender-42-src` (icons are
  checked: all exist in 4.2).

Later, if wanted: smaller poses (quantised low levels); logarithmic extents
for a rotating part; results rotated instead of re-solved when a group is
rotated; non-mesh objects checked instead of warned about.
