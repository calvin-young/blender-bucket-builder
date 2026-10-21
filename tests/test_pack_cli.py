"""The report command: numbers, and that private names never reach its output.

    python3 tests/test_pack_cli.py
"""

import io
import os
import re
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'bucket_builder'))
sys.path.insert(0, HERE)

import meshes  # noqa: E402
from pack import cli, meshio, printers  # noqa: E402
from pack.meshio import Mesh  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ok   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def run(argv):
    out = io.StringIO()
    code = cli.main(argv, out)
    return code, out.getvalue()


def main():
    tmp = tempfile.mkdtemp(prefix='packcli_')
    secret = os.path.join(tmp, 'AcmeCorp_orthosis_job42.3mf')
    cube = Mesh(*[np.asarray(x) for x in meshes.box((20, 20, 20))], 'AcmeSocket')
    cube.tris = cube.tris.astype(np.int32)
    parts = []
    for i in range(4):
        M = np.eye(4)
        M[:3, 3] = [20 + 40 * i, 20, 10]
        parts.append((cube, M, 'AcmeSocket-%d' % i))
    M = np.eye(4)
    M[:3, 3] = [375, 20, 10]                         # sticks out of the 380 mm wall
    parts.append((cube, M, 'AcmeSocket-out'))
    meshio.write_3mf(secret, parts)

    print('report')
    code, text = run(['report', secret, '--profile', '--check'])
    check(code == 0, 'exit code 0')
    check(re.search(r'parts\s+5 ', text) is not None, 'five parts')
    check('4 inside, 1 partly outside, 0 outside' in text, 'one part is partly outside')
    # 5 cubes of 8000 mm3 = 40 cm3; packed height 20 mm; area 380 x 284
    check('volume         40.0 cm3' in text, 'volume 40.0 cm3')
    check('packed height  20.0 mm' in text, 'height 20 mm')
    want = 40000 / (380 * 284 * 20) * 100
    m = re.search(r'3D density\s+([\d.]+) %', text)
    check(m is not None and abs(float(m.group(1)) - want) < 0.06, 'density %.2f %%' % want)
    check('partly outside' in text.split('3D density')[1].split('\n')[0],
          'the density line carries the partly-outside warning')
    check('every mesh is closed' in text, '--check finds closed meshes')

    print('privacy')
    for word in ('Acme', 'orthosis', 'job42', tmp, 'secret'):
        check(word not in text, 'the output does not contain "%s"' % word)
    check('bucket-01' in text, 'the file is called bucket-01')
    code, named = run(['report', secret, '--names'])
    check('AcmeSocket-0' not in text and secret in named, '--names puts names back')

    code, text = run(['report', secret, os.path.join(tmp, 'Missing_Customer.3mf')])
    check(code == 1, 'a missing file gives exit code 1')

    print('printers and volumes')
    check(printers.find('5600')[0] == 'HP MJF 5600', 'find by number')
    check(printers.find('mjf 1200')[1] == (320.0, 165.0, 230.0), 'find by words')
    try:
        printers.find('nope')
        check(False, 'unknown printer is an error')
    except KeyError:
        check(True, 'unknown printer is an error')
    code, text = run(['report', secret, '--volume', '100', '100', '100', '--center'])
    check('1 inside, 0 partly outside, 4 outside' in text, 'a centered 100 mm volume holds one cube, and one touching its wall is outside: '
          + [l for l in text.split('\n') if 'inside' in l][0].strip())

    # the sizes match the add-on's printer list (props needs bpy, so read the source)
    src = open(os.path.join(ROOT, 'bucket_builder', 'props.py')).read()
    block = src[src.index('DEFAULT_PROFILES = ('):]
    block = block[:block.index('\n)\n')]
    found = {n: tuple(float(x) for x in s.split(','))
             for n, s in re.findall(r'\("([^"]+)", \(([^)]+)\)\)', block)}
    check(found == printers.PRINTERS, 'printer sizes match props.DEFAULT_PROFILES')

    print('pack')
    packed = os.path.join(tmp, 'AcmeCorp_packed.3mf')
    code, text = run(['pack', secret, '--volume', '150', '150', '100', '--mode', 'fixed', '--gap', '2',
                      '--wall-gap', '2', '--max-density', '0', '--out', packed, '--referee'])
    check(code == 0 and 'placed         5 of 5' in text, 'five cubes packed: ' +
          [l for l in text.split('\n') if 'placed' in l][0].strip())
    check('referee        clean' in text, 'the exact engine finds nothing wrong')
    check(not any(w in text for w in ('Acme', 'orthosis', tmp)), 'and no names in the output')
    code, text = run(['report', packed, '--volume', '150', '150', '100'])
    check('5 inside, 0 partly outside, 0 outside' in text and 'packed height  20.0 mm' in text,
          'the written 3MF reads back: all inside, 20 mm high')

    print('as a program')
    for argv in ([sys.executable, '-m', 'pack', 'report', secret],
                 [sys.executable, os.path.join(ROOT, 'tools', 'pack_report.py'), secret]):
        r = subprocess.run(argv, cwd=os.path.join(ROOT, 'bucket_builder'),
                           capture_output=True, text=True)
        check(r.returncode == 0 and 'bucket-01' in r.stdout and 'Acme' not in r.stdout + r.stderr,
              ' '.join(os.path.basename(a) for a in argv[1:3]))

    print()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
