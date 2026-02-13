#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

TRACKER_SCRIPT="$REPO_ROOT/host/pi/comms/track_runpy_access.sh"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$UNIT_DIR/runpy-access.service"
TIMER_FILE="$UNIT_DIR/runpy-access.timer"
INTERVAL_SEC="${RUNPY_ACCESS_INTERVAL_SEC:-300}"
LOG_MAX_BYTES="${RUNPY_ACCESS_LOG_MAX_BYTES:-1048576}"

if ! [[ "$INTERVAL_SEC" =~ ^[0-9]+$ ]] || ((INTERVAL_SEC < 60)); then
  INTERVAL_SEC=300
fi
if ! [[ "$LOG_MAX_BYTES" =~ ^[0-9]+$ ]] || ((LOG_MAX_BYTES < 4096)); then
  LOG_MAX_BYTES=1048576
fi

if [[ ! -x "$TRACKER_SCRIPT" ]]; then
  chmod +x "$TRACKER_SCRIPT"
fi

mkdir -p "$UNIT_DIR"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Track run.py access status snapshots

[Service]
Type=oneshot
Environment=RUNPY_ACCESS_LOG_MAX_BYTES=$LOG_MAX_BYTES
ExecStart=$TRACKER_SCRIPT
EOF

cat > "$TIMER_FILE" <<EOF
[Unit]
Description=Periodic run.py access tracker

[Timer]
OnBootSec=30s
OnUnitActiveSec=${INTERVAL_SEC}s
AccuracySec=2s
Unit=runpy-access.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now runpy-access.timer
systemctl --user start runpy-access.service

echo "runpy-access timer started."
systemctl --user status runpy-access.timer --no-pager | sed -n '1,12p'
