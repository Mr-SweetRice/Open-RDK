#!/usr/bin/env sh
set -eu
service_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
venv_python="$service_root/.venv/bin/python"
if [ ! -x "$venv_python" ]; then
  python3 -m venv "$service_root/.venv"
fi
"$venv_python" -m pip install --disable-pip-version-check -e "$service_root"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
escaped_root=$(printf '%s' "$service_root" | sed 's/ /\\x20/g')
unit_file="$unit_dir/openrdk-control-hub.service"
{
  printf '%s\n' '[Unit]'
  printf '%s\n' 'Description=Open-RDK independent control module service'
  printf '%s\n' 'After=default.target'
  printf '%s\n' '' '[Service]'
  printf 'WorkingDirectory=%s\n' "$escaped_root"
  printf 'ExecStart=%s/.venv/bin/python -m control_hub_service\n' "$escaped_root"
  printf '%s\n' 'Restart=on-failure' 'RestartSec=2'
  printf '%s\n' '' '[Install]'
  printf '%s\n' 'WantedBy=default.target'
} > "$unit_file"
systemctl --user daemon-reload
systemctl --user enable --now openrdk-control-hub.service
printf '%s\n' 'Servico instalado e iniciado em http://127.0.0.1:8770'
