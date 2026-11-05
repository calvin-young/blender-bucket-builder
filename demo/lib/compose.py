# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit side of a reel (plain Python 3 with Pillow and numpy, plus ffmpeg):
title and end cards, caption cards with fades, cross-fades, H.264 encode.

A reel's edit.py builds a list of clips and captions and calls `render()`.
Frames are composed in worker processes and piped straight into ffmpeg, so no
intermediate frames are written.
"""
import glob
import os
import subprocess
from dataclasses import dataclass, field
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS = 30
W, H = 1920, 1080
FONTS = "/usr/share/fonts/truetype/lato/"
CROP = (0, 30, 1920, 1110)       # Blender window shot (1920x1115) -> client area

ACCENT = {
    "bad": (229, 83, 61),
    "good": (108, 197, 106),
    "amber": (242, 165, 49),
    "info": (120, 170, 230),
    "magenta": (226, 80, 220),
    "neutral": (170, 174, 182),
}


def font(weight, size):
    name = {"bold": "Lato-Bold.ttf", "regular": "Lato-Regular.ttf", "light": "Lato-Light.ttf",
            "black": "Lato-Black.ttf"}[weight]
    return ImageFont.truetype(os.path.join(FONTS, name), size)


# ------------------------------------------------------------------ clips

@dataclass
class Frames:
    """Captured frames of one segment (a folder of PNG window shots).
    hold: seconds to repeat the last frame; speed: playback speed."""
    folder: str
    hold: float = 0.0
    fade_in: float = 0.0             # cross-fade from the previous clip, seconds
    name: str = ""
    fit: float = 0.0                 # play all frames in this many seconds (a time-lapse), 0: as captured

    def __post_init__(self):
        self.files = sorted(glob.glob(os.path.join(self.folder, "*.png")))
        if not self.files:
            raise SystemExit(f"no frames in {self.folder}")
        self.name = self.name or os.path.basename(self.folder.rstrip("/"))

    @property
    def n(self):
        played = int(round(self.fit * FPS)) if self.fit else len(self.files)
        return played + int(round(self.hold * FPS))

    def frame(self, i):
        if self.fit:
            m = int(round(self.fit * FPS))
            i = round(min(i, m - 1) * (len(self.files) - 1) / max(m - 1, 1))
        f = self.files[min(i, len(self.files) - 1)]
        im = Image.open(f).convert("RGB")
        if im.size != (W, H):
            im = im.crop(CROP)
        return im


@dataclass
class Still:
    """A card or any other generated image, shown for `seconds`."""
    make: object                     # callable -> PIL image (picklable: a module-level function)
    seconds: float
    fade_in: float = 0.0
    name: str = "card"

    @property
    def n(self):
        return int(round(self.seconds * FPS))

    def frame(self, i):
        return self.make()


@dataclass
class Caption:
    """A caption card from clip `start` + t0 to clip `end` + t1 (seconds)."""
    title: str
    sub: str = ""
    accent: str = "neutral"
    start: str = ""
    t0: float = 0.0
    end: str = ""
    t1: float = None                 # None: the end clip's end
    fade: float = 0.3
    y: int = 820                     # higher, to clear a tall readout


@dataclass
class Panel:
    """A generated picture (callable -> RGBA, picklable) over the footage, like a caption;
    end=None: to the end of the start clip."""
    make: object
    start: str = ""
    t0: float = 0.0
    end: str = None
    t1: float = None
    x: int = 20
    y: int = 120
    fade: float = 0.3


@dataclass
class Edit:
    clips: list
    captions: list = field(default_factory=list)
    panels: list = field(default_factory=list)
    caption_x: object = "center"     # or a left edge in pixels (to keep clear of a corner badge)

    def layout(self):
        """Start frame of every clip (a cross-fade overlaps it with the previous one)."""
        starts, t = [], 0
        for c in self.clips:
            t -= int(round(c.fade_in * FPS))
            starts.append(max(t, 0))
            t = starts[-1] + c.n
        return starts, t

    def caption_spans(self):
        starts, _ = self.layout()
        idx = {c.name: k for k, c in enumerate(self.clips)}
        spans = []
        for cap in self.captions:
            a = starts[idx[cap.start]] + int(round(cap.t0 * FPS))
            e = idx[cap.end or cap.start]
            b = starts[e] + (self.clips[e].n if cap.t1 is None else int(round(cap.t1 * FPS)))
            spans.append((a, b, cap))
        return spans

    def panel_spans(self):
        starts, _ = self.layout()
        idx = {c.name: k for k, c in enumerate(self.clips)}
        spans = []
        for p in self.panels:
            a = starts[idx[p.start]] + int(round(p.t0 * FPS))
            e = idx[p.end or p.start]
            b = starts[e] + (self.clips[e].n if p.t1 is None else int(round(p.t1 * FPS)))
            spans.append((a, b, p))
        return spans


# ------------------------------------------------------------------ drawing

_card_cache = {}


def caption_image(cap):
    key = (cap.title, cap.sub, cap.accent)
    im = _card_cache.get(key)
    if im is not None:
        return im
    ft, fs = font("bold", 46), font("regular", 29)
    tw = max(ft.getlength(cap.title), fs.getlength(cap.sub) if cap.sub else 0)
    w = int(tw + 110)
    h = 140 if cap.sub else 92
    pad = 30
    im = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad + 6, pad + w, pad + h + 6), 18, fill=(0, 0, 0, 110))
    sh = sh.filter(ImageFilter.GaussianBlur(12))
    im.alpha_composite(sh)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((pad, pad, pad + w, pad + h), 18, fill=(17, 18, 21, 225))
    d.rounded_rectangle((pad, pad, pad + 11, pad + h), 5, fill=ACCENT[cap.accent] + (255,))
    d.text((pad + 44, pad + 22), cap.title, font=ft, fill=(255, 255, 255, 255))
    if cap.sub:
        d.text((pad + 45, pad + 84), cap.sub, font=fs, fill=(205, 208, 214, 255))
    _card_cache[key] = im
    return im


def place_caption(frame, cap, alpha, y=820, x="center"):
    im = caption_image(cap)
    if alpha < 1.0:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
    frame.alpha_composite(im, ((W - im.width) // 2 if x == "center" else int(x), y))


# ------------------------------------------------------------------ cards

def gradient(top=(36, 39, 46), bottom=(17, 18, 21)):
    t = np.linspace(0, 1, H)[:, None, None]
    g = (np.array(top) * (1 - t) + np.array(bottom) * t).astype(np.uint8)
    return Image.fromarray(np.repeat(g, W, axis=1), "RGB")


def rounded(im, r=28):
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, im.width - 1, im.height - 1), r, fill=255)
    out = im.convert("RGBA")
    out.putalpha(m)
    return out


def title_card(name, lines, byline, footer, hero=None, accent="amber"):
    """Name, a description (list of lines), byline and footer, with an optional
    hero image (PIL) on the right."""
    im = gradient().convert("RGBA")
    d = ImageDraw.Draw(im)
    x = 190
    if hero is not None:
        hero = rounded(hero.resize((760, 760), Image.LANCZOS))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((1060, 172, 1820, 932), 28, fill=(0, 0, 0, 140))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
        im.alpha_composite(hero, (1060, 160))
    d.rectangle((x - 50, 300, x - 38, 300 + 70 + 52 * len(lines) + 40), fill=ACCENT[accent])
    size = 132                                   # shrink a long name to clear the hero image
    while hero is not None and size > 72 and font("bold", size).getlength(name) > 1040 - 40 - x:
        size -= 4
    d.text((x - 6, 250 + (132 - size) * 0.6), name, font=font("bold", size), fill="white")
    for k, line in enumerate(lines):
        d.text((x, 420 + 52 * k), line, font=font("light", 40), fill=(213, 216, 222))
    y = 420 + 52 * len(lines) + 90
    d.text((x, y), byline, font=font("regular", 36), fill="white")
    d.text((x, y + 54), footer, font=font("regular", 28), fill=(154, 160, 170))
    return im.convert("RGB")


def hero_card(hero, name, lines, byline, footer, accent="amber", x=110):  # footer: a list of lines
    """A full-bleed shot (1920x1080, chrome already cropped) darkening to the left,
    with the name and description over the dark side."""
    im = hero.convert("RGB").resize((W, H), Image.LANCZOS)
    t = np.clip((np.linspace(0, 1, W) - 0.30) / 0.42, 0, 1)
    t = t * t * (3 - 2 * t)
    a = np.array(im, dtype=np.float32)
    dark = np.array(gradient((30, 32, 38), (17, 18, 21)), dtype=np.float32)
    a = dark * (1 - t[None, :, None]) + a * t[None, :, None]
    im = Image.fromarray(a.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im)
    y0 = 300
    d.rectangle((x - 40, y0 + 10, x - 28, y0 + 150 + 52 * len(lines)), fill=ACCENT[accent])
    d.text((x, y0 - 30), name, font=font("bold", 132), fill="white")
    for k, line in enumerate(lines):
        d.text((x + 4, y0 + 130 + 52 * k), line, font=font("light", 40), fill=(213, 216, 222))
    y = y0 + 130 + 52 * len(lines) + 70
    d.text((x + 4, y), byline, font=font("regular", 36), fill="white")
    for k, line in enumerate(footer):
        d.text((x + 4, y + 54 + 38 * k), line, font=font("regular", 28), fill=(154, 160, 170))
    return im


# ------------------------------------------------------------------ render

_EDIT = None
_panel_cache = {}


def _init(edit):
    global _EDIT
    _EDIT = edit


def _compose(f):
    edit = _EDIT
    starts, _ = edit.layout()
    layers = []
    for c, s in zip(edit.clips, starts):
        if s <= f < s + c.n:
            layers.append((c, f - s, s))
    base = None
    for c, i, s in layers:
        im = c.frame(i)
        if base is None:
            base = im
        else:
            k = int(round(c.fade_in * FPS))
            a = min(1.0, (i + 1) / max(k, 1))
            base = Image.blend(base, im, a * a * (3 - 2 * a))
    frame = base.convert("RGBA")
    for a, b, p in edit.panel_spans():
        if a <= f < b:
            k = max(1, int(round(p.fade * FPS)))
            im = _panel_cache.setdefault(id(p), p.make())
            alpha = min(1.0, (f - a + 1) / k, (b - f) / k)
            if alpha < 1.0:
                im = im.copy()
                im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
            frame.alpha_composite(im, (p.x, p.y))
    for a, b, cap in edit.caption_spans():
        if a <= f < b:
            k = max(1, int(round(cap.fade * FPS)))
            alpha = min(1.0, (f - a + 1) / k, (b - f) / k)
            place_caption(frame, cap, alpha, y=cap.y, x=edit.caption_x)
    return frame.convert("RGB").tobytes()


def render(edit, out, crf=20, preset="slow", workers=None):
    """Compose every frame and encode H.264 (yuv420p, +faststart: plays in
    PowerPoint, browsers and phones)."""
    _, n = edit.layout()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-tune", "animation",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(workers or max(1, os.cpu_count() - 4), initializer=_init, initargs=(edit,)) as pool:
        for k, buf in enumerate(pool.imap(_compose, range(n), chunksize=4)):
            enc.stdin.write(buf)
            if k % 150 == 0:
                print(f"  frame {k}/{n}", flush=True)
    enc.stdin.close()
    enc.wait()
    size = os.path.getsize(out) / 1e6
    print(f"{out}: {n / FPS:.1f} s, {size:.1f} MB")
    return out


def still(edit, f, path):
    """One composed frame as a PNG (for checking an edit without encoding)."""
    _init(edit)
    Image.frombytes("RGB", (W, H), _compose(f)).save(path)
