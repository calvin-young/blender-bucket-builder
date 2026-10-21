# SPDX-License-Identifier: GPL-3.0-or-later
"""The engines (bucket_builder/core and bucket_builder/pack) stay free of
Blender, so that they can become a package of their own (AGENTS.md,
"Engine boundary").  They may import the standard library, NumPy, the
optional accelerators below, each other (pack -> core through
pack/_core.py only) and their own modules.  Anything else fails here, so
that a Blender-specific need is raised and decided rather than slipped in.

    python3 tests/test_engine_boundary.py
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'bucket_builder')
OPTIONAL = {'numpy', 'scipy', 'cupy'}
BLENDER = {'bpy', 'bmesh', 'mathutils', 'gpu', 'gpu_extras', 'bgl', 'blf', 'aud', 'idprop', 'imbuf',
           'freestyle', 'bl_math', 'bl_ui', 'bl_operators', 'addon_utils', 'rna_prop_ui'}


def main():
    bad = []
    std = set(sys.stdlib_module_names)
    for pkg in ('core', 'pack'):
        d = os.path.join(ROOT, pkg)
        own = {f[:-3] for f in os.listdir(d) if f.endswith('.py')}
        for f in sorted(os.listdir(d)):
            if not f.endswith('.py'):
                continue
            tree = ast.parse(open(os.path.join(d, f), encoding='utf-8').read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                    level = 0
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or '']
                    level = node.level
                else:
                    continue
                for n in names:
                    top = n.split('.')[0]
                    where = '%s/%s:%d %s' % (pkg, f, node.lineno, ('.' * level) + n)
                    if level == 1 or (level == 0 and top in own):
                        continue                               # (its own modules)
                    if level >= 2:
                        if not (pkg == 'pack' and f == '_core.py' and top == 'core'):
                            bad.append(where + '  (reaches outside the engines)')
                        continue
                    if top in BLENDER:
                        bad.append(where + '  (Blender)')
                    elif top == 'core' and pkg == 'pack' and f == '_core.py':
                        continue
                    elif top not in std and top not in OPTIONAL:
                        bad.append(where + '  (not the standard library or NumPy)')
    for b in bad:
        print('  FAIL ' + b)
    print('ALL OK' if not bad else '%d FAILED' % len(bad))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
