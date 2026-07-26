#!/usr/bin/env sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SANITIZER_CC="${ORDKC_SANITIZER_CC:-clang-19}"

if ! command -v "${SANITIZER_CC}" >/dev/null 2>&1; then
    echo "Required sanitizer compiler '${SANITIZER_CC}' is not installed." >&2
    echo "Run tools/install_build_requirements_debian.sh first." >&2
    exit 2
fi

UBSAN_BUILD_DIR="${PROJECT_DIR}/build/clang-ubsan"
cmake -S "${PROJECT_DIR}" -B "${UBSAN_BUILD_DIR}" \
  -DCMAKE_C_COMPILER="${SANITIZER_CC}" \
  -DORDKC_BUILD_TESTS=ON \
  -DORDKC_ENABLE_UBSAN=ON \
  -DCMAKE_BUILD_TYPE=Debug
cmake --build "${UBSAN_BUILD_DIR}" --parallel
UBSAN_OPTIONS=halt_on_error=1 \
ctest --test-dir "${UBSAN_BUILD_DIR}" --output-on-failure

ASAN_BUILD_DIR="${PROJECT_DIR}/build/clang-asan"
cmake -S "${PROJECT_DIR}" -B "${ASAN_BUILD_DIR}" \
  -DCMAKE_C_COMPILER="${SANITIZER_CC}" \
  -DORDKC_BUILD_TESTS=ON \
  -DORDKC_ENABLE_ASAN=ON \
  -DCMAKE_BUILD_TYPE=Debug
cmake --build "${ASAN_BUILD_DIR}" --parallel

if ! ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
    ctest --test-dir "${ASAN_BUILD_DIR}" --output-on-failure; then
    kernel_config="/boot/config-$(uname -r)"
    if [ -r "${kernel_config}" ] &&
        grep -q '^CONFIG_ARM64_VA_BITS=39$' "${kernel_config}"; then
        echo >&2
        echo "ASan is installed, but this 39-bit Raspberry Pi kernel cannot" >&2
        echo "map the sanitizer allocator's required address range." >&2
        echo "Run this mandatory validation on an ASan-capable runner." >&2
    fi
    exit 1
fi
