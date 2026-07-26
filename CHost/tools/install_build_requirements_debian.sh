#!/usr/bin/env sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SYSTEM_REQUIREMENTS="${PROJECT_DIR}/requirements-system-debian.txt"
PYTHON_REQUIREMENTS="${PROJECT_DIR}/requirements-build.txt"

if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-query >/dev/null 2>&1; then
    echo "This installer supports Debian-family systems only." >&2
    exit 2
fi

missing_packages=""
while IFS= read -r package; do
    case "${package}" in
        ""|\#*) continue ;;
    esac

    if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null |
        grep -q '^install ok installed$'; then
        missing_packages="${missing_packages} ${package}"
    fi
done < "${SYSTEM_REQUIREMENTS}"

if [ -n "${missing_packages}" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        privilege_command=""
    elif command -v sudo >/dev/null 2>&1; then
        privilege_command="sudo"
    else
        echo "Missing system packages:${missing_packages}" >&2
        echo "Run this installer as root or install sudo." >&2
        exit 2
    fi

    ${privilege_command} apt-get update
    # The values come from the repository-owned requirements file.
    # shellcheck disable=SC2086
    ${privilege_command} apt-get install -y ${missing_packages}
fi

if [ ! -x "${PROJECT_DIR}/.venv/bin/python" ]; then
    python3 -m venv "${PROJECT_DIR}/.venv"
fi

"${PROJECT_DIR}/.venv/bin/python" -m pip install -r "${PYTHON_REQUIREMENTS}"

echo "CHost build and sanitizer packages are installed."
echo "Compiler: $(clang-19 --version | sed -n '1p')"
