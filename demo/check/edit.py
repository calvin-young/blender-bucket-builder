# SPDX-License-Identifier: GPL-3.0-or-later
"""Bucket Builder reel 1 (the build check): the edit -> out/check.mp4.

    demo/check/make.sh edit
    python3 demo/check/edit.py --still 12.5 /tmp/x.png
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
from compose import Caption, Edit, Frames, Still, CROP, FPS, render, still, title_card  # noqa: E402
from PIL import Image  # noqa: E402

FR = os.path.join(ROOT, "cache", "check", "frames")
OUT = os.path.join(ROOT, "out", "check.mp4")
DATE = "October 2026"


def seg(name, **kw):
    return Frames(os.path.join(FR, name), **kw)


def _hero():
    im = Image.open(os.path.join(FR, "e_collision", "0194.png")).crop(CROP)
    return im.crop((330, 170, 1230, 1070))


def title():
    return title_card("Bucket Builder", ["The live build check: collisions,", "clearance and the build volume"],
                      "Calvin Young", f"Made with Claude  ·  {DATE}", hero=_hero(), accent="info")


def end_card():
    return title_card("Bucket Builder", ["Open source (GPL-3.0), for Blender 4.2+",
                                         "github.com/calvin-young/blender-bucket-builder"],
                      "Version 1.5, in production use", f"Calvin Young  ·  Made with Claude  ·  {DATE}",
                      hero=_hero(), accent="info")


EDIT = Edit(
    caption_x=20,
    clips=[
        Still(title, 2.6, name="title"),
        seg("a_intro", hold=3.0, fade_in=0.6),
        seg("b_volume"),
        seg("c_outside"),
        seg("d_collisions_on"),
        seg("e_collision"),
        seg("f_clearance"),
        seg("g_wallgap"),
        seg("h_problems"),
        seg("i_ignore"),
        seg("j_fix"),
        seg("k_report", hold=1.0),
        Still(end_card, 3.0, fade_in=0.8, name="end"),
    ],
    captions=[
        Caption("A build in Blender", "24 parts for one printer bucket, arranged by hand", "neutral", "a_intro", 0.5),
        Caption("Build volume check", "The printer's volume (HP MJF 5600), live in the viewport", "info",
                "b_volume", 0.6),
        Caption("Outside the volume", "The part, and the wall it goes through, turn magenta", "magenta",
                "c_outside", 1.2),
        Caption("Collision check on", "Build OK: no collisions, everything inside", "good", "d_collisions_on", 1.0),
        Caption("Collisions", "Red hatching and the exact intersection curve, live while you drag", "bad",
                "e_collision", 1.5),
        Caption("Clearance", "Closer than 3 mm: amber, with the gap measured", "amber", "f_clearance", 1.4),
        Caption("Wall gap", "Too close to a side wall of the build volume", "amber", "g_wallgap", 1.2),
        Caption("Every problem listed, worst first", "Next frames it; the magnifier shows its parts on their own",
                "info", "h_problems", 0.6),
        Caption("Ignore what's meant to be", "The verdict counts what's left: 1 problem ignored", "neutral",
                "i_ignore", 0.8),
        Caption("Fixed: Build OK", "The checks only draw; nothing in the scene is changed", "good", "j_fix", 1.6),
        Caption("Build Report", "Density, packed height and the density of every layer", "info", "k_report", 0.8),
    ],
)

if __name__ == "__main__":
    if "--still" in sys.argv:
        k = sys.argv.index("--still")
        still(EDIT, int(float(sys.argv[k + 1]) * FPS), sys.argv[k + 2])
    else:
        render(EDIT, OUT, crf=int(os.environ.get("CRF", "20")))
