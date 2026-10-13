# SPDX-License-Identifier: GPL-3.0-or-later
"""Build volumes (mm) for the command line.  The same sizes seed the add-on's
printer list in `props.DEFAULT_PROFILES` (which needs `bpy`); a test keeps the
two in step."""

PRINTERS = {
    'HP MJF 5600': (380.0, 284.0, 380.0),
    'HP MJF 1200': (320.0, 165.0, 230.0),
    'HP MJF 580': (332.0, 190.0, 248.0),
    'HP MJF 580+': (332.6, 198.7, 267.8),
}
DEFAULT_PRINTER = 'HP MJF 5600'


def find(name):
    """A printer by name, ignoring case, spaces and punctuation ('5600', 'mjf1200')."""
    def norm(s):
        return ''.join(c for c in s.lower() if c.isalnum() or c == '+')
    want = norm(name)
    hits = [k for k in PRINTERS if norm(k) == want]
    hits = hits or [k for k in PRINTERS if want and want in norm(k)]
    if len(hits) != 1:
        raise KeyError('%s printer "%s"; known: %s' % ('no' if not hits else 'ambiguous',
                                                       name, ', '.join(PRINTERS)))
    return hits[0], PRINTERS[hits[0]]
