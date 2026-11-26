# Changelog

Versions are tagged `vX.Y.Z` on GitHub, each with a pre-release and its zip. Notes on versions
before 1.6.4 are in `AGENTS.md` ("Working notes: state") and the GitHub releases.

## 1.6.4 (2026-11-26)

* **Packing:** a quick first pass turns each part to its smallest box and packs the boxes, so easy
  builds (a few identical parts) are solved in seconds; it keeps the density limit and puts flat
  faces down when Part Quality counts them. Pack From Scratch first moves every part beside the
  bucket. "Improve Packing" when every part is in, "Keep Trying (N waiting)" when some are not; the
  status says Esc keeps the layout on screen.
* **Units:** parts under 0.5 mm or over 1 m across are named with a Scale x1000 / Scale /1000 button
  (`bucketbuilder.fix_scale`, shared with the 3D Printing template). Guessing the unit from the parts
  is back as Settings > Units > Dynamic Bucket Sizing, off by default.
* **Build volume:** New and Edit Printer open a dialog with the dimensions (the Advanced section is
  gone); Snap to Build Volume also under Check Build Volume, snaps to the wall gap when it is on,
  settles turned parts on the wall (#52), and goes away with the monitor; Nudge All Inside / Nudge
  Selected; the wall-gap shading stays beside the out-of-volume hatching.
* **Density:** Evaluate Density (was Density Report); the figure has a header, an expanded view,
  "Build Plate" under it and a legend one line each; print-quality notes in the Problems list and
  the viewport (#51).
* **Manual Packing:** Align to Plane: click a face to lay it on the build plate (#50).
* **Panels:** Options renamed Settings, Part Colors in Display, Units above Performance; the last
  pack or fix result stays under the verdict.
