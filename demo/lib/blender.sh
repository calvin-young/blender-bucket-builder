#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Run Blender on the workstation's desktop with a private user folder (cache/blender),
# so reels never touch your own preferences or installed add-ons.
#   lib/blender.sh [--addon NAME=PATH ...] [blender args...]
# Each --addon links PATH (an extension package folder) into the private extensions repo
# as bl_ext.user_default.NAME.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
U=$ROOT/cache/blender
mkdir -p "$U/config" "$U/scripts" "$U/extensions/user_default"
while [[ ${1:-} == --addon ]]; do
  name=${2%%=*}; path=${2#*=}
  ln -sfn "$(realpath "$path")" "$U/extensions/user_default/$name"
  shift 2
done
export DISPLAY=${DISPLAY:-:0} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export BLENDER_USER_CONFIG=$U/config BLENDER_USER_SCRIPTS=$U/scripts BLENDER_USER_EXTENSIONS=$U/extensions
exec "${BLENDER:-$HOME/apps/blender/5.2.3/blender}" "$@"
