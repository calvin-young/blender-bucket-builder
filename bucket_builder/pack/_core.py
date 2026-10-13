# SPDX-License-Identifier: GPL-3.0-or-later
"""The exact engine, imported the same way inside Blender (as a sibling
package of the add-on) and headless (with bucket_builder/ on sys.path)."""

try:
    from ..core import World, bvh, narrow                 # noqa: F401  (inside Blender)
    from ..core.world import COLLIDE, CLEAR, OK            # noqa: F401
except (ImportError, ValueError):
    from core import World, bvh, narrow                    # noqa: F401  (headless)
    from core.world import COLLIDE, CLEAR, OK              # noqa: F401
