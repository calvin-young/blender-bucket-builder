# SPDX-License-Identifier: GPL-3.0-or-later
"""Bucket Builder reel 2 (the packer): the edit -> out/pack.mp4.

    demo/pack/make.sh edit
    python3 demo/pack/edit.py --still 12.5 /tmp/x.png

The numbers in the captions come from the capture (cache/pack/facts.json)
and the comparison packs (d0.log, d12.log).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
from compose import Caption, Edit, Frames, Still, CROP, FPS, render, still, title_card  # noqa: E402
from PIL import Image  # noqa: E402

CACHE = os.path.join(ROOT, "cache", "pack")
FR = os.path.join(CACHE, "frames")
OUT = os.path.join(ROOT, "out", "pack.mp4")
DATE = "October 2026"

F = json.load(open(os.path.join(CACHE, "facts.json")))


def pack_log(name):
    t = open(os.path.join(CACHE, name)).read()
    h = re.search(r"packed height\s+([\d.]+)", t).group(1)
    d = re.search(r"3D density\s+([\d.]+)", t).group(1)
    return f"{float(h):.0f}", f"{float(d):.0f}"


H0, D0 = pack_log("d0.log")
H12, D12 = pack_log("d12.log")
C_H, _ = pack_log("chips.log")
C_N = re.search(r"placed\s+(\d+)", open(os.path.join(CACHE, "chips.log")).read()).group(1)


def seg(name, **kw):
    return Frames(os.path.join(FR, name), **kw)


def _hero():
    im = Image.open(os.path.join(FR, "c2_fill", "0090.png")).crop(CROP)
    return im.crop((480, 60, 1380, 960))


def title():
    return title_card("Bucket Builder", ["Automatic packing: a printer's", "build volume filled in seconds"],
                      "Calvin Young", f"Made with Claude  ·  {DATE}", hero=_hero(), accent="amber")


def end_card():
    return title_card("Bucket Builder", ["Open source (GPL-3.0), for Blender 4.2+",
                                         "github.com/calvin-young/blender-bucket-builder"],
                      "Packing: experimental in version 1.5", f"Calvin Young  ·  Made with Claude  ·  {DATE}",
                      hero=_hero(), accent="amber")


EDIT = Edit(
    caption_x=20,
    clips=[
        Still(title, 2.6, name="title"),
        seg("a_pile", hold=3.2, fade_in=0.6),
        seg("b_settings"),
        seg("c_packing", fit=2.2),
        seg("c2_fill"),
        seg("d_result"),
        seg("e_report"),
        seg("f_density"),
        seg("g_end"),
        seg("h_patterns", fade_in=0.5),
        Still(end_card, 3.0, fade_in=0.8, name="end"),
    ],
    captions=[
        Caption(f"{F['total']} parts to print", "Unpacked, in front of an empty HP MJF 5600 build volume",
                "magenta", "a_pile", 0.5),
        Caption("Pack settings", "Rotation, gaps, part quality, goal and a density limit", "info",
                "b_settings", 0.3),
        Caption("Pack", f"The packer works in the background: {float(F['seconds']):.0f} s for this build",
                "neutral", "c_packing", 0.0),
        Caption(f"{F['placed']} of {F['total']} parts placed", "The packer's layout, replayed bottom layer first",
                "amber", "c2_fill", 0.2),
        Caption("Build OK", f"{float(F['height']):.0f} mm high, {F['density']} % dense, no collisions", "good",
                "d_result", 0.3),
        Caption("Even layers", "The Build Report shows the density of every layer", "info", "e_report", 0.6),
        Caption("No density limit", f"As tight as it goes: {H0} mm high, {D0} % dense", "bad",
                "f_density", 1.3, "f_density", 5.6),
        Caption(f"The {D12} % limit (default)", f"{H12} mm high: the heat in each layer stays even", "good",
                "f_density", 6.2),
        Caption("Ready to print", "Packed, checked and reported, all inside Blender", "good", "g_end", 0.4),
        Caption("Curved parts nest", f"{C_N} chips: the packer finds a pattern and stacks them", "amber",
                "h_patterns", 1.0, "h_patterns", 6.5),
        Caption("Build OK", f"{C_N} chips, no collisions, {C_H} mm high", "good", "h_patterns", 7.0),
    ],
)

if __name__ == "__main__":
    if "--still" in sys.argv:
        k = sys.argv.index("--still")
        still(EDIT, int(float(sys.argv[k + 1]) * FPS), sys.argv[k + 2])
    else:
        render(EDIT, OUT, crf=int(os.environ.get("CRF", "20")))
