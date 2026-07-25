#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
MODULE_NAME="${1:-}"
SKIP_BUILD="${2:-}"

if [[ ! "$MODULE_NAME" =~ ^[a-z0-9_]+$ ]]; then
    echo "Usage: $0 <module_name> [--skip-build]"
    exit 2
fi

MODULE_PATH="$REPO_ROOT/firmware/esp/modules/$MODULE_NAME"
BUILD_PATH="$MODULE_PATH/build"
ASSET_PATH="$REPO_ROOT/host/main/src/openrdk/firmware/$MODULE_NAME"

[[ -d "$MODULE_PATH" ]] || {
    echo "Error: firmware module not found: $MODULE_PATH"
    exit 1
}

if [[ "$SKIP_BUILD" != "--skip-build" ]]; then
    bash "$SCRIPT_DIR/build_firmware.sh" "$MODULE_PATH"
fi

SOURCES=(
    "$BUILD_PATH/bootloader/bootloader.bin"
    "$BUILD_PATH/partition_table/partition-table.bin"
    "$BUILD_PATH/$MODULE_NAME.bin"
)

for source in "${SOURCES[@]}"; do
    [[ -f "$source" ]] || {
        echo "Error: build artifact not found: $source"
        exit 1
    }
done

mkdir -p "$ASSET_PATH"
cp "${SOURCES[0]}" "$ASSET_PATH/bootloader.bin"
cp "${SOURCES[1]}" "$ASSET_PATH/partition-table.bin"
cp "${SOURCES[2]}" "$ASSET_PATH/$MODULE_NAME.bin"

echo "Packaged $MODULE_NAME firmware assets in $ASSET_PATH"
