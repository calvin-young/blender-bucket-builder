# Tally: asked for, assumed, not asked, later

Kept so that what the owner asked for is never confused with what was
guessed. Each line says where it stands; `CLAUDE.md` and
`docs/AUTOPACK.md` have the detail. Started 2026-10-20 from the notes of
the sessions before; earlier items are summaries.

## Asked for or agreed with the owner

* A live build check in Blender replacing the commercial packer's: collisions,
  clearances, the build volume; results that follow the mouse; production
  quality; the 5600, the 1200 and a 580 with a custom size. (1.0 - 1.2)
* His list after the first trial (1.1.0): no collision distance; hidden
  parts checked unless Ignore Hidden Parts; isolate as a mode; only the
  walls exceeded change color. His lists of 2026-10-09 (1.2.0): two
  switches, both off in a new scene; ignored problems; the problem list.
* Work pushed often and notes kept, because a session can stop at any time.
* The auto packer, in stages, on the `autopack` branch (2026-10-12).
* Packing must not freeze Blender (1.3.91: it runs in a separate process);
  a progress badge with time left; a popup when Measure Packer Speed ends.
* Copies of one mesh grouped however they are turned (his keycap bucket).
* "Jiggle": improve the scene's current layout (Improve Current Layout).
* Even layers: jitter parts across the height rather than 49 in one layer
  and 1 on top; a less prominent switch to keep parts low (Keep Parts Low).
* Density over the height the parts fill, lowest point to highest.
* Big parts first by default; small parts are the better leftovers.
* Priority list (2026-10-20): 3MF export (names and metadata option moved
  to the 3MF add-on), pattern solver, several buckets per file, packing a
  selection around what is there (done), the commercial packer comparison (needs his
  machine).
* Which shapes need every other copy turned 180 degrees, and an algorithm
  (double lattices, 1.4.0); L shapes that nest with an offset (they
  already do).
* Parts that differ a little but are basically the same shape nested
  alike (families, 1.4.0); a shape library with successes, failures,
  recommendations and screenshots; buckets with much more varied shapes
  and sizes (2026-10-20).
* Interlocking (2026-10-20): low priority; on by default, can be turned
  off; only in the background after every other check has passed, never
  slowing anything; a pair found to interlock remembered as able to.
  Design in AUTOPACK §8.9, not built.
* Interlocking, more (2026-10-21): a topological filter (rings that link
  against an O on an I); "Build OK*" with an asterisk while checking.
* Large flat faces close together or parallel to the walls avoided unless
  necessary (elephant skin; 2026-10-21): built as soft rules (1.4.2).
* Fast against thorough (2026-10-21): left to Claude's judgment; built as
  one progressive packer with an Effort choice (Quick / Standard / Best)
  and a time cap (1.4.2).
* Pin (2026-10-21): other print technologies later, and differences between
  printers (580, 5600): rules switched on and off by the printer profile.
* Prompt statistics in each project (2026-10-21): `docs/PROMPT_STATS.md`;
  use the ai-context repo in this thread.
* Part quality against packing density as a dial (2026-10-21): soft rules
  scale with it, from "quality first" to "fit as many as possible, throw the
  other rules out" (built in 1.4.3 as Part Quality). Faces facing up matter
  (capillary / meniscus defect), faces facing down do not (1.4.3, at the
  quality end). Height matters near the top of the volume, little below
  it; try hard when close (1.4.3: height curve, extra time once).
* Choices instead of one answer, e.g. one bucket at the cost of quality
  against two buckets that keep it: "let's noodle on that" (design in
  AUTOPACK §5.2, not built).
* Case studies on his own buckets once a local machine is set up, to learn
  the trade-offs.
* Print-quality knowledge written down in a document of its own, to be
  referenced by other projects and moved to its own repository later
  (`docs/PRINT_QUALITY.md`, 2026-10-21).
* Follow the ai-context conventions (AGENTS.md, DECISIONS.md and the rest)
  on every branch; push to ai-context directly. The collaboration log may
  be public without prompt text; word counts dropped (2026-10-21).
