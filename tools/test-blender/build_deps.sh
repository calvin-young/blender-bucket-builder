#!/bin/bash
# Builds the third-party libraries a "lite" Blender needs, from their GitHub
# source repositories, into $ROOT/deps.  Re-runnable: finished steps are
# skipped.  See README.md in this folder.
set -u
ROOT=${ROOT:-$HOME/build}
SRC=$ROOT/deps-src
PREFIX=$ROOT/deps
LOGS=$ROOT/logs
mkdir -p "$SRC" "$PREFIX" "$LOGS"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib64/pkgconfig:${PKG_CONFIG_PATH:-}"
export CMAKE_PREFIX_PATH="$PREFIX"
J=2
COMMON="-G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$PREFIX -DCMAKE_PREFIX_PATH=$PREFIX -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_INSTALL_LIBDIR=lib"

step() {  # step <name> <function>
  local name=$1; shift
  if [ -f "$LOGS/$name.done" ]; then echo "[skip] $name"; return 0; fi
  echo "[start] $name $(date +%H:%M:%S)"
  if ( set -e; "$@" ) > "$LOGS/$name.log" 2>&1; then
    touch "$LOGS/$name.done"; echo "[done]  $name $(date +%H:%M:%S)"
  else
    echo "[FAIL]  $name $(date +%H:%M:%S) -- see $LOGS/$name.log"; return 1
  fi
}

clone() {  # clone <repo> <ref> <dir>
  if [ ! -d "$SRC/$3/.git" ]; then
    rm -rf "$SRC/$3"
    git clone --depth 1 --branch "$2" "https://github.com/$1.git" "$SRC/$3"
  fi
}

cm() {  # cm <dir> [subdir-with-cmakelists] -- extra args
  local d=$1; shift
  local s="$SRC/$d"
  if [ "${1:-}" != "--" ]; then s="$SRC/$d/$1"; shift; fi
  shift
  rm -rf "$SRC/$d/_build"
  cmake -S "$s" -B "$SRC/$d/_build" $COMMON "$@"
  cmake --build "$SRC/$d/_build" -j$J
  cmake --install "$SRC/$d/_build"
}

