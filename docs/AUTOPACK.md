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
  "here is one pringle, I want 20" (an array). The commercial packer's array is a grid from
  a center-to-center distance; ours should find nested arrangements.
* Serial labels: embossed (or, later, color), **overlapping the part by about
  0.5 mm**, not face to face (HP's zonal scaling can separate parts that only
  touch). Two modes: kept as separate overlapping objects (preserves
  instancing, small file) or booleaned in at generation. The printer unions
  overlapping parts itself. His intake tools (commercial print-prep software, the vendor's job software, the API)
  preserve 3MF instancing; no round-trip test is needed.

**Scale and speed**

* Benchmarks: **60 parts of 1 M triangles**; **5 parts of 20 M**. Dense
  regular jobs (500 keycaps) are designed as sprued arrays and not
  auto-packed (parked, §12).
* Instant for simple builds; **first result under a minute** for a dense
  build; about 5 minutes for a very complicated one, longer for "as many as
  fit". Progressive: watch it improve, stop, keep going.
* **GPU usually available; detect it, run on CPU otherwise.**
* Solvers in a **dropdown**; ideally the tool characterizes the parts and
  **recommends** a solver and parameters.

**Workflow in Blender**

* Several buckets per file, like the commercial packer's workspaces: switch between them,
  clone one to try another layout and throw it away, delete, ignore, see each
  bucket's status ("C has clearance issues"), export one, selected or all.
  Today he does this by hand with collections. Mixed printer types are rare.
* The commercial packer counts only parts fully inside the volume in its density report,
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
  a later subprocess or server mode only has to serialize them.

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
* orientation penalties: large up-facing flat areas (meniscus, PQ-07: built
  in 1.4.3, faces within 1 degree of up (the owner: 10 was far too
  generous), `flat_top_weight`, counted in the score, in greedy's pose
  score and in the Build Report; on at the quality end of the dial only),
  cosmetic faces (later). Faces facing down do not matter (the owner,
  2026-10-21);
* optional: compactness (contact with neighbors), which helps later parts;
* **flat-face rules** (the owner, 2026-10-21; built in 1.4.2,
  `pack/quality.py`): large flat faces of two parts parallel and close to
  each other invite defects ("elephant skin"), and so do large flat faces
  parallel to the side walls; avoided unless necessary. A face counts from
  2500 mm^2, 50 x 50 mm (triangles grouped by normal within about 3 degrees and plane
  within 0.5 mm); a facing pair counts while the second face is less than
  10 mm in front of the first, by the overlap of their rectangles weighted
  by closeness (`flat_pair_weight`, 0.001 mm of height per mm^2); a wall
  face by its area when its normal is within 10 degrees of x or y
  (`flat_wall_weight`, 0.0004). Parts with wall faces also get their two
  flattest orientations turned 45 degrees about z to choose from. Greedy
  adds a pose's wall faces to its score and moves a part it has just
  placed sideways, away from a big face it would face closely, if there
  is room (`Job._nudge`: before smaller parts fill the space); the
  improvement loop weighs both rules against height; at the end, pairs
  still facing closely are moved apart where there is room, upwards only
  when the score says the extra height is worth it (`Job._separate`), and
  the compaction leaves those parts alone. The Build Report counts both.
  Huge-and-tiny: two 150 mm housings 3 mm apart with a 300 mm plate 3 mm
  above them (157 mm) became housings 10 mm apart and the plate 10 mm
  above (164 mm). Not yet: per-printer rules (§12).

The rules themselves (what goes wrong, why, how to detect and avoid it,
who cares) are kept apart from this spec, with stable IDs, in
`docs/PRINT_QUALITY.md`, so that other tools can use them.

**The quality / efficiency dial and the height curve (1.4.3)**, from the
owner (2026-10-21): customers sit anywhere between "part quality first"
(more room, preferred orientations, even layers) and "fit as many parts
as possible, throw the other rules out"; and a few millimeters of height
matter only near the top of the volume (157 against 164 mm: nobody
cares; 377 against 384 mm in a 380 mm volume: a deal breaker).
`problem.dial_settings(q)` maps the panel's **Part Quality** (0 - 100 %,
default 50) to the soft weights: facing pairs `0.002 q`, wall faces
`0.0008 q`, faces facing up `0.0016 max(0, q - 0.5)`, layer spread
`2 q` times the preset's, the final lift for even layers off below 5 %;
the hard limits never change. The score's height term is
`score.height_cost`: a millimeter costs `height_far = 1 - 0.8 q` far
below the top, rising evenly to `height_top = 3` over the last 10 % of
the volume's height (38 mm on a 5600), and 3 above it. At 50 % the flat
and spread weights are those of 1.4.2, height far below the top counts
0.6. **Trying harder near the top**: when the time set runs out while
parts are left out or the best build is within 3 % of the volume's
height, the job takes `near_top_extra` (1.0) times the time set once
more (`Job._try_harder`, logged). Engine defaults (`Settings()`) are
unchanged (linear height, no extra time); the operator and the command
line (`--quality`) apply the dial. Measured on six ridges (80 x 80 mm
flat bases, free turns): 0 % packs them in 26 mm with two bases facing
up, 100 % in 43 mm with every base down.

