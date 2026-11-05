#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the chip reel.
#   demo/chips/make.sh data                 # the can, the layout by hand, the packers' inputs and results
#   demo/chips/make.sh pour                 # simulate the can pouring the chips
#   demo/chips/make.sh capture [seg ...]    # play the segments in Blender, save frames
#   demo/chips/make.sh edit                 # cards, captions, cross-fades -> out/chips.mp4
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CACHE=$ROOT/cache/chips
TAG=${TAG:-}          # a released snapshot if set; the working tree otherwise
ADDON=${BUCKET_BUILDER:-$ROOT/../bucket_builder}
mkdir -p "$CACHE"
case ${1:-} in
  data)
    python3 "$HERE/data.py" "$CACHE"
    cd "$ROOT/../bucket_builder"
    python3 -m pack pack "$CACHE/chips_in.3mf" --volume 380 284 380 --mode free --gap 3 --wall-gap 5 --names --referee \
      --max-density 0 --time 20 --out "$CACHE/good.3mf" > "$CACHE/good.log"
    python3 -m pack pack "$CACHE/chips_tumbled.3mf" --volume 380 284 380 --mode z --z-step 90 --gap 3 --wall-gap 5 --names --referee \
      --max-density 0 --time 30 --out "$CACHE/bad.3mf" > "$CACHE/bad.log" ;;
  pour)
    "$ROOT/lib/blender.sh" --background --python "$HERE/pour.py" -- "$CACHE/pour.npz" ;;
  capture)
    shift
    "$ROOT/lib/blender.sh" --addon bucket_builder="$ADDON" --window-fullscreen --python "$HERE/shots.py" -- "$@" ;;
  edit)
    python3 "$HERE/edit.py" ;;
  *)
    sed -n 3,7p "$0"; exit 1 ;;
esac
