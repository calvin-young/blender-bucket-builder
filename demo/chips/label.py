# SPDX-License-Identifier: GPL-3.0-or-later
"""The can's label: a made-up brand, as plain as a real can of chips. Two faces around the can.

    python3 demo/chips/label.py <cache dir>
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BLACK = "/usr/share/fonts/truetype/lato/Lato-BlackItalic.ttf"
BOLD = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"
W, H = 3040, 4000
RED, GOLD, CREAM = (196, 28, 32), (250, 200, 80), (252, 246, 232)


def font(path, size):
    return ImageFont.truetype(path, size)


def centered(d, x, y, text, f, fill, shadow=None, by=10, spacing=0):
    if spacing:
        w = sum(d.textlength(c, font=f) + spacing for c in text) - spacing
        px = x - w / 2
        for c in text:
            if shadow:
                d.text((px + by, y + by), c, font=f, fill=shadow)
            d.text((px, y), c, font=f, fill=fill)
            px += d.textlength(c, font=f) + spacing
        return
    w = d.textlength(text, font=f)
    if shadow:
        d.text((x - w / 2 + by, y + by), text, font=f, fill=shadow)
    d.text((x - w / 2, y), text, font=f, fill=fill)


def face():
    """One panel, half the way around the can. What matters stays in the middle: only about two thirds
    of a panel can be seen at once."""
    w, h = W // 2, H
    im = Image.new("RGB", (w, h), RED)
    d = ImageDraw.Draw(im)
    centered(d, w / 2, 1230, "HYPAR", font(BLACK, 235), CREAM)
    centered(d, w / 2, 1520, "HYPERBOLIC CRISPS", font(BOLD, 50), GOLD, spacing=6)
    centered(d, w / 2, 1980, "No two are alike!", font(BLACK, 84), CREAM)
    return im


def main(cache):
    f = face()
    im = Image.new("RGB", (W, H))
    # (u runs around the can; the panel the camera sees first is centered at 3/4)
    im.paste(f, (W // 2, 0))
    im.paste(f, (0, 0))
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    im.save(os.path.join(cache, "label.png"))


if __name__ == "__main__":
    main(sys.argv[1])
