# What sets Bucket Builder apart

A running list of what this add-on does differently from the usual
print-preparation and packing tools, for presenting it to colleagues.
High level on purpose; the details are in the user guide
(`bucket_builder/README.md`), `docs/AUTOPACK.md` and
`docs/PRINT_QUALITY.md`. Kept up to date as features land.
"Usual tools" means common practice as the owner knows it; no product is
named here. A side-by-side comparison may follow later.

## Checking a build

* **Live while you drag.** Collisions, clearances and geometry outside the
  build volume are shown while a part is being moved, turned or scaled,
  not after a separate "check" step. Moving a part costs almost nothing,
  however many triangles it has, because what was worked out for its shape
  is reused and only its position changes.
* **Exact, and honest about it.** Clearances are measured on the meshes
  themselves, never on simplified copies; a result that is not proven yet
  says so ("Checking", "~") instead of looking final. Near-coincident
  copies (a part duplicated in place) are caught as collisions.
* **Builds of production size.** Tens of millions of triangles, hundreds
  of parts, instanced copies, inside Blender, on a laptop.
* **What it cannot check, it says.** Instances, non-mesh objects and
  hidden parts are reported rather than silently skipped.
* **In Blender.** The full modeling toolset next to the check: fix a part
  and see the build verdict change.

## Packing

* **Height counts where it matters.** A few millimeters of height hardly
  count when a build is far below the top of the bucket, and count three
  times when it is close to it, where they decide whether a job fits at
  all; there the packer also takes extra time once. (157 against 164 mm:
  nobody cares; 377 against 384 mm in a 380 mm bucket: a deal breaker.)
* **One dial between part quality and packing density.** Hard limits
  (gaps, density) never move; soft rules scale with one Part Quality
  setting, from "fit as many as possible" to "quality first".
* **Print-quality rules from production experience**, written down with
  IDs, sources and how firm their numbers are (`docs/PRINT_QUALITY.md`):
  large flat faces kept apart (elephant skin), off the side walls, and not
  facing up (the capillary / meniscus defect, within a degree of flat);
  faces facing down left alone.
* **Density as a printer sees it**: over the height the parts actually
  fill, lowest point to highest, so raising a whole build off the floor
  does not cheat the limit.
* **Even layers**: parts are staggered across the height so that no layer
  is crowded while the rest are nearly empty; a switch keeps them low for
  builds that are filled up by hand.
* **Big parts first**: when not everything fits, what is left over is
  small parts, which fit into the next build more easily (and a big part
  that fails is reprinted sooner).
* **Patterns found automatically**: copies of a part are tried as regular
  lattices, including every other copy turned over (wedges, tees,
  triangles nest far closer that way), and parts that are different meshes
  but basically the same shape are recognized as a family and nested alike.
* **Always a valid answer, getting better.** The packer keeps the best
  layout found so far and the parts move as it improves; it can be stopped
  at any time. Every layout is checked by the exact collision engine, and
  a final pass moves parts to the exact gap on the meshes.
* **Effort to match the job**: quick for a day's mixed work, or "keep
  going until nothing better turns up" for a bucket that will be printed
  for weeks.
* **Improve a layout you already have**, including one arranged by hand,
  instead of starting over.
* **Does not freeze the program**: packing runs in a process of its own;
  you keep working while it runs.

## Data

* **Safe with sensitive parts.** No network access; the packer's temporary
  files hold numbered parts and no names, and are removed at the end;
  reports and copied diagnostics hold counts and timings only.

## How it is made

* Developed almost entirely by an AI coding assistant, directed and tested
  by its maintainer on real production builds (`docs/PROMPT_STATS.md`).
  Large test suites check the engine against brute force, and window tests
  drive a real Blender.
