# SPDX-License-Identifier: GPL-3.0-or-later
"""The print-quality thresholds live in one place (pack/pq_rules.py), and docs/PRINT_QUALITY.md
carries the table made from it. NumPy only.

    python3 tests/test_pq_rules.py
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'bucket_builder'))

import numpy as np                                      # noqa: E402

from pack import pq_rules, quality                      # noqa: E402

FAILS = []


def check(cond, label, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + (f'   [{detail}]' if detail else ''))
    if not cond:
        FAILS.append(label)


def main():
    doc = open(os.path.join(HERE, '..', 'docs', 'PRINT_QUALITY.md'), encoding='utf-8').read()
    a, b = doc.find('<!-- pq-table -->'), doc.find('<!-- /pq-table -->')
    table = doc[a + len('<!-- pq-table -->'):b].strip() if a >= 0 and b > a else ''
    check(table == pq_rules.markdown_table(), 'the doc carries the table the code makes')
    check(quality.MIN_AREA == pq_rules.MIN_AREA_MM2 and quality.NEAR == pq_rules.FACE_NEAR_MM
          and quality.TOP_COS == pq_rules.CAPILLARY_COS and quality.WALL_COS == pq_rules.WALL_COS,
          'the packer and the report read the same numbers')
    # the owner's tilt rule (2026-11-19): flat up is capillary, under 10 degrees stair-steps, 10 and more is fine
    f = quality.Faces()
    tilts = [0.0, 0.5, 3.0, 9.0, 12.0, 45.0, 90.0, 180.0, 175.0]
    f.n = np.array([[math.sin(math.radians(t)), 0.0, math.cos(math.radians(t))] for t in tilts])
    got = quality.classify(f)
    want = ['up', 'up', 'shallow', 'shallow', '', '', 'wall', '', 'shallow']
    check(got == want, 'faces by tilt: flat up, shallow, fine, wall, flat down, shallow down', got)


main()
print()
if FAILS:
    print('FAILED: %d' % len(FAILS))
    sys.exit(1)
print('ALL OK')
