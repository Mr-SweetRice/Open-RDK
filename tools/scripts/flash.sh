#!/usr/bin/env bash
set -e

# --- Ensure ESP-IDF environment is exported (needed in scripts / fresh shells) ---
if ! command -v idf.py >/dev/null 2>&1; then
    if [ -n "${IDF_PATH:-}" ] && [ -f "$IDF_PATH/export.sh" ]; then
        # shellcheck disable=SC1090
        . "$IDF_PATH/export.sh" >/dev/null
    elif [ -f "/opt/esp/esp-idf/export.sh" ]; then
        # Common path in ESP-IDF devcontainer images
        # shellcheck disable=SC1091
        . "/opt/esp/esp-idf/export.sh" >/dev/null
    elif [ -f "$HOME/esp/esp-idf/export.sh" ]; then
        # Common local install path
        # shellcheck disable=SC1091
        . "$HOME/esp/esp-idf/export.sh" >/dev/null
    else
        echo "Error: idf.py not found and ESP-IDF export.sh could not be located."
        echo "Set IDF_PATH or install ESP-IDF (expected export.sh at \$IDF_PATH/export.sh, /opt/esp/esp-idf/export.sh, or \$HOME/esp/esp-idf/export.sh)."
        exit 1
    fi
fi

# Final sanity check
command -v idf.py >/dev/null 2>&1 || { echo "Error: idf.py still not found after exporting ESP-IDF."; exit 1; }
# ------------------------------------------------------------------------------

# Automatically detect serial port (first ttyACM* or ttyUSB* device)
PORT=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | head -n1)

if [ -z "${PORT:-}" ]; then
    echo "Error: No serial device found. Please plug in your ESP board."
    exit 1
fi

echo "Using serial port: $PORT"

# Set target for ESP32-C3 (safe to run multiple times)
idf.py set-target esp32c3

# Build the project
idf.py build

# Flash and open monitor
idf.py -p "$PORT" flash monitor
