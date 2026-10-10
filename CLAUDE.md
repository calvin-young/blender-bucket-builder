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
  `python3 tests/test_world.py` (about 10 minutes), `python3 tests/bench.py --quick`,
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
* **Whether two parts intersect is never left to a capped search.** The
  search of one pass is bounded per pair (`CAP_FINE`, `CAP_OVERLAP`). Two fine
  meshes that nearly coincide (a part and its copy a fraction of a triangle
  away, a shell closely inside another) have millions of triangle pairs with
  overlapping boxes, and the few the beam picks are the twins, which never
  cut each other: up to 1.1.0 such a collision came out as a clearance
  warning, or as nothing with the clearance check off. Now a pair that runs
  into a cap without a hit is flagged `PairResult.unproven` and settled by
  `narrow.scan`: exhaustive, depth first over blocks, stops at the first cut
  or contact, resumable (`World._pend_scan`, done when idle, counted in
  `unsettled`, so the badge says "Checking" until then). The distance proof
  of such a pair waits until the scan has cleared it (`no_cut`). Tests:
  `capped_search_tests` (tiny caps, against brute force) and
  `near_coincident_tests`; `scratchpad/mutate_scan.py` shows both fail
  without the scan. `unsettled` means "whether they collide is not certain
  yet"; a clearance warning may still be on its way while a pair is only in
  `_pend_refine` (the badge then says "measuring clearances").
* **A live solve has a budget for its whole search** (`narrow.LIVE_BOX` box
  tests, `LIVE_TESTS` triangle tests; ordinary steps reach 210 k / 21 k).
  Duplicating a dozen parts in place and nudging them cost 0.5 - 4 s per
  step before, now 30 - 50 ms. As the budget runs out the pairs with the
  most rows stop first (`_fine(work=)`); pairs cut short are `unproven` and
  `refine`, and when things are quiet `_scan_step` moves them to the cold
  queue, ahead of all refinement. Contact found by the sketch's dive
  (`touch`) proves a collision: a copy lying exactly on its original is red
  at once, without any search. Test: `live_budget_tests`.
* **The hatched collision region** is the surface of both parts within a few
  millimetres of the other (from the coarse traversal), not the triangles the
  intersection curve crosses, which are an invisible sliver on dense meshes.
* **Status badge "busy"** only waits for work that can change the verdict
  (unread parts, unsolved pairs), not for refinement or the periodic resync.
* **The timer paces itself by what the user is doing** (`monitor._tick_scene`,
  `_pace`). Measured in a window: checking a part that lands on 33 others
  is 0.35 s of work, but took 12 s to appear, because every 24 ms slice was
  followed by a redraw (and each change restarts the viewport's
  anti-aliasing passes, 8 redraws by default). Now: while the user edits
  (`last_hot`) or turns the view (the overlay sees the view matrix change:
  `user_active`), slices are short and come with pauses; otherwise they are
  `BOOST` times longer, or `SHARE` of what Blender takes per pass of its main
  loop if that is more (up to `MAX_SLICE`), and follow each other directly
  (interval 0: Blender still handles events between them). Results are shown
  at most every `redraw_gap()` while work goes on, and at once when it ends.
  A slice that did next to nothing does not spin. A redraw alone is not the
  user: anti-aliasing passes redraw without anybody doing anything.
* `_ensure_timer` restarts a timer that is in a long sleep, so work that
  turns up starts at once and not up to half a second later.
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
* **The verdict's wording** is in one place, `overlay.summary` (lines with a
  tone); the sidebar shows the same text. Tests compare heads and lines.
* **Printer names changed once** (`props.PROFILES_VERSION`, `_PROFILES_V1`):
  a 1.0 library is migrated, a scene saved with a 1.0 name shows the new one
  (`current_printer_name`) and never has its volume changed.
