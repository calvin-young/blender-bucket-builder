# SPDX-License-Identifier: GPL-3.0-or-later
"""A contact sheet of the captured segments: python3 sheet.py <frames dir> <out.png> [per segment] [seg ...]"""
import os
import sys
from PIL import Image, ImageDraw
root, out = sys.argv[1], sys.argv[2]
per = int(sys.argv[3]) if len(sys.argv) > 3 else 4
segs = sys.argv[4:] or sorted(os.listdir(root))
W, H = 384, 216
cells = []
for s in segs:
    fs = sorted(os.listdir(os.path.join(root, s)))
    for i in range(per):
        f = fs[min(len(fs) - 1, int((i + 0.5) * len(fs) / per))]
        im = Image.open(os.path.join(root, s, f)).convert("RGB").resize((W, H))
        ImageDraw.Draw(im).text((6, 4), f"{s} {f}", fill=(255, 255, 0))
        cells.append(im)
cols = per
rows = len(cells) // cols
sheet = Image.new("RGB", (cols * W, rows * H))
for i, c in enumerate(cells):
    sheet.paste(c, ((i % cols) * W, (i // cols) * H))
sheet.save(out)
