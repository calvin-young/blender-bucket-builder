"""What the add-on promises about the user's data, as checks.

    python3 tests/test_privacy.py

* nothing in it can reach the network: no network module is imported and the
  manifest asks for no network permission;
* the packer's job folder holds geometry and numbers, no names;
* what a failed packer run or a failed command shows names no folder, file
  or part, and no traceback;
* job folders left behind by a crash are removed, and nothing else is.

(Copy Report is checked in Blender: tests/blender_privacy_test.py.)
"""

import ast
import contextlib
import io
import os
import re
import struct
import sys
import tempfile
import time
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, 'bucket_builder')
sys.path.insert(0, ADDON)
sys.path.insert(0, HERE)

import meshes  # noqa: E402
from pack import cli, safe, tempdir, worker  # noqa: E402
from pack.meshio import Mesh  # noqa: E402
from pack.problem import Part, Settings  # noqa: E402

FAILS = []

NETWORK = {'urllib', 'urllib2', 'urllib3', 'http', 'httplib', 'socket', 'socketserver', 'ssl', 'ftplib',
           'smtplib', 'poplib', 'imaplib', 'telnetlib', 'xmlrpc', 'webbrowser', 'asyncio', 'requests',
           'httpx', 'aiohttp', 'websocket', 'websockets', 'paramiko'}


def check(ok, msg, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + msg + (('   [%s]' % detail) if detail and not ok else ''))
    if not ok:
        FAILS.append(msg)


# ------------------------------------------------------------------ network

def imports_of(path):
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split('.')[0]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '__import__':
            yield '__import__'


def permissions():
    with open(os.path.join(ADDON, 'blender_manifest.toml'), encoding='utf-8') as f:
        text = f.read()
    try:
        import tomllib
        return set(tomllib.loads(text).get('permissions', {}))
    except ImportError:
        m = re.search(r'^\[permissions\]\s*$(.*?)(?=^\[|\Z)', text, re.S | re.M)
        return set(re.findall(r'^\s*(\w+)\s*=', m.group(1), re.M)) if m else set()


def network_tests():
    print('network')
    found, n = [], 0
    for root, dirs, files in os.walk(ADDON):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in files:
            if name.endswith('.py'):
                n += 1
                path = os.path.join(root, name)
                bad = sorted({m for m in imports_of(path) if m in NETWORK or m == '__import__'})
                if bad:
                    found.append('%s: %s' % (os.path.relpath(path, ADDON), ', '.join(bad)))
                with open(path, encoding='utf-8') as f:
                    if 'url_open' in f.read():
                        found.append('%s: opens a web address' % os.path.relpath(path, ADDON))
    check(n > 20, 'looked at %d source files' % n)
    check(not found, 'no network module is imported anywhere', '; '.join(found))
    perms = permissions()
    check('network' not in perms, 'the manifest asks for no network permission', ', '.join(sorted(perms)) or 'none')


# ------------------------------------------------------------------ the job folder

def job_tests():
    print('job folder')
    box = Mesh(*[np.asarray(x) for x in meshes.box((12, 12, 12))], 'AcmeSocket')
    flat = Mesh(*[np.asarray(x) for x in meshes.box((20, 20, 4))], 'AcmeInsole_job42')
    parts = [Part('AcmeSocket_job42', box, 3, 'fixed'), Part('AcmeInsole_job42', flat, 2, 'z')]
    locked = [(Mesh(*[np.asarray(x) for x in meshes.box((10, 10, 10))], 'AcmeLocked'), np.eye(4))]
    folder = tempdir.new_job_folder()
    try:
        s = Settings(vlo=[0, 0, 0], vhi=[120, 120, 100], max_density=None)
        worker.write_job(folder, parts, s, locked)
        text = b''.join(open(os.path.join(folder, n), 'rb').read() for n in os.listdir(folder))
        check(b'Acme' not in text and b'job42' not in text, 'the job holds no part, mesh or object name')
        check(os.listdir(folder) and set(os.listdir(folder)) == {'job.json', 'job.npz'}, 'the job is two files')
        code = worker.main(folder)
        prog = worker.read_progress(folder)
        check(code == 0 and prog and prog['finished'] and not prog['error'] and len(prog['placements']) == 5,
              'the worker places all 5 parts from a job with numbered parts')
        text = b''.join(open(os.path.join(folder, n), 'rb').read() for n in os.listdir(folder))
        check(b'Acme' not in text, 'nor does anything the worker adds')
        if os.name == 'posix':
            check(os.stat(folder).st_mode & 0o077 == 0, 'the folder is closed to other accounts')
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)

    # a failed worker: its report is what the user sees and may paste anywhere
    folder = tempdir.new_job_folder()
    try:
        code = worker.main(folder)                    # (no job in it)
        err = (worker.read_progress(folder) or {}).get('error', '')
        check(code == 1 and 'FileNotFoundError' in err, 'a worker without a job reports the kind of failure')
        check(tempfile.gettempdir() not in err and ADDON not in err and '<temp>' in err,
              'its report names neither the temporary folder nor the add-on folder')
        home = os.path.expanduser('~')
        check(home in ('~', os.sep) or home not in err, 'nor the home folder')
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


