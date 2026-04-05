#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
MODULE_PATH="${1:-firmware/esp/modules/traction_module}"
TARGET="${IDF_TARGET:-esp32c3}"

if [[ "$MODULE_PATH" = /* ]]; then
    PROJECT_DIR="$MODULE_PATH"
else
    PROJECT_DIR="$REPO_ROOT/$MODULE_PATH"
fi

ensure_idf() {
    if command -v idf.py >/dev/null 2>&1; then
        return
    fi

    if [[ -n "${IDF_PATH:-}" && -f "$IDF_PATH/export.sh" ]]; then
        # shellcheck disable=SC1090
        . "$IDF_PATH/export.sh" >/dev/null
        return
    fi

    if [[ -f "/opt/esp/esp-idf/export.sh" ]]; then
        # shellcheck disable=SC1091
        . "/opt/esp/esp-idf/export.sh" >/dev/null
        return
    fi

    if [[ -f "$HOME/esp/esp-idf/export.sh" ]]; then
        # shellcheck disable=SC1091
        . "$HOME/esp/esp-idf/export.sh" >/dev/null
        return
    fi

    echo "Error: idf.py not found and ESP-IDF export.sh could not be located."
    exit 1
}

remove_stale_build() {
    local build_dir="$PROJECT_DIR/build"
    local description_file="$build_dir/project_description.json"
    local expected_project expected_config current_project current_config

    [[ -f "$description_file" ]] || return

    expected_project=$(cd "$PROJECT_DIR" && pwd)
    expected_config="$expected_project/sdkconfig"
    current_project=$(grep -oE '"project_path":[[:space:]]*"[^"]+"' "$description_file" | cut -d'"' -f4 || true)
    current_config=$(grep -oE '"config_file":[[:space:]]*"[^"]+"' "$description_file" | cut -d'"' -f4 || true)

    if [[ "$current_project" != "$expected_project" || "$current_config" != "$expected_config" ]]; then
        echo "Removing stale build directory: $build_dir"
        rm -rf "$build_dir"
    fi
}

[[ -d "$PROJECT_DIR" ]] || {
    echo "Error: module directory not found: $PROJECT_DIR"
    exit 1
}

ensure_idf
cd "$PROJECT_DIR"
remove_stale_build

if [[ ! -f sdkconfig ]]; then
    echo "sdkconfig not found. Bootstrapping target $TARGET."
    idf.py set-target "$TARGET"
fi

idf.py build
