#!/usr/bin/env bash
set -euo pipefail

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TIMER_UNIT="runpy-access.timer"
SERVICE_UNIT="runpy-access.service"
PURGE="${1:-}"

systemctl --user stop "$TIMER_UNIT" 2>/dev/null || true
systemctl --user disable "$TIMER_UNIT" 2>/dev/null || true
systemctl --user stop "$SERVICE_UNIT" 2>/dev/null || true

if [[ "$PURGE" == "--purge" ]]; then
  rm -f "$UNIT_DIR/$TIMER_UNIT" "$UNIT_DIR/$SERVICE_UNIT"
  systemctl --user daemon-reload
fi

timer_active="$(systemctl --user is-active "$TIMER_UNIT" 2>/dev/null || true)"
timer_enabled="$(systemctl --user is-enabled "$TIMER_UNIT" 2>/dev/null || true)"
service_active="$(systemctl --user is-active "$SERVICE_UNIT" 2>/dev/null || true)"

echo "runpy-access stop completed."
echo "  timer_active=$timer_active"
echo "  timer_enabled=$timer_enabled"
echo "  service_active=$service_active"
if [[ "$PURGE" == "--purge" ]]; then
  echo "  units_removed=yes"
else
  echo "  units_removed=no (use --purge to remove unit files)"
fi
