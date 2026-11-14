# Audit, part A: the live check's two open bugs, the engine, the packer

2026-11-13. Review only: nothing in the code was changed. Findings are ranked
by impact, each with where it is and a proposed fix. Part B (monitor,
overlay, interface) is still to come.

**What was read, and what was not.** Read in full: `core/tritri.py`,
`pack/referee.py`, `pack/_core.py`. Read in part: `core/narrow.py` (the
distance proof and the scan, lines 1100 - 1230 and 1400 - 1530),
`core/world.py` (storage and pair updates), `monitor.py` (pause, resume,
resync, the handler). Not read line by line: `core/bvh.py`, `pack/job.py`,
`greedy.py`, `bucket.py`, `voxel.py`, `pattern.py`, `stack.py`, `jiggle.py`,
`compact.py`. What is said about those rests on the notes, the tests and the
experiments below, and is marked as such.

## 1. Issue #19: a clearance warning at 1.95 mm on a pair 3.09 mm apart

**Not reproduced, and the engine is cleared as far as it can be without
Blender.** On the film's data (100 chips of 5120 triangles, chips 52 and 54,
clearance 2.99 mm), all run headless against `core.World`:

| Tried | Result |
| --- | --- |
| The final layout, all 100 chips, clearance on from the start, or switched on afterwards | 0 warnings |
| The film's moves replayed (60 frames, 0 - 2 live steps per frame, with and without a 250 MB memory limit) | 0 warnings once settled |
| 200 random sequences of moves, turns and steps for the pair, then back to the final placement | clean every time |
| Every search cap scaled down to 0.3 % of normal, five clearance values | never below 3.0906 mm |
| Brute force over the 58 398 candidate triangle pairs, in float64 and in the engine's float32 | 3.0906 mm, matching point sampling to 0.001 mm |

Three suspects inside the engine are ruled out for this case:

* **Borrowed poses** never apply: parts under 40 000 triangles are always
  fitted (`world.py:64`, `FIT_NOW`).
* **Stale results:** every change of an object's version, offset or
  parameters drops the pair's result and its pending scan
  (`world.py:1311-1354`).
* **The triangle distance itself:** every value it returns is a distance
  between two real points of the two triangles, so it cannot come out
  under the true minimum. I also tested the one place where it could
  (a corner projected onto a sliver triangle, `tritri.py:154-172`): 4000
  random sliver pairs, no error. These chips have no slivers anyway.

**What the report's wording tells.** "approx True, nothing pending" is the
signature of `narrow.scan_report` (`narrow.py:1503-1525`): the threshold scan
found two triangles closer than the clearance and stopped. That value is a
true distance for the geometry and matrices *the world held at that moment*.
So the world held something other than the scene: a matrix or a mesh that
the monitor did not bring up to date. The film moves 100 objects per frame
from a script while the monitor is paused, then switches it on. That path
(pause, resume, `resync`) reads right on paper (`monitor.py:583-606`,
`681-750`), which is exactly why it needs a run in Blender to settle.

**Fix, as a spec (three parts, do all three):**

1. **A reproduction in Blender**, `tests/blender_film_test.py`: build 100
   small parts, move all of them from a script over several frames with both
   switches off, switch the checks on, switch clearance on, let the timer
   settle (drive `monitor._tick_scene` by hand: timers do not run in
   background mode). Then compare, for every object, the world's matrix
   (`World.O_T`, `O_L`) with `matrix_world`, and the verdict with
   `pack.referee`. Run it with the film's real data once
   (`demo/chips/shots.py`, last segment, clearance on): if the matrices
   differ, the monitor is at fault; if they agree and 1.95 still shows, the
   engine is, and the dump names the triangles.
2. **A self-check when the monitor comes to rest.** When nothing is pending
   (the moment the badge turns to its final state), compare every mirrored
   matrix with the evaluated object's and every mesh signature with the
   stored one; on a difference call `set_matrix` / re-read, count it in
   `Monitor.fixups`, and show the count in Performance > Copy Report. It is
   one pass over the objects, microseconds at 600 parts. This turns a silent
   wrong verdict into a self-healing, counted event, whatever the cause.
