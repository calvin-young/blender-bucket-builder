#!/bin/bash
# Runs a window test on a virtual X display (Linux, needs Xvfb) and saves
# screenshots of what that display shows.
#
#   tests/gui_on_xvfb.sh /path/to/blender output_directory [test script] [arguments]
#
# The test script defaults to blender_gui_test.py.  The add-on must be
# installed and enabled, and the splash screen switched off (Preferences >
# Interface > Splash Screen), or the first simulated key press only closes
# the splash.
set -u
BLENDER=${1:?path to the blender executable}
OUT=${2:?output directory}
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=${3:-blender_gui_test.py}
shift 2
[ $# -gt 0 ] && shift
FB=$(mktemp -d)
DISP=:$((90 + RANDOM % 9))
mkdir -p "$OUT"
Xvfb $DISP -screen 0 1600x1000x24 -fbdir "$FB" -nolisten tcp > "$FB/xvfb.log" 2>&1 &
XPID=$!
sleep 2
DISPLAY=$DISP BUCKET_BUILDER_XVFB_FB="$FB/Xvfb_screen0" \
    "$BLENDER" --enable-event-simulate --python "$HERE/$SCRIPT" -- "$OUT" "$@"
STATUS=$?
kill $XPID 2>/dev/null
rm -rf "$FB"
exit $STATUS
