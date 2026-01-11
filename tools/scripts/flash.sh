#!/bin/bash
# flash.sh — Build, flash, and monitor ESP32-C3 firmware automatically
# Place this at the root of your repo

set -e

# Automatically detect serial port (first ttyACM* or ttyUSB* device)
PORT=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | head -n1)

if [ -z "$PORT" ]; then
    echo "Error: No serial device found. Please plug in your ESP board."
    exit 1
fi

echo "Using serial port: $PORT"

# Set target for ESP32-C3 (only first time; safe to run multiple times)
idf.py set-target esp32c3

# Build the project
idf.py build

# Flash and open monitor
idf.py -p "$PORT" flash monitor
