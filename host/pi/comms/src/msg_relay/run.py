#!/usr/bin/env python3
import os
import subprocess
import threading
import time

from msg_relay.constants import (
    DEFAULT_SERIAL_BAUD,
    DEFAULT_SERIAL_PORT,
    READ_TIMEOUT_SEC,
    RETRY_DELAY_SEC,
    DEFAULT_DEVICE_DB_PATH,
    DEFAULT_COMMS_LOG_PATH,
    DEFAULT_SERVICE_LOG_PATH,
    LOG_MAX_BYTES,
    LOG_TRIM_INTERVAL_SEC,
    WEBVIEW_HOST,
    WEBVIEW_PORT,
)
from msg_relay.functions import run_with_retries, conex, configure_comms_log_path
from msg_relay.webview import start_webview_server


def _track_runpy_access_once():
    tracker_script = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "..",
            "track_runpy_access.sh",
        )
    )
    if not os.path.isfile(tracker_script):
        return

    try:
        result = subprocess.run(
            [tracker_script],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        print(f"[runpy-access] tracker failed: {exc}", flush=True)
        return

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if stdout:
        print(f"[runpy-access] {stdout}", flush=True)
    if result.returncode != 0:
        print(
            f"[runpy-access] tracker exit={result.returncode} stderr={stderr}",
            flush=True,
        )


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


def _resolve_comms_log_path(configured_path: str) -> str:
    comms_log_path = os.path.abspath(configured_path)
    folder = os.path.dirname(comms_log_path) or "."
    os.makedirs(folder, exist_ok=True)
    return comms_log_path


def _start_webview_thread(
    db_path: str,
    comms_log_path: str,
    host: str,
    port: int,
):
    def _worker():
        start_webview_server(
            db_path=db_path,
            comms_log_path=comms_log_path,
            host=host,
            port=port,
        )

    thread = threading.Thread(
        target=_worker,
        name="msg-relay-webview",
        daemon=True,
    )
    thread.start()
    print(f"[webview] running at http://{host}:{port}", flush=True)
    return thread


def _trim_log_file_by_size(log_path: str, max_bytes: int):
    if max_bytes <= 0:
        return
    try:
        size = os.path.getsize(log_path)
    except FileNotFoundError:
        return
    except Exception as exc:
        print(f"[log] trim error: {exc}", flush=True)
        return

    if size <= max_bytes:
        return

    keep_bytes = max(1, int(max_bytes * 0.8))
    try:
        with open(log_path, "rb") as fp:
            if size > keep_bytes:
                fp.seek(-keep_bytes, os.SEEK_END)
            tail = fp.read()

        # Keep complete lines when possible.
        first_newline = tail.find(b"\n")
        if 0 <= first_newline < len(tail) - 1:
            tail = tail[first_newline + 1 :]

        with open(log_path, "wb") as fp:
            fp.write(tail)

        print(
            f"[log] trimmed by size old={size}B new={len(tail)}B cap={max_bytes}B",
            flush=True,
        )
    except Exception as exc:
        print(f"[log] trim error: {exc}", flush=True)

def _start_log_trimmer(log_path: str, max_bytes: int, interval_sec: float):
    if max_bytes <= 0:
        return

    def _worker():
        interval = max(interval_sec, 0.2)
        while True:
            _trim_log_file_by_size(log_path, max_bytes)
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
    log_max_bytes = int(os.getenv("MSG_RELAY_LOG_MAX_BYTES", str(LOG_MAX_BYTES)))
    if "MSG_RELAY_LOG_MAX_LINES" in os.environ and "MSG_RELAY_LOG_MAX_BYTES" not in os.environ:
        print(
            "[log] MSG_RELAY_LOG_MAX_LINES is deprecated; use MSG_RELAY_LOG_MAX_BYTES",
            flush=True,
        )
    log_trim_interval = float(
        os.getenv("MSG_RELAY_LOG_TRIM_INTERVAL_SEC", str(LOG_TRIM_INTERVAL_SEC))
    )
    _start_log_trimmer(
        log_path=log_path,
        max_bytes=log_max_bytes,
        interval_sec=log_trim_interval,
    )
    _track_runpy_access_once()
    comms_log_path = _resolve_comms_log_path(
        os.getenv("MSG_RELAY_COMMS_LOG_PATH", DEFAULT_COMMS_LOG_PATH)
    )
    configure_comms_log_path(comms_log_path)
    db_path = _resolve_db_path(
        os.getenv("ESPRESSIF_DEVICE_DB_PATH", DEFAULT_DEVICE_DB_PATH)
    )
    web_host = os.getenv("MSG_RELAY_WEB_HOST", WEBVIEW_HOST)
    web_port = int(os.getenv("MSG_RELAY_WEB_PORT", str(WEBVIEW_PORT)))
    _start_webview_thread(
        db_path=db_path,
        comms_log_path=comms_log_path,
        host=web_host,
        port=web_port,
    )
    conex(db_path)


if __name__ == "__main__":
    main()
