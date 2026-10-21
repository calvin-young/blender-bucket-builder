# SPDX-License-Identifier: GPL-3.0-or-later
"""The layer-density profile as a small picture (float RGBA, premultiplied).

Height runs up the picture, density to the right.  Pale bars are the density
of every layer band, the bright line is the profile smoothed over the window
the spread is measured on.  No `bpy`: the sidebar hands the pixels to an icon.
"""

import numpy as np

from . import metrics

BAR = (0.45, 0.62, 0.85)
LINE = (1.0, 0.82, 0.25)
GRID = (0.7, 0.7, 0.7)
BACK = (0.0, 0.0, 0.0)


def _blend(img, mask, color, alpha):
    a = (mask * alpha)[..., None]
    img[..., :3] = img[..., :3] * (1.0 - a) + np.asarray(color, np.float32) * a
    img[..., 3:] = img[..., 3:] * (1.0 - a) + a


def profile_pixels(rep, size=192):
    """(size, size, 4) float32, the first row at the bottom, or None if the
    report has no profile.  The x scale is the peak layer (at least 5 %)."""
    if rep is None or rep.layer_density is None or rep.height <= 0:
        return None
    used = max(1, int(np.ceil(rep.height / rep.band - 1e-9)))
    d = np.maximum(rep.layer_density[:used], 0.0)
    sm = np.maximum(metrics.smooth(d, rep.smooth_mm / rep.band), 0.0)
    xmax = max(float(d.max()), 0.05) * 1.08
    img = np.zeros((size, size, 4), np.float32)
    pad = 3
    h = size - 2 * pad
    w = size - 2 * pad
    rows = np.arange(h)
    band = np.minimum(((rows + 0.5) / h * used).astype(int), used - 1)
    cols = np.arange(w)[None, :]
    bars = cols < (d[band] / xmax * w)[:, None]
    smx = sm[band] / xmax * w
    # background, quarter lines, bars, line
    canvas = np.zeros((h, w, 4), np.float32)
    canvas[..., 3] = 0.0
    _blend(canvas, np.ones((h, w), np.float32), BACK, 0.28)
    for q in (0.25, 0.5, 0.75):
        _blend(canvas, (np.abs(cols - q * w) < 0.6) * np.ones((h, 1)), GRID, 0.25)
    _blend(canvas, bars.astype(np.float32), BAR, 0.55)
    prev = np.concatenate([smx[:1], smx[:-1]])
    lo = np.minimum(smx, prev) - 1.0
    hi = np.maximum(smx, prev) + 1.0
    line = ((cols >= lo[:, None]) & (cols <= hi[:, None])).astype(np.float32)
    _blend(canvas, line, LINE, 0.95)
    # floor line
    canvas[0, :, :3] = canvas[0, :, :3] * 0.4 + 0.6
    canvas[0, :, 3] = 1.0
    img[pad:pad + h, pad:pad + w] = canvas
    # premultiply by alpha, as Blender wants the pixels of an icon
    img[..., :3] *= img[..., 3:]
    return img
