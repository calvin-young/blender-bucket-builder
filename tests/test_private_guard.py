"""Fail if anything in benchmarks/private/ other than its README and .gitignore
is tracked by git, or would be committed if staged with a plain `git add`.

    python3 tests/test_private_guard.py

Real buckets there may hold customer data (docs/AUTOPACK.md §16).
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FOLDER = 'benchmarks/private'
ALLOWED = {FOLDER + '/README.md', FOLDER + '/.gitignore'}


def git(*args):
    return subprocess.run(('git', '-C', REPO) + args, check=True,
                          capture_output=True, text=True).stdout


def tracked():
    """Files under the folder in the index (what a commit would contain)."""
    out = git('ls-files', '-z', '--', FOLDER)
    return {p for p in out.split('\0') if p}


def ignored(path):
    """Whether git would ignore the path (relative to the repository)."""
    r = subprocess.run(('git', '-C', REPO, 'check-ignore', '-q', '--no-index', path))
    return r.returncode == 0


def main():
    failures = []

    extra = sorted(tracked() - ALLOWED)
    if extra:
        failures.append('tracked in %s (remove with `git rm --cached`): %s'
                        % (FOLDER, ', '.join(extra)))

    for p in sorted(ALLOWED):
        if ignored(p):
            failures.append('%s is ignored but must be committed' % p)

    # Anything a user might drop in is ignored, including in subfolders and
    # names that look like the allowed ones.
    for p in ('bucket.3mf', 'part.stl', 'manifest.json', 'results.csv',
              'sub/README.md', 'sub/bucket.3mf', 'README.md.bak', '.hidden'):
        if not ignored(FOLDER + '/' + p):
            failures.append('%s/%s would not be ignored' % (FOLDER, p))

    # A real file in the folder is not picked up by `git add -A`.
    with tempfile.NamedTemporaryFile(dir=os.path.join(REPO, FOLDER),
                                     suffix='.3mf') as f:
        f.write(b'not a real bucket')
        f.flush()
        name = FOLDER + '/' + os.path.basename(f.name)
        status = git('status', '--porcelain', '--untracked-files=all', '--', FOLDER)
        if name in status:
            failures.append('a new file in %s shows up as untracked' % FOLDER)

    if failures:
        print('FAIL')
        for m in failures:
            print('  ' + m)
        return 1
    print('OK: only %s are tracked in %s; everything else there is ignored'
          % (' and '.join(sorted(os.path.basename(p) for p in ALLOWED)), FOLDER))
    return 0


if __name__ == '__main__':
    sys.exit(main())
