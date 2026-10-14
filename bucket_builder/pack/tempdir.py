# SPDX-License-Identifier: GPL-3.0-or-later
"""The folder a packing run keeps its job in.

The packer's worker process reads the meshes from a folder in the temporary
directory (which only the user's own account can read).  The operator removes
the folder when the run ends or is stopped.  If Blender is killed or crashes
mid-run that does not happen, so `sweep_stale` removes what is left over: at
start-up and before the next run.  No `bpy` here."""

import os
import shutil
import tempfile
import time

PREFIX = 'bucket_builder_pack_'
STALE_AFTER = 6 * 3600          # seconds without any change in the folder


def new_job_folder(base=None):
    """A new, empty job folder (readable by this user only where the system supports that)."""
    return tempfile.mkdtemp(prefix=PREFIX, dir=base)


def _last_change(folder):
    """The newest modification time of the folder and the files directly in it."""
    t = os.path.getmtime(folder)
    with os.scandir(folder) as it:
        for e in it:
            try:
                t = max(t, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                pass
    return t


def sweep_stale(base=None, max_age=STALE_AFTER, now=None):
    """Remove job folders nothing has touched for `max_age` seconds.  Returns
    how many were removed.  A running job rewrites its progress file all the
    time, and folders of another name, links and plain files are left alone.
    Never raises."""
    removed = 0
    try:
        base = base or tempfile.gettempdir()
        now = time.time() if now is None else now
        with os.scandir(base) as it:
            entries = [e for e in it if e.name.startswith(PREFIX)]
        for e in entries:
            try:
                if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                    continue
                if now - _last_change(e.path) > max_age:
                    shutil.rmtree(e.path, ignore_errors=True)
                    removed += 0 if os.path.exists(e.path) else 1
            except OSError:
                continue
    except OSError:
        pass
    return removed
