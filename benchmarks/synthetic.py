# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic shapes and buckets: the module is in the add-on
(bucket_builder/pack/synthetic.py); this is the command line.

    python3 benchmarks/synthetic.py --list
    python3 benchmarks/synthetic.py --write out_dir
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bucket_builder'))

from pack.synthetic import *  # noqa: E402,F401,F403
from pack.synthetic import main  # noqa: E402

if __name__ == '__main__':
    main()
