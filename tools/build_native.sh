#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the compiled voxel steps (bucket_builder/pack/native/bbcore.c) for Linux
# and, with mingw-w64, for Windows.  No fused multiply-add: the results must
# match NumPy's to the last bit.
set -e
cd "$(dirname "$0")/../bucket_builder/pack/native"
FLAGS="-O2 -ffp-contract=off -fno-fast-math -std=c99 -Wall"
gcc $FLAGS -shared -fPIC -o bbcore.so bbcore.c -lm
echo "built bbcore.so"
if command -v x86_64-w64-mingw32-gcc >/dev/null; then
    x86_64-w64-mingw32-gcc $FLAGS -shared -static-libgcc -o bbcore.dll bbcore.c
    echo "built bbcore.dll"
fi