3. **A verdict is never drawn from a result older than its parts.** Give
   each `PairResult` the two object versions it was computed from, and have
   `Monitor.problems()` skip (and re-queue) a result whose versions are not
   the current ones. Cheap, and it closes the whole class.

Why this ranks first: the same mechanism that shows a false warning can hide
a real one, and a missed collision is the one error this tool must not make.

## 2. Issue #22: the two failing job tests

**Fixed already, by commit 2e6b12f (2026-11-10); the issue was left open.**
Both checks pass five times in a row. The cancel test passes because the
stacks wrapper no longer yields before placing anything; the same-seed test
passes because the test helper now sets `workers=1`
(`tests/test_pack_job.py:133`).

What remains true, and belongs in the issue's closing note: **a packing job
is repeatable only with one worker and no time budget.** Layouts depend on
the clock or on which worker finishes first in these places:

* results adopted in completion order: `job.py:1966` (`collect`), and a
  3 s wait at `job.py:2083`;
* the clock deciding what is tried: `_time_up` (`job.py:330-345`), skipping
  runs that would overrun (`job.py:730`, `1416-1418`), timed rebuilds
  (`job.py:619-623`, `1480`, `1513`), the first layout's share
  (`job.py:906`), jiggle seconds (`job.py:1295`).

**Fix, as a spec:** keep the default as it is (a time budget is what users
want), and add `Settings.repeatable`: results are adopted in the order the
tasks were submitted, and every budget is counted in work done (placements
tried) instead of seconds. Benchmarks and bug reports use it; so does the
comparison with other packers, where a number that changes from run to run
cannot be argued about.

## 3. Findings, ranked

### High

**A1. The referee is the engine it referees** (`pack/referee.py:9`, the
whole file). Every "referee clean" in the benchmark tables, every Jiggle
result and every test of the packer is judged by `core.World`. If the engine
misses a collision, the referee misses it too, and nothing in the project
would notice. `tests/test_world.py` compares the engine with brute force on
its own scenes, which is good, but it is not run on packed layouts.
*Fix:* an independent checker in the tests, `tests/ref_slow.py`: plain
brute-force triangle distances with a simple grid to find candidates, no
shared code with `core/` beyond `tritri` (or none at all: point sampling).
Use it (a) on every layout the packer tests produce, at small sizes, and (b)
once per release on the benchmark buckets. Also: `check()` repeats `_world()`
line for line (`referee.py:24-45` and `66-83`); make it call it.

**A2. The live check has no end-to-end test of "the world equals the
scene"** (section 1). Every Blender test checks verdicts on scenes the test
built through the normal path. None moves many objects by script with the
checks off, or compares the mirror with the scene afterwards. The film found
#19 by accident. *Fix:* section 1, parts 1 and 2.

**A3. Packing results cannot be repeated** (section 2). *Fix:*
`Settings.repeatable`.

### Medium

**A4. The narrow phase carries too many states to reason about**
(`core/narrow.py`, 1680 lines; `world.py:111` lists the flags). A pair's
result can be `approx`, `refine`, `lite`, `loose`, `unproven`, `unsure`,
`no_cut`, `no_near`, and four queues decide what is redone when. Each flag
was added to close a real hole (the notes say which), and the brute-force
tests are the reason it works. But every one exists because NumPy cannot
search a pair of large meshes exhaustively in a frame, so the search is
capped and the caps need bookkeeping.
*Where I think this is on the wrong track:* the project now ships compiled
code (`pack/native`, plain C through `ctypes`). An exact tree-against-tree
distance in C is about 200 lines and typically 50 - 200 times faster than
the NumPy traversal. With that, most pairs are simply *solved*, every step,
and most of the caps, the two-pass proof, the scan and five of the eight
flags have nothing left to do. *Proposal:* prototype one function,
`pair_distance(tree_a, tree_b, transform, stop_below)`, measure it on
`tests/bench.py --storm` and the 30 M triangle scene, and decide from the
numbers. Keep the NumPy path as the fallback and as the reference the tests
compare against. This is the largest change worth making to the engine, and
it should wait until A1 and A2 exist, because they are what would catch a
mistake in it.

