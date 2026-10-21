# Auto packer: specification

Status: **Stage 0, design.** Nothing here is built yet. This document is the
reference for every later session: what the owner asked for, what was decided
and why, the architecture, and the plan in stages. Keep it current; when a
decision changes, change it here and say why.

Written 2026-10-12 from the design conversation with the owner
(github.com/calvin-young). Work happens on the `autopack` branch; `main`
stays releasable.

---

## 1. Purpose

Automatic part packing ("nesting") for powder-bed printers, HP Multi Jet
Fusion first, inside the Bucket Builder add-on, and the same engine without
Blender, from the command line. Today the owner uses a commercial Monte Carlo packer
and his colleagues use another commercial tool. The aim is not only a denser packer, but one
that understands what an MJF operator is trying to do:

* put as many parts as possible into a bucket while respecting constraints;
* spread jobs across buckets (fill to N, ratios, fewest buckets, even);
* keep the build thermally sane (3D density cap, even density per layer);
* tell the user what a constraint costs ("4.5 mm instead of 5 mm gives +2 %");
* be fast enough to be progressive: a first answer quickly, better answers
  while the user watches, stop when it is good enough, "keep going" later.

## 2. Requirements from the owner

Recorded as he stated them; defaults he named are in **bold**.

**Buckets and goals**

* A bucket is one print job, normally the full build volume. Sub-buckets
  (two jobs stacked in one build) are ignored for now.
* What pays is **parts per build**. Lower Z height saves a little energy and
  consumables; it matters most for rush jobs. Normal jobs run overnight, and
  10 h vs 12 h does not matter.
* Typical builds float at **5 - 7 % packing density**, limited by geometry. Some
  geometry can exceed a recommended limit by accident, so: **maximum packing
  density, default 12 %**.
* **Layer (areal) density** matters: a regular array of cubes alternates
  between X % and 0 % from layer to layer, which is bad. A checkbox, **on by
  default**, spreads density across layers with a *soft* per-layer maximum
  (often unavoidable; must be fuzzy, not a hard limit).
* Other trade-offs: part spacing, wall gap, orientation for layer-line
  aesthetics, and the meniscus defect on perfectly flat top surfaces.
* Job planning across buckets is "essential, a big ask from industrial users":
  quantities ("50 of these, 10 of these"), ratios (1:1:1:1:1), "spread across
  5 buckets", "as few buckets as possible", "as many as fit".
* **Priority**: "pack as many of these 50 as fit, these 7 first, the rest
  equally". One level may suffice; tiers are possible.

**Spacing**

* Global part-to-part gap: 0.5 mm for small figurines that never nest
  closely, 5 mm for cubes with parallel walls, 10 mm for the very
  conservative. Most users are fine with a global value; showing the *value*
  of variable spacing would be "awesome".
* Warn when large, dense parts face each other with flat parallel faces at a
  small gap; do not warn for small, light, or point contacts (two spheres).
* Thin-walled and hollowed parts can sit closer than solid ones (the halo
  should know). A hollowing tool is a separate, later add-on.

**Parts and constraints**

* Rotation per part: **none**, **about Z**, or **free**. Set in the part list
  (one button per row) or on a selection from a right-click menu.
* **Lock position** per part (pack around it).
* Packing parts inside other parts' cavities is common (a transtibial socket
  full of insoles): **allowed**. Interlocking is rare: a **placeholder
  checkbox**, implemented only if easy.
* Copies: "here are 20 pringles" (20 parts, maybe slightly different) and
  "here is one pringle, I want 20" (an array). The array tool he uses today is a grid from
  a center-to-center distance; ours should find nested arrangements.
