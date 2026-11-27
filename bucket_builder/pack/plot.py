# SPDX-License-Identifier: GPL-3.0-or-later
"""The layer-density profile as a small picture (float RGBA, premultiplied).

Height runs up the picture, density to the right. Each layer band is a dot (a scatter, the owner,
2026-11-20: a line was hard to read with big swings); dots denser than the mean by more than
OUTLIER standard deviations are drawn in the warning color, sparser ones gray. A thin line marks
the mean. No `bpy`: the sidebar hands the pixels to an icon.
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


OUTLIER = 1.5            # standard deviations from the mean that mark a layer as dense or sparse
DENSE = (1.0, 0.45, 0.2)
SPARSE = (0.55, 0.55, 0.55)


def layer_stats(rep):
    """Statistics of the layer densities over the packed height, or None: mean, std, min, max,
    the dense and sparse thresholds and how many layers are past each."""
    if rep is None or rep.layer_density is None or rep.height <= 0:
        return None
    lo = max(0, int(np.floor(getattr(rep, 'bottom', 0.0) / rep.band + 1e-9)))
    hi = max(lo + 1, int(np.ceil(rep.height / rep.band - 1e-9)))
    d = np.maximum(rep.layer_density[lo:hi], 0.0)
    if d.size == 0:
        return None
    mean, std = float(d.mean()), float(d.std())
    dense, sparse = mean + OUTLIER * std, mean - OUTLIER * std
    return {'mean': mean, 'std': std, 'min': float(d.min()), 'max': float(d.max()),
            'dense': dense, 'sparse': sparse, 'n_dense': int((d > dense).sum()) if std > 0 else 0,
            'n_sparse': int((d < sparse).sum()) if std > 0 else 0, 'layers': int(d.size), 'band': rep.band}


def dense_bands(rep, merge_mm=2.0):
    """[(z0, z1)] in mm above the floor: runs of dense layers (the orange dots, `layer_stats`), runs
    closer than merge_mm joined. What the Build Report's Show Dense Layers highlights and steps through
    (#53)."""
    st = layer_stats(rep)
    if st is None or st['std'] <= 0 or not st['n_dense']:
        return []
    used = max(1, int(np.ceil(rep.height / rep.band - 1e-9)))
    d = np.maximum(rep.layer_density[:used], 0.0)
    out = []
    for i in np.nonzero(d > st['dense'])[0]:
        z0, z1 = float(i * rep.band), float((i + 1) * rep.band)
        if out and z0 - out[-1][1] < merge_mm - 1e-9:
            out[-1] = (out[-1][0], z1)
        else:
            out.append((z0, z1))
    return out


def profile_pixels(rep, size=192):
    """(size, size, 4) float32, the first row at the bottom, or None if the
    report has no profile.  The x scale is the peak layer (at least 5 %)."""
    if rep is None or rep.layer_density is None or rep.height <= 0:
        return None
    used = max(1, int(np.ceil(rep.height / rep.band - 1e-9)))
    d = np.maximum(rep.layer_density[:used], 0.0)
    st = layer_stats(rep)
    xmax = max(float(d.max()), 0.05) * 1.08
    img = np.zeros((size, size, 4), np.float32)
    pad = 3
    h = size - 2 * pad
    w = size - 2 * pad
    canvas = np.zeros((h, w, 4), np.float32)
    _blend(canvas, np.ones((h, w), np.float32), BACK, 0.28)
    cols = np.arange(w)[None, :]
    for q in (0.25, 0.5, 0.75):
        _blend(canvas, (np.abs(cols - q * w) < 0.6) * np.ones((h, 1)), GRID, 0.25)
    if st is not None:
        _blend(canvas, (np.abs(cols - st['mean'] / xmax * w) < 0.6) * np.ones((h, 1)), LINE, 0.6)
    # one dot per band, at its height and density
    yy, xx = np.mgrid[0:h, 0:w]
    r = 1.6 if used < h / 3 else 1.1
    ys = (np.arange(used) + 0.5) / used * h - 0.5
    xs = d / xmax * w
    color = np.zeros((used, 3), np.float32) + np.asarray(BAR, np.float32)
    if st is not None and st['std'] > 0:
        color[d > st['dense']] = DENSE
        color[(d < st['sparse']) & (np.arange(used) * rep.band >= getattr(rep, 'bottom', 0.0))] = SPARSE
    for c in {tuple(v) for v in color.tolist()}:
        sel = np.all(color == np.asarray(c, np.float32), axis=1)
        mask = np.zeros((h, w), np.float32)
        for y, x in zip(ys[sel], xs[sel]):
            y0, y1 = int(max(0, y - 3)), int(min(h, y + 4))
            x0, x1 = int(max(0, x - 3)), int(min(w, x + 4))
            dd = (yy[y0:y1, x0:x1] - y) ** 2 + (xx[y0:y1, x0:x1] - x) ** 2
            mask[y0:y1, x0:x1] = np.maximum(mask[y0:y1, x0:x1], np.clip(r + 0.5 - np.sqrt(dd), 0.0, 1.0))
        _blend(canvas, mask, c, 0.95)
    # floor line
    canvas[0, :, :3] = canvas[0, :, :3] * 0.4 + 0.6
    canvas[0, :, 3] = 1.0
    img[pad:pad + h, pad:pad + w] = canvas
    # premultiply by alpha, as Blender wants the pixels of an icon
    img[..., :3] *= img[..., 3:]
    return img
