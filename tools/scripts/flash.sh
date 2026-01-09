#!/usr/bin/env bash
set -euo pipefail

MODULE_PATH="${1:-firmware/esp/modules/color_module}"
PORT="${2:-/dev/ttyUSB0}"

cd "$MODULE_PATH"
. "$IDF_PATH/export.sh" >/dev/null 2>&1 || true

idf.py -p "$PORT" flash monitor
