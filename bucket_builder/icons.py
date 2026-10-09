# SPDX-License-Identifier: GPL-3.0-or-later
"""Small coloured icons for the sidebar.

Blender lets an add-on colour the text of a panel in one way only (red, for
alerts), and its own icons take their colour from the theme.  So that every
kind of problem has its colour in the sidebar too, as it has in the viewport,
these icons are drawn here, as pixels, when the add-on is switched on.
"""

import numpy as np

SIZE = 32

RED = (0.96, 0.22, 0.17)
AMBER = (1.0, 0.72, 0.0)
MAGENTA = (0.95, 0.25, 0.85)
GREEN = (0.3, 0.86, 0.4)
GREY = (0.62, 0.62, 0.62)
BLUE = (0.35, 0.75, 1.0)

_pcoll = None
_ids = {}          # name -> the preview that holds the icon


def _segment(x, y, a, b):
    """Distance of every pixel from the segment a-b."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    t = np.clip(((x - ax) * dx + (y - ay) * dy) / n, 0.0, 1.0) if n > 0.0 else 0.0
    return np.hypot(x - (ax + t * dx), y - (ay + t * dy))


def _strokes(x, y, lines, width):
    """Signed distance from a set of round-ended strokes."""
    d = None
    for line in lines:
        for a, b in zip(line[:-1], line[1:]):
            s = _segment(x, y, a, b)
            d = s if d is None else np.minimum(d, s)
    return d - 0.5 * width


def _shape(name, x, y):
    """Signed distance of every pixel from the outline of a shape, in units
    of half the icon: negative inside."""
    if name == 'cross':
        k = 0.56
        return _strokes(x, y, [[(-k, -k), (k, k)], [(-k, k), (k, -k)]], 0.34)
    if name == 'ok':
        return _strokes(x, y, [[(-0.66, -0.02), (-0.22, -0.5), (0.68, 0.52)]], 0.34)
    if name == 'warn':
        top, left, right = (0.0, 0.72), (-0.78, -0.62), (0.78, -0.62)
        d = _strokes(x, y, [[top, left, right, top]], 0.24)
        d = np.minimum(d, _strokes(x, y, [[(0.0, 0.28), (0.0, -0.1)]], 0.22))
        return np.minimum(d, np.hypot(x, y + 0.38) - 0.13)
    if name in {'box', 'gap'}:
        k = 0.62
        return _strokes(x, y, [[(-k, -k), (k, -k), (k, k), (-k, k), (-k, -k)]], 0.3)
    if name == 'note':
        return np.abs(np.hypot(x, y) - 0.56) - 0.14
    if name == 'busy':
        return np.minimum(np.minimum(np.hypot(x + 0.62, y), np.hypot(x, y)), np.hypot(x - 0.62, y)) - 0.17
    return np.hypot(x, y) - 0.26          # 'dot'


_COLOUR = {'cross': RED, 'ok': GREEN, 'warn': AMBER, 'box': MAGENTA, 'gap': AMBER, 'note': AMBER,
           'busy': GREY, 'dot': GREY}


def pixels(name, size=SIZE):
    """An icon as float RGBA, (size, size, 4), the first row at the bottom.
    The edge of the shape is one pixel of gradient wide.  The colours are
    multiplied by the alpha, which is how Blender wants the pixels of an
    icon: a colour with no alpha is not "nothing" to it but light that is
    added, and the icon comes out as a glowing square."""
    c = (np.arange(size) + 0.5) * (2.0 / size) - 1.0
    x, y = np.meshgrid(c, c)
    alpha = np.clip(0.5 - _shape(name, x, y) * (0.5 * size), 0.0, 1.0)
    out = np.empty((size, size, 4), dtype=np.float32)
    out[..., :3] = np.asarray(_COLOUR[name], dtype=np.float32) * alpha[..., None]
    out[..., 3] = alpha
    return out


def get(name):
    """The number Blender knows an icon by (for ``icon_value``), or 0 if the
    icons could not be made (there are none without a window)."""
    icon = _ids.get(name)
    if icon is None:
        return 0
    try:
        return icon.icon_id
    except Exception:
        return 0


def register():
    global _pcoll
    try:
        import bpy.utils.previews
        _pcoll = bpy.utils.previews.new()
        for name in _COLOUR:
            icon = _pcoll.new("bucket_builder_" + name)
            icon.icon_size = (SIZE, SIZE)
            icon.icon_pixels_float = pixels(name).ravel().tolist()
            _ids[name] = icon
    except Exception as ex:           # without them the sidebar uses Blender's own icons
        print("Bucket Builder: coloured icons unavailable:", ex)
        _ids.clear()


def unregister():
    global _pcoll
    _ids.clear()
    if _pcoll is not None:
        try:
            import bpy.utils.previews
            bpy.utils.previews.remove(_pcoll)
        except Exception:
            pass
        _pcoll = None
