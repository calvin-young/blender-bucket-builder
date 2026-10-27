#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Packer speed on this machine (the code is bucket_builder/pack/timing.py;
in Blender: Bucket sidebar > Pack > Advanced > Measure Packer Speed).

    python3 tools/pack_timing.py [--greedy]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bucket_builder'))
from pack.timing import main  # noqa: E402

main()
