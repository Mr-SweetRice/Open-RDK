#!/usr/bin/env python3
import os
import threading
import time

from msg_relay.constants import (
    DEFAULT_SERIAL_BAUD,
    DEFAULT_SERIAL_PORT,
    READ_TIMEOUT_SEC,
    RETRY_DELAY_SEC,
    DEFAULT_DEVICE_DB_PATH,
    WEBVIEW_HOST,
    WEBVIEW_PORT,
    WEBVIEW_REFRESH_SECONDS,
    DEFAULT_SERVICE_LOG_PATH,
    LOG_MAX_LINES,
    LOG_TRIM_INTERVAL_SEC,
)
from msg_relay.functions import run_with_retries, conex
from msg_relay.webview import start_webview_server


def _resolve_db_path(configured_path: str) -> str:
    db_path = os.path.abspath(configured_path)
    folder = os.path.dirname(db_path) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.access(folder, os.W_OK):
        raise PermissionError(f"DB directory is not writable: {folder}")
    print(f"[db] Using DB path: {db_path}", flush=True)
    return db_path


def _resolve_log_path(configured_path: str) -> str:
    log_path = os.path.abspath(configured_path)
    folder = os.path.dirname(log_path) or "."
    os.makedirs(folder, exist_ok=True)
    return log_path


def _trim_log_file(log_path: str, max_lines: int):
    if max_lines <= 0:
        return
    try:
        with open(log_path, "r+", encoding="utf-8", errors="ignore") as fp:
            lines = fp.readlines()
            if len(lines) <= max_lines:
                return
            fp.seek(0)
            fp.writelines(lines[-max_lines:])
            fp.truncate()
    except FileNotFoundError:
        return
    except Exception as exc:
        print(f"[log] trim error: {exc}", flush=True)


def _start_log_trimmer(log_path: str, max_lines: int, interval_sec: float):
    if max_lines <= 0:
        return

    def _worker():
        interval = max(interval_sec, 0.2)
        while True:
            _trim_log_file(log_path, max_lines)
            time.sleep(interval)

    threading.Thread(
        target=_worker,
        name="msg-relay-log-trimmer",
        daemon=True,
    ).start()


def test():
    port = os.getenv("SERIAL_PORT", DEFAULT_SERIAL_PORT)
    baud = int(os.getenv("SERIAL_BAUD", str(DEFAULT_SERIAL_BAUD)))
    run_with_retries(
        port=port,
        baud=baud,
        timeout=READ_TIMEOUT_SEC,
        retry_delay=RETRY_DELAY_SEC,
    )


def main():
    log_path = _resolve_log_path(
        os.getenv("MSG_RELAY_LOG_PATH", DEFAULT_SERVICE_LOG_PATH)
    )
    log_max_lines = int(os.getenv("MSG_RELAY_LOG_MAX_LINES", str(LOG_MAX_LINES)))
    log_trim_interval = float(
        os.getenv("MSG_RELAY_LOG_TRIM_INTERVAL_SEC", str(LOG_TRIM_INTERVAL_SEC))
    )
    _start_log_trimmer(
        log_path=log_path,
        max_lines=log_max_lines,
        interval_sec=log_trim_interval,
    )

    db_path = _resolve_db_path(
        os.getenv("ESPRESSIF_DEVICE_DB_PATH", DEFAULT_DEVICE_DB_PATH)
    )
    web_host = os.getenv("ESPRESSIF_WEBVIEW_HOST", WEBVIEW_HOST)
    web_port = int(os.getenv("ESPRESSIF_WEBVIEW_PORT", str(WEBVIEW_PORT)))
    web_refresh = float(
        os.getenv("ESPRESSIF_WEBVIEW_REFRESH_SECONDS", str(WEBVIEW_REFRESH_SECONDS))
    )
    try:
        start_webview_server(
            db_path=db_path,
            host=web_host,
            port=web_port,
            refresh_seconds=web_refresh,
        )
        print(
            f"[webview] Live view available at http://{web_host}:{web_port}/",
            flush=True,
        )
    except Exception as exc:
        print(f"[webview] Failed to start ({web_host}:{web_port}): {exc}", flush=True)
    conex(db_path)


if __name__ == "__main__":
    main()
