#!/bin/bash
# Runs tests/blender_gui_test.py in a real Blender window on a virtual X display
# (Linux, needs Xvfb) and saves screenshots of what that display shows.
#
#   tests/gui_on_xvfb.sh /path/to/blender output_directory
#
# The add-on must be installed and enabled, and the splash screen switched off
# (Preferences > Interface > Splash Screen), or the first simulated key press
# only closes the splash.
set -u
BLENDER=${1:?path to the blender executable}
OUT=${2:?output directory}
HERE=$(cd "$(dirname "$0")" && pwd)
FB=$(mktemp -d)
DISP=:$((90 + RANDOM % 9))
mkdir -p "$OUT"
Xvfb $DISP -screen 0 1600x1000x24 -fbdir "$FB" -nolisten tcp > "$FB/xvfb.log" 2>&1 &
XPID=$!
sleep 2
DISPLAY=$DISP BUILD_CHECK_XVFB_FB="$FB/Xvfb_screen0" \
    "$BLENDER" --enable-event-simulate --python "$HERE/blender_gui_test.py" -- "$OUT"
STATUS=$?
kill $XPID 2>/dev/null
rm -rf "$FB"
exit $STATUS
