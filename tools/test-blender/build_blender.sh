#!/bin/bash
# Builds two test copies of Blender from source into $ROOT (default ~/build):
#
#   blender-install/      headless, for the tests run with --background
#   blender-install-gui/  with a window (SDL), for the tests in tests/gui_on_xvfb.sh
#
# This exists for a sandbox where Blender cannot be downloaded but GitHub can
# be cloned.  On an ordinary machine, download Blender instead.
# About three hours on two cores.  Re-runnable.  See README.md in this folder.
set -u
ROOT=${ROOT:-$HOME/build}
HERE=$(cd "$(dirname "$0")" && pwd)
TAG=${BLENDER_TAG:-v5.2.2}
VER=${TAG#v}; VER=${VER%.*}                       # 5.2
PREFIX=$ROOT/deps
J=${J:-2}
mkdir -p "$ROOT" && cd "$ROOT" || exit 1

# 1. sources ------------------------------------------------------------------
[ -d blender-src/.git ] || git clone --depth 1 --branch "$TAG" --single-branch \
    https://github.com/blender/blender.git blender-src || exit 1

# Blender keeps binary data files (startup.blend, fonts, splash, ...) in Git
# LFS, which the sandbox's git proxy does not serve: the checkout has pointer
# files and CMake stops with "incomplete startup blend".  In 4.2 the same
# files were ordinary git objects, so take them from there.  A few dozen asset
# .blend files have no 4.2 counterpart and stay pointers; Blender only prints
# "Unrecognized file format" warnings for those.
[ -d blender-42-src/.git ] || GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --branch v4.2.0 \
    --single-branch https://github.com/blender/blender.git blender-42-src || exit 1
python3 - <<'PY'
import os, shutil, subprocess
SIG = b'version https://git-lfs'
new, old = 'blender-src', 'blender-42-src'
restored = left = 0
for f in subprocess.run(['git', '-C', new, 'ls-files'], capture_output=True, text=True).stdout.split('\n'):
    if not f or f.startswith(('tests/', 'release/windows', 'release/darwin')):
        continue
    b = os.path.join(new, f)
    if not os.path.isfile(b) or os.path.getsize(b) > 1024:
        continue
    with open(b, 'rb') as fh:
        if not fh.read(40).startswith(SIG):
            continue
    a = os.path.join(old, f)
    if os.path.isfile(a) and os.path.getsize(a) > 200:
        with open(a, 'rb') as fh:
            if not fh.read(40).startswith(SIG):
                shutil.copyfile(a, b)
                restored += 1
                continue
    left += 1
print('data files restored from 4.2:', restored, '| still pointers:', left)
PY

# oiiotool is not built here; one CMake line assumes its target exists
python3 - <<'PY'
p = 'blender-src/build_files/cmake/platform/dependency_targets.cmake'
s = open(p).read()
line = 'get_target_property(OPENIMAGEIO_TOOL OpenImageIO::oiiotool LOCATION)'
if line in s and 'if(TARGET OpenImageIO::oiiotool)' not in s:
    open(p, 'w').write(s.replace(line, 'if(TARGET OpenImageIO::oiiotool)\n  ' + line + '\nendif()'))
    print('patched', p)
PY

# 2. libraries ------------------------------------------------------------------
ROOT=$ROOT "$HERE/build_deps.sh" || exit 1

# 3. configure and build ----------------------------------------------------------
# Blender 5.2 needs GCC 14 or Clang; Ubuntu 24.04 ships GCC 13, so use Clang.
configure() {
  PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib64/pkgconfig" CMAKE_PREFIX_PATH="$PREFIX" \
  cmake -S "$ROOT/blender-src" -B "$ROOT/blender-build" -G Ninja \
    -C "$ROOT/blender-src/build_files/cmake/config/blender_lite.cmake" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DWITH_LIBS_PRECOMPILED=OFF -DWITH_GHOST_WAYLAND=OFF -DWITH_GHOST_X11=OFF \
    -DWITH_VULKAN_BACKEND=OFF -DWITH_OPENGL_BACKEND=ON \
    -DWITH_PYTHON_INSTALL=OFF -DWITH_PYTHON_INSTALL_NUMPY=OFF \
    -DWITH_PYTHON_INSTALL_REQUESTS=OFF -DWITH_PYTHON_INSTALL_ZSTANDARD=OFF \
    -DPYTHON_VERSION=3.13 -DWITH_TBB=ON -DWITH_IO_STL=ON \
    -DWITH_INSTALL_PORTABLE=ON -DWITH_GTESTS=OFF -DWITH_DOC_MANPAGE=OFF \
    -DWITH_STRICT_BUILD_OPTIONS=OFF \
    -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath,$PREFIX/lib" "$@"
}

finish() {  # the extension system imports these two pure-Python packages
  local dst="$1/$VER/scripts/modules"
  mkdir -p "$ROOT/pydeps" && cd "$ROOT/pydeps" || return 1
  [ -d attrs-src ] || git clone -q --depth 1 https://github.com/python-attrs/attrs.git attrs-src
  [ -d cattrs-src ] || git clone -q --depth 1 https://github.com/python-attrs/cattrs.git cattrs-src
  cp -r attrs-src/src/attr attrs-src/src/attrs cattrs-src/src/cattrs cattrs-src/src/cattr "$dst/"
  cd "$ROOT" || return 1
}

if [ ! -x blender-install/blender ]; then
  configure -DWITH_HEADLESS=ON -DCMAKE_INSTALL_PREFIX="$ROOT/blender-install" > configure.log 2>&1 || { tail -20 configure.log; exit 1; }
  nice -n 5 ninja -C blender-build -j"$J" install > ninja.log 2>&1 || { grep -A8 FAILED ninja.log | head -40; exit 1; }
  finish "$ROOT/blender-install"
fi

# The windowed copy reuses the same build directory: only the window system,
# the interface and the icon data are recompiled (about 25 minutes).
if [ ! -x blender-install-gui/blender ]; then
  configure -DWITH_HEADLESS=OFF -DWITH_GHOST_SDL=ON -DCMAKE_INSTALL_PREFIX="$ROOT/blender-install-gui" > configure_gui.log 2>&1 || { tail -20 configure_gui.log; exit 1; }
  nice -n 5 ninja -C blender-build -j"$J" install > ninja_gui.log 2>&1 || { grep -A8 FAILED ninja_gui.log | head -40; exit 1; }
  finish "$ROOT/blender-install-gui"
fi
echo "done: $ROOT/blender-install/blender and $ROOT/blender-install-gui/blender"
