# Decisions

A short register of what was decided, what was tried and dropped, and what is
still open. The details are in `AGENTS.md` (Implementation notes).

## Decisions

**Own collision engine instead of Blender's BVHTree**
- Context: results must follow the mouse during a drag.
- Decision: a NumPy engine with one tree per unique mesh, translation applied at query time, so dragging rebuilds nothing.
- Alternatives: `mathutils.bvhtree.BVHTree` (no transform in `overlap()`, no tree-to-tree distance).
- Revisit if: Blender gains transformed overlap and distance queries.

**Implicit tree in sorted triangle order**
- Context: whole frontiers of node pairs must be expanded in a few NumPy calls.
- Decision: node `i` on level `h` covers triangles `[i << h, (i + 1) << h)`; no pointers.
- Alternatives: pointer-based trees (Python-level traversal).
- Revisit if: the engine moves to compiled code.

**Fitted boxes per pose, as a bounded cache**
- Context: copies in the same rotation should share work; memory is about 55 bytes per triangle per pose.
- Decision: poses are fitted on demand, least recently used dropped first, under a memory limit.
- Alternatives: fitting every part up front.
- Revisit if: memory per pose can be cut (quantized boxes).

**A collision verdict is never left to a capped search**
- Context: nearly coincident fine meshes have millions of candidate triangle pairs; a capped search missed collisions and contacts.
- Decision: searches cut short are flagged and settled by an exhaustive, resumable scan; the badge says "Checking" meanwhile.
- Alternatives: larger caps (still missed cases, and slow).
- Revisit if: a branch-and-bound distance search replaces the scan for near-clearance pairs.

**Live steps have a work budget**
- Context: duplicating parts in place stalled single steps for seconds.
- Decision: a box-test and triangle-test budget per live step; what is cut short is redone exactly when idle.
- Alternatives: per-pair caps only.
- Revisit if: steps are compiled and much faster.

**Two independent switches**
- Context: collisions and the build volume are separate concerns to the user.
- Decision: "Detect Collisions" and "Monitor Build Volume", both off in a new scene; switching off pauses, it does not discard.
- Alternatives: one switch (versions up to 1.1).
- Revisit if: users find two switches confusing.

**Hidden parts are still checked**
- Context: parts are hidden to see into a bucket, but they are still in the build.
- Decision: checked unless "Ignore Hidden Parts" is ticked; parts disabled in viewports or excluded collections are left out.
- Alternatives: ignoring hidden parts by default.
- Revisit if: users mostly hide parts to leave them out.

**Ignored problems are matched by placement**
- Context: a problem the user accepts should stay ignored until it changes.
- Decision: stored with the relative placement of the parts; it counts again when that changes, unless locked; a lock on "too close" never covers a collision.
- Alternatives: ignoring by part names only.
- Revisit if: users want ignore-by-part rules.

**Large meshes enter unsorted; worker threads sort them**
- Context: the build-volume verdict should not wait for sorting.
- Decision: a part's place is known at once; sorting runs in threads, those that pairs wait for first.
- Alternatives: sorting everything before any verdict.
- Revisit if: a disk cache of sorted meshes makes sorting rare.

**The problem list is Blender's UIList; color as tabs and icons**
- Context: panels cannot color rows, and text only red.
- Decision: a UIList (left-aligned, clickable rows), a slim color swatch per row, colored icons beside plain text.
- Alternatives: colored icons in every row (judged too loud).
- Revisit if: Blender's layout API gains color.

**Arrange tools are independent of the checker**
- Context: drop to floor, minimize box / height, tilt off axis may later live in a general 3D-print add-on.
- Decision: `arrange.py` changes transforms only and imports nothing from the checker; its own convex hull.
- Alternatives: a separate add-on now.
- Revisit if: more general print tools accumulate.