* Commercial print-prep products are never named in the repository or in
  commits (2026-10-21).
* The "3D Printing" application template: "take a crack at it". Not started.
* 3MF export: "the next project". Not started here.

## Assumed by Claude (not confirmed by the owner)

* Packer defaults: gap 5 mm and wall gap 5 mm, density limit 12 %, soft
  layer limit 50 %, 1 mm cells, coarse blocks of 4 mm, 30 degree steps for
  turning about z, 60 s of improvement. The synthetic buckets use 1.5 -
  3 mm gaps.
* Free rotation means the 24 axis-aligned turns of the part's principal
  frame, plus turns in 15 degree steps near the best ones; no arbitrary
  angles.
* The score's preset weights (height against layer spread) are first
  guesses.
* A part needs 20 copies before it is tried as a lattice; lattices come
  first when such parts and families are nine tenths of the copies and
  there are at most three kinds of them; parts larger than every lattice
  part go in before the lattices.
* The random mixes of the library (sizes x0.2 to x4, many copies of small
  kinds and few of large ones) stand for customers with very varied parts.
* Families: sizes within a factor 1.5 along each axis, coarse pictures
  overlapping at least 55 %; a family is spaced as its largest member
  needs.
* Greedy counts raising the build as much as a higher bottom
  (overshoot weight 1).
* Interlocking: a separate badge line while it is checked rather than
  holding back "Build OK"; the packer takes interlocked pairs apart itself
  instead of asking.
* Up to 8 helper processes for packing.
* The dial's weights (facing pairs 0.002 q, wall faces 0.0008 q, faces up
  0.0016 (q - 0.5), height far below the top 1 - 0.8 q, three times near
  it, over the last 10 % of the height);
  "close to the top" is within 3 % of the height; the extra time is the
  time set, once. All first guesses for the case studies to correct.
* Effort: Quick is 10 s of improving; Best gives up after 10 minutes without
  a better layout and stops at 2 hours at most; Best also restarts from
  scratch now and then.
* Flat-face rules: faces from 2500 mm^2 (50 x 50 mm; 1500 made a bucket of
  40 mm blocks 10 mm higher), pairs closer than 10 mm, wall faces
  within 10 degrees of x or y; their weights against height (a 50 x 50 mm
  facing pair at contact is worth 2.5 mm, a 150 x 150 mm wall face 9 mm);
  a 45 degree turn about z offered to parts with wall faces.

## Not asked yet

* The gaps and density limits he uses per material and printer.
* His commercial packer results on the same buckets (the comparison waits for his
  machine).
* Whether customers' parts need arbitrary angles of rotation.
* Whether a family should be split when its members differ a lot (spacing
  by the largest member costs height).
* How the Goal choices Lowest Build and Even Layers should relate to the
  Part Quality dial (they overlap; fold them into it?).
* Whether the extra time near the top should be more than once, or be a
  setting.
* Which commit trailer to use for AI help: Claude Code's `Co-Authored-By`
  or the kernel's `Assisted-by` (see ai-context, TEAM_BEST_PRACTICES 8).
* How nested parts that are too dense in one layer should be handled
  (his idea: re-nest them farther apart; he asked to review first).
* GPU for the packer: waits for his timing table.

## Later

* Nesting open containers inside larger ones ("Russian dolls"; the
  one-offs bucket shows it).
* The first layout of a varied build in the helper processes too (it runs
  in one process now: 40 - 100 s here on 2 cores).
* Exact refinement of lattice steps (gently tapered cups that nest, boxy
  parts at the array's pitch).
* Partner grids for double lattices by flipping (twice as cheap).
* The interlocking check (§8.9).
* A line in the Pack panel saying what was recognized (families,
  patterns, pairs).
* Several buckets per file (Stage 5), what-if, labels (Stages 6 - 7).
* The performance list and the review items in `CLAUDE.md` (B, C).