**A5. The engines are importable only by a trick** (`pack/_core.py:5-10`).
Headless use puts `bucket_builder/` on `sys.path`, which makes the engines
top-level packages called `core` and `pack`: names that collide with other
software, and a `try / except ImportError` that hides real import errors.
*Fix:* D5, below.

**A6. Compiled binaries are committed and built by hand**
(`pack/native/bbcore.so`, `bbcore.dll`; `tools/build_native.sh`). There is
no macOS library (it falls back to NumPy, silently), and nothing proves the
committed binaries were built from the committed `bbcore.c`. *Fix:* a CI job
that builds all three, runs `tests/test_pack_native.py` against each, and
fails when a committed binary differs from a fresh build. Say in Measure
Packer Speed which path is in use.

### Low

**A7. `tritri._vertex_face` trusts the normal of an almost-degenerate
triangle** (`tritri.py:163-171`: `nn > 1e-300`). No wrong result found (4000
sliver pairs, and the argument that a projection inside a thin triangle is
still a real closest point), but nothing in `tests/test_tritri.py` covers
slivers. *Fix:* add sliver pairs to `special_pairs()` and a random sliver
test against point sampling; raise the guard to a relative one
(`nn > 1e-24 * longest_edge**4`).

**A8. Not verified in this pass, from the notes only:** the packer's limit
on real builds is the 1 mm grid at gaps under a cell (AGENTS.md,
2026-11-10). The Magics and Netfabb results will show how much that costs.
If it is the main loss, the answer is exact refinement after the grid
(`pack/compact.py` already moves parts by exact distances), not a finer
grid. Hold #24 - #27 until those numbers are in.

## 4. D5: `buildcore` as an installable package in this repo

It changes the layout in one place, and fixes A5 on the way.

* **Where:** `bucket_builder/buildcore/` with `collide/` (today's `core/`)
  and `pack/` inside it. It stays inside the add-on's folder, so the zip
  and the "copy `bucket_builder/` over the installed copy" loop are
  unchanged, and no file is vendored or copied at build time.
* **Installable:** a `pyproject.toml` at the repo root with
  `package-dir = {"buildcore" = "bucket_builder/buildcore"}`. `pip install .`
  gives `import buildcore`; the add-on uses `from .buildcore.collide import
  World`. Inside the package only relative imports. `pack/_core.py` and its
  `try / except` go away, and so do the top-level names `core` and `pack`.
* **Command line:** `python -m buildcore.pack` (today `python -m pack`).
  The worker process is started with the add-on's folder on its path, as
  now.
* **License:** Apache-2.0 for `buildcore/`, GPL-3.0-or-later for the add-on
  around it (Apache-2.0 code may be part of a GPL-3 work, not the other way
  round). Every file in `core/` and `pack/` carries a GPL header today; the
  headers change, and `buildcore/` gets its own `LICENSE`. This is only
  sound if nothing in those folders was derived from GPL code. They import
  nothing from Blender, and I saw nothing copied, but I did not read every
  file: check before publishing the package.
* **Guard:** `tests/test_engine_boundary.py` extends to "nothing in
  `buildcore/` imports from the add-on".
* **Not now:** a separate repo, a PyPI release, platform wheels. One
  package in this repo is enough until someone outside Blender uses it.

## 5. What I would do next, in order

1. Section 1, parts 1 - 3 (#19). One day, most of it the Blender test.
2. A1: the slow independent checker, and run it over the benchmark layouts.
3. D5 (mechanical; hand it to a cheaper model with this section as the spec).
4. `Settings.repeatable`, then rerun the benchmark tables with it.
5. The A4 prototype, measured, then a decision.
6. Packer work (#24 - #27) after the Magics results.

## Requested, assumed, not asked

* **Requested:** diagnose #19 and #22 without implementing; ranked findings
  with locations and fixes; whether D5 changes the layout.
* **Assumed:** that a wrong verdict ranks above every packer finding (the
  portfolio review says so); that `buildcore` stays inside the add-on's
  folder; Apache-2.0 for it.
* **Not asked:** whether macOS matters enough for A6; whether a compiled
  narrow phase (A4) is welcome at all, given the NumPy-only history.
* **Follow-ups:** part B of the audit; the Blender reproduction of #19;
  reading `pack/job.py` and `core/bvh.py` line by line.
