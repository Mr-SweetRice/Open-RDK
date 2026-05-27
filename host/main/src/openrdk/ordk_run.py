#!/usr/bin/env python3
import os
import subprocess
import threading
import time

from openrdk.constants import (
    DEFAULT_DEVICE_DB_PATH,
    DEFAULT_COMMS_LOG_PATH,
    DEFAULT_SERVICE_LOG_PATH,
    LOG_MAX_BYTES,
    LOG_TRIM_INTERVAL_SEC,
    WEBVIEW_HOST,
    WEBVIEW_PORT,
)
from openrdk.ordk_runtime import CommsRuntime


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


def _parse_env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on", "y"}:
        return True
    if value in {"0", "false", "no", "off", "n"}:
        return False
    print(
        f"[config] invalid boolean for {name}='{raw}', using default={default}",
        flush=True,
    )
    return bool(default)


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
        name="openrdk-log-trimmer",
        daemon=True,
    ).start()


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
    db_path = _resolve_db_path(
        os.getenv("ESPRESSIF_DEVICE_DB_PATH", DEFAULT_DEVICE_DB_PATH)
    )
    web_host = os.getenv("MSG_RELAY_WEB_HOST", WEBVIEW_HOST)
    web_port = int(os.getenv("MSG_RELAY_WEB_PORT", str(WEBVIEW_PORT)))
    enable_webview = _parse_env_flag("MSG_RELAY_ENABLE_WEBVIEW", default=True)
    enable_webview_updates = _parse_env_flag(
        "MSG_RELAY_ENABLE_WEBVIEW_UPDATES",
        default=True,
    )
    enable_mdns = _parse_env_flag("MSG_RELAY_ENABLE_MDNS", default=True)
    mdns_name = os.getenv("MSG_RELAY_MDNS_NAME", "rdk")
    enable_http_redirect = _parse_env_flag(
        "MSG_RELAY_ENABLE_HTTP_REDIRECT",
        default=True,
    )

    runtime = CommsRuntime(
        db_path=db_path,
        comms_log_path=comms_log_path,
        enable_webview=enable_webview,
        enable_webview_updates=enable_webview_updates,
        enable_mdns=enable_mdns,
        mdns_name=mdns_name,
        enable_http_redirect=enable_http_redirect,
        webview_host=web_host,
        webview_port=web_port,
        auto_start=True,
    )
    runtime.ensure_running()

    print(
        f"[runtime] running db={db_path} comms_log={comms_log_path} "
        f"webview={'on' if enable_webview else 'off'} "
        f"webview_updates={'on' if enable_webview_updates else 'off'} "
        f"mdns={'on' if enable_mdns else 'off'} "
        f"http_redirect={'on' if enable_http_redirect else 'off'}",
        flush=True,
    )
    if enable_webview:
        print(f"[webview] running at {runtime.webview_url}", flush=True)
        if enable_mdns:
            print(f"[webview] mDNS URL {runtime.mdns_url}", flush=True)

    try:
        while True:
            if not runtime.is_running:
                err = runtime.last_error
                if err is not None:
                    raise RuntimeError(f"runtime stopped: {err}") from err
                raise RuntimeError("runtime stopped")

            if enable_webview and not runtime.is_webview_running:
                err = runtime.last_webview_error
                if err is not None:
                    raise RuntimeError(f"webview stopped: {err}") from err
                raise RuntimeError("webview stopped")

            time.sleep(0.2)
    except KeyboardInterrupt:
        print("[runtime] shutdown requested", flush=True)
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
