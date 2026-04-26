import os
import queue
import threading
from datetime import datetime

from ..constants import (
    COMMS_LOG_WRITER_POLL_SEC,
    DEFAULT_COMMS_LOG_PATH,
    HOST_TIMESTAMP_FORMAT,
    HOST_TIMEZONE,
)
from . import _state


def _now_iso() -> str:
    now = datetime.now(HOST_TIMEZONE)
    return now.strftime(HOST_TIMESTAMP_FORMAT)


def configure_comms_log_path(path: str) -> str:
    resolved = os.path.abspath(path)
    folder = os.path.dirname(resolved) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(resolved):
        with open(resolved, "a", encoding="utf-8"):
            pass
    _state._COMMS_LOG_PATH = resolved
    _start_comms_log_writer()
    return resolved


def _ensure_comms_log_path() -> str:
    path = _state._COMMS_LOG_PATH or DEFAULT_COMMS_LOG_PATH
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "a", encoding="utf-8"):
            pass
    return path


def _enqueue_comms_log_line(line: str):
    text = str(line or "").strip()
    if not text:
        return
    try:
        _state._COMMS_LOG_QUEUE.put_nowait(text)
        return
    except queue.Full:
        pass
    try:
        _state._COMMS_LOG_QUEUE.get_nowait()
    except queue.Empty:
        pass
    try:
        _state._COMMS_LOG_QUEUE.put_nowait(text)
    except queue.Full:
        pass


def _comms_log_writer_loop():
    fp = None
    opened_path = ""
    poll_sec = max(0.01, float(COMMS_LOG_WRITER_POLL_SEC))
    try:
        while not _state._COMMS_LOG_WRITER_STOP.is_set():
            try:
                line = _state._COMMS_LOG_QUEUE.get(timeout=poll_sec)
            except queue.Empty:
                continue

            target_path = _ensure_comms_log_path()
            if fp is None or opened_path != target_path:
                if fp is not None:
                    try:
                        fp.close()
                    except Exception:
                        pass
                fp = open(target_path, "a", encoding="utf-8", buffering=1)
                opened_path = target_path

            fp.write(line)
            fp.write("\n")
    finally:
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass


def _start_comms_log_writer():
    with _state._COMMS_LOG_LOCK:
        if _state._COMMS_LOG_WRITER_THREAD and _state._COMMS_LOG_WRITER_THREAD.is_alive():
            return
        _state._COMMS_LOG_WRITER_STOP.clear()
        _state._COMMS_LOG_WRITER_THREAD = threading.Thread(
            target=_comms_log_writer_loop,
            name="openrdk-comms-log-writer",
            daemon=True,
        )
        _state._COMMS_LOG_WRITER_THREAD.start()
