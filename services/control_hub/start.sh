#!/usr/bin/env sh
set -eu
service_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
venv_python="$service_root/.venv/bin/python"
if [ ! -x "$venv_python" ]; then
  python3 -m venv "$service_root/.venv"
fi
"$venv_python" -m pip install --disable-pip-version-check -e "$service_root"
exec "$venv_python" -m control_hub_service "$@"
