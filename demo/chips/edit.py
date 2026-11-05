# SPDX-License-Identifier: GPL-3.0-or-later
"""The chip reel: the edit -> out/chips.mp4. Its clips and captions also go in the packing reel (pack/edit.py).

    demo/chips/make.sh edit
    python3 demo/chips/edit.py --still 12.5 /tmp/x.png
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
from compose import Caption, Edit, Frames, Still, FPS, hero_card, render, still  # noqa: E402
from PIL import Image  # noqa: E402

CACHE = os.path.join(ROOT, "cache", "chips")
FR = os.path.join(CACHE, "frames")
OUT = os.path.join(ROOT, "out", "chips.mp4")
DATE = "November 2026"


def pack_log(name):
    t = open(os.path.join(CACHE, name + ".log")).read()
    return {"h": float(re.search(r"packed height\s+([\d.]+)", t).group(1)), "s": float(re.search(r"pack ([\d.]+) s", t).group(1)),
            "n": int(re.search(r"placed\s+(\d+)", t).group(1))}


def clips():
    def seg(name, **kw):
        return Frames(os.path.join(FR, name), **kw)
    return [seg("a_label", fade_in=0.6), seg("b_lid"), seg("c_hand"), seg("d_back"), seg("e_dump"), seg("f_bad"),
            seg("g_undo"), seg("h_good"), seg("i_unique"), seg("j_ok")]


def captions():
    G, B = pack_log("good"), pack_log("bad")
    return [
        Caption("This is how you'd pack this build by hand", "Half the can laid on its side, the other half next to it", "info", "c_hand", 0.6, "c_hand", None),
        Caption("Now dump them out", f"{G['n']} chips, and every one is a different mesh", "magenta", "e_dump", 1.2, "e_dump", None),
        Caption("This is how most packers handle it", f"Simulated: any orientation, dropped in. {B['h']:.0f} mm, nearly the whole bucket", "bad", "f_bad", 0.5, "f_bad", None),
        Caption("This algorithm recognizes similar shapes and how they nest", f"Each pair at its own exact gap: {G['h']:.0f} mm high, packed in {G['s']:.0f} s", "good", "h_good", 0.5, "h_good", None),
        Caption("Every one of these chips is a unique mesh", "Not one part repeated 100 times, the easy case for nesting", "amber", "i_unique", 0.3, "i_unique", None),
        Caption("The same result as packing it by hand", f"{G['h']:.0f} mm, two stacks side by side, no collisions", "good", "j_ok", 0.6, "j_ok", None),
    ]


def card():
    im = Image.open(os.path.join(FR, "z_hero", "0000.png")).convert("RGB")
    return hero_card(im, "Bucket Builder", ["Auto packing: 100 different chips,", "nested as if by hand"], "Calvin Young", [f"Made with Claude  ·  {DATE}"])


EDIT = Edit(caption_x=20, clips=[Still(card, 3.0, name="title")] + clips() + [Still(card, 3.5, fade_in=1.0, name="end")], captions=captions(), panels=[])

if __name__ == "__main__":
    if "--still" in sys.argv:
        k = sys.argv.index("--still")
        still(EDIT, int(float(sys.argv[k + 1]) * FPS), sys.argv[k + 2])
    elif "--times" in sys.argv:
        s, n = EDIT.layout()
        for c, t in zip(EDIT.clips, s):
            print(f"{t / FPS:7.1f} s  {c.name}  ({c.n / FPS:.1f} s)")
        print(f"{n / FPS:7.1f} s  total")
    else:
        render(EDIT, OUT, crf=int(os.environ.get("CRF", "21")))