**Snapping to the build volume through a helper wireframe**
- Context: Blender snaps only to real, visible geometry; the volume is drawn as an overlay.
- Decision: a switch adds an unselectable, unrendered 12-edge wireframe that follows the volume; the checks skip it.
- Alternatives: a custom snapping modal (would fight Blender's own snapping).
- Revisit if: Blender lets add-ons add snap targets.

**The packer is part of the add-on, not an add-on of its own**
- Context: it needs the collision engine (its referee) and the scene reading of Bucket Builder; Blender extensions have no way to declare one add-on as a dependency of another.
- Decision: one add-on, one line of development (`main`, 1.5.0); the Pack panel is closed by default and labeled Experimental, so users who do not pack can ignore it. Cost: a larger download.
- Revisit if: the engines become a package of their own (AGENTS.md, Engine boundary).

**The packer runs in a process of its own**
- Context: a packer in a thread of Blender's Python froze the interface (one GIL, many short NumPy calls).
- Decision: `pack/worker.py` in Blender's own Python, reading a job folder and writing progress; parts move as results arrive.
- Revisit if: never for the main loop; helper processes may grow.

**Voxels and FFT correlation for placement, the exact engine as referee**
- Context: hundreds of parts, arbitrary shapes, gaps that must hold.
- Decision: conservative solids on a 1 mm grid, the gap as a dilation of one side; every layout is checked with the collision engine; a final compaction moves parts to the exact gap.
- Alternatives: no-fit polygons (2D only), physics settling (slow, no guarantee).

**Density over the height the parts fill** (the owner's correction)
- Decision: volume of the parts over the bucket's area times their height from lowest to highest point; lifting the whole build changes nothing.

**Print-quality rules in a document of their own** (`docs/PRINT_QUALITY.md`)
- Context: the owner's experience of what packing does to parts is the project's real knowledge, and other tools need it too.
- Decision: rules with stable IDs (PQ-, PK-), each with problem, cause, detection, avoidance, who cares, source and how firm its numbers are; code refers to the IDs. To move to a repository of its own once it has outside sources.

**One dial between part quality and packing density**
- Context: customers range from "quality first" to "as many parts as possible".
- Decision: hard limits fixed; soft weights and the height curve scale with one Part Quality setting (`problem.dial_settings`).
- Revisit if: the case studies on real buckets show the rules need separate controls.

## Tried and abandoned

**Hiding the snap wireframe**
- Why it failed: Blender does not snap to hidden objects.
- Worth retrying if: never; making it unselectable is the way.

**`bmesh.ops.convex_hull` for the Arrange tools**
- Why it failed: it needs Blender to be built with Bullet, which not every build has.
- Worth retrying if: never needed; the own hull is small.

**A rigid-group shortcut for moving many parts at once**
- Why it failed: comparing per-part reference positions is not sound (counter-examples found).
- Worth retrying if: done per pair, vectorized over a pair table.

**A redraw after every background slice**
- Why it failed: each redraw restarted the viewport's anti-aliasing; a result that took 0.35 s of work appeared after 12 s.
- Worth retrying if: never; redraws are rate-limited while work goes on.

**Colored icons in every row of the problem list**
- Why it failed: too loud, by the user's judgement.
- Worth retrying if: rows can be tinted subtly.

## Open questions and assumptions

| Question or assumption | Status |
| --- | --- |
| Works on Blender 4.2 - 5.1 | Assumed: API audited against the 4.2 source and compiles on Python 3.11; not run |
| Works on macOS, Vulkan, Metal, a real GPU | Not tested |
| Cost of drawing and of the sidebar read-back on a real GPU | Unknown; Copy Report would tell |
| Snapping to the helper wireframe feels right in daily use | To try by hand |
| The tilt-off-axis rule (large flat faces at least 20 degrees from every axis) | A first rule, to refine |
| Sorting speed on a machine with 6 - 8 worker threads (estimated 2 - 4 s for 30 M triangles) | Estimate; not measured |
| Disk cache of sorted meshes, fast / accurate switch, per-collection ignore | Offered; no decision |
| The packer's weights, gaps and density limit | First guesses; to be corrected in case studies on real buckets |
| What was asked and what was assumed, item by item | `docs/TALLY.md` |