**Choices instead of one answer** (the owner, 2026-10-21, "let's noodle on
that"): sometimes the operator should see, say, one bucket that fits
everything at the cost of part quality and two buckets that keep the
rules. Design, not built: the job keeps a small archive of layouts that
are best on different axes (parts placed, height near the limit, the
quality cost) instead of one best; the panel offers "Show alternatives"
when the archive's ends differ in something that matters (a part left
out, a bucket more, a rule broken). With several buckets (Stage 5) the
same archive compares "N buckets, dial at 0" with "N + 1 buckets, dial at
100".

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
  build area times packed height** (the figure the owner uses; the commercial packer's
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
  linear in triangles, vectorized, done once per unique mesh *and rotation*
  (translation in Z only shifts the profile).
* Band thickness: 1 mm for display and scoring by default (layers are
  0.08 mm; a single layer is too fine to matter thermally), configurable.
* Layer density = band volume / (build area x band thickness).
* Displayed as a small profile plot (density against Z) in the sidebar, with
  the cap drawn as a line, and as a colored strip beside the volume in the
  viewport (later).
* **Spread penalty**: the profile is smoothed over a window (**5 mm**, the
  owner's choice) and penalized mainly for variation across the packed height
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
    voxelized interior, the distribution of local thickness. A hollowed or
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

> **As built (Stage 2, `pack/voxel.py`, `pack/bucket.py`).** Differences from
> the plan below: the bucket grid holds the placed parts' *solid* cells, and
> only the candidate is dilated, by the whole gap g (dilating both sides by
> g/2 adds the conservative neighbor ring twice). The dilation is by exactly
> the cell offsets d with h^2 sum max(|d_i| - 1, 0)^2 < g^2, the infimum
> distance between points of two half-open cells, so no shared cell means a
> real gap of at least g (checked against brute force). Triangles are cut
> across their longest edge until no edge exceeds h, then their bounding-box
> cells are marked; open meshes get a morphological closing before the flood
> fill. SciPy is optional (Blender does not ship it and PyPI was unreachable,
> so no wheels are bundled): NumPy paths give identical grids. The coarse
> grid is rounded up to sizes with factors 2, 3, 5 only (a prime length was
> 2.5 x slower), and single precision is used only where a bound on the
> FFT's rounding keeps it far from 0.5. Refinement slides a part a cell at a
> time checking only the kernel cells that lead in that direction.

* Grid over the bucket at resolution h (default **1 mm**; finer levels for
  refinement). The bucket grid holds, per cell, whether it is taken by an
  already placed part's *dilated* shape.
* Each part, per orientation, is voxelized **conservatively** (every cell the
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

> **As built (Stage 3, `pack/job.py`).** A `Job` runs, in order: greedy with
> every orientation (the first result); **probes**, each part's copies alone
> in an empty bucket in one orientation at a time, without the density limit,
> tallest and one of each bounding-box shape first, a probe stopping as soon
> as it is higher than the best finished one; **turned probes** near the
> best (turned about the vertical in 15 degree steps, and in free mode the
> best of each of the three best footprint shapes turned about its thinnest
> horizontal axis); greedy with each part limited to its best 1, 2, 3
> orientations; ruin and recreate (below); exact compaction (§8.5). It stops
> early at a lower bound (the tallest part at its flattest, the height the
> density limit demands). Why probes: greedy looks one part ahead, so it laid
> 81 keycaps flat in two layers where standing they fit in one, and mixed
> the orientations of 150 chips that nest when all are turned alike.
> Ruin: the top layer, the tallest part and its neighbors, a region, or a
> few at random; recreate in a shuffled size order; then every part settles
> (down, back, left on the grid); accepted when better, or within a
> threshold that shrinks to zero over the run. The bucket grid holds counts
> so parts can be taken out. With more than two cores the probes, the
> best-orientation runs and the ruin-and-recreate chains (slices of 8 s from
> the best layout) run in up to eight `spawn` processes while the first
> layout is built in the main one. Free mode is made deterministic: the
> principal frame of a degenerate shape is fixed by its smallest bounding
> rectangle (square shapes square to the axes) or, for a round outline, by
> the direction it bends toward the third axis; orientations are sorted by
> their posed shape. Score (`pack/score.py`): parts left out per tier, then
> placed volume, then a preset's weighted height and layer spread.

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

> **As built (Stage 4, `pack/pattern.py`, 1.3.99).** Single lattices in one
> orientation; double lattices are not built yet. Free offsets: one FFT
> correlation of the part's solid with its flipped kernel (`free_offsets`;
> agrees with the bucket's own test). Plane lattice: the nearest free offset
> along each of 90 directions, pairs of them smallest area first (a cheap
> test of a few combinations for all pairs at once), Gauss-reduced, the
> first whose every vector within reach is free. Stacking vector: the lowest
> dz over the plane cell that keeps every vector free (chips stack 9 mm
> apart though 11 mm thick). Into the bucket: 16 phases of the plane
> lattice, ordered by what they would give empty, so that only the promising
> ones are checked against what is there; the q lowest lattice points. Valid
> on the grid by construction; the referee checks the layout as always.
> In the job: a probe of a part with 20 copies or more is a lattice (0.2 -
> 0.5 s against about 2 s for greedy with 150 copies; below 20, greedy,
> which stops early, is cheaper); after "best 1" come the **lattice runs**:
> the part with the largest share on its lattice, then (if several) every
> such part in turn, each in the best of its two best orientations and the
> best of each other bounding-box shape, the rest by greedy around them.
> With the density limit met late, a lattice denser than the limit is
> passed over (its copies nest and cannot be lifted apart), and the job
> keeps the best layout that meets the limit as it is, to fall back on when
> lifting fails. Results (60 s, 2 cores): 150 chips without the limit at
> 60 mm after 30 s (standing in one layer; before, only the turned probes
> found it, near the end of the time); with the 12 % limit 111 -> 87.5 mm
> (nested flat stacks, 9 %: the one-layer layout is 13 %); bureau-day,
> keycaps and the rest as before. Against the bounding-box array (the commercial packer's
> array), copies in a 5600 volume with a 2 mm gap
> (`tests/bench_pack_pattern.py`):
>
> | shape | lattice | array | |
> |---|---|---|---|
> | chip | 885 | 360 | +146 % |
> | figurine (z only) | 2021 | 924 | +119 % |
> | bracket | 592 | 270 | +119 % |
> | big keycap | 1070 | 1100 | -3 % |
> | ring | 832 | 930 | -11 % |
> | small keycap (z only) | 7006 | 8568 | -18 % |
> | cube 20 mm | 2928 | 3468 | -16 % |
> | insole | 1740 | 2079 | -16 % |
>
> Parts that nest gain a lot; boxy ones lose to the array because a 1 mm
> grid is conservative (a 20 mm cube with a 2 mm gap needs a step of 23
> cells, the array 22 mm). Not built yet: the array itself as a pattern
> (it needs copies off the grid, as pinned parts are).
> `tests/test_pack_pattern.py` checks the free offsets against the bucket,
> every lattice with the referee, a lattice around a locked part, and the
> chips (lattice probes 0.5 s and 60 mm, greedy probes 5.8 s and 106.5 mm).
>
> **Double lattices** (1.4.0): a copy A and its partner B, A turned 180
> degrees about x, y or z (with free rotation these are among the 24
> orientations; in z mode only the turn about z, if the step allows it).
> One FFT of A's solid with B's kernel gives every offset where B clears A
> (`pattern.cross_free`); the nearest such offset along each of 400
> directions over the sphere is a candidate pairing, ranked by the
> bounding box of the pair (`pairings`, three kept). A and B glued at the
> pairing make one grid (`Envelope`) whose own free table covers A-A, B-B,
> A-B and B-A, so the existing lattice search applies unchanged and a
> lattice of pairs is valid copy by copy. Tried for the two best
> orientations; the run keeps whichever lattice places most copies lowest.
> Gains in a 5600 volume against the single lattice: gusset +48 %, wedge
> +33 %, knob +20 %, tee +13 %; chips, L plates and angles keep the single
> lattice (its steps may lean: L shapes nest along a diagonal by
> translation alone).
>
> **Families** (1.4.0, `pack/family.py`): parts that turn freely and are
> basically the same shape: size and volume within a factor 1.5 along each
> principal axis, and a coarse picture (16 cells along the longest side)
> whose overlap with an already accepted member's is at least 0.55 under
> one of the 24 turns of the principal frame. Members are compared with the
> four most alike in size of each family, so a chain of small differences
> is one family. The turn found lines the member up, so the reference's
> orientation k means the same for every member (`Family.matrix`). A
> family is probed as its reference with as many copies as the family has,
> then patterned on the **envelope** of its members in the best
> orientations, lined up by the centers of their bounding boxes: valid
> whichever member stands in a place. Families of up to 24 members also
> get double lattices. Cost: every member is voxelized per orientation
> tried (about 40 ms per chip here). What it does not do: chips that
> differ in bend stack as far apart as the deepest needs (150 different
> chips 108 mm against 87.5 mm for 150 identical ones in flat stacks);
> placing each copy at the lattice point but checked as itself, and
> sorting members so that neighbors are alike, were measured and gave
> nothing.
>
> **The shape library** (1.4.0, `synthetic.library()`,
> `tests/bench_pack_library.py --baseline`): 60 s, 2 cores, one process,
> no density limit, every layout clean by the referee; "greedy" is one copy
> at a time as before Stage 4.
>
> | bucket | parts | greedy | now | first layout |
> |---|---|---|---|---|
> | 50 different chips (a family) | 50 | 69.3 mm | 46.2 mm | 12 s |
> | wedges (door stops) | 150 | 128.2 | 72.3 | 12 s |
> | gussets | 300 | 117.7 | 67.0 | 9 s |
> | flat tees | 200 | 69.0 | 42.0 | 8 s |
> | knobs | 150 | 57.0 | 45.0 | 6 s |
> | 40 different knobs (a family) | 40 | 38.7 | 36.0 (bound) | 10 s |
> | tapered cups | 60 | 146.0 | 115.0 | 21 s |
> | domes | 60 | 107.0 | 60.0 | 14 s |
> | hex nuts | 600 | 65.1 | 41.0 | 3 s |
> | stars | 200 | 77.0 | 33.0 | 5 s |
> | C clips | 200 | 60.0 | 39.9 | 4 s |
> | J hooks | 200 | 63.8 | 33.0 | 9 s |
> | angles + L plates | 200 | 133.1 | 60.0 | 32 s |
> | keycaps, four sizes | 80 | 26.4 | 26.4 (bound) | 6 s |
> | mixed day (9 kinds) | 186 | 200 -> 86 | 86 | 22 s |
>
> Failures found: (1) the mixed day was 200 mm high because ten 200 mm
> rods, placed late, stood up in gaps on the floor (greedy took the lowest
> bottom whatever the top); greedy's score now counts how far a placement
> raises the build (`Settings.overshoot_weight`). (2) Tapered cups with a
> gentle taper do not nest: on 1 mm cells the conservative grid needs a
> step of about 36 mm where 21 mm is enough exactly, so the lattice lays
> them on their sides. (3) Boxy parts lose to the array (above). (2) and
> (3) want an exact refinement of the lattice (shrink each lattice vector
> by conservative advancement on the engine, copies off the grid). Rings
> (tori) do not come out linked (0 linked pairs in 120), but nothing stops
> a lattice from threading rings: the interlocking check (§8.9) matters
> more with patterns.
>
> **Mixed buckets** (1.4.1, the owner asked for much more varied shapes
> and sizes): `huge-and-tiny` (two 150 mm housings and a 300 mm plate with
> 500 parts of 5 - 15 mm), `mix-a` / `mix-b` (20 kinds at x0.3 - x3, all
> free), `mix-c` (30 kinds at x0.2 - x4, some fixed upright), `one-offs`
> (60 different parts, one of each). 60 s, 2 cores:
>
> | bucket | copies | height | lower bound | first layout |
> |---|---|---|---|---|
> | huge-and-tiny | 503 | 157 mm (176 before the fix below) | 150 mm | 23 s |
> | mix-a | 375 | 166 | 165.7 | 49 s (146 s before turned grids) |
> | mix-b | 208 | 117.9 | 117.9 | 42 s |
> | mix-c | 814 | 274.6 | 255.6 | 92 s |
> | one-offs | 60 | 255.6 (232 with 300 s) | 191.7 | 106 s (341 s before) |
>
> What they showed: (1) the height of a varied build is mostly set by its
> largest parts, and the packer reaches or nears the lower bound; (2) the
> first layout is the cost: large parts were voxelized in each of 24
> orientations, seconds each; now a large part (1.5 M cells or more) is
> voxelized once and its quarter turns are made by permuting and flipping
> the arrays (`Greedy._turned_grid`; a superset still, with up to a cell of
> slack on a flipped axis that the exact compaction takes back sideways):
> 3 x faster on these buckets; (3) lattices first put 500 small parts on
> the floor and the housings on top (176 mm): parts larger than every
> lattice part now go in before the lattices, and lattices come first only
> for builds of at most three kinds; (4) not solved: one-offs holds two
> open housings, one of which would fit inside the other (191.7 mm); the
> greedy only fills a cavity when the floor is full, and the improvement
> loop did not find it in 5 minutes. A "nest the containers" move (try
> each open part inside each larger one first) would.
>
> **Effort** (1.4.2, the owner asked 2026-10-21 whether a fast / optimal
> switch is needed): one progressive packer, and an Effort choice.
> Quick: the first layout and at most 10 s more. Standard: the Time given.
> Best: until no better layout has turned up for Give Up After (10 min by
> default), at most the Time Limit (2 h by default); the improvement loop
> then also starts afresh now and then (every part again in a shuffled
> order, orientations drawn from the good ones), so it does not only polish
> one start. The best layout so far is always kept and shown, so any run
> can be stopped. `Settings.stall_time`, `restarts`; CLI `--effort`,
> `--stall`.
>
> **Lattices first** (1.4.0): when parts with 20 copies or more, and
> families, are nine tenths of the copies, their lattice probes (with at
> most 12 turned ones, within a quarter of the time) and a lattice layout
> come before anything else, and greedy's layout of every copy one at a
> time only if that leaves parts out.

For "one part, make 20" and for groups of identical copies:

* The **feasible offset set** of a copy relative to another, in one
  orientation, is where the autocorrelation of the dilated grid is zero
  (each copy grown by its halo).
* Search lattices: pick short feasible vectors in directions of interest as
  candidates for basis vectors v1, v2, v3; keep combinations whose small
  integer combinations (|n_i| <= 2, more for long thin parts) are all
  feasible; maximize density V_part / |det(v1, v2, v3)|. Also try **double
  lattices** (every other copy rotated 180 degrees about Z), which is how
  many concave parts nest.
* Fit the lattice to the bucket: try a few orientations and phase offsets of
  the lattice in the volume, keep the arrangement with most copies inside, and
  hand the remaining space to the greedy solver for other parts.
* The commercial packer's array is the special case where the basis is the bounding box
  plus the gap; we report both counts so the gain is visible.
* Exact check: the lattice's few distinct neighbor offsets are checked once
  with the referee (all copies share them).

### 8.5 Compaction (all solvers)

> **As built (Stage 3, `pack/compact.py`).** Conservative advancement on the
> exact engine: the engine measures distances up to gap + 4 mm (its clearance
> threshold set there); a part may move by d - gap in any direction without
> coming closer than the gap, so it moves that much (less a margin of 1 % of
> the gap plus 5 micrometres, for the engine's 1 % proofs), measures again,
> and stops when the step is under 0.02 mm; a step after which the engine
> finds a pair under the gap is halved until it does not. Floor, side walls
> (wall gap) and the density limit's height are checked from the vertices.
> The job keeps min(30 % of the time, 1 s + 0.15 s per part) for it, and
> skips it when reading the meshes into the engine would take most of that.
> A compacted layout is off the packer's grid, so Improve Current Layout
> keeps such parts *pinned*: voxelized where they are (as locked parts),
> placed again only if ruin and recreate takes them out.

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

The owner's requirement (2026-10-20): rare in real builds, so low
priority, but expected (most programs have it). On by default, can be
turned off. It must never slow anything down: it runs in the background
only after every other check has passed and nothing else is waiting, and
an interlock found says so a moment later. Two parts that were found to
interlock are remembered as able to.

Design (not built yet):

* **What counts.** Two parts interlock when neither can be taken away from
  the other by a straight move without touching it (a hook through a loop,
  linked rings, a pin through a closed eye). Pairs only: three parts that
  lock only together are left out (much rarer, much dearer to find).
* **Candidates, cheaply.** Only pairs that already passed collision and
  clearance, and whose convex hulls overlap: if the hulls are apart, a
  plane separates the parts and one slides away. Nearly every pair in a
  packed build is dropped here (vertex-in-hull tests, or the hull distance
  from the engine).
* **The test.** For a candidate pair, try to move A away from B along 26
  directions (the faces, edges and corners of a cube) and along the line
  between their centers, on voxel grids that are supersets of the parts:
  if the swept A misses B in one direction, they come apart (sound,
  because the grids are larger than the parts). If every direction is
  blocked, check the most promising few exactly with the engine
  (conservative advancement along the direction). Blocked exactly
  everywhere means interlocked; blocked on the grid but not decided
  exactly means "may interlock", shown as a warning.
* **In the monitor.** The lowest priority of the timer's work: it starts
  only when no pair is unsolved, unsettled or being refined, and stops
  whenever the user does anything. Results are kept per pair and placement,
  like the other proofs, so moving one part re-checks only its candidate
  pairs. The badge says "Checking for interlocked parts" (neutral, not
  green) while that is going on, then "Build OK"; an interlock is a
  problem in the list like a collision. Guidance given to the owner: a
  separate line rather than holding back the whole verdict, so a slow
  check on a dense build never makes the badge look stuck.
* **In the packer.** The same test runs on the final layout before it is
  handed over (the packer knows which pairs it nested). An interlocked
  pair is taken apart by the packer itself: one part is taken out and
  placed again, and the layout is checked again; the result says "2 parts
  placed again: they interlocked". No question to the user.
* **Remembered.** Mesh pairs (by mesh hash) that interlocked are recorded
  (scene data and the packer's job), so the packer checks those pairs
  during packing (not only at the end) and the monitor tests them first.
* **Switch.** "Check for Interlocking", on by default, in the checks'
  settings; off skips the work entirely.

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
  present; see `CLAUDE.md`).
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
  picks** (as in the commercial packer's dialog: pick the surface, then size, depth and
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
* Interlocking check (§8.9). The owner (2026-10-21): "Build OK" may show an
  asterisk while interlocks are still being checked. Cheap topological
  filters, in order: hulls apart, free; neither part has a through-hole
  (genus 0, from the Euler characteristic of the mesh or of its voxels),
  only hooking is possible; both have holes, an exact linking test of
  their hole loops (linked rings can never come apart); otherwise (an O on
  the shaft of an I) the sliding test. The packer can also avoid threading
  outright: no part placed through a ring's hole.
* **Print technologies and printers** (the owner, 2026-10-21): this packer is
  for MJF buckets, but other technologies will follow, and the 580 and the
  5600 differ in details. Rules and parts of the algorithm (density limit,
  flat-face rules, wall gaps, layer spread, interlocking) should come from
  the printer profile and be switched on or off with it.
* **Several printers at once** (Stage 5, with the owner's two profiles): a
  service bureau sorts 1000 random parts into 4 printers every day and wants
  it quick, spread evenly rather than 3.5 buckets; a medical device maker
  prints the same bucket for weeks and wants 1 bucket rather than 1.5,
  however long it takes (Effort Best, saved layouts reused).
* Color (texture) labels for color printers.
* A GPU path inside Blender (bundled), depending on Stage 2.

---

## 13. Open questions, as answered by the owner (2026-10-12)

1. **Density reference**: the commercial packer reports both full bucket height and max
   part height; the latter is the useful one, "almost nobody cares about" the
   former. **Decision: report density against build area x packed height
   only.** The 12 % limit applies to it. **Refined (2026-10-20):** the height
   is the one the parts fill, from their lowest point to their highest: a
   single cube in the middle of the bucket is as dense as one on the floor
   (the commercial packer's "with object height" ignores only the empty space above).
   Raising a whole build off the floor changes nothing. (§6.1 mentioned both; the full-volume
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
5. **Labels**: placement needs user input. In the commercial packer a dialog lets the user
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
`CLAUDE.md` / this file updated. The model and effort are recommendations
for the owner to set before asking for the stage.

| Stage | Work | Done when | Model, effort |
|---|---|---|---|
| 0 | This spec; benchmark folders | spec reviewed by the owner | Opus 5.5, high |
| 1 (**done**, 2026-10-13) | `pack/meshio.py` (STL, 3MF), `pack/metrics.py` (volume, layer profile, density, partial-OOB warning), sidebar report and profile plot, `python -m pack report` | exact against analytic shapes (boxes, spheres, tori, rotated) to 1e-9; report on a 60 x 1 M build in seconds | Sonnet 5.5, medium |
| 2 | Voxel layer (conservative voxelization, flood fill, dilation), FFT feasibility, coarse to fine, greedy placement; CPU and GPU timing script for the owner | feasible layouts verified by the referee on all synthetic buckets; timing table on the VM and the owner's laptop; a decision on the GPU path | Opus 5.5, high |
| 3 (**mostly done**, 2026-10-16) | `Job` loop, improvement (ruin and recreate), compaction, rotation modes, locks, tiers, density cap, layer penalty; Pack panel and part list | beats or matches the owner's commercial packer baseline on the synthetic set; first result within the targets of §2 | Sonnet 5.5, medium |
| 4 (**mostly done**, 2026-10-20) | Pattern solver (lattices, double lattices, families) | beats the bounding-box array on pringle, figurine and Suzanne copies | Opus 5.5, high |
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

Stage 2 results so far (2026-10-13, 2 cores; `tests/bench_pack_greedy.py`,
every layout checked by the exact engine: no violation anywhere):

| Bucket (default settings, 12 % cap) | parts | height | density | time |
|---|---|---|---|---|
| bureau-day (7 kinds, mixed modes, gap 3) | 82/82 | 85 mm | 11.9 % | 9.7 s |
| pringles (free rotation, gap 2) | 150/150 | 109 mm | 7.2 % | 18.8 s |
| figurines (z, gap 1) | 80/80 | 55.5 mm | 4.8 % | 4.8 s |
| sockets + insoles (gap 3) | 30/30 | 140 mm | 7.3 % | 3.4 s |
| giant housing + keycaps | 31/31 | 180 mm | 5.6 % | 4.9 s |

The same in Blender's Python (NumPy only): identical layouts, same times
(the orientations of a part are searched in threads). With the cap binding
the height is the cap's, so resolution was compared without it, quantities
x 3 (bureau-day, 246 parts): cell 1 mm with coarse blocks of 2 / 4 / 8
cells: 153 / 161 / 182 mm in 500 / 25 / 12 s; cell 2 mm: 193 mm (blocks of
2) and 201 mm (4). So 1 mm cells matter (+20 % height at 2 mm), and a finer
coarse search buys about 5 % for 20 x the time on the CPU. That is the GPU
question: on a GPU a 2 mm-block query (200 x 150 x 200 cells) should cost
about what a 4 mm one costs here. Decision waits for `tools/pack_timing.py`
on the owner's laptop: if a 2 mm query is under about 2 ms there, add an
array-module backend (CuPy) for the coarse search; otherwise the CPU at
4 mm stays the default and Stage 3's compaction and improvement recover the
rest.

Large meshes that turn are voxelized from a surface cloud made once per
mesh (`voxel.Cloud`), so an orientation costs what the part's size costs,
not its triangle count: 5 ellipsoids of 4 M triangles (20 M) packed in 21 s
here, referee clean (10 s).

![The bureau-day bucket packed by the greedy solver, loaded into Blender:
the monitor, with its clearance set to the 3 mm packing gap, finds nothing
wrong](images/autopack-greedy-bureau-day.png)

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
