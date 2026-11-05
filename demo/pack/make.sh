#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the Bucket Builder packing reel.
#   demo/pack/make.sh pack                 # the density-limit comparison layouts (CLI, ~2 min)
#   demo/pack/make.sh capture [seg ...]    # play the segments in Blender, save frames
#   demo/pack/make.sh edit                 # cards, captions, cross-fades -> out/pack.mp4
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CACHE=$ROOT/cache/pack
BUCKET_BUILDER=${BUCKET_BUILDER:-$ROOT/../bucket_builder}
mkdir -p "$CACHE"
case ${1:-} in
  pack)
    python3 -c "import sys; sys.path.insert(0, '$ROOT/reel'); import parts; parts.write_bucket('$CACHE/unpacked.3mf', with_demo=False, scale=0.6)"
    # the density comparison: no limit vs. the 12 % default
    for d in 0 12; do
      (cd "$BUCKET_BUILDER" && python3 -m pack pack "$CACHE/unpacked.3mf" --volume 380 284 380 --mode free \
         --gap 3 --wall-gap 5 --max-density $d --time 90 --names --out "$CACHE/d$d.3mf") > "$CACHE/d$d.log" &
    done
    # the pattern demo: 150 copies of one curved part
    (cd "$BUCKET_BUILDER" && python3 - "$CACHE/chips_unpacked.3mf" <<'PY'
import sys, numpy as np
from pack import synthetic as S
from pack.meshio import write_3mf
m = S.pringle()
items = []
for i in range(150):
    T = np.eye(4); T[:3, 3] = [(i % 15) * 70, (i // 15) * 70, 0]
    items.append((m, T, 'chip-%d' % i))
write_3mf(sys.argv[1], items)
PY
     python3 -m pack pack "$CACHE/chips_unpacked.3mf" --volume 380 284 380 --mode free --gap 3 --wall-gap 5 \
       --time 30 --names --out "$CACHE/chips.3mf") > "$CACHE/chips.log" &
    wait; tail -n 9 "$CACHE"/d*.log ;;
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
