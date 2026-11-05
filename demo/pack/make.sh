#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the Bucket Builder packing reel.
#   demo/pack/make.sh pack                 # every layout the reel shows (CLI, ~1 min)
#   demo/pack/make.sh pour                 # simulate the can pouring the chips (~1 min)
#   demo/pack/make.sh capture [seg ...]    # play the segments in Blender, save frames
#   demo/pack/make.sh hero                 # the title card's picture: a full bucket
#   demo/pack/make.sh edit                 # cards, captions, cross-fades -> out/pack.mp4
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CACHE=$ROOT/cache/pack
TAG=${TAG:-v1.5.1}    # a released snapshot, so a reel never films work in progress
ADDON=$CACHE/addon/$TAG
mkdir -p "$CACHE"
case ${1:-} in
  pack)
    # every layout the reel shows; each log holds its height, density and time
    if [[ ! -d $ADDON/bucket_builder ]]; then
      mkdir -p "$ADDON"
      git -C "$ROOT/.." archive "$TAG" bucket_builder | tar -x -C "$ADDON"
    fi
    python3 -c "import sys; sys.path.insert(0, '$ROOT/lib'); import parts; parts.write_bucket('$CACHE/unpacked.3mf', with_demo=False, scale=0.6)"
    python3 "$HERE/inputs.py" "$CACHE"
    run() {   # output name, input, then options
      local out=$1 in=$2; shift 2
      (cd "$ADDON/bucket_builder" && python3 -m pack pack "$CACHE/$in.3mf" --volume 380 284 380 --mode free \
         --gap 3 --wall-gap 5 --names --referee --out "$CACHE/$out.3mf" "$@") > "$CACHE/$out.log" &
    }
    run d0 unpacked --max-density 0 --time 60       # the density comparison
    run d12 unpacked --time 40
    run keep unpacked --keep-low --time 40          # even layers against parts kept low
    run dial0 ridges --quality 0 --max-density 0 --time 15          # the Part Quality dial
    run dial100 ridges --quality 100 --max-density 0 --time 15
    run fam fam_in --max-density 0 --time 60                           # all different meshes
    run wedges wedges_in --max-density 0 --time 45                     # every other copy turned over
    wait; tail -n 9 "$CACHE"/{d0,d12,keep,dial0,dial100,fam,wedges}.log ;;
  pour)
    # the can pouring chips, simulated once and saved for the capture to play back
    "$ROOT/lib/blender.sh" --background --python "$HERE/pour.py" -- "$CACHE/pour.npz" ;;
  capture)
    shift
    BUCKET_BUILDER=${BUCKET_BUILDER:-$ADDON/bucket_builder}
    # Full screen: a normal window can't reach 1920x1080 on a 1920x1080 desktop
    "$ROOT/lib/blender.sh" --addon bucket_builder="$BUCKET_BUILDER" \
      --window-fullscreen --python "$HERE/shots.py" -- "$@" ;;
  hero)
    # the title card's picture: a full bucket (735 parts), zoomed out
    (cd "$ADDON/bucket_builder" && python3 -m pack pack "$CACHE/unpacked.3mf" --copies 5 --volume 380 284 380 --mode free \
       --gap 3 --wall-gap 5 --names --referee --time 180 --effort quick --out "$CACHE/hero.3mf") > "$CACHE/hero.log"
    BUCKET_BUILDER=${BUCKET_BUILDER:-$ADDON/bucket_builder}
    "$ROOT/lib/blender.sh" --addon bucket_builder="$BUCKET_BUILDER" --window-fullscreen --python "$HERE/hero.py" ;;
  edit)
    python3 "$HERE/edit.py" ;;
  *)
    sed -n 3,9p "$0"; exit 1 ;;
esac
