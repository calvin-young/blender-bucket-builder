# SPDX-License-Identifier: GPL-3.0-or-later
"""Bucket Builder reel 2 (the packer): the edit -> out/pack.mp4.

    demo/pack/make.sh edit
    python3 demo/pack/edit.py --still 12.5 /tmp/x.png

The numbers in the captions come from the capture (cache/pack/facts.json)
and the packs' logs (make.sh pack).
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
from compose import Caption, Edit, Frames, Panel, Still, FPS, H, W, font, gradient, hero_card, render, still  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

_spec = importlib.util.spec_from_file_location("chipreel", os.path.join(ROOT, "chips", "edit.py"))
chipreel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chipreel)

CACHE = os.path.join(ROOT, "cache", "pack")
FR = os.path.join(CACHE, "frames")
OUT = os.path.join(ROOT, "out", "pack.mp4")
ADDON = os.path.join(CACHE, "addon", "v1.5.1", "bucket_builder")
DATE = "November 2026"

F = json.load(open(os.path.join(CACHE, "facts.json")))


def pack_log(name):
    t = open(os.path.join(CACHE, name + ".log")).read()
    return {"h": float(re.search(r"packed height\s+([\d.]+)", t).group(1)),
            "d": float(re.search(r"3D density\s+([\d.]+)", t).group(1)),
            "peak": float(re.search(r"peak ([\d.]+) %", t).group(1)),
            "s": float(re.search(r"pack ([\d.]+) s", t).group(1)),
            "n": int(re.search(r"placed\s+(\d+)", t).group(1))}


D0, D12, KEEP, WED = (pack_log(n) for n in ("d0", "d12", "keep", "wedges"))


def seconds(x):
    return f"packed in {x:.0f} s"


def seg(name, **kw):
    return Frames(os.path.join(FR, name), **kw)


def card():
    """The first and the last frame of the video (PowerPoint shows one of them as its poster)."""
    im = Image.open(os.path.join(CACHE, "hero.png")).convert("RGB")     # a full bucket, zoomed out (make.sh hero)
    return hero_card(im, "Bucket Builder",
                     ["Automatic packing: nesting,", "even layers and full buckets"],
                     "Calvin Young", [f"Made with Claude  ·  {DATE}"])


# ------------------------------------------------------------------ data shown as pictures

INK, MUTED, BAR = (235, 237, 241), (165, 170, 178), (242, 165, 49)


def profile(name):
    """Density of each 1 mm layer, from the packer's own report."""
    path = os.path.join(CACHE, name + ".profile")
    if not os.path.exists(path):
        t = subprocess.run([sys.executable, "-m", "pack", "report", os.path.join(CACHE, name + ".3mf"), "--volume", "380",
                            "284", "380", "--profile"], cwd=ADDON, capture_output=True, text=True).stdout
        rows = re.findall(r"^\s+([\d.]+)\s+([\d.]+) %", t, re.M)
        with open(path, "w") as f:
            json.dump([[float(a), float(b)] for a, b in rows], f)
    return json.load(open(path))


def _layers(d, x, y, w, h, rows, title, note, top=66.0, scale=50.0):
    d.text((x, y), title, font=font("bold", 25), fill=INK)
    y0 = y + 40
    d.line((x, y0, x, y0 + h), fill=(90, 94, 102), width=1)         # the baseline: 0 %
    u = h / top
    for z, v in rows:
        if z >= top:
            continue
        yy = y0 + h - (z + 1) * u
        d.rounded_rectangle((x, yy + 0.5, x + max(1.0, w * v / scale), yy + u - 0.5), 1, fill=BAR)
    d.text((x, y0 + h + 8), note, font=font("regular", 22), fill=MUTED)


def layer_panel(both):
    """Small multiples on one scale: the same parts, kept low and staggered."""
    w, h = 410, 540
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 16, fill=(17, 18, 21, 232))
    d.text((22, 16), "Density of each 1 mm layer", font=font("bold", 26), fill=INK)
    d.text((22, 50), "floor at the bottom, same scale", font=font("regular", 20), fill=MUTED)
    ch = 340
    _layers(d, 22, 92, 170, ch, profile("keep"), "Kept low", f"peak {KEEP['peak']:.0f} %")
    if both:
        _layers(d, 222, 92, 170, ch, profile("d12"), "Staggered", f"peak {D12['peak']:.0f} %")
    return im


def panel_keep():
    return layer_panel(False)


def panel_both():
    return layer_panel(True)


def fit_map():
    """Every place one gear could go on the floor among parts already placed: a real correlation
    of their voxel grids by FFT, as the packer does."""
    path = os.path.join(CACHE, "fit_map.npz")
    if not os.path.exists(path):
        code = """
import sys, numpy as np
from pack import meshio, voxel
h, gap = 4.0, 3.0
items = meshio.read(sys.argv[1]).items
n = (95, 71, 30)
taken = np.zeros(n, bool)
placed = [i for i in items if np.asarray(i.matrix)[2, 3] < 30][::2]
for it in placed:
    M = np.asarray(it.matrix)
    pg = voxel.voxelize(it.mesh.verts, it.mesh.tris, M[:3, :3], h, gap)
    o = np.floor((np.asarray(pg.origin) + M[:3, 3]) / h).astype(int)
    idx = np.argwhere(pg.solid) + o
    idx = idx[(idx >= 0).all(1) & (idx < n).all(1)]
    taken[tuple(idx.T)] = True
gear = next(i for i in items if len(i.mesh.verts) > 2000)
pg = voxel.voxelize(gear.mesh.verts, gear.mesh.tris, np.eye(3), h, gap)
k = pg.kernel[::-1, ::-1, ::-1]
s = [a + b for a, b in zip(n, k.shape)]
corr = np.fft.irfftn(np.fft.rfftn(taken.astype(float), s) * np.fft.rfftn(k.astype(float), s), s)
kx, ky, kz = k.shape
fits = corr[kx - 1:kx - 1 + n[0] - kx, ky - 1:ky - 1 + n[1] - ky, kz - 1] < 0.5
np.savez(sys.argv[2], fits=fits, taken=taken.any(2), kx=kx, ky=ky)
"""
        subprocess.run([sys.executable, "-c", code, os.path.join(CACHE, "d12.3mf"), path], cwd=ADDON, check=True)
    return np.load(path)


