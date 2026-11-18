# Use cases

Who packs a build, and what a good result means to them. Each use case
drives a preset: how long the packer runs, what it tries to do, and which
alternatives it offers. The first three are the owner's framing
(2026-11-18). The others are Claude's additions and are assumed until the
owner confirms them.

| Use case | Time budget | Objective | Alternatives offered | Benchmark |
| --- | --- | --- | --- | --- |
| Production repeat | hours, up to 48 h | best density for a set ratio; whole sets | ratio across buckets, compromises | challenge parts (local), whole sets |
| Service bureau | until a deadline (say 5 pm) | most parts by priority, before the deadline | fewer parts with better quality | `bureau-day` |
| R&D quick look | seconds | a rough layout and a clear list of what is left out | none | `one-offs`, `mix-a` |
| Kits and cages (assumed) | minutes | N complete kits, small parts caged | cage or no cage | to build: a kit bucket |
| Racks for post-processing (assumed) | minutes | racks that fit the machine, then a dense bucket of racks | parts per rack | to build: a rack bucket |
| Fill the gaps (assumed) | seconds to minutes | add waiting parts to a layout without moving locked ones | none | `bureau-day` with locked parts |

## Production repeat (the owner's framing)

* **Who:** an engineer preparing a build that will be printed hundreds of
  times, for example 150 of each part of a product.
* **In their words:** "I don't care if it takes 48 hours; I want the
  optimal pack, because I'll print this 3MF hundreds of times."
* **Goal:** the most complete sets per bucket. A ratio may break inside
  one bucket if it holds across buckets (#46): two buckets of
  6, 6, 7, 7, 7 and 7, 7, 6, 6, 6 beat 4, 4, 4, 4, 4. Yield comes back per
  bucket, and counts get adjusted when a tight pair fails.
* **Good means:** more sets than an expert reaches by hand. The owner's
  7 sets on the challenge parts is the bar (#47).
* **Features that matter:** set mode, aligned faces, the clearance tiers
  with compromises reported for the user to accept, Jiggle, alternatives
  (#45), long effort levels (Overnight and longer).
* **Benchmark:** the challenge parts at 1:1, whole sets (local), against
  Netfabb and Magics with the same cascade (7, 6, 5 ... until all fit).

## Service bureau (the owner's framing)

* **Who:** a bureau operator with a printer that starts at a fixed time.
* **In their words:** "The printer starts at 5 pm; squeeze in as many
  parts as you can before then; printed once."
* **Goal:** the most parts, by priority (essential parts first, big parts
  before small ones), packed before the deadline.
* **Good means:** nothing essential left out; what is left out is small
  and easy to fit tomorrow; the run ends on time.
* **Features that matter:** priorities (tiers), a time budget given as a
  clock time, the list of left-out parts, adding waiting parts to an
  existing layout.
* **Benchmark:** `bureau-day` (synthetic, `tools/bench_suite.py`).

## R&D quick look (the owner's framing)

* **Who:** an engineer who wants to see what fits today.
* **In their words:** "Pack this ASAP so I can see what fits and what to
  prioritize for today vs tomorrow."
* **Goal:** a layout in seconds; rough is fine.
* **Good means:** fast, a clear count of what fits, and a leftovers list
  the user can act on.
* **Features that matter:** the Quick effort, the leftovers list, the
  Build Report.
* **Benchmark:** `one-offs` and `mix-a` with a 15 s budget.

## Kits and cages (assumed)

* **Who:** someone who prints sets of parts that belong together, with the
  small ones caged so they survive blasting.
* **Goal:** N complete kits, each kit's small parts in one cage
  (`docs/SPEC_PACK.md` 5.1, 5.2).
* **Good means:** every kit complete, cages that pack well, no part able
  to escape its cage.
* **Benchmark:** none yet; to build from synthetic kits.

## Racks for post-processing (assumed)

* **Who:** someone who vapor-smooths or dyes many small parts on racks
  (AMT, DyeMansion; `docs/POSTPROCESS_MACHINES.md`).
* **Goal:** racks that fit the machine's chamber, then as many racks per
  bucket as fit.
* **Good means:** every part on its rack, racks within the chamber, dense
  bucket.
* **Benchmark:** none yet; needs the owner's rack measurements.

## Fill the gaps (assumed)

* **Who:** anyone who packed or placed parts by hand and has more waiting.
* **Goal:** add the waiting parts without moving what is locked or
  already placed.
* **Good means:** as many waiting parts placed as fit, nothing else moved.
* **Benchmark:** `bureau-day` with half its parts locked.

## How the presets follow

The packer's Effort and Goal settings map onto these use cases. The
mapping is a proposal, not built yet:

* Production repeat: Goal "Whole Sets", Effort "Overnight" or longer,
  alternatives on.
* Service bureau: Goal "Big Parts First", Effort by deadline.
* R&D quick look: Goal "Most Parts", Effort "Quick".
