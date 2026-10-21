# Collaboration log

How much steering went into this project, kept so the owner's share of the
work stays visible. Counts only: no prompt text. Updated at the end of each
working session with `tools/prompt_stats.py` from the owner's ai-context
repo. A long prompt is not better than a short one: read the numbers
together with the steering list.

## Sessions

Counted from the transcripts that still exist. Sessions before 2026-10-14
ran in cloud containers that are gone, so they cannot be counted; their
steering is in the list below.

| Date | Tool | Prompts | Words | Median words | Prompts of 10 words or fewer |
|---|---|---|---|---|---|
| 2026-10-14 | Claude Code | 5 | 418 | 70 | 0 |
| 2026-10-20 - 21 | Claude Code | 4 | 1305 | 186 | 0 |

## Steering

What the owner decided, corrected or checked that the work would not have
done on its own (newest first). `docs/TALLY.md` has the detail.

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
