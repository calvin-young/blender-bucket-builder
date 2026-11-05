#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the Bucket Builder build-check reel.
#   demo/check/make.sh capture [seg ...]    # play the segments in Blender, save frames
#   demo/check/make.sh edit                 # cards, captions, cross-fades -> out/check.mp4
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CACHE=$ROOT/cache/check
BUCKET_BUILDER=${BUCKET_BUILDER:-$ROOT/../bucket_builder}
mkdir -p "$CACHE"
case ${1:-} in
  capture)
    shift
    # Full screen: a normal window can't reach 1920x1080 on a 1920x1080 desktop
    "$ROOT/lib/blender.sh" --addon bucket_builder="$BUCKET_BUILDER" \
      --window-fullscreen --python "$HERE/shots.py" -- "$@" ;;
  edit)
    python3 "$HERE/edit.py" ;;
  *)
    sed -n 3,7p "$0"; exit 1 ;;
esac
