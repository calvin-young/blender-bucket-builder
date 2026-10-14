# SPDX-License-Identifier: GPL-3.0-or-later
"""Text that is fit to paste anywhere.

An error message or a traceback names folders: the add-on's, the temporary
folder, the user's home folder (and so the user's account name).  `tidy`
replaces them with short stand-ins, so that what the add-on shows after a
failure can be copied into a bug report without giving the machine away.
No `bpy` here."""

import os
import re
import tempfile


def _spellings(path):
    p = os.path.normpath(path)
    return {p, p.replace('\\', '/'), p.replace('/', '\\')}


def _roots(addon):
    pairs = []
    for path, label in ((addon, '<addon>'), (tempfile.gettempdir(), '<temp>'),
                        (os.path.expanduser('~'), '~')):
        if not path or path in ('~', os.sep, os.curdir):
            continue
        pairs += [(s, label) for s in _spellings(path)]
    return sorted(pairs, key=lambda r: -len(r[0]))      # the longest first: they sit inside each other


def tidy(text, addon=None):
    """`text` with the add-on's folder as <addon>, the temporary folder as
    <temp> and the home folder as ~.  `addon` defaults to this package's folder."""
    if addon is None:
        addon = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flags = re.IGNORECASE if os.name == 'nt' else 0
    for path, label in _roots(addon):
        text = re.sub(re.escape(path), lambda m, label=label: label, text, flags=flags)
    return text
