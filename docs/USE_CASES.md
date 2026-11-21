# Use cases

Who packs a build, and what a good result means to them. Each use case
drives a preset: how long the packer runs, what it tries to do, and which
alternatives it offers.

| Use case | Type | Time Budget | Objective | Alternatives Offered | Benchmark |
| --- | --- | --- | --- | --- | --- |
| Full Production | full bucket | no limit | a client is producing one or more parts at scale, over a prolonged period; the parts must be produced in particular ratios; a marginal increase in packing efficiency can make a big difference on part cost; maximize packing efficiency given the PQ requirements | achieve specified ratio across multiple buckets, if it cannot be accomplished in a single bucket; compromises to PQ to improve packing efficiency | challenge buckets packed by an expert human (existing algorithms cannot compete) |
| Service Bureau | full bucket | minutes to hours | an assortment of random parts need to be packed into as few buckets as possible; parts can be sorted by priority, and packing must be finished before the deadline | compromises to PQ to avoid a small number of leftover parts | `bureau-day` |
| Quick Job | partial or full bucket | seconds | a small number of parts that are expected to fit into a single bucket; prioritize speed over efficiency | improved efficiency in exchange for more packing time | `one-offs`, `mix-a` |
| Kits and Cages | assembly | seconds | small parts that need to be packed into a cage; sometimes to group together and print in multiples for easy distribution; other times to simplify extraction and prevent small parts from being lost | different arrangement of parts to make the kits easier to nest into a bucket | tba |
| Sprues and Racks for Post-Processing | assembly | minutes | small parts that need to be assembled into a rack; simplifies extraction, bead blasting, and vapor polishing; racks should be designed to fit in all three machines | number of parts per rack; optimized for one process or another | tba |
| Part injection | amending a bucket | seconds to minutes | add additional parts to an existing bucket with or without (user specified) moving the existing parts | none | `bureau-day` with locked parts |

The table is the owner's (2026-11-20). The sections below expand it; where
they go beyond the table they say so.

## Full Production

* **Who:** a client producing one or more parts at scale over a long
  period, in set ratios (for example 150 of each part of a product).
* **Goal:** the best packing efficiency the print-quality requirements
  allow. A marginal gain matters, because it multiplies over every bucket
  printed.
* **Time:** no limit. (The longest Effort today, Overnight, stops at 12 h.)
* **Alternatives offered:** the ratio met across several buckets when one
  bucket can't hold it (#46): two buckets of 6, 6, 7, 7, 7 and 7, 7, 6, 6, 6
  beat 4, 4, 4, 4, 4; and compromises on print quality that buy density,
  for the user to accept.
* **Benchmark:** challenge buckets packed by an expert by hand. Existing
  packers can't reach those yet; the owner's 7 sets on the challenge parts
  is the bar (#47). Netfabb and Magics are run on the same parts with the
  same cascade (7, 6, 5 ... until every copy fits).
* **Features that matter:** set mode (#47), the clearance tiers with
  compromises reported, Jiggle, alternatives (#45), long runs.

## Service Bureau

* **Who:** a bureau with an assortment of unrelated parts and a printer
  that starts at a fixed time.
* **Goal:** as few buckets as possible, parts sorted by priority, packing
  finished before the deadline.
* **Time:** minutes to hours.
* **Alternatives offered:** compromises on print quality that avoid
  leaving a few parts over.
* **Benchmark:** `bureau-day` (synthetic, `tools/bench_suite.py`).
* **Features that matter:** priorities (tiers), a time budget as a clock
  time, the list of left-out parts, and packing into more than one bucket
  (not built: the packer fills one bucket at a time).

## Quick Job

* **Who:** anyone with a few parts that should fit in one bucket, partly or
  wholly filling it.
* **Goal:** speed over efficiency.
* **Time:** seconds.
* **Alternatives offered:** a denser layout in exchange for more packing
  time (Keep Trying).
* **Benchmark:** `one-offs` and `mix-a` with a 15 s budget.
* **Features that matter:** the Quick effort, the leftovers list, the
  density evaluation.

## Kits and Cages

* **Who:** someone printing small parts that belong in a cage: grouped and
  printed in multiples for easy distribution, or caged so they are easy to
  extract and don't get lost.
* **Goal:** an assembly: the parts packed into a cage
  (`docs/SPEC_PACK.md` 5.1, 5.2).
* **Time:** seconds.
* **Alternatives offered:** other arrangements of the parts that make the
  kits nest into a bucket more easily.
* **Benchmark:** to be announced.

## Sprues and Racks for Post-Processing

* **Who:** someone whose small parts go through extraction, bead blasting
  and vapor polishing (AMT, DyeMansion; `docs/POSTPROCESS_MACHINES.md`).
* **Goal:** an assembly: the parts on a rack or sprue that fits all three
  machines.
* **Time:** minutes.
* **Alternatives offered:** the number of parts per rack, and a rack
  optimized for one process or another.
* **Benchmark:** to be announced. The machines' rack and hook geometry
  isn't published, so it has to come from measurements of the owner's
  machines.

## Part Injection

* **Who:** anyone who packed or placed parts and has more waiting.
* **Goal:** amend a bucket: add parts to it, with or without moving the
  parts already there, as the user chooses.
* **Time:** seconds to minutes.
* **Alternatives offered:** none.
* **Benchmark:** `bureau-day` with locked parts.
* **Features that matter:** locking, Keep Trying, and adding parts from the
  command line (#34).

## How the presets follow

The packer's Effort and Goal settings map onto these use cases. The
mapping is a proposal, not built yet:

* Full Production: Goal "Whole Sets", no time limit, alternatives on.
* Service Bureau: Goal "Big Parts First", Effort by deadline.
* Quick Job: Effort "Quick".
* Kits and Cages, Sprues and Racks: their own actions (`docs/SPEC_PACK.md`),
  not Pack From Scratch.
* Part Injection: Keep Trying with the existing parts locked or free.
