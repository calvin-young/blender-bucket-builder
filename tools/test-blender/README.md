# Test copies of Blender, built from source

Only needed in a sandbox that cannot download Blender (no access to
blender.org or PyPI) but can clone from GitHub. On an ordinary machine,
download Blender and skip this folder.

    tools/test-blender/build_blender.sh                      # about 3 hours on 2 cores
    tools/test-blender/setup_profile.sh ~/build/blender-install/blender
    export BLENDER_USER_RESOURCES=~/build/blender-user

Result, under `$ROOT` (default `~/build`):

| Path | What |
| --- | --- |
| `blender-install/blender` | headless Blender 5.2.2, for the `--background` tests |
| `blender-install-gui/blender` | the same with a window (SDL), for `tests/gui_on_xvfb.sh` |
| `blender-user/` | private profile with the add-on installed and enabled |

Run the windowed copy with `SDL_VIDEO_FORCE_EGL=1` (there is no GLX).

These scripts were assembled from the commands that worked when the builds
were first made, one step at a time. The steps are the same, but the scripts
as a whole have not been run from start to finish, so expect to nurse them.

## What went wrong the first time, and the fix that is now in the scripts

* **`pip install bpy` and blender.org downloads are blocked.** Hence the
  source build. `git clone` of public GitHub repositories works.
* **Git LFS is not served.** Blender's binary data files arrive as pointer
  files and CMake stops with "incomplete startup blend". They are copied from
  the 4.2 source, where they were ordinary git objects.
* **GCC 13 is too old** for Blender 5.2 (needs 14). Clang 18 works.
* **`OpenImageIO::oiiotool` target missing**: guarded in
  `dependency_targets.cmake`.
* **Subdivision Surface does nothing** in this "lite" build (no OpenSubdiv), so
  tests use other modifiers.
* **The extension system needs `attrs` and `cattrs`**, which a build without
  bundled Python packages lacks. They are copied into `scripts/modules`.
* **No OpenGL headers for SDL**: empty stand-in headers; SDL then uses EGL.
* **Software OpenGL cannot read its front buffer back**, so Blender's own
  screenshot operator returns black. `tests/gui_on_xvfb.sh` starts Xvfb with
  `-fbdir` and the tests read the display's framebuffer file instead.
* **The splash screen swallows the first simulated key press.** It is switched
  off in the test profile.
* **In the sandbox, the clock only runs while a command is running.** A
  background build makes no progress between commands; keep a foreground
  `sleep` loop going while waiting for it.