def fit_card():
    m = fit_map()
    fits, taken = m["fits"], m["taken"]
    kx, ky = int(m["kx"]), int(m["ky"])
    u = 11
    bw, bh = taken.shape[0] * u, taken.shape[1] * u
    x0, y0 = (W - bw) // 2, 70
    im = gradient().copy()
    d = ImageDraw.Draw(im)
    d.rectangle((x0, y0, x0 + bw, y0 + bh), fill=(44, 47, 54))
    for i, j in np.argwhere(fits):                                  # where the gear's middle could sit
        cx, cy = i + kx // 2, j + ky // 2
        d.rectangle((x0 + cx * u, y0 + bh - (cy + 1) * u, x0 + (cx + 1) * u - 1, y0 + bh - cy * u - 1), fill=(86, 160, 96))
    for i, j in np.argwhere(taken):
        d.rectangle((x0 + i * u, y0 + bh - (j + 1) * u, x0 + (i + 1) * u - 1, y0 + bh - j * u - 1), fill=(205, 208, 214))
    d.rectangle((x0, y0, x0 + bw, y0 + bh), outline=(120, 170, 230), width=2)
    return im


# ------------------------------------------------------------------ the timeline

EDIT = Edit(
    caption_x=20,
    clips=[
        Still(card, 3.0, name="title"),
        seg("a_pile", hold=3.2, fade_in=0.6),
        seg("b_settings"),
        seg("c_packing"),
        seg("c2_fill"),
        seg("d_result"),
        seg("d2_voxels", fade_in=0.4),
        Still(fit_card, 4.0, fade_in=0.4, name="fit"),
        seg("d3_exact", fade_in=0.4),
        seg("e_report"),
        seg("f_density"),
        seg("f2_even"),
        *chipreel.clips(),
        seg("i_wedges", fade_in=0.4),
        Still(card, 3.5, fade_in=1.0, name="end"),
    ],
    captions=[
        Caption(f"{F['total']} parts to print", "Unpacked, in front of an empty HP MJF 5600 build volume",
                "magenta", "a_pile", 0.5),
        Caption("Limits and goals", "Gaps and density never move; big parts go first, so leftovers are easy to fit later",
                "info", "b_settings", 0.3),
        Caption("Pack", "It works in the background: 8 processes on a workstation, so times vary by machine",
                "neutral", "c_packing", 0.0),
        Caption(f"{F['placed']} of {F['total']} parts placed", f"{seconds(float(F['seconds']))}; the layout replayed bottom layer first",
                "amber", "c2_fill", 0.2),
        Caption("Build OK", f"{float(F['height']):.0f} mm high, {F['density']} % dense, no collisions: {seconds(float(F['seconds']))}",
                "good", "d_result", 0.3),
        Caption("How it searches: voxels", "Every part becomes a grid (shown at 2 mm); open cavities stay empty for others to nest in",
                "info", "d2_voxels", 0.2, "d2_voxels", 4.3),
        Caption("Grown by the gap", "If two grown parts share no cell, they really are at least 3 mm apart", "info",
                "d2_voxels", 4.5),
        Caption("One FFT tests every position at once", "Green: where one more gear could go on this floor, among the parts in white",
                "info", "fit", 0.3),
        Caption("Then the real meshes", "A BVH collision engine pushes parts to the exact gap and checks every pair", "good",
                "d3_exact", 0.3),
        Caption("Density as the printer sees it", "Measured over the height the parts fill, so raising a build can't cheat it",
                "info", "e_report", 0.6),
        Caption("No density limit", f"As tight as it goes: {D0['h']:.0f} mm high, {D0['d']:.0f} % dense, {seconds(D0['s'])}", "bad",
                "f_density", 1.3, "f_density", 5.6),
        Caption(f"The {D12['d']:.0f} % limit (default)", f"{D12['h']:.0f} mm high: the heat in each layer stays even, {seconds(D12['s'])}",
                "good", "f_density", 6.2),
        Caption("Without even layers", f"Parts kept low: one layer is {KEEP['peak']:.0f} % dense, {seconds(KEEP['s'])}", "bad",
                "f2_even", 0.6, "f2_even", 5.6),
        Caption("Even layers", f"Same parts, height and overall density; the busiest layer is {D12['peak']:.0f} %, {seconds(D12['s'])}",
                "good", "f2_even", 5.8, "f2_even", 8.4),
        Caption("Not something we've seen in other packers", "Heat is believed to concentrate in a crowded layer",
                "good", "f2_even", 8.6),
        *chipreel.captions(),
        Caption("Half of them turned over", f"{WED['n']} wedges nested in pairs, one color each way: {WED['h']:.0f} mm, {seconds(WED['s'])}", "amber",
                "i_wedges", 0.3),
    ],
    panels=[
        Panel(panel_keep, "f2_even", 0.6, "f2_even", 5.6, x=20, y=120),
        Panel(panel_both, "f2_even", 5.6, None, None, x=20, y=120),
    ],
)

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
