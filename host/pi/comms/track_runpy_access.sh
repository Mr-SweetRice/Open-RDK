#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_PY_PATH="$BASE_DIR/src/msg_relay/run.py"
OUT_LOG="$BASE_DIR/runpy-access.log"
STATE_FILE="$BASE_DIR/.runpy-access.state"
NOW_UTC="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
MAX_LOG_BYTES="${RUNPY_ACCESS_LOG_MAX_BYTES:-1048576}"

if ! [[ "$MAX_LOG_BYTES" =~ ^[0-9]+$ ]]; then
  MAX_LOG_BYTES=1048576
fi
if ((MAX_LOG_BYTES < 4096)); then
  MAX_LOG_BYTES=4096
fi

trim_log_by_size() {
  local path="$1"
  local max_bytes="$2"
  [[ -f "$path" ]] || return 0

  local size
  size="$(wc -c < "$path" | tr -d ' ')"
  if ! [[ "$size" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  if ((size <= max_bytes)); then
    return 0
  fi

  local keep_bytes=$((max_bytes * 80 / 100))
  if ((keep_bytes < 1024)); then
    keep_bytes="$max_bytes"
  fi

  local tmp
  tmp="$(mktemp "${path}.trim.XXXXXX")"
  tail -c "$keep_bytes" "$path" > "$tmp" || true

  # If tail starts mid-line, drop the first partial line.
  local tmp2="${tmp}.body"
  tail -n +2 "$tmp" > "$tmp2" || true
  if [[ -s "$tmp2" ]]; then
    mv "$tmp2" "$tmp"
  else
    rm -f "$tmp2"
  fi

  mv "$tmp" "$path"
}

LAST_RUNNING_AT="never"
if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE" || true
  LAST_RUNNING_AT="${last_running_at:-never}"
fi

PROC_PATTERN="python(3)? .*msg_relay\\.run|${RUN_PY_PATH//\//\\/}"
mapfile -t PROC_LINES < <(pgrep -af "$PROC_PATTERN" || true)

RUNNING="no"
PIDS="none"
SERVICE_UNITS="none"
INVOCATION_IDS="none"
PROCESS_LIST="none"

if ((${#PROC_LINES[@]} > 0)); then
  RUNNING="yes"
  LAST_RUNNING_AT="$NOW_UTC"
  printf 'last_running_at=%s\n' "$LAST_RUNNING_AT" > "$STATE_FILE"

  declare -a PIDS_ARR=()
  declare -a UNIT_ARR=()
  declare -a INVOC_ARR=()

  for line in "${PROC_LINES[@]}"; do
    pid="${line%% *}"
    PIDS_ARR+=("$pid")

    if [[ -r "/proc/$pid/cgroup" ]]; then
      unit_name="$(awk -F/ '/\.service/ {for (i=NF; i>=1; i--) if ($i ~ /\.service$/) {print $i; exit}}' "/proc/$pid/cgroup")"
      if [[ -n "${unit_name:-}" ]]; then
        UNIT_ARR+=("$unit_name")
      fi
    fi

    if command -v systemctl >/dev/null 2>&1; then
      inv_id="$(systemctl --user status "$pid" --no-pager 2>/dev/null | awk '/^[[:space:]]*Invocation:/{print $2; exit}')"
      if [[ -n "${inv_id:-}" ]]; then
        INVOC_ARR+=("$inv_id")
      fi
    fi
  done

  PIDS="$(printf '%s\n' "${PIDS_ARR[@]}" | paste -sd, -)"
  PROCESS_LIST="$(printf '%s || ' "${PROC_LINES[@]}")"
  PROCESS_LIST="${PROCESS_LIST% || }"

  if ((${#UNIT_ARR[@]} > 0)); then
    SERVICE_UNITS="$(printf '%s\n' "${UNIT_ARR[@]}" | sort -u | paste -sd, -)"
  fi
  if ((${#INVOC_ARR[@]} > 0)); then
    INVOCATION_IDS="$(printf '%s\n' "${INVOC_ARR[@]}" | sort -u | paste -sd, -)"
  fi
fi

RUN_PY_ACCESSORS="none"
if command -v lsof >/dev/null 2>&1; then
  accessors="$(lsof "$RUN_PY_PATH" 2>/dev/null | awk 'NR>1 {print $1 ":" $2 ":" $3}' | paste -sd, -)"
  if [[ -n "${accessors:-}" ]]; then
    RUN_PY_ACCESSORS="$accessors"
  fi
fi

{
  echo "[$NOW_UTC] running=$RUNNING pids=$PIDS service_units=$SERVICE_UNITS invocation_ids=$INVOCATION_IDS last_running_at=$LAST_RUNNING_AT run_py_path=$RUN_PY_PATH accessors=$RUN_PY_ACCESSORS"
  echo "  process_cmds=$PROCESS_LIST"
} >> "$OUT_LOG"

trim_log_by_size "$OUT_LOG" "$MAX_LOG_BYTES"

echo "status written to: $OUT_LOG"
