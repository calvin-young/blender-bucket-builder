# Collaboration log

How this project is made with AI help, and how much of it the owner
steered. Counts and one-line summaries only, never prompt text. Updated at
the end of each working session (`tools/prompt_stats.py` in the owner's
ai-context repo counts prompts from Claude Code transcripts).

## Disclosure

- **Tools and models:** Claude Code with Claude Opus (4.x, then 5.5).
- **How much is AI:** almost all of it. The code, the tests and the
  documents are written by the AI, which also runs the tests (engine,
  Blender, window tests), commits and pushes. The owner reviews little of
  the code itself.
- **What the owner does:** sets the requirements, makes the design
  decisions, brings what he knows from running MJF printers in production,
  tries the releases on his own builds and reports back, and polices the
  interface and the user experience far more than the code. His focus is
  the results, not the process.
- **How AI work is marked:** commits are made by the AI and end with a
  `Co-Authored-By: Claude ...` trailer naming the model, and a link to the
  session. (Not `Assisted-by:`, which open-source projects such as the
  Linux kernel use for patches a human wrote with some help: here the AI
  is the main author.)
- **Accountability:** the maintainer publishes and answers for everything
  in the repository.

## Sessions

Sessions before 2026-10-14 ran in cloud containers that are gone, so their
prompts cannot be counted; their steering is in the list below. A steering
line counts once, in the session it came up in.

| Date | Tool and model | Prompts | Steering lines |
|---|---|---|---|
| before 2026-10-14 | Claude Code, Opus | not counted | 3 |
| 2026-10-14 | Claude Code, Opus 5.5 | 5 | 0 |
| 2026-10-16 - 19 | Claude Code, Opus 5.5 | not counted | 1 |
| 2026-10-20 - 21 | Claude Code, Opus 5.5 | 6 | 17 |
| 2026-10-26 | Claude Code, Opus 5.5 | 1 | 1 |

## Steering

What the owner decided, corrected, taught, tested or changed in scope that
the work would not have done on its own, newest first. `docs/TALLY.md` has
the detail.

- 2026-10-26 [scope]: work moves to a local machine of the owner's (case
  studies on his own buckets next).
- 2026-10-21 [correct]: the meniscus needs a face within about a degree of
  flat (10 degrees was far too generous).
- 2026-10-21 [decide]: one add-on and one branch; the engines to become a
  package of their own later, Blender-only needs flagged.
- 2026-10-21 [scope]: published packing practices researched (packing only,
  read as old and conservative); a running list of differentiators;
  screenshots for review.
- 2026-10-21 [decide]: disclosure to say plainly that the work is almost all
  AI with little code review; the owner polices UI and UX.
- 2026-10-21 [domain]: faces facing up get the capillary / meniscus defect;
  faces facing down do not matter.
- 2026-10-21 [domain]: height matters near the top of the volume, hardly
  below it (157 vs 164 mm nobody cares; 377 vs 384 is a deal breaker).
- 2026-10-21 [decide]: part quality against packing density as a dial;
  sometimes offer choices (one bucket against two).
- 2026-10-21 [scope]: print-quality knowledge in a document of its own, for
  other projects and a repository of its own later.
- 2026-10-21 [decide]: the collaboration log may be public without prompt
  text; word counts say nothing.
- 2026-10-21: large flat faces close together or parallel to the walls are a
  recipe for defects ("elephant skin"); the "huge and tiny" screenshot showed it.
- 2026-10-21: two customer profiles (a service bureau that wants it quick, a
  maker that prints one bucket for weeks and wants the best): asked for a
  fast / thorough choice; packer rules to depend on the printer and, later,
  the print technology.
- 2026-10-21: interlocking rings are a different problem from an O on the
  shaft of an I (topological filter); "Build OK" may show an asterisk while
  interlocks are checked.
- 2026-10-20: interlocking: low priority, on by default, background only,
  never slowing anything, pairs remembered.
- 2026-10-20: asked whether slightly different parts are recognized as alike
  (they were not: families); asked for buckets with much more varied parts.
- 2026-10-20: asked which shapes pack better with every other copy turned
  over, and about L shapes that nest with an offset.
- 2026-10-20: corrected the density: over the height the parts fill, bottom
  to top; raising the whole build changes nothing.
- 2026-10-20: big parts first by default: leftovers should be small parts,
  and a big part that fails should be reprinted sooner.
- 2026-10-19: one crowded layer with one part on top is bad: spread parts
  across the height, with a less prominent switch to keep them low.
- 2026-10-13: the packer froze Blender on his machine (now a separate
  process); his keycap bucket showed copies treated as different parts and
  the lack of foresight (probes); his speed table showed a platform
  difference (pringles 66 against 109 mm).
- 2026-10-09: his lists after trying 1.1: two switches off in a new scene,
  ignored problems, the problem list, local view, isolate.
- Before 2026-10-09: his list after the first trial: no collision distance, hidden
  parts checked, isolate as a mode, only the exceeded walls colored.