def tidy_tests():
    print('tidy')
    addon = r'C:\Users\someone\AppData\Roaming\Blender Foundation\Blender\5.2\extensions\user_default\bucket_builder'
    text = 'File "%s\\pack\\greedy.py", line 5\nFile "%s/pack/worker.py", line 9' % (addon, addon.replace('\\', '/'))
    out = safe.tidy(text, addon=addon)
    check(out.count('<addon>') == 2 and 'someone' not in out, 'the add-on folder is replaced, in both spellings', out)
    saved = {k: os.environ.get(k) for k in ('HOME', 'USERPROFILE')}
    try:
        os.environ['HOME'] = os.environ['USERPROFILE'] = os.path.join(os.sep, 'home', 'ivan_example')
        out = safe.tidy(os.path.join(os.sep, 'home', 'ivan_example', 'Documents', 'x.py'), addon=ADDON)
        check('ivan_example' not in out and out.startswith('~'), 'the home folder becomes ~', out)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check(safe.tidy('nothing to hide: 12 parts, 3 collisions') == 'nothing to hide: 12 parts, 3 collisions',
          'other text is left alone')


# ------------------------------------------------------------------ leftover folders

def sweep_tests():
    print('leftover folders')
    base = tempfile.mkdtemp(prefix='sweep_')
    now = time.time()
    old = now - 10 * 3600

    def make(name, age, inner_age=None):
        p = os.path.join(base, name)
        os.mkdir(p)
        f = os.path.join(p, 'job.npz')
        open(f, 'wb').write(b'x')
        os.utime(f, (inner_age or age, inner_age or age))
        os.utime(p, (age, age))
        return p

    gone = make(tempdir.PREFIX + 'old', old)
    fresh = make(tempdir.PREFIX + 'fresh', now)
    busy = make(tempdir.PREFIX + 'busy', old, inner_age=now - 60)     # (a running job rewrites its files)
    other = make('something_else', old)
    plain = os.path.join(base, tempdir.PREFIX + 'plainfile')
    open(plain, 'wb').write(b'x')
    os.utime(plain, (old, old))
    target = make('target_of_a_link', old)
    link = os.path.join(base, tempdir.PREFIX + 'link')
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        link = None
    n = tempdir.sweep_stale(base, max_age=6 * 3600, now=now)
    check(n == 1 and not os.path.exists(gone), 'a job folder untouched for hours is removed')
    check(os.path.exists(fresh), 'a new one stays')
    check(os.path.exists(busy), 'one whose files are still changing stays')
    check(os.path.exists(other) and os.path.exists(plain), 'folders of other names and plain files stay')
    check(link is None or (os.path.islink(link) and os.path.exists(target)), 'a link and its target stay')
    check(tempdir.sweep_stale(os.path.join(base, 'missing')) == 0, 'a missing temporary folder is no problem')
    f = tempdir.new_job_folder(base)
    check(os.path.basename(f).startswith(tempdir.PREFIX) and os.path.dirname(f) == base, 'new folders are made where asked')
    import shutil
    shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------------ the command line

def broken_files(folder):
    out = {}
    p = os.path.join(folder, 'AcmeJob42_empty.stl')
    open(p, 'wb').close()
    out['empty STL'] = p
    p = os.path.join(folder, 'AcmeJob42_noise.3mf')
    open(p, 'wb').write(os.urandom(500))
    out['a 3MF that is not a zip'] = p
    p = os.path.join(folder, 'AcmeJob42_text.stl')
    open(p, 'w').write('solid AcmeSecretPart\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 x\n')
    out['an STL with a bad number'] = p
    p = os.path.join(folder, 'AcmeJob42_nomodel.3mf')
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('hello.txt', 'AcmeSecretPart')
    out['a zip without a model'] = p
    p = os.path.join(folder, 'AcmeJob42_badxml.3mf')
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('3D/3dmodel.model', '<model><AcmeSecretPart')
    out['a 3MF with broken XML'] = p
    p = os.path.join(folder, 'AcmeJob42_cut.stl')
    open(p, 'wb').write(b'\0' * 80 + struct.pack('<I', 5) + b'\0' * 10)
    out['a cut-off binary STL'] = p
    p = os.path.join(folder, 'AcmeJob42_dir.3mf')
    os.makedirs(p)
    out['a folder'] = p
    out['a file that is not there'] = os.path.join(folder, 'AcmeJob42_missing.3mf')
    return out


def cli_tests():
    print('command line errors')
    folder = tempfile.mkdtemp(prefix='AcmeDir_job42_')
    try:
        files = broken_files(folder)
        for what, path in files.items():
            for argv in (['report', path], ['pack', path, '--volume', '100', '100', '100']):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stderr(err):
                    try:
                        code = cli.main(argv, out)
                        raised = None
                    except Exception as e:          # (would be a traceback on the user's screen)
                        code, raised = None, e
                shown = out.getvalue() + err.getvalue()
                clean = not any(w in shown for w in ('Acme', 'job42', 'Traceback', folder, tempfile.gettempdir()))
                check(raised is None and code == 1 and clean,
                      '%s, %s: exit 1, nothing identifying' % (what, argv[0]),
                      repr(raised) if raised else shown.strip()[:80])
        # with --names the details are allowed: the original error comes through
        bad = files['an STL with a bad number']
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(['report', bad, '--names'], io.StringIO())
            check(False, '--names shows the details of an unexpected failure')
        except ValueError:
            check(True, '--names shows the details of an unexpected failure')
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


def main():
    network_tests()
    job_tests()
    tidy_tests()
    sweep_tests()
    cli_tests()
    if FAILS:
        print('FAILED: %d' % len(FAILS))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