Interface and behaviour (1.2.0, the owner's lists of 2026-10-09):

* **Two switches**, `detect_collisions` and `monitor_volume`, both off in a
  new scene. `World.set_pair_check` stops pair work without forgetting
  anything; with both off the monitor is paused (`Monitor.pause` / `resume`),
  not discarded. The legacy `enabled` is migrated (`props.migrate_scene`).
* **Large meshes enter the world unsorted** (`add_raw`): their place is known
  at once, so the volume verdict does not wait; worker threads sort them,
  those that pairs wait for first (`_schedule`, `World.wanted`). A job has its
  own stop event: when its mesh is gone or replaced (a modifier being
  dragged) it is told to stop. `Monitor.drop()` when a monitor is thrown
  away: stops its jobs and calls `overlay.forget()`, because the overlay's
  caches held the old monitor, its arrays and GPU batches after File > Open.
* **Ignored problems** are a scene collection (`BucketBuilderIgnoredProblem`),
  matched by a signature: the second part as seen from the first, with the
  offset in *scene units* (multiplied by the first part's scale) and
  magnitude-aware tolerances, so that an object scale of 0.001 or 1000 and
  the single precision of the stored numbers do not matter; plus whether
  the pair collided (`aux[7]`). An entry made for "too close" never covers a
  collision, locked or not. Entries are written by operators and `save_pre`
  only, with one exception: the timer prunes entries whose part has left
  the scene (`Monitor.prune`), and `prune_ignored` removes the object if
  nothing else uses it (the entry's pointer was what kept a deleted part,
  and its name, alive).
* **The list of problems is a UIList** (`BUCKETBUILDER_UL_problems`): the only
  way to get left-aligned text in rows that can be clicked (operator buttons
  centre their text; enum buttons too). Its rows come from the monitor; the
  collection it is given (`WindowManager.bucket_builder_rows`) only has to be
  long enough (`props.ensure_rows`, called from `Monitor.tick`, never from a
  draw). The lit row is `bucket_builder_row`, an IntProperty with a getter
  (`Monitor.active_index`) and a setter that calls the `focus_problem`
  operator (so that a click is an undo step). Verified with a real click in
  `blender_gui_test.py`.
* **Colour in the sidebar**: Blender gives a panel no coloured rows and no
  coloured text except red. The list has a slim tab per row: a greyed colour
  swatch (`props._tab`, read-only properties on the preferences that follow
  the overlay colours). The status box has custom icons (`icons.py`, pixels
  premultiplied by alpha) beside plain text. The preference colours are
  `COLOR_GAMMA` (as plain `COLOR` their swatches were paler than what the
  overlay draws).
* **The arrows of Previous / Next are text glyphs** (U+25C0, U+25B6, in the
  bundled Inter of 4.2 and 5.2): a button's icon is always before its text.
* **A viewport in local view** says so in its badge and in its sidebar
  (`overlay.local_count`, `summary(status, local)`); the verdict stays the
  whole build's. `local_view_mode` decides whether problems among parts that
  are not shown are drawn.
* **The verdict is not "OK" while gaps are still being measured**
  (`status['refining']` with the clearance check on): such a gap can still
  become a warning.
* **The handler only acts on the graph the viewport shows**
  (`depsgraph.view_layer.depsgraph` is the one that fired, and the view layer
  is the window's): an exporter's own graph, in which everything counts as
  changed and what is not exported is missing, made every part be read again
  and parts drop out.
* `frame()` raises the view's Clip End when what it frames would be cut off
  (a build volume in millimetres is larger than the default 1000 units).

Engine, from the review of 2026-10-09 (four reviewers, cut off by the usage
limit but their scripts are in `scratchpad/review_*`):

* **The distance proof is bounded too, and what it leaves out is gone
  through**: a pair still cut short after the second pass whose lower bound
  allows a contact is flagged `unproven` (as a capped intersection search
  is), and one whose lower bound is under the clearance while the best
  found is not is flagged `unsure`; `narrow.scan` settles both (with a state
  made with `near=True` it stops at the first two triangles closer than the
  clearance: `('near', ...)`, reported as a warning with "~"). `no_cut` /
  `no_near` keep a cleared pair from being flagged again at the same
  placement. A quick (live) search that was cut with a lower bound at
  contact also leaves the pair unsettled. Before this a flange lying flat
  on a plate came out as "~0.1 mm apart", or as nothing with the clearance
  check off. Test: `capped_proof_tests`.
* **Out-of-volume pictures give way to a fit that pairs wait for**
  (`World._fitting`): with memory for two poses and not three the fits threw
  each other's poses out for ever.
* `_batch` looks a bounded stretch ahead for pairs that are ready (it walked
  the whole queue: a third of a first analysis of 600 parts).
* `_oob_fill` gives the results it fills a new serial (the overlay's static
  group is keyed by it: late pictures of large parts were not drawn until
  something moved). `set_scale` invalidates when the scale changes.

* Collapsible parts of a panel use `layout.panel(idname, default_closed=True)`;
  their state cannot be set from Python (the scratch script
  `scratchpad/panel_shots.py` opens them with simulated clicks for
  screenshots).
* Blender takes `bl_info` away from an extension's module: read the version
  with `addon_utils.module_bl_info` (`ops.addon_version`).
* The manifest's `schema_version` is the format's version, "1.0.0". Do not
  touch it when bumping the add-on's version.

## State

Last updated: 2026-10-10, early morning (session 2; the owner's lists of
2026-10-09 11:14, 13:55 and 21:52 are done as version 1.2.0).

* `main` is **1.2.0**, sent to the owner. He had 1.1.0 before. He works with
  a limited usage allowance: be economical (no large fan-outs without need).
* Tests at 1.2.0: `test_world.py` (about 10 minutes), 197 checks in
  `blender_test.py`, 48 in `blender_large_test.py`, the UI test (a stand-in
  layout that also runs the list's `draw_item`), the off-screen overlay
  test, and in a window `blender_gui_test.py` (with a real click on a row of
  the list), `blender_gui_undo.py`, `blender_gui_stress.py`,
  `blender_upgrade_test.py` from the 1.1.0 zip (`scratchpad/old_zips`, or
  build it from b9e2464). `python3.11 -m compileall bucket_builder` passes
  (Blender 4.2 has Python 3.11).
* The owner runs **Blender 5.2.0, Windows 11, NVIDIA GPU, OpenGL backend** on
  an HP ZBook Firefly 14 G11 with 32 GB. Performance > **Copy Report** is how
  to find out where time goes on his machine; he has not sent one yet.
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
* The sandbox VM changes speed from day to day (2.1 to 2.8 GHz Xeons, 2
  cores, so one worker thread). Figures from different sessions are not
  comparable: measure old and new in the same session
  (`scratchpad/run_bench_pair.sh`, `scratchpad/old_profile` has 1.1.0).
* Do not wait for a background job with `pgrep -f <its command line>` in the
  same command: it matches the waiting shell itself.
* Not tested: Blender 4.2 - 5.1, macOS, Vulkan / Metal, a real GPU.

## Next

### A. With the owner

* **3MF export** is "the next project" (the button is a placeholder that
  already warns about parts outside the volume).
* **The "3D Printing" application template** (File > New): he said "take a
  crack at it"; for the owner / operator of an MJF machine, import through
  export, easier than Netfabb / Magics. Not started. A template is a folder
  with a `startup.blend`, installed with
  `bpy.ops.preferences.app_template_install`.
* Offered, no answer: a per-collection ignore switch; a disk cache of sorted
  meshes; an explicit fast / accurate switch; wall gap on by default; amber
  shading for warning-only parts; the collision distance back as a "must not
  be closer than" red tier.

### B. Performance (he said: later, unless there are big fish)

Where the time goes now, and what each idea would buy (figures from the
2-core sandbox; his machine has 6 - 8 worker threads):

1. **Taking a build in** is sorting: 0.25 - 0.5 s per million triangles and
   thread. 30 M triangles: about 13 s here with one worker, an estimated
   2 - 4 s on his machine. *A disk cache of the sorted order* (12 bytes per
   triangle, keyed by the mesh hash, beside the .blend or in the user cache
   directory) would make reopening a build almost free. *A compiled sorter*
   (extensions may bundle wheels per platform) would be 5 - 10 x per thread.
   Hashing and cleaning (about 25 ms per million triangles) are still on the
   main thread.
2. **Live steps** are bounded by budgets and nearly independent of the
   triangle count, so there is little left to win per step in NumPy; a
   compiled traversal would let many more pairs have full detail per step.
3. **Large selections**: 63 ms per step when all of 600 parts move
   (`scratchpad/bl_group_move.py`): out-of-volume pictures 42 ms (no budget),
   box tests of all against all 15 ms, per-pair bookkeeping 16 ms, one
   `set_matrix` per object 10 ms. A rigid-group shortcut by per-part
   reference positions is NOT sound (two counter-examples in the session
   log); compare per pair, vectorised over a pair table.
4. **Per redraw, in Python**: `status()` and `_waiting()`, `problems()`
   rebuilt and sorted on every move step, `_groups` walking every problem
   and rebuilding the static group at the start and end of every drag (the
   number of hot slots is in its key), one batch and two draws per clipped
   patch, `_local_view` twice (0.5 ms per call at 600 parts here), the tint
   pass redrawing every colliding part (up to the Tint Detail budget;
   up to 8 meshes stay uploaded). Unknown on a real GPU.
5. **A resync on every selection change** (8 ms at 600 parts): any Scene or
   Collection update asks for one. Moving an ignored part no longer does.
6. **The sidebar read-back** (`_measure_sidebar`): three `read_color` calls
   per redraw of the sidebar, now at most four times a second during a drag.
   One read would do; cost on a real GPU unknown, worst on Vulkan.
7. **Rotating a 1.5 M triangle part at the memory limit**: 27 ms per step
   with 1.2.0 against 16 ms with 1.1.0 in the 30 M triangle bench of
   2026-10-09 (20 steps, one run each; at 12 M triangles there is no
   difference beyond noise, 14 - 20 ms). Not looked into.
8. A threshold scan (`unsure`) of two large faces at about the clearance can
   take long: it goes through every pair of triangles within the clearance.
   A branch-and-bound on the distance would be the proper tool.

### C. From the review, not done

* Results solved with borrowed boxes are not redone if the borrowed pose is
  dropped first (`_pose_drop` discards the slot from `_virt_todo`): the
  hatched region then stays the looser one. Cosmetic.
* int32 index arithmetic (`narrow.py` `t.ravel() + int(...)`, `world.py`
  `p.vbase + t[:, 0]`) wraps above about 26 GB of pose storage.
* Up to `2 * workers - 1` threads can sort at once (a lone large mesh gets
  the idle ones, later jobs still fill the pool).
* Problem ids and the row buttons go by part names: a linked library object
  and a local one can share a name.
* Update callbacks use `context.scene`, not `self.id_data` (a script
  flipping a switch on another scene pokes the wrong monitor until the next
  resync).
* The printer operators change `st.printer` without `'UNDO'`;
  `profile_rename` accepts a name that exists.
* `free_corner` in quad view: the sidebar and asset shelf are subtracted in
  every quadrant. A header flipped to the bottom is not counted.
* A built-in printer that keeps its name can never have its size corrected
  by a later version (the user's size wins).
* Blender 4.2 - 5.1 have still not been run: the API audit against the 4.2
  source found nothing missing.

Later, if wanted: smaller poses (quantised low levels); logarithmic extents
for a rotating part; results rotated instead of re-solved when a group is
rotated; non-mesh objects checked instead of warned about.
