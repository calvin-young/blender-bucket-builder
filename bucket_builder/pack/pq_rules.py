# SPDX-License-Identifier: GPL-3.0-or-later
"""Every print-quality threshold in one place (the owner, 2026-11-19: "one central place for every
print-quality rule and threshold, changed in one place").

`pack/quality.py` (the packer's soft goals and the Build Report), the Problems list and the
viewport's highlighting all read these. `docs/PRINT_QUALITY.md` carries the table that
`markdown_table()` makes, between the markers `<!-- pq-table -->` and `<!-- /pq-table -->`;
`tests/test_pq_rules.py` fails when the two differ, so the doc can't drift from the code.
"""

import math

# (rule id, name in the Problems list, threshold name, value, unit, what it means, source)
RULES = (
    ('PQ-05', "Flat Faces Close", 'FACE_NEAR_MM', 10.0, "mm",
     "large flat faces of two parts, facing each other, closer than this", "practitioner, assumed"),
    ('PQ-05', "Flat Faces Close", 'FACING_DEG', 5.0, "degrees",
     "faces count as facing each other within this of opposite", "assumed"),
    ('PQ-06', "Flat Face by a Wall", 'WALL_DEG', 10.0, "degrees",
     "a large flat face within this of parallel to a side wall", "practitioner, assumed"),
    ('PQ-07', "Flat Face Up", 'CAPILLARY_DEG', 1.0, "degrees",
     "a large flat face facing up within this of flat: the capillary (meniscus) defect", "practitioner"),
    ('PQ-09', "Shallow Face", 'STAIR_DEG', 10.0, "degrees",
     "a large flat face tilted more than the capillary limit but less than this: stair-stepping, "
     "worse than the capillary defect; 10 degrees and more is fine", "the owner, 2026-11-19"),
    ('PQ-05', "(all face rules)", 'MIN_AREA_MM2', 2500.0, "mm^2",
     "a flat face counts from this area (50 x 50 mm)", "assumed"),
)

_VALUES = {r[2]: r[3] for r in RULES}
FACE_NEAR_MM = _VALUES['FACE_NEAR_MM']
FACING_DEG = _VALUES['FACING_DEG']
WALL_DEG = _VALUES['WALL_DEG']
CAPILLARY_DEG = _VALUES['CAPILLARY_DEG']
STAIR_DEG = _VALUES['STAIR_DEG']
MIN_AREA_MM2 = _VALUES['MIN_AREA_MM2']

WALL_COS = math.cos(math.radians(WALL_DEG))
FACING_COS = -math.cos(math.radians(FACING_DEG))
CAPILLARY_COS = math.cos(math.radians(CAPILLARY_DEG))
STAIR_COS = math.cos(math.radians(STAIR_DEG))


def markdown_table():
    """The table of thresholds as docs/PRINT_QUALITY.md carries it."""
    out = ["| Rule | Shown as | Threshold | Value | Meaning | Source |",
           "| --- | --- | --- | --- | --- | --- |"]
    for rid, shown, name, value, unit, meaning, source in RULES:
        out.append(f"| {rid} | {shown} | `{name}` | {value:g} {unit} | {meaning} | {source} |")
    return "\n".join(out)
