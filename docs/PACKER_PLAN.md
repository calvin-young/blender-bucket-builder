# Packer: what to build next, in order

2026-11-16, written at the end of a session for whoever builds next.
**Claude's proposal**, not the owner's decision. Rule for every step: measure
before and after on `tools/bench_suite.py` (synthetic, numbers may be
published) and on the owner's real builds (local only, reported as
bucket-01 and so on), and keep a change only if the numbers improve.

Who packs and what "good" means for them: [USE_CASES.md](USE_CASES.md).

## The order, and why

Revised 2026-11-17 with the owner's answers:

1. **The independent check of the referee (1b)**, with only as much of
   1a as makes before/after numbers trustworthy: a fixed-seed,
   single-worker mode for the bench scripts. The owner sees little daily
   value in a deterministic packer for users, and he is right: it is for
   measuring. The independent check matters daily: "clean" must really be
   clean.
2. **Jiggle speed, then small turns (opt-in).** The owner said yes to
   turns, as an option; speed first.
3. **Soft clearance in the packer (4 below)**, moved up: the owner calls it
   "something a human would do but an autopacker normally wouldn't".
4. **#24 and #26** (3a, 3b below).

## Revised again, 2026-11-17 evening (the owner's solved challenge build)

The owner's own solution of the challenge build (local comparison:
`~/data/packbench/challenge/COMPARE.md` on the workstation) re-seated
parts rather than nudging them: three flat plates turned 90 degrees and
moved 170 - 260 mm, one long part dropped 77 mm, one row moved 15 mm, and
the rest by small uniform shifts. No small turns. That changes the order:

1. **Jiggle re-seats** (new, first): when small moves cannot clear a part,
   take out the part that blocks most (not necessarily one in the
   collision) and place it in the build's free room with every other part
   held, in any of the 24 axis turns; the packer's lock does the placing.
   Measured on the challenge build: does it reach zero collisions and his
   clearance tiers? And on his future blind challenge builds.
2. **Clearance tiers** instead of two numbers (docs/SPEC_PACK.md,
   section 4): Minimum 1.5, Acceptable 3, Preferred 5, Roomy 10 mm by
   default. `pack/soft.py` and Jiggle's `aim` generalize to a list of
   tiers and one cost curve.
3. **#44 (one X-ray toggle, exact hatching) and #43 (rank warnings by
   contact area):** the toggle is small and goes in the next build; the
   exact per-triangle distances serve both the hatching and the contact
   area, and the area then weighs Jiggle's priorities. After 1 and 2,
   because they change what the owner can do; #43 and #44 change what he
   sees.
4. **#24 and #26**, as before.