b_zstd()   { clone facebook/zstd v1.5.7 zstd; cm zstd build/cmake -- -DZSTD_BUILD_PROGRAMS=OFF -DZSTD_BUILD_SHARED=OFF -DZSTD_BUILD_STATIC=ON -DZSTD_BUILD_TESTS=OFF; }
b_jpeg()   { clone libjpeg-turbo/libjpeg-turbo 3.1.0 jpeg; cm jpeg -- -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DWITH_TURBOJPEG=OFF; }
b_fmt()    { clone fmtlib/fmt 12.1.0 fmt; cm fmt -- -DFMT_TEST=OFF -DFMT_DOC=OFF -DBUILD_SHARED_LIBS=OFF; }
b_eigen()  {
  if [ ! -d "$SRC/eigen/.git" ]; then
    rm -rf "$SRC/eigen"; mkdir -p "$SRC/eigen"; cd "$SRC/eigen"; git init -q
    git remote add origin https://github.com/eigen-mirror/eigen.git
    git fetch --depth 1 origin 8a1083e9bf41b91fdea6546681f806154efdc25a || git fetch --depth 1 origin master
    git checkout -q FETCH_HEAD
  fi
  cm eigen -- -DEIGEN_BUILD_TESTING=OFF -DBUILD_TESTING=OFF -DEIGEN_BUILD_DOC=OFF -DEIGEN_BUILD_PKGCONFIG=ON -DEIGEN_BUILD_BLAS=OFF -DEIGEN_BUILD_LAPACK=OFF -DEIGEN_BUILD_DEMOS=OFF
}
b_deflate(){ clone ebiggers/libdeflate v1.18 deflate; cm deflate -- -DLIBDEFLATE_BUILD_SHARED_LIB=OFF -DLIBDEFLATE_BUILD_GZIP=OFF; }
b_openjph(){ clone aous72/OpenJPH 0.25.2 openjph; cm openjph -- -DOJPH_BUILD_EXECUTABLES=OFF -DOJPH_ENABLE_TIFF_SUPPORT=OFF -DBUILD_SHARED_LIBS=ON; }
b_imath()  { clone AcademySoftwareFoundation/Imath v3.2.2 imath; cm imath -- -DBUILD_TESTING=OFF -DPYTHON=OFF -DBUILD_SHARED_LIBS=ON -DIMATH_INSTALL_PKG_CONFIG=ON; }
b_openexr(){ clone AcademySoftwareFoundation/openexr v3.4.10 openexr; cm openexr -- -DBUILD_TESTING=OFF -DOPENEXR_BUILD_TOOLS=OFF -DOPENEXR_INSTALL_TOOLS=OFF -DOPENEXR_BUILD_EXAMPLES=OFF -DOPENEXR_INSTALL_EXAMPLES=OFF -DOPENEXR_BUILD_PYTHON=OFF -DOPENEXR_INSTALL_DOCS=OFF -DBUILD_SHARED_LIBS=ON -DOPENEXR_FORCE_INTERNAL_DEFLATE=OFF -DOPENEXR_FORCE_INTERNAL_OPENJPH=OFF -DBUILD_WEBSITE=OFF; }
b_tiff()   { clone libsdl-org/libtiff v4.7.1 tiff; cm tiff -- -Dtiff-tools=OFF -Dtiff-tests=OFF -Dtiff-contrib=OFF -Dtiff-docs=OFF -Djbig=OFF -Dlzma=OFF -Dwebp=OFF -Dlerc=OFF -Dzstd=OFF -Dlibdeflate=OFF -Djpeg12=OFF -Dtiff-opengl=OFF -Dcxx=OFF -DBUILD_SHARED_LIBS=ON; }
b_yaml()   { clone jbeder/yaml-cpp 0.8.0 yaml; cm yaml -- -DYAML_CPP_BUILD_TESTS=OFF -DYAML_CPP_BUILD_TOOLS=OFF -DYAML_CPP_BUILD_CONTRIB=OFF -DYAML_BUILD_SHARED_LIBS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5; }
b_pystring(){
  clone imageworks/pystring v1.1.4 pystring
  cd "$SRC/pystring"
  g++ -O2 -fPIC -c pystring.cpp -o pystring.o
  ar rcs libpystring.a pystring.o
  mkdir -p "$PREFIX/include/pystring" "$PREFIX/lib"
  cp pystring.h "$PREFIX/include/pystring/"; cp libpystring.a "$PREFIX/lib/"
}
b_minizip(){ clone zlib-ng/minizip-ng 4.0.10 minizip; cm minizip -- -DMZ_COMPAT=OFF -DMZ_BZIP2=OFF -DMZ_LZMA=OFF -DMZ_ZSTD=OFF -DMZ_OPENSSL=OFF -DMZ_LIBCOMP=OFF -DMZ_FETCH_LIBS=OFF -DMZ_ICONV=OFF -DMZ_LIBBSD=OFF -DMZ_PKCRYPT=OFF -DMZ_WZAES=OFF -DMZ_SIGNING=OFF -DMZ_BUILD_TESTS=OFF -DBUILD_SHARED_LIBS=OFF; }
b_ocio()   { clone AcademySoftwareFoundation/OpenColorIO v2.5.0 ocio; cm ocio -- -DOCIO_BUILD_APPS=OFF -DOCIO_BUILD_TESTS=OFF -DOCIO_BUILD_GPU_TESTS=OFF -DOCIO_BUILD_PYTHON=OFF -DOCIO_BUILD_DOCS=OFF -DOCIO_BUILD_JAVA=OFF -DOCIO_BUILD_NUKE=OFF -DOCIO_INSTALL_EXT_PACKAGES=NONE -DBUILD_SHARED_LIBS=ON -DOCIO_USE_OIIO_FOR_APPS=OFF -DOCIO_BUILD_OPENFX=OFF; }
b_robin()  { clone Tessil/robin-map v1.3.0 robin; cm robin -- ; }
b_oiio()   { clone AcademySoftwareFoundation/OpenImageIO v3.1.13.1 oiio; cm oiio -- -DUSE_PYTHON=OFF -DOIIO_BUILD_TOOLS=OFF -DOIIO_BUILD_TESTS=OFF -DBUILD_TESTING=OFF -DBUILD_DOCS=OFF -DINSTALL_DOCS=OFF -DINSTALL_FONTS=OFF -DBUILD_SHARED_LIBS=ON -DOIIO_INTERNALIZE_FMT=OFF -DUSE_QT=OFF -DUSE_OPENGL=OFF -DENABLE_OpenCV=OFF -DENABLE_FFmpeg=OFF -DENABLE_Freetype=OFF -DENABLE_GIF=OFF -DENABLE_Libheif=OFF -DENABLE_LibRaw=OFF -DENABLE_OpenJPEG=OFF -DENABLE_OpenVDB=OFF -DENABLE_Ptex=OFF -DENABLE_WebP=OFF -DENABLE_DCMTK=OFF -DENABLE_Nuke=OFF -DENABLE_R3DSDK=OFF -DENABLE_BZip2=OFF -DENABLE_TBB=OFF -DENABLE_libuhdr=OFF -DENABLE_JXL=OFF -DENABLE_openjph=OFF -DLINKSTATIC=OFF -DSTOP_ON_WARNING=OFF; }
b_tbb()    { clone uxlfoundation/oneTBB v2022.3.0 tbb; cm tbb -- -DTBB_TEST=OFF -DTBB_EXAMPLES=OFF -DTBB_STRICT=OFF -DTBBMALLOC_BUILD=ON -DTBB4PY_BUILD=OFF; }
b_epoxy()  {
  clone anholt/libepoxy 1.5.10 epoxy
  clone mesonbuild/meson 1.6.1 meson
  clone KhronosGroup/EGL-Registry main eglreg
  mkdir -p "$PREFIX/include"
  cp -r "$SRC/eglreg/api/EGL" "$SRC/eglreg/api/KHR" "$PREFIX/include/"
  cd "$SRC/epoxy"; rm -rf _build
  python3 "$SRC/meson/meson.py" setup _build --prefix "$PREFIX" --libdir lib --buildtype release \
     -Ddefault_library=static -Degl=yes -Dglx=no -Dx11=false -Dtests=false -Ddocs=false \
     -Dc_args="-I$PREFIX/include -fPIC"
  ninja -C _build -j$J
  ninja -C _build install
}

