# Changelog

Versions are tagged `vX.Y.Z` on GitHub, each with a pre-release and its zip. Notes on versions
before 1.6.4 are in `AGENTS.md` ("Working notes: state") and the GitHub releases.

## 1.6.9 (2026-11-26)

* **Mesh check:** shells within one part that touch (along an edge or at a corner) or overlap are named
  too. Blender's non-manifold count calls them clean, but an STL export welds the touching vertices and
  other programs then report open or non-manifold edges; a shell wholly inside another (a hollow part's
  inner wall) is not counted.

## 1.6.8 (2026-11-26)

* **Overlay:** the bucket's outline sits behind the parts (no faint line through them) and always wins
  over the floor grid and axes it used to flicker with (#28).

* **Mesh check:** parts with open edges, non-manifold edges or inside out (a negative scale included)
  are named in the Problems list and the verdict, and warned about at export; Collision Detection >
  Check Part Meshes turns it off (#29).

## 1.6.7 (2026-11-26)

* **Printers:** a name another printer already has is refused with a message; adding, editing, renaming
  and deleting a printer are undo steps for the scene (the library itself lives in the preferences) (#15).
* **Export:** a red box above Export Print File lists what is wrong with the build (parts outside the
  volume, staged ones too, collisions, gaps under the clearance) before you press it (#31).
* **Fix Issues:** when the live check has caught up, the last-result line says what is still wrong
  ("Still to fix, selected: A x B (collision)") and selects those parts, or says nothing is left (#32).

## 1.6.6 (2026-11-26)

* **Panels:** Preferences > Panel Location puts the panels in the 3D Viewport's sidebar (default) or the
  Scene tab of the Properties editor; Sidebar Tab and Packer Tab rename the two tabs (the same name puts
  everything in one tab). The names match Crosscut's (#38).
* **Packing:** the status line says when the packer takes extra time (parts still left out, or the build
  near the top), and the Time setting's tooltip explains it.

## 1.6.5 (2026-11-26)

* **Packing:** a part that may turn freely now starts from its smallest bounding box when its principal
  axes would leave it on a slant (open frames and trays), so such parts pack square and closer.
* **Packing:** the finished layout is checked once more by the exact engine; a part that breaks a rule
  is left beside the bucket with the reason "failed the final check", never handed back wrong.
* **Overlay:** stays drawn over Crosscut's cut faces whichever add-on loads first (#28).

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