**Update, later the same day:** re-seating is built (`reseat=`,
`Settings.jiggle_reseat`, 3 by default since 1.5.5) and solved the
challenge build (local numbers: no collision, nothing under 1.5 mm, fewer
pairs under 3 mm than the owner's own solution). And the owner's direction
on clearances: the minimum is a strong, area-aware preference with
compromises reported for him to accept (#43), and alternative layouts
(#45) with ratios kept (#42). Order from here: Jiggle speed (re-seating
spends most of its time voxelizing the held parts again for every try:
build their grid once per run), then turns, then tiers with the
area-aware cost (needs #44's exact per-triangle distances), then #45.

**Jiggle speed, measured 2026-11-18 (timeboxed; the owner's challenge
build, least 1.5 mm, aim 3 mm, up to 6 re-seats, runs of 15 - 20 min):**

* Profile of a 400 s run with re-seating (cProfile): re-seat tries 284 s,
  of which 256 s re-voxelized the 34 held parts for every try (476 pins at
  0.54 s each); trial moves on the quick solve 82 s; exact settles 33 s.
* **Kept:** the held parts' grids are cached across tries
  (`greedy._PIN_CACHE`): voxelizing fell from 256 s to 33 s in the same
  profile. Results are identical (same grids; the coarse-count cache now
  keys on the coarse cell too).
* **Kept:** a move made only for the aim must lower the whole layout's
  cost, not just the moved part's (parts traded room for ever).
* **Tried and dropped:** re-seating with the packer's first fit alone (its
  few orientations found no seat where a packing job found one); a packing
  job of 8 s per try (found none either; back to 30 s); re-seating as soon
  as a part's ways out fail instead of after the small steps run out (no
  re-seat happened at all in two runs).
* **Not yet solved again:** since the solving run (16 min, re-seated parts
  32 and 34, then a new round cleared the rest), four runs re-seated the
  same two parts but ended with one collision: the run keeps trading small
  moves on gaps under the least (thousands of moves, no gain) and never
  reaches the fresh round that finished the job. So the solve depended on
  the path.
* **Next idea:** stop on stagnation, not on the clock: when the best
  layout has not improved for a minute, end the round and start a fresh
  Jiggler from the best (what `mend` does after a round, which is what
  solved it), and judge every move, hard ones too, by the whole layout's
  cost. Target stays about a minute.

**Stall restarts, measured 2026-11-18** (rounds end after 60 s without a
better layout and a fresh one starts from the best; every move judged by
the whole layout's cost; three runs of 15 min side by side, least 1.5 mm):
0 of 3 solved. Two runs cleared every collision but left 3 - 4 pairs under
1.5 mm, the tightest 0.1 mm and 0.001 mm (parts 14, 15, 23, 21: the same
cluster); one left a collision. Moves fell from thousands to under 200 per
run (no more trading of room), and each fresh round spent most of its time
on re-seat tries (30 s each). Re-seating stays on: without it every run
ends with a collision. Not good enough for a build. Next: re-seat the
cluster's parts together (take out 14, 15 and 23 at once and place them
again as a group), and cut a re-seat try that cannot find room early (the
packing job's first layout already says "no room" in a few seconds).

**Contact area and compromises, measured 2026-11-18** (three runs of
15 min): all three ended with one collision and no part re-seated, worse
than the run before (two of three collision-free, both re-seating parts 32
and 34). The compromises left were reported with their areas, e.g. one run
"1.35 mm over 2259 mm2", another "1.49 mm over 13 mm2 and 1.50 mm over
4 mm2". Not understood yet why no re-seat happened: the cluster re-seat
(two packing jobs of up to 60 s) and the score of every round (contact
areas of the close pairs) are the changes since; the next step is to log
each re-seat try and its outcome, then decide. Contact areas themselves
check out on synthetic cases (two faces 0.5 mm apart: 500 mm2 for 400 mm2
faces, the strip along their edges counted too) and take 0.2 - 5 s per
pair on parts of 200 k triangles.

**Packing from scratch, measured 2026-11-18** (the owner's request as a
user: five distinct parts, "1:1 ratio, fit as many as you can"; local
files, results in `~/data/packbench/challenge/loose/`): at most **4
complete sets**. Asking for 5 - 9 of each placed 24 - 28 parts in all, the
large parts first, so higher counts broke more sets; giving each set its
own priority tier (set 1 before set 2) still gave 4. A careful hand pack of
the same parts holds clearly more, so the packer's density on large,
interlocking parts is the real gap, not Jiggle. The two-clearance packing
spent most of its time at the aim (430 - 470 s for the first stage alone)
and nothing was squeezed in at the least.

Next, in order: (1) sets as a hard rule (#42, #45): a result never keeps a
broken set, and the run tries N, N + 1 ... and offers the best two; (2)
why only 24 - 28 parts fit: look at the layouts with the owner, then the
orientation and nesting search for few large parts (#24, #26); (3) the
first stage's time on large parts (minutes for 25 parts).

**How the owner packs whole sets (2026-11-18), to teach the packer.**
His verdict on the 7-set request (4 complete sets): "quite good as far as
autopackers go", but chaotic: every part has flat faces, yet none is
aligned to the bucket or to its neighbors, which leaves dead space. His
own method, in order:

1. Estimate N by eye.
2. Place the biggest, hardest part first, its N copies nested in a simple
   pattern at the core of the bucket (here the L-shaped holder, 7 nested).
3. Judge from the room that took whether N of everything else fits.
4. Then the parts that nest best, then squeeze the rest into the gaps.
5. If it does not fit, drop to N - 1 and start again.

What that means for the packer, as the next build steps (replacing (1) and
(2) above): (a) a **set mode** that runs this loop: N from the volume
bound, the hardest kind first as a lattice of N (the pattern packer
already makes nested lattices), then the rest by nesting quality, whole
sets only, N - 1 on failure; (b) **aligned orientations first**: for
parts with large flat faces, prefer turns that put those faces parallel
to the walls and to neighbors' faces (the free search draws from 24 turns
of the principal frame, so alignment with the bucket is there; alignment
between parts is not scored); (c) a score term for **dead space** between
parallel faces that are not touching. His 7 sets is the expert bar (ten
years of packing; he believes few colleagues would reach 7), and he wants
to put this bucket to the company as a challenge: "how many copies can
YOU fit?"

**Ratios across buckets (the owner, 2026-11-18).** The job repeats (about
150 of each), so a ratio may break inside one bucket if it holds across
several: two buckets of 6, 6, 7, 7, 7 and 7, 7, 6, 6, 6 beat 4, 4, 4, 4, 4.
Offer that as an alternative with its logistics noted (which bucket gets
which extra). Yield matters too: a pair that is too close may fail every
time, so a nominal 7 can really be 6; counts per bucket get adjusted as
yield data comes in. Issues #46, #47.

Jiggle's move limit is now `Settings.jiggle_reach` (20 mm by default,
adjustable; the owner: the limit only keeps the search quick).

## 1a. Repeatable runs (shrunk: a bench mode only)

* **Approach:** `Settings.repeatable` (docs/AUDIT.md, section 2). Results
  of worker tasks are adopted in the order they were submitted
  (`pack/job.py:1966`, `collect`), and every budget counts work done
  (placements tried) instead of seconds (`_time_up`, `_run_cost`, timed
  rebuilds, `FIRST_SHARE`, jiggle seconds). `tools/bench_suite.py` gets
  `--repeatable` and uses it by default.
* **Measure:** three runs of each suite bucket with 8 workers give the
  same layout, bit for bit. A test in `tests/test_pack_job.py`.
* **Risks:** a work budget is not a time budget, so "30 s" becomes "about
  30 s on this machine"; keep the clock as the default for users.

## 1b. An independent check of the referee

* **Approach:** `tests/ref_slow.py`, sharing no code with `core/`:
  sample points on every triangle (about 1 per mm², plus the corners), a
  uniform grid of cell size = the gap, nearest-point distances between
  parts by brute force inside neighboring cells; collisions by a ray
  parity test of sampled points against the other mesh. Slow and simple
  on purpose.
* **Measure:** on every layout the packer tests produce, and on each
  bench bucket once, the slow check and `pack/referee.py` agree: same
  colliding pairs; closest distances within the sampling error
  (about 0.5 mm at that density, so compare against gap - 0.5).
* **Risks:** sampling misses thin overlaps (state the bound); run time on
  large builds (sample fewer points, or check a random third of the pairs
  per run and say so).

## 2a. Jiggle speed

* **Where the time goes:** each trial move re-solves every pair of the
  moved part exactly; a part of 200 k triangles costs about 0.1 s per
  trial, and a move tries 7 to 27 directions.
* **Approach, cheapest first:**
  1. Fewer trials: try the directions away from the problems first (it
     does) and stop at the first one that reaches zero hard penalty;
     cache the cost of a part between trials that did not move it or its
     neighbors (`cost(j)` is recomputed for every part every turn:
     `run()`, the `costs` list).
  2. Only the pairs of the moved part change: keep per-pair results and
     recompute those, not `total()` over the whole world.
  3. Proxy meshes for the search: decimate each mesh once (or use the
     packer's voxel solid dilated by half a cell), search on those with a
     margin equal to the proxy's error, then run the exact check on the
     final layout and let a last exact pass fix what the proxy missed.
* **Measure:** the owner's challenge build (local): the same end state
  (one collision left, none under the least) in under 2 minutes instead
  of 15; synthetic tests unchanged; add a timing line to
  `tests/test_pack_jiggle.py`.
* **Risks:** a proxy that is not conservative lets a collision through:
  the final exact check is not optional.

## 2b. Small turns, opt-in

* **Approach:** `jiggle(..., turns=degrees)` (default 0: off). A part
  with a hard problem that no translation, way out or row push helps may
  try small rotations about its own center around x, y, z (plus and minus
  1, 2, 4 degrees up to the cap), each followed by the usual steps. At
  most N parts turn (default 2). A turned part re-fits its pose (one fit
  per trial: dear, so only as the last resort).
* **Measure:** does it clear the last collision of the challenge build
  (local)? If not, say what the smallest turn that would is. Synthetic
  test: a bar wedged diagonally between two locked boxes that only a turn
  frees.
* **Risks:** turning a part changes how it prints (surface finish by
  orientation): the owner has not said yes. Off by default, reported
  per part ("part-012 turned 2 degrees about x"), never applied to parts
  whose turning rule is Fixed.

## 3a. #24: mixed sizes with free rotation build two layers

* **What is known:** on `mix-a`-like buckets Netfabb's Monte Carlo packer
  builds lower (local numbers). Ours places large parts first, lying
  flat, and the small ones then need a second layer.
* **Approach to test, in this order:** (1) score the first layout by the
  *final* height of a quick fill of the small parts, not by the large
  parts' own height (a look-ahead of one greedy pass); (2) let large
  parts stand on edge when the small ones fit beside them (add the
  on-edge orientations to `_first_allowed` for parts above a size
  share); (3) pack the small parts into the pockets of the large ones
  before the next large part (order by "fills a pocket" instead of by
  size alone).
* **Measure:** `mix-a`, `mix-b`, `mix-c`, `huge-and-tiny`, `one-offs`:
  height and density at 30 s and 120 s, repeatable mode, against today.
  Keep whichever of the three wins on at least three of five and loses
  on none by more than 2 %.
* **Risks:** look-ahead doubles the first layout's time; cap it at the
  time the first layout takes today.

## 3b. #26: the coarse cell per bucket

* **Approach:** choose the coarse factor from the parts, not a constant:
  coarse cell = about a quarter of the smallest part's smallest box
  dimension, clamped to 2 - 8 cells. Small parts with coarse 4 lose
  placements between coarse cells; large parts with coarse 4 waste time.
* **Measure:** the whole suite at coarse 2, 4, 8 and "auto": auto must
  match the best of the three within 1 % in height on every bucket, in
  no more than the time of coarse 4.
* **Risks:** memory and FFT time at coarse 2 on a full 5600 volume;
  bound the grid size and fall back to 4.

## 4. Soft clearance in the packer (#35)

* **Approach, two steps (docs/SPEC_PACK.md, section 4):**
  1. Pack at the aim. Bodies left over are tried again at the least; the
     result says which ones were squeezed in. Then Jiggle with `aim=`
     (it exists) spreads what slack there is.
  2. Only if step 1 leaves density on the table: score placements by how
     much of the band between the two clearances they use (a second,
     wider kernel; one more correlation per orientation).
* **Measure:** suite buckets that leave parts out at 3 mm today: parts
  placed, and the count of pairs under the aim, against packing at 3 mm
  and against packing at 1 mm. The owner's real builds, local.
* **Risks:** step 1 changes results only when parts are left out; say so
  in the panel, or it looks like the setting does nothing.

## Built, 2026-11-17

* **1b, the independent check:** `tests/ref_slow.py` (no code shared with
  the engine; samples about 0.5 mm apart; a pair it calls too close is
  certainly too close), `tests/test_ref_slow.py`, and `--slow-check` in
  `tools/bench_suite.py`. On the owner's challenge build it finds exactly
  the referee's 13 pairs (5 minutes); on three packed synthetic buckets
  both say clean.
* **1a, shrunk:** `tools/bench_suite.py` runs one worker with seed 0
  unless `--parallel`; two runs of four buckets gave the same numbers.
* **2a, Jiggle speed:** trial moves on the quick solve, the step taken
  measured exactly and undone if not better; cached boxes. 4x the moves
  per second; the challenge build reaches a better end state in 5 minutes
  than in 15 before (same collision left, 12 pairs short of the aim
  instead of 20).
* **2b, turns:** `jiggle(..., turns=degrees)`, off by default, at most two
  parts, `fixed` parts never. They do not clear the challenge build's last
  collision (tried 1 - 4 degrees about each axis on both parts).
* **4, soft clearance in the packer:** `pack/soft.py`, `--aim` on the
  command line, the worker when `Settings.gap_aim` is set. No panel
  control yet (docs/SPEC_PACK.md, step 1).

Still to do: #24 and #26 (3a, 3b); the panel's two clearance fields.

## State at hand-over

* Jiggle has two clearances, ways out of collisions, row pushes and a
  reach (commits 270438f to e99e845); no panel control yet.
* The challenge build (local, `~/data/packbench/challenge/RESULT.md` on
  the workstation): all but one collision cleared; the last has no room
  by small moves.
* See "Built" above for what changed since.
