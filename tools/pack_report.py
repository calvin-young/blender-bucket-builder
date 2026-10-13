#!/usr/bin/env python3
"""Density report of a 3MF/STL bucket without Blender: `python3 tools/pack_report.py -h`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bucket_builder'))
from pack.cli import main  # noqa: E402

argv = sys.argv[1:]
sys.exit(main(argv if argv[:1] in (['report'], ['pack']) else ['report'] + argv))