* Serial labels: embossed (or, later, color), **overlapping the part by about
  0.5 mm**, not face to face (HP's zonal scaling can separate parts that only
  touch). Two modes: kept as separate overlapping objects (preserves
  instancing, small file) or booleaned in at generation. The printer unions
  overlapping parts itself. His intake tools (commercial print-prep software, the printer vendor's job software, the API)
  preserve 3MF instancing; no round-trip test is needed.

**Scale and speed**

* Benchmarks: **60 parts of 1 M triangles**; **5 parts of 20 M**. Dense
  regular jobs (500 keycaps) are designed as sprued arrays and not
  auto-packed (parked, §12).
* Instant for simple builds; **first result under a minute** for a dense
  build; about 5 minutes for a very complicated one, longer for "as many as
  fit". Progressive: watch it improve, stop, keep going.
* **GPU usually available; detect it, run on CPU otherwise.**
* Solvers in a **dropdown**; ideally the tool characterises the parts and
  **recommends** a solver and parameters.

**Workflow in Blender**

* Several buckets per file, like the workspaces of his current tool: switch between them,
  clone one to try another layout and throw it away, delete, ignore, see each
  bucket's status ("C has clearance issues"), export one, selected or all.
  Today he does this by hand with collections. Mixed printer types are rare.
* His current tool counts only parts fully inside the volume in its density report,
  without warning. **Warn next to the density whenever a part is partly out
  of bounds.**

---

## 3. Vocabulary

| Term | Meaning |
|---|---|
| bucket | one print job: a printer volume plus the parts in it |
| part | one object to print; *copies* share a mesh (linked duplicates) |
| halo | a part's own clearance radius; the gap required between two parts is the sum of their halos |
| wall gap | required distance from parts to the volume's walls (the add-on already has this setting) |
| 3D density | sum of part volumes divided by the reference volume (§6.1) |
| layer density | part volume within a Z band divided by the band's volume |
| solver | an algorithm that places parts in one bucket |
| planner | the outer loop that decides which parts go into which bucket |
| referee | the existing exact collision engine (`core/`), which verifies every layout |

---

## 4. Architecture

```
            Blender (UI, scene, preview)                CLI (no Blender)
                    |                                       |
     bucket_builder/autopack_ui.py ...                  pack/cli.py
                    \                                       /
                     +------------- pack/ ----------------+
                     |  planner -> solvers -> referee     |
                     |  voxel grids, metrics, halos       |
                     +------------------------------------+
                                       |
                                     core/   (exact engine, unchanged API)
```

### 4.1 Package and imports

* New package **`bucket_builder/pack/`**, beside `core/`, **no `bpy`
  imports**, so it ships inside the add-on zip and also runs headless.
* `bucket_builder/__init__.py` imports `bpy`, so headless code cannot import
  `bucket_builder.pack`. Headless callers put `bucket_builder/` on `sys.path`
  and import `pack` and `core` as top-level packages, exactly as the engine
  tests do today. Inside `pack`, the only import of the engine is in
  `pack/_core.py`:

  ```python
  try:
      from ..core import World, narrow, bvh   # inside Blender
  except ImportError:
      from core import World, narrow, bvh     # headless
  ```

  Every other `pack` module imports from `pack._core` (relative imports
  between `pack` modules work in both cases).
* If they are ever needed outside the add-on, `core/` and `pack/` move to a
  standalone Python package and the add-on vendors it at build time. Not now.

### 4.2 Dependencies

* **NumPy** (Blender bundles it).
* **SciPy** for `scipy.fft` (multithreaded real FFTs), `scipy.ndimage`
  (connected components for flood fill, Euclidean distance transforms for
  wall thickness and halos). Blender does not bundle SciPy, so the extension
  bundles its wheels (`blender --command extension build --split-platforms`).
  Cost: roughly 30 - 40 MB per platform and Python version; Blender 4.2 - 4.x
  use Python 3.11 and Blender 5.x a newer one, so several wheels per
  platform. Accepted because flood fill and distance transforms in pure NumPy
  are slow and error-prone. `pack` must still *import* without SciPy and say
  clearly which features are unavailable (so the monitor never breaks).
* **GPU** (optional): an array-module abstraction (`pack/xp.py`) selects
  NumPy/SciPy or CuPy at run time. CuPy is not bundled in the extension; it is
  detected if the user has installed it into Blender's Python, and the CLI can
  use it from a normal environment. The decision whether to bundle a GPU path
  waits for the Stage 2 measurements.
* No trimesh or other mesh library. `pack/meshio.py` reads STL (binary and
  ASCII) and 3MF (mesh objects, components, build items with transforms) with
  NumPy and `zipfile` / `xml.etree`.

### 4.3 Jobs: one interface for Blender and the CLI

A packing run is a `Job` object with the same shape as `World.step`:

```python
job = pack.Job(problem)          # parts, bucket(s), settings; plain data
while not job.done:
    job.step(budget=0.05)        # does a slice of work, never blocks long
    snap = job.snapshot()        # best layout so far + metrics + log
job.keep_going(seconds=60)       # continue from the best layout
```

* Blender drives it from a worker thread (NumPy, SciPy and CuPy release the
  GIL in the heavy calls) and a timer that applies `snapshot()` to object
  matrices, so the user watches parts move into place. While a job runs, the
  monitor's pair checks are paused (`World.set_pair_check(False)`), and the
  referee runs at the end.
* The CLI drives the same loop and writes results to JSON/3MF.
* `Problem` and results are plain dataclasses of NumPy arrays and numbers, so
  a later subprocess or server mode only has to serialise them.

---

## 5. Problem model

### 5.1 Inputs

Per **part** (or per group of copies of one mesh):

| Field | Default | Notes |
|---|---|---|
| mesh | | vertices, triangles (millimeters); copies share one |
| quantity | 1 | for "make 20 of this" |
| rotation mode | bucket default | `fixed`, `z`, `free` |
| locked | false | fixed position and rotation; packed around |
| tier | 1 | integer, 1 = most important; one level in the UI at first |
| halo | auto | override in mm |
| ratio group | none | parts with ratios belong to a set (§8) |
| label | none | serial label settings (§10) |

Per **bucket**: printer volume, wall gap, no-build zones (boxes or meshes),
preset and goal (below).

Global **settings**: part gap (uniform) or halo mode (adaptive, §7), maximum
3D density (**12 %**), spread layer density (**on**) with a soft per-layer
maximum, voxel resolution, time budget, solver (`auto` or explicit), seed.

### 5.2 Hard constraints and soft goals

Hard constraints are never violated; if they cannot all be met the result
says which part could not be placed and why.

* no two parts closer than their required gap (collision is the case gap 0);
* every part inside the volume, at least the wall gap from the walls;
* outside no-build zones;
* locked parts stay; rotations stay within each part's mode;
* 3D density at most the maximum (12 %).

Soft goals are folded into one **score** that solvers minimize:

* parts placed, by tier (dominant: see below);
* Z height (packed height);
* layer-density penalty (§6.2);
* orientation penalties: large up-facing flat areas (meniscus), cosmetic
  faces (later);
* optional: compactness (contact with neighbors), which helps later parts.

Tiers are lexicographic: no number of tier 2 parts outweighs one tier 1 part.
Within a tier the score is a weighted sum. A "soft" limit becomes a penalty
that is zero below the limit and grows steeply above it, so it can be broken
when nothing else works but is never broken for free.

### 5.3 What the user chooses

The interface exposes three things, not a page of weights:

1. **Goal**: *as many as fit*, *these quantities in as few buckets as
   possible*, *spread across N buckets*, *lowest height*.
2. **Preset** of soft weights: *Overnight* (count first, height nearly free),
   *Rush* (height first), *Quality* (layer spread and orientation first).
   An *Advanced* fold shows the weights themselves.
3. Plain numbers for the hard limits (gap, wall gap, maximum density) and the
   time budget.

The preset weights are guesses until calibrated on the benchmark buckets
(§11). They live in one table in `pack/score.py`.

---

## 6. Measuring a build (Stage 1)

This is the packer's objective and also useful on its own in the manual
workflow, so it comes first.

### 6.1 3D density

* Part volume: signed tetrahedron sum over the closed mesh, exact, per unique
  mesh, scaled by the object's determinant. Non-closed meshes are reported,
  not guessed.
* Reported: total part volume, packed height, and **density against the
  build area times packed height** (the figure the owner uses; his current tool's
  full-bucket figure is dropped, §13). The maximum-density limit applies to
  it.
* Parts partly outside the volume: their volume is counted, and a **warning
  sits next to the density** ("2 parts partly out of bounds; density counts
  their full volume"). A part wholly outside is listed and excluded.

### 6.2 Layer density

* Exact volume per Z band without voxels: by the divergence theorem with the
  field F = (x, 0, 0), the volume of a closed mesh below a plane z is the
  integral of x n_x over the part of its surface below that plane (the cap
  contributes nothing). Each triangle is clipped only against the band
  boundaries it spans, its contribution scattered into those bands. Cost is
  linear in triangles, vectorised, done once per unique mesh *and rotation*
  (translation in Z only shifts the profile).
* Band thickness: 1 mm for display and scoring by default (layers are
  0.08 mm; a single layer is too fine to matter thermally), configurable.
* Layer density = band volume / (build area x band thickness).
* Displayed as a small profile plot (density against Z) in the sidebar, with
  the cap drawn as a line, and as a colored strip beside the volume in the
  viewport (later).
* **Spread penalty**: the profile is smoothed over a window (**5 mm**, the
  owner's choice) and penalised mainly for variation across the packed height
  (coefficient of variation of the smoothed profile): the owner wants density
  spread across layers when that is easy, not a limit. A per-layer **cap of
  50 %** is only a backstop, with a steep penalty above it. The cube-array
  example scores badly on the variation term. Weights are guesses until
  calibrated.

### 6.3 Where it is shown

* Sidebar (built, Stage 1): parts, volume, height, density, peak layer,
  spread, the layer profile as a picture, the out-of-bounds warning. Results
  are cached per (mesh, linear part, height): a sideways move is free, a lift
  or a turn costs one pass over that mesh, in a worker thread. The panel has
  its own switch (`report_density`) and needs one of the two checks, which
  read the parts.
* CLI: `python -m pack report bucket.3mf` prints the same numbers, so private
  buckets can be measured on the owner's machine without leaving it.

---

## 7. Spacing: halos

Each part gets a **halo** r; the gap required between parts i and j is
r_i + r_j. A uniform gap g is the special case r = g/2 for every part.

* **Uniform mode** (default): exactly the user's gap. Plus warnings (below).
* **Adaptive mode**: halos come from part descriptors, clamped to a minimum
  and maximum the user sets (for example 0.25 - 2.5 mm, i.e. gaps of
  0.5 - 5 mm). First offered as a what-if suggestion with its density gain
  (§9.4), so the user sees its value before adopting it.
* Descriptors (per unique mesh and scale):
  * volume and bounding size (thermal mass);
  * **wall thickness**: from the Euclidean distance transform of the part's
    voxelised interior, the distribution of local thickness. A hollowed or
    thin-walled part has a small thickness even if its envelope is large, so
    it gets a small halo. (The owner asked for this explicitly.)
  * **flat area**: triangles clustered by normal and plane offset; the area
    of large planar patches.
* Halo formula: `r = clamp(r_min, r_max, r_base * f(thickness, flat area,
  volume))`, with `f` a small monotone table calibrated against the owner's
  rules of thumb (figurines 0.5 mm gap, cubes 5 mm, conservative 10 mm).
  It is deliberately simple and explainable.
* **Pair warnings** use the referee, not the halo: after packing, for each
  pair closer than a warning distance, measure the facing area of nearly
  parallel surfaces within that distance (from the near region the engine
  already computes for the hatching) and weight it by the thinner part's wall
  thickness. Above a threshold: "parallel faces 1.2 mm apart over 2400 mm²".
  Two spheres touching at a point produce no facing area and no warning.
* Engine change needed: the referee currently has one clearance threshold
  (`World.set_thresholds`). It needs **per-object halos** so the required
  distance of a pair is r_i + r_j. Small, contained change in `world.py` and
  `narrow.py`; with every halo equal it must give today's results exactly.

---

## 8. Solvers

All solvers place parts in **one bucket**. They share the voxel layer (§8.1)
and the referee. They are listed in the solver dropdown; `auto` picks one
(§8.7).

### 8.1 The voxel layer

* Grid over the bucket at resolution h (default **1 mm**; finer levels for
  refinement). The bucket grid holds, per cell, whether it is taken by an
  already placed part's *dilated* shape.
* Each part, per orientation, is voxelised **conservatively** (every cell the
  surface touches, by triangle-box overlap) and **filled by flood fill from
  outside**: a cavity open to the outside stays empty (other parts may go
  there: insoles in a socket); a sealed void counts as solid (nothing may be
  trapped there). Two grids are kept: the *solid* grid (for walls and
  density) and the *dilated* grid (solid grown by the halo, a ball of radius
  r, conservatively rounded up to cells).
* A part fits at a translation t when its dilated grid shifted by t meets no
  taken cell, and its solid grid lies inside the volume shrunk by the wall
  gap (a box, so this is just a range of t). Because both grids are
  conservative, a voxel-feasible layout always satisfies the real gaps; the
  price is up to about 2h of wasted gap, recovered by compaction (§8.5).
* **All translations at once** by cross-correlation: the collision count for
  every t is `irfftn(rfftn(taken) * conj(rfftn(part)))`. The bucket's
  spectrum can be updated by adding a placed part's shifted spectrum (linear,
  O(N)) instead of recomputed.
* Cost: the 5600 volume at 1 mm is 380 x 284 x 380 = 41 M cells; one full
  correlation per orientation per part is too slow on a CPU for hundreds of
  parts. So the default is **coarse to fine**: correlate at 4 mm (64 x fewer
  cells) to find feasible regions, keep the best K candidates, and confirm
  each at 1 mm with a direct (non-FFT) check in a small window. Stage 2
  measures where the crossover lies on CPU and GPU, and whether a
  height-map pre-filter is worth adding for the bottom-up case.
* No-build zones are taken cells from the start. Locked parts are placed
  first, as taken cells.

### 8.2 Greedy placement (the fast tier)

* Order: tier, then a size key (largest dilated volume first), then the given
  order. Copies of the same part are consecutive.
* For each part and each allowed orientation: compute feasible translations,
  then pick the one with the best **placement score**: lowest bottom (deepest
  bottom left fill: Z, then Y, then X), adjusted by the layer-density
  penalty. The layer term is one-dimensional: adding the part's profile at
  height z changes the bucket's profile, and the penalty for every candidate z
  comes from one 1-D convolution, so it costs almost nothing.
* Orientations per mode: `fixed`: 1; `z`: rotations about Z sampled
  every **30 degrees** by default (a setting; the owner wants continuous
  rotation, so the improvement stage may refine angles continuously);
  `free`: the 24 axis-aligned orientations of the part's principal frame, each also with the Z steps of its mode, pruned
  by the meniscus penalty. Orientation grids are cached per (mesh, rotation).
* Stop when every part is placed, when none of the remaining fits, or at the
  density cap.

### 8.3 Improvement (progressive, anytime)

* **Ruin and recreate** (large neighborhood search): repeatedly remove a set
  of placed parts (the top slab, a random region, the parts around a gap, or
  random parts), re-insert them and any unplaced parts greedily with a
  shuffled order and orientation choice, and keep the result if its score is
  better (or, early on, if it is not much worse, as in simulated annealing).
* It is anytime: every accepted layout is a valid snapshot, so the user can
  stop it, and "keep going" just continues the loop.
* Chosen over a genetic algorithm because each step is cheap, local, and
  naturally progressive; a GA needs whole populations of full layouts.

### 8.4 Pattern solver (copies of one part)

For "one part, make 20" and for groups of identical copies:

* The **feasible offset set** of a copy relative to another, in one
  orientation, is where the autocorrelation of the dilated grid is zero
  (each copy grown by its halo).
* Search lattices: pick short feasible vectors in directions of interest as
  candidates for basis vectors v1, v2, v3; keep combinations whose small
  integer combinations (|n_i| <= 2, more for long thin parts) are all
  feasible; maximise density V_part / |det(v1, v2, v3)|. Also try **double
  lattices** (every other copy rotated 180 degrees about Z), which is how
  many concave parts nest.
* Fit the lattice to the bucket: try a few orientations and phase offsets of
  the lattice in the volume, keep the arrangement with most copies inside, and
  hand the remaining space to the greedy solver for other parts.
* The grid array of his current tool is the special case where the basis is the bounding box
  plus the gap; we report both counts so the gain is visible.
* Exact check: the lattice's few distinct neighbor offsets are checked once
  with the referee (all copies share them).

### 8.5 Compaction (all solvers)

Voxel layouts waste up to about 2h of gap. A final pass moves each part, in
order from the bottom, along -Z, then -Y, -X, by bisection between where it
is and where it would touch, using the referee's exact distances, until each
required gap is met exactly. Optional, on by default; skipped for locked
parts.

### 8.6 Simple solvers

* **Single part fit**: one big part; search orientations (fine steps in its
  mode) for one that fits; report "does not fit" with the smallest overshoot.
* **Box/shelf packing**: parts whose volume is close to their bounding box
  (ratio above about 0.8) or when the user asks for bounding-box behavior.
  Instant.

### 8.7 Recommending a solver (`auto`)

From cheap features of the problem: part count, size spread, number of
identical copies, convexity (volume / hull volume), thickness, rotation
modes.

* one part: single part fit;
* many copies of few meshes: pattern solver, then greedy for the rest;
* mostly box-like parts: shelf packing, then compaction;
* otherwise: greedy, then improvement for the time budget.

The recommendation, and why, is shown beside the dropdown. Rules first; data
from benchmark runs may refine them later.

### 8.8 Verification (the referee)

Every result is checked by the exact engine before it is shown as final:
collisions, gaps (per pair, with halos), walls. A violation is a bug; the
layout is still shown, with the problem listed by the monitor as usual. In
Blender this is the existing monitor, switched back on.

### 8.9 Interlocking

A checkbox, off, labeled as not implemented, until it is easy: the voxel
disassembly test (remove parts along the three axes until none can move;
anything left interlocks) fits the voxel layer well and may come with the
improvement stage.

### 8.10 Large meshes

Solvers never see full meshes. Voxelisation reads triangles in chunks, and
its result depends on the part's size, not its triangle count: a 20 M
triangle part becomes as many cells as a 20 k triangle part of the same
size. The referee works on the full meshes, as it does today.

---

## 9. Planner (several buckets) and what-if

### 9.1 Goals

* **As many as fit** (one bucket): greedy then improvement; tiers first.
* **Quantities in as few buckets as possible**: estimate a lower bound from
  volume and expected density; fill bucket by bucket (first fit, largest
  parts first); then move parts between buckets to empty the last one.
* **Spread across N buckets**: split quantities evenly (or by ratio) and pack
  each; then balance heights.
* **Lowest height**: pack, then lower the volume's ceiling step by step
  (bisection) while everything still fits.

### 9.2 Ratios

Parts in a ratio group are added as **sets** (1:1:1:1:1 is one of each). The
planner adds whole sets until a set no longer fits, then tries
ruin-and-recreate to fit one more set; leftover space can take lower tiers.

### 9.3 Tiers

Tier 1 is packed first and must fit (if it does not, the result says so and
which parts are left). Lower tiers fill the rest. The UI shows one checkbox
("essential") at first; the model is an integer.

### 9.4 What-if

Once the requested run has a result, variants run in the background with
the same time budget: gap 0.5 mm less, wall gap less, rotation one step
freer, adaptive halos. A suggestion is shown only if its gain is larger than
the run-to-run spread (measured by running the base case with several
seeds), e.g. "4.5 mm gap: 23 parts instead of 20 (+15 %)". Only worth doing
when the fast tier is fast; it waits for Stage 2's numbers.

---

## 10. Blender integration

### 10.1 Buckets panel (Stage 5)

* A bucket is a **collection** tagged by the add-on, holding its printer
  profile, ignore flag and cached status. No Blender Scenes (more isolation,
  but re-pointing the monitor and much more plumbing for little gain).
* Panel: list of buckets with status icons (clean, warnings, collisions, out
  of bounds, ignored, not checked yet), and buttons: view, new (printer),
  clone, delete, ignore, export (one, selected, all).
* **View** shows one bucket and hides the others with the layer collection's
  eye, so they are still evaluated (the monitor already treats hidden parts as
  present; see `AGENTS.md`).
* **Clone** copies the collection's objects with shared mesh data (linked
  duplicates), so cloning a 600-part bucket costs almost nothing, and the
  engine shares one tree per unique mesh.
* Engine change needed: **groups in the World**. Every object carries a
  bucket id; the broad phase pairs only objects of the same bucket, and each
  bucket has its own volume. One engine checks all buckets: the visible one
  live, the others at low priority, and their verdicts feed the list.
* Packer output: N bucket collections, parts as linked duplicates.

### 10.2 Packing panel (Stages 3 - 6)

* Goal, preset, solver (with the recommendation), gap / halo mode, wall gap,
  maximum density, spread layers, time budget; Pack, Stop, Keep going.
* Part list (UIList, as the problem list): name, quantity, rotation mode
  button, lock button, tier, halo. Right-click in the viewport or outliner on
  a selection: set rotation mode, lock, tier.
* Results: placed / requested, density, height, layer profile, warnings,
  what-if suggestions with an "apply" button.

### 10.3 3MF export

Export is "the next project" for the add-on and the owner maintains a fork of
the Blender 3MF add-on (`calvin-young/blender-3mf-io`). Export all buckets
calls that exporter per bucket collection; whether to depend on it or write a
minimal exporter in `pack/meshio.py` is decided at Stage 5.

---

## 11. Labels (Stage 7)

* Text from a pattern (`{job}-{n:04}`) placed on a surface **the user
  picks** (as in the dialog of his current tool: pick the surface, then size, depth and
  text), embossed and **embedded 0.5 mm** (parameter) into the part.
  Defaults: **5 mm text height, 0.5 mm emboss**. Engraving is also offered; it
  changes the part's mesh, so that part becomes single-user and loses
  instancing (the dialog says so).
* Mode **separate** (default): one label object per copy, parented to it; the
  part's mesh stays shared. In 3MF each build item becomes a component object
  (shared part mesh + its own label mesh). Mode **booleaned**: union at
  generation; every copy gets its own mesh.
* The referee must not report a label against its own part: a label is
  treated as part of its host (engine skips that pair; for other pairs the
  label counts as the host's geometry). The packer packs the part with its
  label.
* Text meshes come from Blender text objects in the add-on; the headless
  tool may skip labels at first.

---

## 12. Parked (later, with the owner)

* Keycap-style jobs: sprued arrays, 100 per sprue, handled as one part for
  bead blasting and vapour polishing.
* Sub-buckets: two jobs stacked in one build.
* Near-duplicate families ("20 slightly different pringles" treated as one
  lattice with their common envelope).
* The "3D Printing" application template (File > New).
* A hollowing add-on (separate from this one); halos already account for
  thin walls.
* Interlocking check (§8.9).
* Color (texture) labels for color printers.
* A GPU path inside Blender (bundled), depending on Stage 2.

---

## 13. Open questions, as answered by the owner (2026-10-12)

1. **Density reference**: his current tool reports both full bucket height and max
   part height; the latter is the useful one, "almost nobody cares about" the
   former. **Decision: report density against build area x packed height
   only.** The 12 % limit applies to it. (§6.1 mentioned both; the full-volume
   figure is dropped.)
2. **Layer spread**: smoothing window **5 mm**. A per-layer *cap* is not the
   right way to think about it: the goal is to spread density across layers
   when that is easy. If a cap is needed, **50 %** is the default. So the
   spread term (variation of the smoothed profile) carries the weight, and
   the cap is a backstop with a steep penalty.
3. **Rotation about Z**: no reason to limit it to steps; **continuous**. The
   greedy solver samples angles (default step 30 degrees, a setting); the
   improvement and compaction stages may refine an angle continuously.
4. **Halo calibration** (the owner's rules of thumb for the spacing he gives,
   which is gap = sum of two halos):

   | Part | Wall | Gap he uses |
   |---|---|---|
   | transtibial socket (half a 580 bucket) | 3 - 5 mm | 3 - 5 mm |
   | D&D figurine, 1" base | solid | 1 mm (0.5 mm "if naughty") |
   | cube 50 mm | solid | 5 mm |
   | cube 50 mm | 2 mm shell | 3 mm (to another cube) |
   | sphere 50 mm | solid | 3 mm |
   | sphere 50 mm | 2 mm shell | 1 mm |
   | typical AFO | 3 - 4 mm | 3 - 5 mm (rarely parallel surfaces) |

   These are the first calibration points for the halo table (§7): flat
   faces and bulk raise the gap, thin walls and curvature lower it. Shell
   thickness is what takes a cube from 5 to 3 mm and a sphere from 3 to 1 mm.
5. **Labels**: placement needs user input. In his current tool a dialog lets the user
   pick a surface, then size, depth and text. **Defaults: 5 mm tall text,
   0.5 mm emboss.** Engraving is also wanted; it changes the mesh, so that
   part becomes single-user and loses instancing (the UI says so).

**Halo cost gate** (owner): the halo engine change goes ahead only if it has
no large performance hit. Measure `tests/bench.py --quick` and the scale
benchmarks before and after, in the same session; if the cost is large,
table the feature (the packer works with a uniform gap without it).

---

## 14. Stages

Each stage ends with tests, benchmarks where relevant, a commit and push, and
`AGENTS.md` / this file updated. The model and effort are recommendations
for the owner to set before asking for the stage.

| Stage | Work | Done when | Model, effort |
|---|---|---|---|
| 0 | This spec; benchmark folders | spec reviewed by the owner | Opus 5.5, high |
| 1 (**done**, 2026-10-13) | `pack/meshio.py` (STL, 3MF), `pack/metrics.py` (volume, layer profile, density, partial-OOB warning), sidebar report and profile plot, `python -m pack report` | exact against analytic shapes (boxes, spheres, tori, rotated) to 1e-9; report on a 60 x 1 M build in seconds | Sonnet 5.5, medium |
| 2 | Voxel layer (conservative voxelisation, flood fill, dilation), FFT feasibility, coarse to fine, greedy placement; CPU and GPU timing script for the owner | feasible layouts verified by the referee on all synthetic buckets; timing table on the VM and the owner's laptop; a decision on the GPU path | Opus 5.5, high |
| 3 | `Job` loop, improvement (ruin and recreate), compaction, rotation modes, locks, tiers, density cap, layer penalty; Pack panel and part list | beats or matches the owner's current packer as a baseline on the synthetic set; first result within the targets of §2 | Sonnet 5.5, medium |
| 4 | Pattern solver (lattices, double lattices) | beats the bounding-box array on pringle, figurine and Suzanne copies | Opus 5.5, high |
| 5 | Planner (goals, ratios, quantities); Buckets panel; World groups; export all | multi-bucket jobs from the synthetic set; bucket workflow tested in a window | Opus to design, Sonnet to build |
| 6 | Halos (adaptive, thickness), pair warnings, what-if, solver recommendation | what-if suggestions reproducible across seeds | Sonnet 5.5, medium |
| 7 | Labels | labeled builds pass the referee | Sonnet 5.5, low to medium |

Stage 1 results (2 cores, 2.4 GHz): reports against Blender's own bmesh
volume agree to 1e-4 in 46 cases of moves, lifts, rotations, scales,
modifiers and linked copies; against an independent slicing reference to 1e-9
for analytic and random shapes. Timings of `tests/bench_pack_metrics.py`:
60 distinct meshes of 1 M triangles 13 s (220 ns per triangle); 12 meshes x 5
copies 3.7 s (same orientation) to 9 s (every copy turned); 5 meshes of 20 M
triangles 15 s. In Blender, 46 parts of 490 k triangles (22 M in all, 40 of
them copies): first report 1.8 s, a lift 0.3 s, main-thread slices under
25 ms. Reading a 3MF costs 2.2 s per million triangles.

Blender in the sandbox: `download.blender.org` and PyPI are blocked by the
sandbox's network policy (2026-10-12); GitHub is reachable. Stages 1 - 4 are
engine work tested headless. Blender is needed from the Blender parts of
Stage 1 onward: build it from source as in session 1
(`tools/test-blender/README.md`, asking the owner first; slow on 2 cores), or
the owner runs the Blender tests on his machine with commands given to him.
The VM has 2 cores and no GPU; GPU numbers come from the owner's laptop.

---

## 15. Benchmarks

* **Synthetic shapes** (generated, in the repo): cube, sphere, figurine with a
  spear (sphere + thin cylinder), saddle ("pringle"), hollow socket with
  insoles to go inside, keycap-like shells, Suzanne at several scales (the
  owner exports it as STL from Blender at a few subdivision levels, since the
  sandbox has no Blender). Scale sets: **60 parts x 1 M triangles** and
  **5 parts x 20 M triangles**.
* **Synthetic buckets**: fixed recipes of the shapes (mixed bureau day, all
  pringles, swarm of figurines, socket with insoles, one giant part), each
  with its settings. Public, so the owner can run his current packer and
  Monte Carlo on them and record density, height and time as a baseline in
  `benchmarks/baselines.csv`.
* **Private buckets** stay on the owner's machine in `benchmarks/private/`,
  which git ignores except for its README (a test fails if anything else in
  it is ever tracked). The benchmark script reads them there and writes only a
  results table with generic labels (`bucket-01`); the mapping from labels to
  file names is in a manifest inside the same ignored folder. File names are
  never written to results.
* Metrics per run: parts placed / requested, 3D density (both forms), packed
  height, layer profile statistics, time to first result, time to final,
  peak memory, and referee violations (must be zero).
