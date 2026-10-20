# SPDX-License-Identifier: GPL-3.0-or-later
"""How layouts are compared (docs/AUTOPACK.md §5.2, §5.3).

A layout's score is a tuple, smaller is better, compared element by
element:

    (parts left out of tier 1, of tier 2, ..., -volume placed, value)

so no number of lower-tier parts outweighs one tier-1 part, and among
layouts that place the same parts the value decides.  With the preset
'volume' (Fullest Bucket) the last tier counts by volume instead:

    (..., left out of the tier before last, -volume placed, left out of the last tier, value)

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
    'volume': (1.0, 5.0),        # as 'overnight', but the most part volume rather than the most parts
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
    if preset == 'volume' and tiers:
        return tuple(left_by_tier[t] for t in tiers[:-1]) + (-round(volume, 3), left_by_tier[tiers[-1]], v)
    return tuple(left_by_tier[t] for t in tiers) + (-round(volume, 3), v)
