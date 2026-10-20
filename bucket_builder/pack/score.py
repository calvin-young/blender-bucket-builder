# SPDX-License-Identifier: GPL-3.0-or-later
"""How layouts are compared (docs/AUTOPACK.md §5.2, §5.3).

A layout's score is a tuple, smaller is better, compared element by
element:

    (parts left out of tier 1, ..., of the tier before last,
     -volume placed, parts left out of the last tier, value)

so no number of lower-tier parts outweighs one tier-1 part, and within the
last tier the placed volume comes first: big parts first, and what is
left over is small parts (they fit into the next build more easily, and a
big part that fails is reprinted sooner: the owner's rule).  With the
preset 'count' (Most Parts) the number of parts comes first instead:

    (parts left out of tier 1, of tier 2, ..., -volume placed, value)

where

    value = height_weight * packed height (mm)
            + spread_weight * spread (the coefficient of variation of the
              layer density, smoothed over 5 mm)
            + layer_weight * the squared excess over the soft per-layer cap

The weights come from a preset.  They are first guesses, to be calibrated
on the benchmark buckets with the owner.
"""

import numpy as np

# preset -> (height weight, spread weight in mm per unit of spread)
PRESETS = {
    'overnight': (1.0, 5.0),     # count first, then height; spread as a tie-breaker
    'rush': (1.0, 0.0),          # height only
    'quality': (0.3, 60.0),      # even layers first
    'count': (1.0, 5.0),         # as 'overnight', but the most parts rather than the most part volume
}
DEFAULT = 'overnight'
SMOOTH_MM = 5.0


def spread(profile, height, h, smooth_mm=SMOOTH_MM):
    """Coefficient of variation of the volume per band of h, below the
    packed height, smoothed over `smooth_mm` (as metrics reports it)."""
    used = int(np.ceil(height / h - 1e-9))
    if used < 2:
        return 0.0
    d = np.asarray(profile[:used], float)
    w = max(1, int(round(smooth_mm / h)))
    if w > 1 and used > w:
        d = np.convolve(d, np.ones(w) / w, mode='valid')
    m = float(d.mean())
    return float(d.std() / m) if m > 0 else 0.0


def layer_excess(profile, area, h, cap):
    norm = area * h
    ex = np.maximum(np.asarray(profile, float) / norm - cap, 0.0)
    return float((ex * ex).sum())


def value(height, profile, settings, preset=None):
    wh, ws = PRESETS.get(preset or DEFAULT, PRESETS[DEFAULT])
    v = wh * height
    if ws:
        v += ws * spread(profile, height, settings.h)
    if settings.layer_weight:
        v += settings.layer_weight * layer_excess(profile, settings.area, settings.h, settings.layer_cap)
    return v


def key(left_by_tier, volume, height, profile, settings, preset=None):
    """left_by_tier: {tier: number of parts not placed}."""
    tiers = sorted(left_by_tier)
    v = value(height, profile, settings, preset)
    if preset == 'count' or not tiers:
        return tuple(left_by_tier[t] for t in tiers) + (-round(volume, 3), v)
    return tuple(left_by_tier[t] for t in tiers[:-1]) + (-round(volume, 3), left_by_tier[tiers[-1]], v)
