#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE="${IDF_CONTAINER_IMAGE:-rdk-idf-dev:latest}"

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "[idf] Image '${IMAGE}' not found. Building from ${REPO_ROOT}/.devcontainer ..."
  docker build -t "${IMAGE}" "${REPO_ROOT}/.devcontainer"
fi

DOCKER_ARGS=(
  --rm
  --privileged
  --network host
  -v /dev:/dev
  -v "${REPO_ROOT}:/work"
  -w /work
)

# Allocate a TTY when invoked from an interactive shell.
if [[ -t 0 && -t 1 ]]; then
  DOCKER_ARGS+=(-it)
fi

if [[ $# -eq 0 ]]; then
  exec docker run "${DOCKER_ARGS[@]}" "${IMAGE}" bash
fi

exec docker run "${DOCKER_ARGS[@]}" "${IMAGE}" "$@"
