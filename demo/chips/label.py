# SPDX-License-Identifier: GPL-3.0-or-later
"""The can's label: a made-up brand. The pictures come from an image model (cache/chips/art, not in the
repo); the type is set here. Two faces around the can.

    python3 demo/chips/label.py <cache dir>
"""
import os
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

BLACK = "/usr/share/fonts/truetype/lato/Lato-BlackItalic.ttf"
BOLD = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"
W, H = 3040, 4000
NAVY, GOLD, CREAM, ORANGE = (12, 26, 66), (246, 190, 70), (252, 244, 226), (238, 112, 30)


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


def face(art):
    """One panel of the label, half the way around the can. What matters stays in the middle: only about
    two thirds of a panel can be seen at once."""
    w, h = W // 2, H
    # warm, so the can stands out from a dark studio, with the model's swirl showing through
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        grad.putpixel((0, y), (int(205 + 40 * t), int(38 + 95 * t), int(28 + 8 * t)))
    grad = grad.resize((w, h))
    swirl = Image.open(os.path.join(art, "bg_1.png")).convert("L").resize((h, h), Image.LANCZOS).crop(((h - w) // 2, 0, (h + w) // 2, h))
    swirl = ImageEnhance.Contrast(swirl).enhance(1.2).point(lambda v: 150 + v * 0.45)
    im = Image.composite(grad, Image.new("RGB", (w, h), (120, 20, 20)), swirl)
    d = ImageDraw.Draw(im)
    wave = Image.open(os.path.join(art, "wave_2.png")).convert("RGB").resize((w, w), Image.LANCZOS).crop((0, 0, w, 760))
    im.paste(wave, (0, h - 760))
    d.rectangle((0, h - 780, w, h - 760), fill=GOLD)
    centered(d, w / 2, 420, "HYPAR", font(BLACK, 300), CREAM, shadow=(110, 16, 16), by=12)
    centered(d, w / 2, 760, "HYPERBOLIC CRISPS", font(BOLD, 62), (255, 226, 130), spacing=7)
    pic = Image.open(os.path.join(art, "chips_1.png")).convert("RGB").resize((900, 900), Image.LANCZOS)
    pic = ImageEnhance.Color(pic).enhance(1.15)
    mask = Image.new("L", pic.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 899, 899), fill=255)
    cx, cy = w // 2, 1520
    d.ellipse((cx - 476, cy - 476, cx + 476, cy + 476), fill=GOLD)
    im.paste(pic, (cx - 450, cy - 450), mask)
    d.rounded_rectangle((w / 2 - 420, 2120, w / 2 + 420, 2330), 50, fill=NAVY)
    centered(d, w / 2, 2148, "SEA SALT", font(BLACK, 130), CREAM)
    d.rounded_rectangle((w / 2 - 430, h - 610, w / 2 + 430, h - 170), 50, fill=NAVY)
    centered(d, w / 2, h - 570, "100 crisps", font(BLACK, 130), CREAM)
    centered(d, w / 2, h - 390, "no two alike", font(BOLD, 112), GOLD)
    return im


def main(cache):
    art = os.path.join(cache, "art")
    f = face(art)
    im = Image.new("RGB", (W, H))
    # (u runs around the can; the panel the camera sees first is centered at 3/4)
    im.paste(f, (W // 2, 0))
    im.paste(f, (0, 0))
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    im.save(os.path.join(cache, "label.png"))


if __name__ == "__main__":
    main(sys.argv[1])
