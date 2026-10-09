#!/bin/bash
# Installs the add-on into a private Blender profile for the tests.
#
#   tools/test-blender/setup_profile.sh /path/to/blender
#
# Afterwards run every test with the same BLENDER_USER_RESOURCES.
set -eu
BLENDER=${1:?path to the blender executable}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export BLENDER_USER_RESOURCES=${BLENDER_USER_RESOURCES:-$HOME/build/blender-user}
mkdir -p "$BLENDER_USER_RESOURCES" "$REPO/dist"
find "$REPO/bucket_builder" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
"$BLENDER" --command extension build --source-dir "$REPO/bucket_builder" --output-dir "$REPO/dist"
"$BLENDER" --command extension remove bucket_builder > /dev/null 2>&1 || true
# (the newest package: dist may hold older versions too)
"$BLENDER" --command extension install-file -r user_default -e "$(ls -t "$REPO"/dist/bucket_builder-*.zip | head -n 1)"
# without this the first simulated key press in a window test only closes the splash
"$BLENDER" --background --python-expr "
import bpy
bpy.context.preferences.view.show_splash = False
bpy.ops.wm.save_userpref()
"
echo "profile ready: BLENDER_USER_RESOURCES=$BLENDER_USER_RESOURCES"