# SDL3 gives the windowed test build a window on a virtual X display.  The
# sandbox has no OpenGL headers; SDL only needs them to exist (it carries its
# own GL declarations), so empty stand-ins are enough.  GLX is not available,
# so SDL uses EGL: run the windowed Blender with SDL_VIDEO_FORCE_EGL=1.
b_sdl3()   {
  clone libsdl-org/SDL release-3.4.8 sdl3
  mkdir -p "$ROOT/stubs/GL"
  printf '/* stand-in: SDL only needs this header to exist */\n' > "$ROOT/stubs/GL/gl.h"
  cp "$ROOT/stubs/GL/gl.h" "$ROOT/stubs/GL/glext.h"
  cd "$SRC/sdl3"; rm -rf _build
  cmake -S . -B _build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_C_FLAGS="-I$ROOT/stubs" -DSDL_SHARED=ON -DSDL_STATIC=OFF \
    -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF -DSDL_X11=ON -DSDL_WAYLAND=OFF -DSDL_KMSDRM=OFF \
    -DSDL_VULKAN=OFF -DSDL_AUDIO=OFF -DSDL_CAMERA=OFF -DSDL_JOYSTICK=OFF -DSDL_HAPTIC=OFF \
    -DSDL_SENSOR=OFF -DSDL_HIDAPI=OFF -DSDL_POWER=OFF -DSDL_DIALOG=OFF -DSDL_GPU=OFF \
    -DSDL_RENDER=OFF -DSDL_X11_XTEST=OFF -DSDL_X11_XCURSOR=OFF -DSDL_X11_XINPUT=OFF \
    -DSDL_X11_XFIXES=OFF -DSDL_X11_XRANDR=OFF -DSDL_X11_XSCRNSAVER=OFF -DSDL_X11_XSHAPE=OFF \
    -DSDL_X11_XDBE=OFF -DSDL_X11_XSYNC=OFF -DSDL_DBUS=OFF -DSDL_IBUS=OFF -DSDL_LIBUDEV=OFF
  ninja -C _build -j$J
  cmake --install _build
}

FAILED=0
for s in zstd jpeg fmt eigen deflate openjph imath openexr tiff yaml pystring minizip tbb epoxy robin ocio oiio sdl3; do
  step "$s" "b_$s" || FAILED=1
done
echo "ALL DEPS FINISHED failed=$FAILED $(date +%H:%M:%S)"
