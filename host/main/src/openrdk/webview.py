import asyncio
import base64
import hashlib
import json
import math
import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from collections import deque

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .color_support import (
    PALETTE_DEFINITIONS,
    COLOR_MODULE_TYPE,
    default_device_profile,
    get_device_profile,
    merge_device_profile,
    parse_color_cal_line,
    parse_color_cfg_line,
    parse_color_data_line,
    parse_color_info_line,
    parse_color_patch_line,
    parse_color_selftest_line,
    set_device_profile,
    update_device_mode_profile,
)
from .constants import BAUD_RATE_MAX, BAUD_RATE_MIN, MESSAGE_TYPE_CMD, MESSAGE_TYPE_CONTROL
from .functions import (
    clear_devices_registry,
    get_active_message_type,
    get_device_message_type,
    get_device_traction_out_value,
    get_active_serial_baud,
    get_latest_ds_frame,
    get_latest_ls_frame,
    resume_keepalive_monitors,
    send_device_cmd_once,
    send_device_traction_command_once,
    set_active_message_type,
    set_device_message_type,
    send_device_traction_out_once,
    set_device_traction_out_value,
    set_active_serial_baud,
    set_device_name,
    set_device_telemetry_requested,
    stop_all_keepalive_monitors,
    supported_message_types,
    supported_serial_baud_rates,
)


def _ensure_file(path: str):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "a", encoding="utf-8"):
            pass


def _parse_comms_line(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    sender = payload.get("sender")
    raw_hex = payload.get("raw_hex")
    if not isinstance(sender, str) or not sender.strip():
        return None
    if not isinstance(raw_hex, str) or not raw_hex.strip():
        return None
    event = {
        "sender": sender.strip(),
        "raw_hex": raw_hex.strip().lower(),
    }
    device_serial = payload.get("device_serial")
    if isinstance(device_serial, str) and device_serial.strip():
        event["device_serial"] = device_serial.strip()
    direction = payload.get("direction")
    if isinstance(direction, str) and direction.strip():
        event["direction"] = direction.strip().lower()
    message_type = payload.get("message_type")
    if isinstance(message_type, str) and message_type.strip():
        event["message_type"] = message_type.strip().upper()
    message = payload.get("message")
    if isinstance(message, str):
        event["message"] = message
    seq = payload.get("seq")
    if isinstance(seq, int):
        event["seq"] = seq
    seq_abs = payload.get("seq_abs")
    if isinstance(seq_abs, int):
        event["seq_abs"] = seq_abs
    latency_ms = payload.get("latency_ms")
    if isinstance(latency_ms, (int, float)):
        event["latency_ms"] = float(latency_ms)
    retry = payload.get("retry")
    if isinstance(retry, bool):
        event["retry"] = retry
    retry_count = payload.get("retry_count")
    if isinstance(retry_count, int):
        event["retry_count"] = retry_count
    error_kind = payload.get("error_kind")
    if isinstance(error_kind, str) and error_kind.strip():
        event["error_kind"] = error_kind.strip()
    seq_valid = payload.get("seq_valid")
    if isinstance(seq_valid, bool):
        event["seq_valid"] = seq_valid
    expected_seq = payload.get("expected_seq")
    if isinstance(expected_seq, int):
        event["expected_seq"] = expected_seq
    length = payload.get("len")
    if isinstance(length, int):
        event["len"] = length
    phase = payload.get("phase")
    if isinstance(phase, str) and phase.strip():
        event["phase"] = phase.strip().lower()
    return event


def _normalize_baud_rate_for_api(value: int | str | None) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid baud rate")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid baud rate")
    if parsed < BAUD_RATE_MIN or parsed > BAUD_RATE_MAX:
        raise ValueError(f"baud rate must be between {BAUD_RATE_MIN} and {BAUD_RATE_MAX}")
    return parsed


def _online_traction_devices(db_path: str) -> list[dict]:
    devices = []
    for item in _load_devices(db_path):
        if str(item.get("status") or "").lower() != "online connected":
            continue
        if str(item.get("module_type") or "").lower() != "traction_module":
            continue
        if not item.get("serial_number"):
            continue
        devices.append(item)
    return devices


def _load_devices(db_path: str) -> list[dict]:
    try:
        with open(db_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return []

    devices = data.get("devices")
    if not isinstance(devices, list):
        return []
    out = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        serial = item.get("serial_number")
        if not isinstance(serial, str) or not serial.strip():
            continue
        module_type = item.get("module_type", item.get("firmware_module", ""))
        message_type = str(item.get("message_type", "CMD") or "CMD").strip().upper()
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = module_type
        out.append(
            {
                "serial_number": serial.strip(),
                "name": name,
                "status": item.get("status", ""),
                "module_type": module_type,
                "message_type": message_type,
                "traction_out_value": int(item.get("traction_out_value", 0) or 0),
                "link_status": item.get("link_status", ""),
                "device_node": item.get("device_node"),
                "last_event_at": item.get("last_event_at"),
                "last_link_check_at": item.get("last_link_check_at"),
                "error_count": int(item.get("error_count", 0) or 0),
                "last_error_kind": str(item.get("last_error_kind", "") or ""),
                "last_error_at": str(item.get("last_error_at", "") or ""),
                "telemetry_requested": bool(item.get("telemetry_requested", False)),
                "telemetry_active": bool(item.get("telemetry_active", False)),
            }
        )
    out.sort(key=lambda dev: dev.get("serial_number", ""))
    return out


class CommsStreamBroker:
    def __init__(self, comms_log_path: str, history_size: int = 5000, event_callback=None):
        self._comms_log_path = os.path.abspath(comms_log_path)
        self._history = deque(maxlen=max(100, history_size))
        self._history_lock = threading.Lock()
        self._subscribers: dict[int, queue.Queue] = {}
        self._subscribers_lock = threading.Lock()
        self._ingest_queue: queue.Queue = queue.Queue(maxsize=4096)
        self._line_counter = 0
        self._line_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._log_thread: threading.Thread | None = None
        self._stream_thread: threading.Thread | None = None
        self._next_subscriber_id = 1
        self._event_callback = event_callback

    def start(self):
        _ensure_file(self._comms_log_path)
        self._bootstrap_history()
        self._stop_event.clear()
        self._log_thread = threading.Thread(
            target=self._log_tail_worker,
            name="msg-relay-log-thread",
            daemon=True,
        )
        self._stream_thread = threading.Thread(
            target=self._ui_stream_worker,
            name="msg-relay-ui-stream-thread",
            daemon=True,
        )
        self._log_thread.start()
        self._stream_thread.start()

    def stop(self):
        self._stop_event.set()
        if self._log_thread and self._log_thread.is_alive():
            self._log_thread.join(timeout=2)
        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=2)

    def _next_line(self) -> int:
        with self._line_lock:
            self._line_counter += 1
            return self._line_counter

    def _bootstrap_history(self):
        with self._history_lock:
            self._history.clear()

        count = 0
        try:
            with open(self._comms_log_path, "r", encoding="utf-8", errors="ignore") as fp:
                for raw_line in fp:
                    parsed = _parse_comms_line(raw_line)
                    if parsed is None:
                        continue
                    count += 1
                    parsed["line"] = count
                    with self._history_lock:
                        self._history.append(parsed)
        except OSError:
            pass

        with self._line_lock:
            self._line_counter = count

    def _enqueue_event(self, event: dict):
        try:
            self._ingest_queue.put_nowait(event)
            return
        except queue.Full:
            pass
        try:
            self._ingest_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._ingest_queue.put_nowait(event)
        except queue.Full:
            pass

    def _log_tail_worker(self):
        _ensure_file(self._comms_log_path)
        try:
            fp = open(self._comms_log_path, "r", encoding="utf-8", errors="ignore")
        except OSError:
            return

        try:
            fp.seek(0, os.SEEK_END)
            while not self._stop_event.is_set():
                line = fp.readline()
                if not line:
                    try:
                        if os.path.getsize(self._comms_log_path) < fp.tell():
                            fp.seek(0)
                    except OSError:
                        pass
                    time.sleep(0.2)
                    continue

                parsed = _parse_comms_line(line)
                if parsed is None:
                    continue
                self._enqueue_event(parsed)
        finally:
            fp.close()

    def _ui_stream_worker(self):
        while not self._stop_event.is_set():
            try:
                event = self._ingest_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            event = dict(event)
            event["line"] = self._next_line()
            event.setdefault("direction", "unknown")
            with self._history_lock:
                self._history.append(event)

            if self._event_callback is not None:
                try:
                    self._event_callback(event)
                except Exception as exc:
                    print(f"[control-hub] event callback failed: {exc}", flush=True)

            dead: list[int] = []
            with self._subscribers_lock:
                subscribers = list(self._subscribers.items())
            for subscriber_id, subscriber_queue in subscribers:
                try:
                    subscriber_queue.put_nowait(event)
                except queue.Full:
                    try:
                        subscriber_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        subscriber_queue.put_nowait(event)
                    except queue.Full:
                        pass
                except Exception:
                    dead.append(subscriber_id)

            if dead:
                with self._subscribers_lock:
                    for subscriber_id in dead:
                        self._subscribers.pop(subscriber_id, None)

    def register(self) -> tuple[int, queue.Queue]:
        with self._subscribers_lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            subscriber_queue: queue.Queue = queue.Queue(maxsize=2048)
            self._subscribers[subscriber_id] = subscriber_queue
            return subscriber_id, subscriber_queue

    def unregister(self, subscriber_id: int):
        with self._subscribers_lock:
            self._subscribers.pop(subscriber_id, None)

    def history(self, limit: int, serial_filter: str | None = None) -> list[dict]:
        keep = max(1, min(int(limit), 5000))
        serial = (serial_filter or "").strip()
        with self._history_lock:
            events = list(self._history)

        if serial:
            events = [
                item
                for item in events
                if item.get("sender") == serial or item.get("device_serial") == serial
            ]
        if len(events) > keep:
            events = events[-keep:]
        return events


class ControlHubScriptStore:
    """Discovers Python scripts across one managed and many external directories."""

    MAX_BYTES = 50 * 1024 * 1024
    _SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,99}\.py$", re.IGNORECASE)
    _HASHED_REFERENCE = re.compile(r"^(?:managed|external):([0-9a-f]{20})$")

    def __init__(self, folder: str, directories_path: str | None = None):
        self.folder = os.path.abspath(folder)
        self.directories_path = os.path.abspath(
            directories_path
            or os.path.join(os.path.dirname(self.folder), "control_hub_script_directories.json")
        )
        self._lock = threading.RLock()
        os.makedirs(self.folder, exist_ok=True)

    @staticmethod
    def _canonical_folder(path: str) -> str:
        return os.path.realpath(os.path.abspath(path))

    @staticmethod
    def _path_key(path: str) -> str:
        return os.path.normcase(ControlHubScriptStore._canonical_folder(path))

    @staticmethod
    def _directory_id(path: str) -> str:
        digest = hashlib.sha256(
            ControlHubScriptStore._path_key(path).encode("utf-8")
        ).hexdigest()[:12]
        return f"directory:{digest}"

    @staticmethod
    def _hashed_reference(path: str, *, managed: bool) -> str:
        digest = hashlib.sha256(
            os.path.normcase(os.path.realpath(path)).encode("utf-8")
        ).hexdigest()[:20]
        return f"{'managed' if managed else 'external'}:{digest}"

    def _load_external_folders(self) -> list[str]:
        try:
            with open(self.directories_path, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return []
        raw_paths = payload.get("directories", []) if isinstance(payload, dict) else []
        if not isinstance(raw_paths, list):
            return []
        managed_key = self._path_key(self.folder)
        seen = {managed_key}
        folders: list[str] = []
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            folder = self._canonical_folder(raw_path.strip())
            key = self._path_key(folder)
            if key in seen:
                continue
            seen.add(key)
            folders.append(folder)
        return folders

    def _save_external_folders(self, folders: list[str]) -> None:
        os.makedirs(os.path.dirname(self.directories_path) or ".", exist_ok=True)
        temp_path = self.directories_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as fp:
            json.dump({"version": 1, "directories": folders}, fp, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.directories_path)

    def _directory_records(self) -> list[dict]:
        records = [{
            "id": "managed",
            "path": self._canonical_folder(self.folder),
            "label": "Diretório gerenciado",
            "managed": True,
        }]
        for folder in self._load_external_folders():
            records.append({
                "id": self._directory_id(folder),
                "path": folder,
                "label": os.path.basename(folder.rstrip(os.sep)) or folder,
                "managed": False,
            })
        return records

    def directories(self) -> list[dict]:
        with self._lock:
            records = self._directory_records()
            scripts = self._scan(records)
        counts: dict[str, int] = {}
        for script in scripts:
            directory_id = str(script["directory_id"])
            counts[directory_id] = counts.get(directory_id, 0) + 1
        return [{
            **record,
            "available": os.path.isdir(record["path"]),
            "script_count": counts.get(record["id"], 0),
        } for record in records]

    def add_directory(self, path: str) -> dict:
        raw_path = str(path or "").strip()
        if not raw_path or "\x00" in raw_path:
            raise ValueError("directory path is required")
        expanded = os.path.expandvars(os.path.expanduser(raw_path))
        if not os.path.isabs(expanded):
            raise ValueError("directory path must be absolute")
        folder = self._canonical_folder(expanded)
        if not os.path.isdir(folder):
            raise ValueError("script directory does not exist")
        try:
            os.listdir(folder)
        except OSError as exc:
            raise ValueError("script directory is not readable") from exc
        with self._lock:
            managed_key = self._path_key(self.folder)
            if self._path_key(folder) == managed_key:
                return next(record for record in self.directories() if record["managed"])
            folders = self._load_external_folders()
            existing = next(
                (item for item in folders if self._path_key(item) == self._path_key(folder)),
                None,
            )
            if existing is None:
                folders.append(folder)
                self._save_external_folders(folders)
            directory_id = self._directory_id(folder)
            return next(record for record in self.directories() if record["id"] == directory_id)

    def remove_directory(self, directory_id: str) -> None:
        requested = str(directory_id or "").strip()
        if requested == "managed":
            raise ValueError("the managed script directory cannot be removed")
        with self._lock:
            folders = self._load_external_folders()
            remaining = [folder for folder in folders if self._directory_id(folder) != requested]
            if len(remaining) == len(folders):
                raise KeyError(requested)
            self._save_external_folders(remaining)

    def resolve(self, reference: str, *, require_exists: bool = True) -> str:
        value = str(reference or "").strip()
        if self._SAFE_NAME.fullmatch(value) and os.path.basename(value) == value:
            path = os.path.abspath(os.path.join(self.folder, value))
            if os.path.dirname(path) != self.folder:
                raise ValueError("invalid Python filename")
        elif self._HASHED_REFERENCE.fullmatch(value):
            matches = [item for item in self.list() if item["reference"] == value]
            if len(matches) != 1:
                if not matches:
                    raise FileNotFoundError(value)
                raise ValueError("ambiguous Python script reference")
            path = str(matches[0]["path"])
        else:
            raise ValueError("invalid Python script reference")
        if require_exists and not os.path.isfile(path):
            raise FileNotFoundError(value)
        return path

    def save(self, filename: str, content: str) -> dict:
        path = self.resolve(filename, require_exists=False)
        try:
            encoded = str(content).encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("script must be valid UTF-8") from exc
        if len(encoded) > self.MAX_BYTES:
            raise ValueError("script exceeds the 50 MB limit")
        with self._lock:
            temp_path = path + ".upload"
            with open(temp_path, "wb") as fp:
                fp.write(encoded)
            os.replace(temp_path, path)
        return self.describe(path)

    @staticmethod
    def describe(
        path: str, *, directory_id: str = "managed", directory_path: str = "",
        directory_label: str = "Diretório gerenciado", managed: bool = True,
    ) -> dict:
        stat = os.stat(path)
        name = os.path.basename(path)
        return {
            "name": name,
            "reference": (
                name if managed and ControlHubScriptStore._SAFE_NAME.fullmatch(name)
                else ControlHubScriptStore._hashed_reference(path, managed=managed)
            ),
            "size": stat.st_size,
            "updated_at": stat.st_mtime,
            "directory_id": directory_id,
            "directory_path": directory_path,
            "directory_label": directory_label,
            "managed": managed,
            "path": os.path.realpath(path),
        }

    def _scan(self, records: list[dict]) -> list[dict]:
        scripts: list[dict] = []
        seen_files: set[str] = set()
        for record in records:
            folder = str(record["path"])
            if not os.path.isdir(folder):
                continue
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                if not name.lower().endswith(".py") or os.path.basename(name) != name:
                    continue
                path = os.path.realpath(os.path.join(folder, name))
                if not os.path.isfile(path):
                    continue
                file_key = os.path.normcase(path)
                if file_key in seen_files:
                    continue
                seen_files.add(file_key)
                try:
                    scripts.append(self.describe(
                        path,
                        directory_id=str(record["id"]),
                        directory_path=folder,
                        directory_label=str(record["label"]),
                        managed=bool(record["managed"]),
                    ))
                except OSError:
                    continue
        return sorted(
            scripts,
            key=lambda item: (
                0 if item["managed"] else 1,
                str(item["directory_label"]).lower(),
                str(item["name"]).lower(),
            ),
        )

    def list(self) -> list[dict]:
        with self._lock:
            return self._scan(self._directory_records())


class ControlHubExecutor:
    """Validates firmware EXEC events against host-owned menu profiles."""

    EVENT_DEDUP_TTL_SEC = 2.0
    EXECUTION_HISTORY_LIMIT = 200
    MODULE_SYNC_REFRESH_SEC = 30.0
    MODULE_SYNC_TRACTION_QUIET_SEC = 3.0

    def __init__(
        self, db_path: str, profiles_path: str, scripts_path: str | None = None,
        enable_module_sync: bool = True, script_directories_path: str | None = None,
        execution_log_path: str | None = None,
    ):
        self.db_path = os.path.abspath(db_path)
        self.profiles_path = os.path.abspath(profiles_path)
        self.execution_log_path = os.path.abspath(
            execution_log_path
            or os.path.join(os.path.dirname(self.profiles_path), "control_hub_execution_log.json")
        )
        self.script_store = ControlHubScriptStore(
            scripts_path or os.path.join(os.path.dirname(self.profiles_path), "control_hub_scripts"),
            directories_path=script_directories_path,
        )
        self._lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._history: dict[str, list[dict]] = self._load_execution_history()
        self._seen_lock = threading.Lock()
        self._seen: deque[tuple[tuple[str, int, str], float]] = deque(maxlen=256)
        self._status: dict[str, dict] = {}
        self._active: dict[str, dict] = {}
        self._module_sync_lock = threading.Lock()
        self._module_sync_running = False
        self._module_sync_last_check = 0.0
        self._module_sync_suspended_until = 0.0
        self._module_sync_signatures: dict[str, tuple[str, ...]] = {}
        self._module_sync_last_success: dict[str, float] = {}
        self._module_sync_targets: dict[str, tuple[tuple[str, str], ...]] = {}
        self._traction_locks: dict[str, threading.Lock] = {}
        self._traction_pending: dict[str, tuple[str, int, str, int]] = {}
        self._traction_workers: set[str] = set()
        self._module_sync_enabled = bool(enable_module_sync)

    @staticmethod
    def _decode(value: str) -> str:
        text = str(value or "").strip()
        padding = "=" * ((4 - len(text) % 4) % 4)
        return base64.urlsafe_b64decode((text + padding).encode("ascii")).decode("utf-8")

    def _load(self) -> dict:
        try:
            with open(self.profiles_path, "r", encoding="utf-8") as fp:
                value = json.load(fp)
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def set_profile(self, serial: str, profile: dict) -> None:
        with self._lock:
            data = self._load()
            data[str(serial)] = profile
            os.makedirs(os.path.dirname(self.profiles_path) or ".", exist_ok=True)
            temp_path = self.profiles_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.profiles_path)

    def get_profile(self, serial: str) -> dict:
        with self._lock:
            value = self._load().get(str(serial), {})
            return dict(value) if isinstance(value, dict) else {}

    def status(self, serial: str) -> dict | None:
        with self._lock:
            value = self._status.get(str(serial))
            return dict(value) if isinstance(value, dict) else None

    def _load_execution_history(self) -> dict[str, list[dict]]:
        try:
            with open(self.execution_log_path, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return {}
        devices = payload.get("devices", {}) if isinstance(payload, dict) else {}
        if not isinstance(devices, dict):
            return {}
        history: dict[str, list[dict]] = {}
        for serial, entries in devices.items():
            if not isinstance(serial, str) or not isinstance(entries, list):
                continue
            cleaned = [dict(item) for item in entries if isinstance(item, dict)]
            if cleaned:
                history[serial] = cleaned[-self.EXECUTION_HISTORY_LIMIT:]
        return history

    def _save_execution_history_locked(self) -> None:
        os.makedirs(os.path.dirname(self.execution_log_path) or ".", exist_ok=True)
        temp_path = self.execution_log_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as fp:
            json.dump(
                {"version": 1, "devices": self._history},
                fp, ensure_ascii=False, indent=2,
            )
        os.replace(temp_path, self.execution_log_path)

    def _append_execution_history(self, serial: str, status: dict) -> dict:
        entry = dict(status)
        entry["log_id"] = str(time.time_ns())
        entry["logged_at"] = float(
            entry.get("finished_at") or entry.get("started_at") or time.time()
        )
        started_at = entry.get("started_at")
        finished_at = entry.get("finished_at")
        if isinstance(started_at, (int, float)) and isinstance(finished_at, (int, float)):
            entry["duration_ms"] = max(0, round((finished_at - started_at) * 1000))
        key = str(serial)
        with self._history_lock:
            entries = self._history.setdefault(key, [])
            entries.append(entry)
            del entries[:-self.EXECUTION_HISTORY_LIMIT]
            try:
                self._save_execution_history_locked()
            except (OSError, TypeError, ValueError) as exc:
                print(f"[control-hub] execution log save failed: {exc}", flush=True)
        return dict(entry)

    def execution_history(self, serial: str, limit: int = 50) -> list[dict]:
        keep = max(1, min(int(limit), self.EXECUTION_HISTORY_LIMIT))
        with self._history_lock:
            entries = self._history.get(str(serial), [])[-keep:]
            return [dict(item) for item in reversed(entries)]

    def execution_history_revision(self, serial: str) -> str:
        with self._history_lock:
            entries = self._history.get(str(serial), [])
            return str(entries[-1].get("log_id") or "0") if entries else "0"

    def clear_execution_history(self, serial: str) -> None:
        with self._history_lock:
            self._history.pop(str(serial), None)
            self._save_execution_history_locked()

    @staticmethod
    def _oled_label(value: str) -> str:
        ascii_text = unicodedata.normalize("NFKD", str(value or ""))
        ascii_text = ascii_text.encode("ascii", errors="ignore").decode("ascii")
        cleaned = " ".join(ascii_text.replace(",", " ").split()).strip()
        encoded = cleaned.encode("utf-8")[:30]
        return encoded.decode("utf-8", errors="ignore") or "Modulo RDK"

    def _schedule_module_sync(self) -> None:
        if not self._module_sync_enabled:
            return
        now = time.monotonic()
        with self._module_sync_lock:
            if (self._module_sync_running or now < self._module_sync_suspended_until
                    or now - self._module_sync_last_check < 2.0):
                return
            self._module_sync_running = True
            self._module_sync_last_check = now
        threading.Thread(
            target=self._sync_connected_modules,
            name="control-hub-module-sync", daemon=True,
        ).start()

    def _is_duplicate_event(self, serial: str, sequence: int, message: str) -> bool:
        """Deduplicate retransmissions without merging distinct encoder values."""
        key = (str(serial), int(sequence), str(message))
        now = time.monotonic()
        with self._seen_lock:
            while (self._seen
                   and now - self._seen[0][1] > self.EVENT_DEDUP_TTL_SEC):
                self._seen.popleft()
            if any(seen_key == key for seen_key, _seen_at in self._seen):
                return True
            self._seen.append((key, now))
        return False

    def _sync_connected_modules(self) -> None:
        try:
            devices = _load_devices(self.db_path)
            connected = [
                item for item in devices
                if str(item.get("status") or "").lower() == "online connected"
            ]
            connected.sort(key=lambda item: str(item.get("serial_number") or ""))
            visible_connected = [
                item for item in connected
                if str(item.get("module_type") or "").lower() != "control_hub_module"
            ][:8]
            labels = tuple(
                self._oled_label(item.get("name") or item.get("module_type") or "Modulo RDK")
                for item in visible_connected
            )
            kinds = tuple(
                1 if str(item.get("module_type") or "").lower() == "traction_module" else 0
                for item in visible_connected
            )
            targets = tuple(
                (str(item.get("serial_number") or ""), str(item.get("module_type") or "").lower())
                for item in visible_connected
            )
            signature = tuple(f"{kind}:{label}" for kind, label in zip(kinds, labels))
            hubs = [
                item for item in connected
                if str(item.get("module_type") or "").lower() == "control_hub_module"
            ]
            for hub in hubs:
                serial = str(hub.get("serial_number") or "").strip()
                if not serial:
                    continue
                self._module_sync_targets[serial] = targets
                if (self._module_sync_signatures.get(serial) == signature
                        and time.monotonic() - self._module_sync_last_success.get(serial, 0.0)
                        < self.MODULE_SYNC_REFRESH_SEC):
                    continue
                if get_device_message_type(db_path=self.db_path, serial_number=serial) != "CMD":
                    continue
                commands = [
                    f"SET MODULE {index} {kinds[index]} {base64.urlsafe_b64encode(label.encode('utf-8')).decode('ascii').rstrip('=')}"
                    for index, label in enumerate(labels)
                ] + [f"SET MODULE COUNT {len(labels)}"]
                complete = True
                for command in commands:
                    result = send_device_cmd_once(
                        db_path=self.db_path, serial_number=serial,
                        command=command, timeout_sec=1.5,
                    )
                    if not result or not bool(result.get("ok")):
                        complete = False
                        break
                if complete:
                    self._module_sync_signatures[serial] = signature
                    self._module_sync_last_success[serial] = time.monotonic()
        except Exception as exc:
            print(f"[control-hub] module sync failed: {exc}", flush=True)
        finally:
            with self._module_sync_lock:
                self._module_sync_running = False

    def handle_event(self, event: dict) -> None:
        if str(event.get("direction") or "").lower() != "rx":
            return
        serial = str(event.get("device_serial") or event.get("sender") or "").strip()
        if str(event.get("message_type") or "").upper() != "CONTROL":
            device = next(
                (item for item in _load_devices(self.db_path)
                 if str(item.get("serial_number") or "") == serial),
                None,
            )
            if str((device or {}).get("module_type") or "").lower() == "control_hub_module":
                self._schedule_module_sync()
            return
        message = str(event.get("message") or "").strip()
        if message.startswith("TRACT,"):
            self._handle_traction_event(serial, event, message)
            return
        if message.startswith("STOP,"):
            try:
                slot = int(message.split(",", 1)[1])
            except (TypeError, ValueError):
                return
            self._request_stop(serial, slot)
            return
        if not message.startswith("EXEC,"):
            return
        parts = message.split(",", 3)
        if len(parts) != 4:
            return
        try:
            slot = int(parts[1])
            mode = int(parts[2])
            sequence = int(event.get("seq", -1))
            requested_value = self._decode(parts[3])
        except (TypeError, ValueError, UnicodeError):
            return
        if not serial or slot < 0 or slot >= 8 or mode not in (0, 1) or not requested_value:
            return
        device = next(
            (item for item in _load_devices(self.db_path)
             if item.get("serial_number") == serial),
            None,
        )
        if str((device or {}).get("module_type") or "").lower() != "control_hub_module":
            return
        profile = self.get_profile(serial)
        menu = profile.get("menu") if isinstance(profile.get("menu"), list) else []
        configured = menu[slot] if slot < len(menu) and isinstance(menu[slot], dict) else {}
        kind = str(configured.get("kind") or "command").strip().lower()
        expected_mode = 1 if kind == "python" else 0
        expected_value = str(
            (configured.get("script") if mode == 1 else configured.get("command")) or ""
        ).strip()
        if (not configured.get("enabled") or mode != expected_mode
                or expected_value != requested_value):
            rejected_status = {
                "state": "rejected", "slot": slot,
                "name": configured.get("name", ""), "kind": kind,
                "target": requested_value,
                "error": "execution request does not match the host profile",
                "finished_at": time.time(),
            }
            with self._lock:
                self._status[serial] = rejected_status
            self._append_execution_history(serial, rejected_status)
            self._notify_firmware(serial, slot, "FAILED")
            return
        script_path = None
        if mode == 1:
            try:
                script_path = self.script_store.resolve(requested_value)
            except (ValueError, FileNotFoundError) as exc:
                rejected_status = {
                    "state": "rejected", "slot": slot,
                    "name": configured.get("name", ""), "kind": kind,
                    "target": requested_value,
                    "error": f"Python script is unavailable: {exc}",
                    "finished_at": time.time(),
                }
                with self._lock:
                    self._status[serial] = rejected_status
                self._append_execution_history(serial, rejected_status)
                self._notify_firmware(serial, slot, "FAILED")
                return
        if self._is_duplicate_event(serial, sequence, message):
            return
        rejected_status = None
        with self._lock:
            if serial in self._active:
                rejected_status = {
                    "state": "rejected", "slot": slot,
                    "name": configured.get("name", ""), "kind": kind,
                    "target": requested_value,
                    "error": "another slot is already running", "finished_at": time.time(),
                }
                self._status[serial] = rejected_status
            else:
                terminal = str(configured.get("shell") or "auto").strip().lower()
                self._status[serial] = {
                    "state": "running", "slot": slot,
                    "name": configured.get("name", ""), "kind": kind,
                    "target": requested_value,
                    "shell": "python" if mode == 1 else terminal,
                    "started_at": time.time(),
                }
                self._active[serial] = {"slot": slot, "process": None, "stop_requested": False}
        if rejected_status is not None:
            self._append_execution_history(serial, rejected_status)
            threading.Thread(
                target=self._notify_firmware, args=(serial, slot, "FAILED"), daemon=True,
            ).start()
            return
        threading.Thread(
            target=self._run,
            args=(serial, slot, str(configured.get("name") or ""), kind,
                  requested_value, terminal, script_path),
            name=f"control-hub-execution-{slot}",
            daemon=True,
        ).start()

    def _handle_traction_event(self, hub_serial: str, event: dict, message: str) -> None:
        parts = message.split(",")
        if len(parts) != 4:
            return
        try:
            module_index = int(parts[1])
            action = parts[2].strip().upper()
            value = int(parts[3])
            sequence = int(event.get("seq", -1))
        except (TypeError, ValueError):
            return
        limits = {"POS": (-3600, 3600), "RPM": (-150, 150), "OUT": (-100, 100), "CLEAR": (0, 0)}
        if action not in limits or not (limits[action][0] <= value <= limits[action][1]):
            return
        with self._module_sync_lock:
            self._module_sync_suspended_until = max(
                self._module_sync_suspended_until,
                time.monotonic() + self.MODULE_SYNC_TRACTION_QUIET_SEC,
            )
        devices = _load_devices(self.db_path)
        hub = next((item for item in devices if str(item.get("serial_number")) == hub_serial), None)
        if str((hub or {}).get("module_type") or "").lower() != "control_hub_module":
            return
        targets = self._module_sync_targets.get(hub_serial, ())
        if module_index < 0 or module_index >= len(targets):
            self._notify_traction_firmware(hub_serial, module_index, action, "FAILED")
            return
        target_serial, target_type = targets[module_index]
        target = next((item for item in devices if str(item.get("serial_number")) == target_serial), None)
        if (target_type != "traction_module" or
                str((target or {}).get("status") or "").lower() != "online connected"):
            self._notify_traction_firmware(hub_serial, module_index, action, "FAILED")
            return
        if self._is_duplicate_event(hub_serial, sequence, message):
            return
        with self._lock:
            self._traction_locks.setdefault(target_serial, threading.Lock())
            self._traction_pending[target_serial] = (
                hub_serial, module_index, action, value,
            )
            self._status[hub_serial] = {
                "state": "traction_running", "module_index": module_index,
                "target_serial": target_serial, "action": action, "value": value,
                "started_at": time.time(),
            }
            if target_serial in self._traction_workers:
                return
            self._traction_workers.add(target_serial)
        threading.Thread(
            target=self._traction_worker,
            args=(target_serial,),
            name=f"control-hub-traction-{target_serial}", daemon=True,
        ).start()

    def _traction_worker(self, target_serial: str) -> None:
        while True:
            with self._lock:
                pending = self._traction_pending.pop(target_serial, None)
                if pending is None:
                    self._traction_workers.discard(target_serial)
                    return
            hub_serial, module_index, action, value = pending
            self._run_traction_control(
                hub_serial, module_index, target_serial, action, value,
            )

    def _run_traction_control(
        self, hub_serial: str, module_index: int, target_serial: str,
        action: str, value: int,
    ) -> None:
        ok = False
        error = ""
        lock = self._traction_locks[target_serial]
        try:
            with lock:
                current_type = get_device_message_type(self.db_path, target_serial)
                if action in ("POS", "RPM") and current_type == "CONTROL":
                    clear_result = send_device_traction_command_once(
                        db_path=self.db_path, serial_number=target_serial,
                        command="CLR OUT", timeout_sec=1.5,
                    )
                    if not clear_result or not clear_result.get("ok"):
                        raise RuntimeError(
                            str((clear_result or {}).get("error_kind") or "force_output_clear_failed")
                        )
                message_type = "CONTROL" if action in ("OUT", "CLEAR") else "CMD"
                if current_type != message_type:
                    set_device_message_type(self.db_path, target_serial, message_type)
                    time.sleep(0.05)
                if action == "POS":
                    commands = [f"SET PID POS ANGLE {value}", "START PID POS"]
                    results = [send_device_cmd_once(
                        db_path=self.db_path, serial_number=target_serial,
                        command=command, timeout_sec=1.5,
                    ) for command in commands]
                    ok = all(result and bool(result.get("ok")) for result in results)
                    if not ok:
                        failed = next(
                            (result for result in results if not result or not result.get("ok")), None,
                        )
                        error = str((failed or {}).get("error_kind") or "position_command_failed")
                elif action == "RPM":
                    result = send_device_cmd_once(
                        db_path=self.db_path, serial_number=target_serial,
                        command=f"SET PID RPM SP {value}", timeout_sec=1.5,
                    )
                    ok = bool(result and result.get("ok"))
                    error = "" if ok else str((result or {}).get("error_kind") or "speed_command_failed")
                elif action == "OUT":
                    result = send_device_traction_out_once(
                        db_path=self.db_path, serial_number=target_serial,
                        value=value, timeout_sec=1.5,
                    )
                    ok = bool(result and result.get("ok"))
                    error = "" if ok else str((result or {}).get("error_kind") or "force_output_failed")
                else:
                    result = send_device_traction_command_once(
                        db_path=self.db_path, serial_number=target_serial,
                        command="CLR OUT", timeout_sec=1.5,
                    )
                    ok = bool(result and result.get("ok"))
                    error = "" if ok else str((result or {}).get("error_kind") or "force_output_clear_failed")
                    if ok:
                        set_device_traction_out_value(self.db_path, target_serial, 0)
                        set_device_message_type(self.db_path, target_serial, "CMD")
        except Exception as exc:
            error = str(exc)
        with self._lock:
            has_newer_value = target_serial in self._traction_pending
            if not has_newer_value:
                self._status[hub_serial] = {
                    "state": "traction_completed" if ok else "traction_failed",
                    "module_index": module_index, "target_serial": target_serial,
                    "action": action, "value": value, "error": error,
                    "finished_at": time.time(),
                }
        if not has_newer_value:
            self._notify_traction_firmware(
                hub_serial, module_index, action, "DONE" if ok else "FAILED",
            )

    def _notify_traction_firmware(
        self, hub_serial: str, module_index: int, action: str, state: str,
    ) -> None:
        try:
            if get_device_message_type(self.db_path, hub_serial) != "CMD":
                set_device_message_type(self.db_path, hub_serial, "CMD")
                time.sleep(0.05)
            send_device_cmd_once(
                db_path=self.db_path, serial_number=hub_serial,
                command=f"TRACT STATE {module_index} {action} {state}", timeout_sec=1.5,
            )
        except Exception:
            pass

    @staticmethod
    def _command_argv(command: str, terminal: str) -> tuple[list[str], str]:
        selected = str(terminal or "auto").strip().lower()
        if selected == "auto":
            selected = "cmd" if os.name == "nt" else "sh"
        if selected == "cmd":
            if os.name != "nt":
                raise RuntimeError("CMD is only available on Windows")
            executable = os.environ.get("COMSPEC") or "cmd.exe"
            return [executable, "/d", "/s", "/c", command], "cmd"
        if selected == "powershell":
            executable = shutil.which("pwsh") or shutil.which("powershell")
            if not executable:
                raise RuntimeError("PowerShell was not found on this host")
            return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], "powershell"
        if selected == "sh":
            executable = shutil.which("sh")
            if not executable:
                raise RuntimeError("sh was not found on this host")
            return [executable, "-lc", command], "sh"
        raise RuntimeError(f"unsupported terminal: {terminal}")

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5, check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _request_stop(self, serial: str, slot: int) -> None:
        process = None
        notify_without_active = False
        stopped_status = None
        with self._lock:
            active = self._active.get(serial)
            if not active or active.get("slot") != slot:
                stopped_status = {
                    "state": "stopped", "slot": slot,
                    "kind": "stop", "target": "STOP",
                    "error": "stop acknowledged without an active host process",
                    "finished_at": time.time(),
                }
                self._status[serial] = stopped_status
                notify_without_active = True
            else:
                active["stop_requested"] = True
                process = active.get("process")
                current = dict(self._status.get(serial) or {})
                current["state"] = "stopping"
                self._status[serial] = current
        if notify_without_active:
            self._append_execution_history(serial, stopped_status or {})
            threading.Thread(
                target=self._notify_firmware, args=(serial, slot, "STOPPED"), daemon=True,
            ).start()
            return
        if process is not None:
            try:
                self._terminate_process(process)
            except Exception:
                pass

    def _notify_firmware(self, serial: str, slot: int, state: str) -> None:
        try:
            if get_device_message_type(db_path=self.db_path, serial_number=serial) != "CMD":
                set_device_message_type(
                    db_path=self.db_path, serial_number=serial, message_type="CMD",
                )
                time.sleep(0.05)
            send_device_cmd_once(
                db_path=self.db_path, serial_number=serial,
                command=f"RUN STATE {slot} {state}", timeout_sec=1.5,
            )
        except Exception:
            pass

    def _stop_all_traction_motors(self) -> dict:
        """Neutralize every connected traction mode once after a menu execution exits."""
        targets = [
            str(device.get("serial_number") or "").strip()
            for device in _load_devices(self.db_path)
            if str(device.get("module_type") or "").lower() == "traction_module"
            and str(device.get("status") or "").lower() == "online connected"
            and str(device.get("serial_number") or "").strip()
        ]
        stopped: list[str] = []
        failed: list[str] = []
        for target_serial in targets:
            with self._lock:
                motor_lock = self._traction_locks.setdefault(target_serial, threading.Lock())
                self._traction_pending.pop(target_serial, None)
            try:
                with motor_lock:
                    set_device_message_type(self.db_path, target_serial, "CONTROL")
                    time.sleep(0.05)
                    immediate = send_device_traction_out_once(
                        db_path=self.db_path, serial_number=target_serial,
                        value=0, timeout_sec=1.5,
                    )
                    set_device_message_type(self.db_path, target_serial, "CMD")
                    time.sleep(0.05)
                    pid_results = [send_device_cmd_once(
                        db_path=self.db_path, serial_number=target_serial,
                        command=command, timeout_sec=1.5,
                    ) for command in (
                        "STOP PID POS", "STOP PID POS SINE", "SET PID RPM SP 0",
                    )]
                    set_device_message_type(self.db_path, target_serial, "CONTROL")
                    time.sleep(0.05)
                    released = send_device_traction_command_once(
                        db_path=self.db_path, serial_number=target_serial,
                        command="CLR OUT", timeout_sec=1.5,
                    )
                    set_device_traction_out_value(self.db_path, target_serial, 0)
                    set_device_message_type(self.db_path, target_serial, "CMD")
                if (immediate and immediate.get("ok") and released and released.get("ok")
                        and all(result and result.get("ok") for result in pid_results)):
                    stopped.append(target_serial)
                else:
                    failed.append(target_serial)
            except Exception:
                failed.append(target_serial)
                try:
                    set_device_traction_out_value(self.db_path, target_serial, 0)
                    set_device_message_type(self.db_path, target_serial, "CMD")
                except Exception:
                    pass
        return {"stopped": stopped, "failed": failed}

    def _run(
        self, serial: str, slot: int, name: str, kind: str,
        value: str, terminal: str, script_path: str | None = None,
    ) -> None:
        process = None
        stop_requested = False
        started_at = time.time()
        resolved_terminal = "python" if kind == "python" else terminal
        try:
            if kind == "python":
                if not script_path:
                    script_path = self.script_store.resolve(value)
                argv = [sys.executable, "-u", script_path]
            else:
                argv, resolved_terminal = self._command_argv(value, terminal)
            popen_options = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                             "text": True, "shell": False}
            if os.name == "nt":
                popen_options["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                popen_options["start_new_session"] = True
            process = subprocess.Popen(argv, **popen_options)
            with self._lock:
                active = self._active.get(serial)
                if active is None or active.get("slot") != slot:
                    self._terminate_process(process)
                else:
                    active["process"] = process
                    stop_requested = bool(active.get("stop_requested"))
            if stop_requested:
                self._terminate_process(process)
            self._notify_firmware(serial, slot, "RUNNING")
            stdout, stderr = process.communicate(timeout=30)
            with self._lock:
                stopped = bool((self._active.get(serial) or {}).get("stop_requested"))
            status = {
                "state": "stopped" if stopped else ("completed" if process.returncode == 0 else "failed"),
                "slot": slot, "name": name, "kind": kind, "shell": resolved_terminal,
                "target": value, "started_at": started_at,
                "returncode": process.returncode,
                "stdout": (stdout or "")[-4000:],
                "stderr": (stderr or "")[-4000:],
                "finished_at": time.time(),
            }
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                self._terminate_process(process)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except Exception:
                    stdout, stderr = exc.stdout, exc.stderr
            status = {
                "state": "timeout", "slot": slot, "name": name, "kind": kind,
                "shell": resolved_terminal, "target": value, "started_at": started_at,
                "stdout": str(stdout or "")[-4000:],
                "stderr": str(stderr or "")[-4000:],
                "finished_at": time.time(),
            }
        except Exception as exc:
            status = {
                "state": "failed", "slot": slot, "name": name, "kind": kind,
                "shell": resolved_terminal, "target": value, "started_at": started_at,
                "error": str(exc), "finished_at": time.time(),
            }
        status["motor_stop"] = self._stop_all_traction_motors()
        with self._lock:
            self._status[serial] = status
            self._active.pop(serial, None)
        self._append_execution_history(serial, status)
        firmware_state = "STOPPED" if status["state"] == "stopped" else (
            "DONE" if status["state"] == "completed" else "FAILED"
        )
        self._notify_firmware(serial, slot, firmware_state)


class DeviceNameUpdatePayload(BaseModel):
    name: str = ""


class MessageTypeUpdatePayload(BaseModel):
    message_type: str = ""


class BaudRateUpdatePayload(BaseModel):
    baud_rate: int


class TractionOutValueUpdatePayload(BaseModel):
    value: int


class TractionOutSendPayload(BaseModel):
    value: int | None = None


class CmdSendPayload(BaseModel):
    command: str = ""


class LineSensorConfigPayload(BaseModel):
    track_type: int | None = None
    digital_threshold: float | None = None
    detect_threshold: float | None = None
    calibration_time_ms: int | None = None
    save: bool = True


class LineSensorCalibrationStartPayload(BaseModel):
    track_type: int | None = None
    digital_threshold: float | None = None
    detect_threshold: float | None = None
    calibration_time_ms: int | None = None
    save_config: bool = True


class LineSensorCalibrationPayload(BaseModel):
    min_raw: list[int]
    max_raw: list[int]
    save: bool = True


class DistanceSensorConfigPayload(BaseModel):
    name: str | None = None
    sample_ms: int | None = None
    max_mm: int | None = None
    filter_window: int | None = None
    save: bool = True


class ControlHubConfigPayload(BaseModel):
    device_name: str | None = None
    menu: list[dict] | None = None
    save: bool = True


class ControlHubScriptUploadPayload(BaseModel):
    filename: str
    content: str


class ControlHubScriptDirectoryPayload(BaseModel):
    path: str = ""


class ControlHubScriptDirectoryRemovePayload(BaseModel):
    directory_id: str = ""


class ControlHubServoPayload(BaseModel):
    channel: int
    angle: int


class ControlHubGpioPayload(BaseModel):
    pin: int
    value: int


def _parse_control_hub_imu_response(response: str) -> dict:
    parts = [part.strip() for part in str(response or "").split(",")]
    if len(parts) != 10 or parts[0] != "IMU":
        raise ValueError("unexpected control hub IMU response")
    try:
        numeric = [float(value) for value in parts[1:7]]
        calibrated = int(parts[7]) == 1
        calibrating = int(parts[8]) == 1
        progress = max(0, min(100, int(parts[9])))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid control hub IMU response") from exc
    if not all(math.isfinite(value) for value in numeric):
        raise ValueError("invalid control hub IMU response")
    return {
        "roll_deg": numeric[0], "pitch_deg": numeric[1], "yaw_deg": numeric[2],
        "gyro_x_dps": numeric[3], "gyro_y_dps": numeric[4], "gyro_z_dps": numeric[5],
        "calibrated": calibrated, "calibrating": calibrating,
        "calibration_progress": progress,
    }


class ColorConfigPayload(BaseModel):
    sensor_name: str | None = None
    palette_mode: int | None = None
    sample_period_ms: int | None = None
    led_mode: int | None = None
    gain_mode: int | None = None
    gain: int | None = None
    integration_ms: int | None = None
    classifier: int | None = None
    confidence_milli: int | None = None
    target_clear: int | None = None
    patch_sample_count: int | None = None
    save: bool = False


class ColorSavePayload(BaseModel):
    persist_cfg: bool = False
    persist_cal: bool = False


class ColorProfilePayload(BaseModel):
    profile: dict
    apply_to_firmware: bool = False


class ColorCalibrationTargetPayload(BaseModel):
    target: str | int


def create_webview_app(
    db_path: str,
    comms_log_path: str,
    enable_realtime_stream: bool = True,
) -> FastAPI:
    app = FastAPI(title="RDK Msg Relay Webview")
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_new")
    control_hub_profiles_path = os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "control_hub_commands.json"
    )
    control_hub_scripts_path = os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "control_hub_scripts"
    )
    control_hub_script_directories_path = os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "control_hub_script_directories.json"
    )
    control_hub_execution_log_path = os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "control_hub_execution_log.json"
    )
    control_hub_executor = ControlHubExecutor(
        db_path=db_path,
        profiles_path=control_hub_profiles_path,
        scripts_path=control_hub_scripts_path,
        script_directories_path=control_hub_script_directories_path,
        execution_log_path=control_hub_execution_log_path,
    )
    control_hub_script_store = control_hub_executor.script_store
    broker = CommsStreamBroker(
        comms_log_path=comms_log_path,
        event_callback=control_hub_executor.handle_event,
    ) if enable_realtime_stream else None
    color_command_locks: dict[str, asyncio.Lock] = {}
    line_sensor_command_locks: dict[str, asyncio.Lock] = {}
    distance_command_locks: dict[str, asyncio.Lock] = {}
    control_hub_command_locks: dict[str, asyncio.Lock] = {}

    app.state.db_path = db_path
    app.state.comms_log_path = comms_log_path
    app.state.broker = broker
    app.state.enable_realtime_stream = bool(enable_realtime_stream)

    def _color_device_or_error(serial_number: str) -> dict:
        device = next(
            (
                item for item in _load_devices(db_path)
                if item.get("serial_number") == serial_number
            ),
            None,
        )
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        if str(device.get("module_type") or "").lower() != COLOR_MODULE_TYPE:
            raise HTTPException(status_code=400, detail="device is not a color module")
        if str(device.get("status") or "").lower() != "online connected":
            raise HTTPException(status_code=409, detail="device is not online")
        return device

    def _color_command_lock(serial_number: str) -> asyncio.Lock:
        lock = color_command_locks.get(serial_number)
        if lock is None:
            lock = asyncio.Lock()
            color_command_locks[serial_number] = lock
        return lock

    async def _send_color_cmds(
        serial_number: str,
        commands: list[str],
        timeout_sec: float = 2.0,
    ) -> list[dict]:
        if not commands:
            return []

        async with _color_command_lock(serial_number):
            _color_device_or_error(serial_number)
            previous_type = get_device_message_type(db_path=db_path, serial_number=serial_number) or "CMD"
            if previous_type != "CMD":
                if previous_type == "TELEMETRY":
                    await asyncio.to_thread(
                        set_device_telemetry_requested,
                        db_path=db_path,
                        serial_number=serial_number,
                        enabled=False,
                    )
                    await asyncio.sleep(0.25)
                await asyncio.to_thread(
                    set_device_message_type,
                    db_path=db_path,
                    serial_number=serial_number,
                    message_type="CMD",
                )
                await asyncio.sleep(0.08)

            results: list[dict] = []
            try:
                for command in commands:
                    result = await asyncio.to_thread(
                        send_device_cmd_once,
                        db_path=db_path,
                        serial_number=serial_number,
                        command=command,
                        timeout_sec=timeout_sec,
                    )
                    if result is None:
                        raise HTTPException(status_code=404, detail="device not found")
                    results.append(result)
                    if not bool(result.get("ok")):
                        detail = str(result.get("error_kind") or result.get("response") or "command failed")
                        status_code = 504 if detail == "cmd_send_timeout" else 409
                        raise HTTPException(status_code=status_code, detail=f"{command}: {detail}")
            finally:
                if previous_type != "CMD":
                    await asyncio.to_thread(
                        set_device_message_type,
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type=previous_type,
                    )
                    if previous_type == "TELEMETRY":
                        await asyncio.to_thread(
                            set_device_telemetry_requested,
                            db_path=db_path,
                            serial_number=serial_number,
                            enabled=True,
                        )

            return results

    def _response_for(results: list[dict], prefix: str) -> str | None:
        prefix_text = str(prefix or "")
        for result in reversed(results):
            response = str(result.get("response") or "").strip()
            if response.startswith(prefix_text):
                return response
        return None

    async def _color_snapshot(serial_number: str) -> dict:
        results = await _send_color_cmds(
            serial_number,
            ["GET INFO", "GET CFG", "GET CAL", "GET DATA"],
            timeout_sec=2.0,
        )
        info = parse_color_info_line(_response_for(results, "INFO,"))
        cfg = parse_color_cfg_line(_response_for(results, "CFG,"))
        cal = parse_color_cal_line(_response_for(results, "CAL,"))
        data = parse_color_data_line(_response_for(results, "DATA,"))
        if info is None or cfg is None or cal is None or data is None:
            raise HTTPException(status_code=502, detail="invalid color module response")
        return {
            "serial": serial_number,
            "info": info,
            "cfg": cfg,
            "cal": cal,
            "data": data,
            "profile": get_device_profile(serial_number),
            "results": results,
        }

    async def _color_calibration(serial_number: str) -> dict:
        cfg_result = await _send_color_cmds(serial_number, ["GET CFG"], timeout_sec=2.0)
        cfg = parse_color_cfg_line(_response_for(cfg_result, "CFG,"))
        mode = int(cfg.get("palette_mode") if cfg else 8)
        labels = PALETTE_DEFINITIONS.get(str(mode), [])
        commands = [f"GET CAL {mode}"]
        commands.extend(f"GET CAL PATCH {mode} {int(item['slot'])}" for item in labels)
        results = await _send_color_cmds(serial_number, commands, timeout_sec=2.0)
        cal = parse_color_cal_line(_response_for(results, "CAL,"))
        patches = [
            parsed for parsed in (
                parse_color_patch_line(str(item.get("response") or ""))
                for item in results
            )
            if parsed is not None
        ]
        profile = update_device_mode_profile(
            serial_number,
            mode,
            summary=cal,
            patches=patches,
        )
        return {
            "serial": serial_number,
            "cfg": cfg,
            "cal": cal,
            "patches": patches,
            "profile": profile,
            "results": results,
        }

    def _target_token(target: str | int) -> str:
        text = str(target).strip().upper()
        if text in {"DARK", "BLACK"}:
            return "DARK"
        if text in {"WHITE"}:
            return "WHITE"
        try:
            slot = int(text)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid calibration target")
        if slot < 0 or slot > 15:
            raise HTTPException(status_code=400, detail="invalid calibration target")
        return str(slot)

    def _commands_from_color_config(payload: ColorConfigPayload) -> list[str]:
        commands: list[str] = []
        if payload.sensor_name is not None:
            name = str(payload.sensor_name).strip()
            if name:
                commands.append(f"SET CFG NAME {name[:31]}")
        if payload.sample_period_ms is not None:
            commands.append(f"SET CFG SAMPLE_MS {max(20, int(payload.sample_period_ms))}")
        if payload.led_mode is not None:
            commands.append(f"SET CFG LED {int(payload.led_mode)}")
        if payload.gain_mode is not None:
            commands.append(f"SET CFG GAIN_MODE {int(payload.gain_mode)}")
        if payload.gain is not None:
            commands.append(f"SET CFG GAIN {max(1, int(payload.gain))}")
        if payload.integration_ms is not None:
            commands.append(f"SET CFG INTEGRATION_MS {max(2, int(payload.integration_ms))}")
        if payload.classifier is not None:
            commands.append(f"SET CFG CLASSIFIER {int(payload.classifier)}")
        if payload.confidence_milli is not None:
            threshold = max(0.0, min(1.0, float(payload.confidence_milli) / 1000.0))
            commands.append(f"SET CFG CONF_TH {threshold:.4f}")
        if payload.target_clear is not None:
            commands.append(f"SET CFG TARGET_CLEAR {max(1, int(payload.target_clear))}")
        if payload.palette_mode is not None:
            mode = int(payload.palette_mode)
            if str(mode) not in PALETTE_DEFINITIONS:
                raise HTTPException(status_code=400, detail="palette_mode must be 5, 8, or 16")
            commands.append(f"SET CFG PALETTE_MODE {mode}")
        if payload.patch_sample_count is not None:
            commands.append(f"SET CFG PATCH_SAMPLES {max(1, int(payload.patch_sample_count))}")
        if payload.save:
            commands.append("SAVE CFG")
        return commands

    def _line_sensor_command_lock(serial_number: str) -> asyncio.Lock:
        lock = line_sensor_command_locks.get(serial_number)
        if lock is None:
            lock = asyncio.Lock()
            line_sensor_command_locks[serial_number] = lock
        return lock

    async def _send_line_sensor_cmds(
        serial_number: str,
        commands: list[str],
        timeout_sec: float = 2.0,
    ) -> list[dict]:
        if not commands:
            return []

        async with _line_sensor_command_lock(serial_number):
            device_before = next(
                (
                    item for item in _load_devices(db_path)
                    if item.get("serial_number") == serial_number
                ),
                None,
            )
            if device_before is None:
                raise HTTPException(status_code=404, detail="device not found")
            if str(device_before.get("module_type") or "").lower() != "line_sensor_module":
                raise HTTPException(status_code=400, detail="device is not a line sensor module")
            if str(device_before.get("status") or "").lower() != "online connected":
                raise HTTPException(status_code=409, detail="device is not online")

            previous_type = get_device_message_type(db_path=db_path, serial_number=serial_number) or "CMD"
            previous_telemetry_requested = bool(
                device_before.get("telemetry_requested", False)
            )
            if previous_type == "TELEMETRY" and previous_telemetry_requested:
                await asyncio.to_thread(
                    set_device_telemetry_requested,
                    db_path=db_path,
                    serial_number=serial_number,
                    enabled=False,
                )
                await asyncio.sleep(0.15)
            await asyncio.to_thread(
                set_device_message_type,
                db_path=db_path,
                serial_number=serial_number,
                message_type="CMD",
            )
            if previous_type != "CMD":
                await asyncio.sleep(0.08)

            results: list[dict] = []
            try:
                for command in commands:
                    result = await asyncio.to_thread(
                        send_device_cmd_once,
                        db_path=db_path,
                        serial_number=serial_number,
                        command=command,
                        timeout_sec=timeout_sec,
                    )
                    if result is None:
                        raise HTTPException(status_code=404, detail="device not found")
                    results.append(result)
                    if not bool(result.get("ok")):
                        detail = str(result.get("error_kind") or result.get("response") or "command failed")
                        status_code = 504 if detail == "cmd_send_timeout" else 409
                        raise HTTPException(status_code=status_code, detail=f"{command}: {detail}")
            finally:
                if previous_type != "CMD":
                    await asyncio.to_thread(
                        set_device_message_type,
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type=previous_type,
                    )
                    if previous_type == "TELEMETRY" and previous_telemetry_requested:
                        await asyncio.to_thread(
                            set_device_telemetry_requested,
                            db_path=db_path,
                            serial_number=serial_number,
                            enabled=True,
                        )

            return results

    def _distance_device_or_error(
        serial_number: str,
        *,
        require_online: bool = True,
    ) -> dict:
        device = next(
            (
                item for item in _load_devices(db_path)
                if item.get("serial_number") == serial_number
            ),
            None,
        )
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        if str(device.get("module_type") or "").lower() != "distance_sensor_module":
            raise HTTPException(status_code=400, detail="device is not a distance sensor module")
        if require_online and str(device.get("status") or "").lower() != "online connected":
            raise HTTPException(status_code=409, detail="device is not online")
        return device

    def _distance_command_lock(serial_number: str) -> asyncio.Lock:
        lock = distance_command_locks.get(serial_number)
        if lock is None:
            lock = asyncio.Lock()
            distance_command_locks[serial_number] = lock
        return lock

    async def _send_distance_cmds(
        serial_number: str,
        commands: list[str],
        timeout_sec: float = 2.0,
    ) -> list[dict]:
        if not commands:
            return []

        async with _distance_command_lock(serial_number):
            device_before = _distance_device_or_error(serial_number)
            previous_type = get_device_message_type(
                db_path=db_path,
                serial_number=serial_number,
            ) or "CMD"
            previous_telemetry_requested = bool(
                device_before.get("telemetry_requested", False)
            )
            if previous_type != "CMD":
                if previous_type == "TELEMETRY" and previous_telemetry_requested:
                    await asyncio.to_thread(
                        set_device_telemetry_requested,
                        db_path=db_path,
                        serial_number=serial_number,
                        enabled=False,
                    )
                    await asyncio.sleep(0.08)
                await asyncio.to_thread(
                    set_device_message_type,
                    db_path=db_path,
                    serial_number=serial_number,
                    message_type="CMD",
                )
                await asyncio.sleep(0.05)

            results: list[dict] = []
            try:
                for command in commands:
                    result = await asyncio.to_thread(
                        send_device_cmd_once,
                        db_path=db_path,
                        serial_number=serial_number,
                        command=command,
                        timeout_sec=timeout_sec,
                    )
                    if result is None:
                        raise HTTPException(status_code=404, detail="device not found")
                    results.append(result)
                    if not bool(result.get("ok")):
                        detail = str(
                            result.get("error_kind")
                            or result.get("response")
                            or "command failed"
                        )
                        status_code = 504 if detail == "cmd_send_timeout" else 409
                        raise HTTPException(
                            status_code=status_code,
                            detail=f"{command}: {detail}",
                        )
            finally:
                if previous_type != "CMD":
                    await asyncio.to_thread(
                        set_device_message_type,
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type=previous_type,
                    )
                    if previous_type == "TELEMETRY" and previous_telemetry_requested:
                        await asyncio.to_thread(
                            set_device_telemetry_requested,
                            db_path=db_path,
                            serial_number=serial_number,
                            enabled=True,
                        )

            return results

    def _control_hub_device_or_error(
        serial_number: str,
        *,
        require_online: bool = True,
    ) -> dict:
        device = next(
            (item for item in _load_devices(db_path)
             if item.get("serial_number") == serial_number),
            None,
        )
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        if str(device.get("module_type") or "").lower() != "control_hub_module":
            raise HTTPException(status_code=400, detail="device is not a control hub module")
        if require_online and str(device.get("status") or "").lower() != "online connected":
            raise HTTPException(status_code=409, detail="device is not online")
        return device

    async def _send_control_hub_cmds(
        serial_number: str,
        commands: list[str],
        timeout_sec: float = 2.0,
    ) -> list[dict]:
        _control_hub_device_or_error(serial_number)
        lock = control_hub_command_locks.setdefault(serial_number, asyncio.Lock())
        async with lock:
            previous_type = get_device_message_type(
                db_path=db_path, serial_number=serial_number,
            ) or "CMD"
            if previous_type != "CMD":
                await asyncio.to_thread(
                    set_device_message_type,
                    db_path=db_path,
                    serial_number=serial_number,
                    message_type="CMD",
                )
                await asyncio.sleep(0.05)
            results: list[dict] = []
            try:
                for command in commands:
                    result = await asyncio.to_thread(
                        send_device_cmd_once,
                        db_path=db_path,
                        serial_number=serial_number,
                        command=command,
                        timeout_sec=timeout_sec,
                    )
                    if result is None:
                        raise HTTPException(status_code=404, detail="device not found")
                    results.append(result)
                    if not bool(result.get("ok")):
                        detail = str(result.get("error_kind") or result.get("response") or "command failed")
                        raise HTTPException(status_code=409, detail=f"{command}: {detail}")
            finally:
                if previous_type != "CMD":
                    await asyncio.to_thread(
                        set_device_message_type,
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type=previous_type,
                    )
            return results

    def _hub_b64(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")

    def _hub_snapshot(serial_number: str) -> dict:
        device = _control_hub_device_or_error(serial_number, require_online=False)
        return {
            "device": device,
            "profile": control_hub_executor.get_profile(serial_number),
            "execution": control_hub_executor.status(serial_number),
            "execution_log_revision": control_hub_executor.execution_history_revision(
                serial_number
            ),
        }

    async def _hub_imu_snapshot(serial_number: str) -> dict:
        results = await _send_control_hub_cmds(serial_number, ["GET IMU"])
        response = str((results[-1] if results else {}).get("response") or "")
        try:
            imu = _parse_control_hub_imu_response(response)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        imu["serial_number"] = serial_number
        return imu

    def _distance_health(flags: int) -> dict:
        value = max(0, int(flags))
        return {
            "value": value,
            "valid": bool(value & (1 << 0)),
            "no_echo": bool(value & (1 << 1)),
            "echo_stuck": bool(value & (1 << 2)),
            "below_min": bool(value & (1 << 3)),
            "above_max": bool(value & (1 << 4)),
            "filter_active": bool(value & (1 << 5)),
            "config_loaded": bool(value & (1 << 6)),
        }

    def _distance_status(valid: bool, flags: int) -> str:
        health = _distance_health(flags)
        if valid:
            return "OK"
        if health["echo_stuck"]:
            return "ECHO_STUCK"
        if health["no_echo"]:
            return "NO_ECHO"
        if health["below_min"]:
            return "BELOW_MIN"
        if health["above_max"]:
            return "ABOVE_MAX"
        return "NOT_READY"

    def _parse_distance_data_line(line: str | None) -> dict | None:
        parts = [part.strip() for part in str(line or "").split(",")]
        if len(parts) < 7 or parts[0] != "DS":
            return None
        try:
            distance_mm = int(parts[1])
            raw_mm = int(parts[2])
            echo_us = int(parts[3])
            valid_raw = int(parts[4])
            health_flags = int(parts[5], 0)
            sample_timestamp_ms = int(parts[6])
        except (TypeError, ValueError, IndexError):
            return None
        if valid_raw not in (0, 1) or (valid_raw == 1 and distance_mm < 0):
            return None
        valid = bool(valid_raw)
        return {
            "distance_mm": distance_mm if valid and distance_mm >= 0 else None,
            "distance_cm": round(distance_mm / 10.0, 3)
            if valid and distance_mm >= 0 else None,
            "distance_m": round(distance_mm / 1000.0, 4)
            if valid and distance_mm >= 0 else None,
            "raw_mm": raw_mm if raw_mm >= 0 else None,
            "echo_us": max(0, echo_us),
            "valid": valid,
            "status": _distance_status(valid, health_flags),
            "health_flags": health_flags,
            "health": _distance_health(health_flags),
            "sample_timestamp_ms": sample_timestamp_ms,
            "raw": str(line or "").strip(),
        }

    def _parse_distance_cfg_line(line: str | None) -> dict | None:
        parts = [part.strip() for part in str(line or "").split(",")]
        if len(parts) < 5 or parts[0] != "CFG":
            return None
        try:
            sample_ms = int(parts[2])
            max_mm = int(parts[3])
            filter_window = int(parts[4])
        except (TypeError, ValueError, IndexError):
            return None
        return {
            "name": parts[1],
            "sample_ms": sample_ms,
            "max_mm": max_mm,
            "filter_window": filter_window,
            "raw": str(line or "").strip(),
        }

    def _parse_distance_info_line(line: str | None) -> dict | None:
        parts = [part.strip() for part in str(line or "").split(",")]
        if len(parts) < 9 or parts[0] != "INFO":
            return None
        try:
            module_id = int(parts[4], 0)
            trigger_gpio = int(parts[6])
            echo_gpio = int(parts[7])
            health_flags = int(parts[8], 0)
        except (TypeError, ValueError, IndexError):
            return None
        return {
            "name": parts[1],
            "module_type": parts[2],
            "firmware_module": parts[3],
            "module_id": module_id,
            "sensor_model": parts[5],
            "trigger_gpio": trigger_gpio,
            "echo_gpio": echo_gpio,
            "health_flags": health_flags,
            "health": _distance_health(health_flags),
            "raw": str(line or "").strip(),
        }

    def _parse_distance_selftest_line(line: str | None) -> dict | None:
        parts = [part.strip() for part in str(line or "").split(",")]
        if len(parts) < 4 or parts[0] != "SELFTEST":
            return None
        try:
            ok_raw = int(parts[1])
            health_flags = int(parts[2], 0)
            distance_mm = int(parts[3])
        except (TypeError, ValueError, IndexError):
            return None
        if ok_raw not in (0, 1):
            return None
        return {
            "ok": bool(ok_raw),
            "health_flags": health_flags,
            "health": _distance_health(health_flags),
            "distance_mm": distance_mm if distance_mm >= 0 else None,
            "raw": str(line or "").strip(),
        }

    def _distance_result_response(results: list[dict], prefix: str) -> str | None:
        for item in reversed(results):
            response = str(item.get("response") or "").strip()
            if response.startswith(prefix):
                return response
        return None

    def _distance_snapshot_from_results(results: list[dict]) -> dict:
        payload: dict = {}
        data = _parse_distance_data_line(_distance_result_response(results, "DS,"))
        cfg = _parse_distance_cfg_line(_distance_result_response(results, "CFG,"))
        info = _parse_distance_info_line(_distance_result_response(results, "INFO,"))
        selftest = _parse_distance_selftest_line(
            _distance_result_response(results, "SELFTEST,")
        )
        if data is not None:
            payload["data"] = data
        if cfg is not None:
            payload["cfg"] = cfg
        if info is not None:
            payload["info"] = info
        if selftest is not None:
            payload["selftest"] = selftest
        return payload

    async def _distance_snapshot(serial_number: str) -> dict:
        device = _distance_device_or_error(serial_number, require_online=False)
        result: dict = {
            "serial": serial_number,
            "device": device,
            "online": str(device.get("status") or "").lower() == "online connected",
            "data": None,
            "cfg": None,
            "info": None,
        }

        cached = get_latest_ds_frame(serial_number)
        if cached is not None:
            received_at, text = cached
            data = _parse_distance_data_line(text)
            if data is not None:
                data["age_ms"] = round(
                    max(0.0, (time.monotonic() - float(received_at)) * 1000.0),
                    1,
                )
                result["data"] = data

        if broker is not None:
            def _scan_broker() -> dict:
                out: dict = {}
                for event in reversed(
                    broker.history(limit=300, serial_filter=serial_number)
                ):
                    if str(event.get("direction") or "") != "rx":
                        continue
                    message = str(event.get("message") or "").strip()
                    if message.startswith("DS,") and "data" not in out:
                        parsed = _parse_distance_data_line(message)
                        if parsed is not None:
                            # Broker history can be bootstrapped from an old log
                            # and has no monotonic receipt time. Never present that
                            # fallback as a fresh live sample.
                            parsed["age_ms"] = 60_000.0
                            out["data"] = parsed
                    elif message.startswith("CFG,") and "cfg" not in out:
                        parsed = _parse_distance_cfg_line(message)
                        if parsed is not None:
                            out["cfg"] = parsed
                    elif message.startswith("INFO,") and "info" not in out:
                        parsed = _parse_distance_info_line(message)
                        if parsed is not None:
                            out["info"] = parsed
                    elif message.startswith("SELFTEST,") and "selftest" not in out:
                        parsed = _parse_distance_selftest_line(message)
                        if parsed is not None:
                            out["selftest"] = parsed
                    if {"data", "cfg", "info", "selftest"}.issubset(out):
                        break
                return out

            history_payload = await asyncio.to_thread(_scan_broker)
            for key, value in history_payload.items():
                if result.get(key) is None:
                    result[key] = value

        return result

    @app.on_event("startup")
    async def _on_startup():
        if broker is not None:
            broker.start()
        print(
            f"[webview] startup db={db_path} comms_log={comms_log_path} "
            f"realtime_stream={'on' if enable_realtime_stream else 'off'}",
            flush=True,
        )

    @app.on_event("shutdown")
    async def _on_shutdown():
        if broker is not None:
            broker.stop()
        print("[webview] shutdown complete", flush=True)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/devices")
    async def devices():
        return {"devices": _load_devices(db_path)}

    @app.get("/api/color/palettes")
    async def color_palettes():
        return {"palettes": PALETTE_DEFINITIONS}

    @app.get("/api/color/devices")
    async def color_devices():
        devices = []
        for device in _load_devices(db_path):
            if str(device.get("module_type") or "").lower() != COLOR_MODULE_TYPE:
                continue
            serial = str(device.get("serial_number") or "").strip()
            item = dict(device)
            item["color_profile"] = get_device_profile(serial)
            devices.append(item)
        return {"devices": devices, "palettes": PALETTE_DEFINITIONS}

    @app.post("/api/devices/clear")
    async def clear_devices():
        cleared = clear_devices_registry(db_path=db_path)
        return {
            "ok": True,
            "cleared": int(cleared),
            "devices": _load_devices(db_path),
        }

    @app.post("/api/devices/{serial_number}/name")
    async def update_device_name(serial_number: str, payload: DeviceNameUpdatePayload):
        updated = set_device_name(
            db_path=db_path,
            serial_number=serial_number,
            name=payload.name,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")

        for device in _load_devices(db_path):
            if device.get("serial_number") == serial_number:
                return {"device": device}

        raise HTTPException(status_code=500, detail="updated device not found")

    @app.get("/api/config/message-type")
    async def get_message_type_config():
        # Legacy global default for new sessions; per-device mode is authoritative.
        return {
            "active_message_type": get_active_message_type(),
            "supported_message_types": supported_message_types(),
        }

    @app.post("/api/config/message-type")
    async def update_message_type_config(payload: MessageTypeUpdatePayload):
        # Legacy global default for new sessions; existing devices keep their own mode.
        active = set_active_message_type(payload.message_type)
        return {
            "active_message_type": active,
            "supported_message_types": supported_message_types(),
        }

    @app.get("/api/devices/{serial_number}/config/message-type")
    async def get_device_message_type_config(serial_number: str):
        active = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if active is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {
            "serial_number": serial_number,
            "active_message_type": active,
            "supported_message_types": supported_message_types(),
        }

    @app.post("/api/devices/{serial_number}/config/message-type")
    async def update_device_message_type_config(
        serial_number: str,
        payload: MessageTypeUpdatePayload,
    ):
        updated = set_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
            message_type=payload.message_type,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        active = str(updated.get("message_type") or "").strip().upper()
        if not active:
            active = get_device_message_type(db_path=db_path, serial_number=serial_number) or "CMD"

        for device in _load_devices(db_path):
            if device.get("serial_number") == serial_number:
                return {
                    "serial_number": serial_number,
                    "active_message_type": active,
                    "supported_message_types": supported_message_types(),
                    "device": device,
                }

        return {
            "serial_number": serial_number,
            "active_message_type": active,
            "supported_message_types": supported_message_types(),
        }

    @app.get("/api/devices/{serial_number}/color/profile")
    async def color_profile(serial_number: str):
        _color_device_or_error(serial_number)
        return {
            "serial": serial_number,
            "profile": get_device_profile(serial_number),
            "palettes": PALETTE_DEFINITIONS,
        }

    @app.post("/api/devices/{serial_number}/color/profile")
    async def update_color_profile(serial_number: str, payload: ColorProfilePayload):
        _color_device_or_error(serial_number)
        profile = set_device_profile(serial_number, merge_device_profile(serial_number, payload.profile))

        firmware_results: list[dict] = []
        if payload.apply_to_firmware:
            commands: list[str] = []
            for mode_key, mode_profile in dict(profile.get("modes") or {}).items():
                patches = mode_profile.get("patches") if isinstance(mode_profile, dict) else None
                if not isinstance(patches, list):
                    continue
                try:
                    mode = int(mode_key)
                except (TypeError, ValueError):
                    continue
                if str(mode) not in PALETTE_DEFINITIONS:
                    continue
                for patch in patches:
                    if not isinstance(patch, dict) or not bool(patch.get("valid", True)):
                        continue
                    try:
                        slot = int(patch.get("slot"))
                        norm = patch.get("norm_rgb_milli") or {}
                        lab = patch.get("lab") or {}
                        nr = int(norm.get("r"))
                        ng = int(norm.get("g"))
                        nb = int(norm.get("b"))
                        luma = int(patch.get("luma_milli", 0) or 0)
                        ll = int(lab.get("l_centi"))
                        la = int(lab.get("a_centi"))
                        lb = int(lab.get("b_centi"))
                        samples = int(patch.get("sample_count", 1) or 1)
                    except (TypeError, ValueError):
                        continue
                    commands.append(
                        f"SET CAL PROTO {mode} {slot} {nr} {ng} {nb} {luma} {ll} {la} {lb} {samples}"
                    )
            if commands:
                commands.append("SAVE CAL")
                firmware_results = await _send_color_cmds(serial_number, commands, timeout_sec=2.0)

        return {
            "serial": serial_number,
            "profile": profile,
            "firmware_results": firmware_results,
        }

    @app.get("/api/devices/{serial_number}/color/snapshot")
    async def color_snapshot(serial_number: str):
        return await _color_snapshot(serial_number)

    @app.get("/api/devices/{serial_number}/color/calibration")
    async def color_calibration(serial_number: str):
        return await _color_calibration(serial_number)

    @app.post("/api/devices/{serial_number}/color/config")
    async def color_config(serial_number: str, payload: ColorConfigPayload):
        commands = _commands_from_color_config(payload)
        if commands:
            commands.append("GET CFG")
            await _send_color_cmds(serial_number, commands, timeout_sec=2.0)
        return await _color_snapshot(serial_number)

    @app.post("/api/devices/{serial_number}/color/save")
    async def color_save(serial_number: str, payload: ColorSavePayload):
        commands: list[str] = []
        if payload.persist_cfg:
            commands.append("SAVE CFG")
        if payload.persist_cal:
            commands.append("SAVE CAL")
        results = await _send_color_cmds(serial_number, commands, timeout_sec=2.0) if commands else []
        return {
            "ok": True,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/selftest")
    async def color_selftest(serial_number: str):
        results = await _send_color_cmds(serial_number, ["RUN SELFTEST"], timeout_sec=2.0)
        result = parse_color_selftest_line(_response_for(results, "SELFTEST,"))
        return {
            "ok": bool(result.get("ok")) if result else False,
            "result": result,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/restore-defaults")
    async def color_restore_defaults(serial_number: str):
        results = await _send_color_cmds(
            serial_number,
            ["RESET CFG", "RESET CAL ALL", "SAVE CFG", "SAVE CAL"],
            timeout_sec=2.0,
        )
        profile = set_device_profile(serial_number, default_device_profile(serial_number))
        return {
            "ok": True,
            "results": results,
            "profile": profile,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/start")
    async def color_calibration_start(serial_number: str):
        results = await _send_color_cmds(serial_number, ["START CAL", "GET DATA"], timeout_sec=2.0)
        return {
            "ok": True,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/stop")
    async def color_calibration_stop(serial_number: str):
        results = await _send_color_cmds(serial_number, ["STOP CAL", "GET CAL"], timeout_sec=2.0)
        return {
            "ok": True,
            "results": results,
            "calibration": await _color_calibration(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/select")
    async def color_calibration_select(serial_number: str, payload: ColorCalibrationTargetPayload):
        target = _target_token(payload.target)
        results = await _send_color_cmds(serial_number, [f"SET CAL PATCH {target}", "GET DATA"], timeout_sec=2.0)
        return {
            "ok": True,
            "target": target,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/commit")
    async def color_calibration_commit(serial_number: str, payload: ColorCalibrationTargetPayload):
        target = _target_token(payload.target)
        results = await _send_color_cmds(serial_number, [f"COMMIT CAL PATCH {target}"], timeout_sec=2.0)

        snapshot = await _color_snapshot(serial_number)
        profile = snapshot.get("profile") or get_device_profile(serial_number)
        try:
            slot = int(target)
        except (TypeError, ValueError):
            slot = None
        if slot is not None:
            mode = int(snapshot.get("cfg", {}).get("palette_mode", 8))
            patch_results = await _send_color_cmds(serial_number, [f"GET CAL PATCH {mode} {slot}"], timeout_sec=2.0)
            patch = parse_color_patch_line(_response_for(patch_results, "PATCH,"))
            if patch is not None:
                mode_profile = profile["modes"].setdefault(str(mode), {
                    "mode": str(mode),
                    "labels": PALETTE_DEFINITIONS.get(str(mode), []),
                    "summary": None,
                    "patches": [],
                    "last_calibrated_at": None,
                    "updated_at": None,
                })
                patches = [
                    item for item in mode_profile.get("patches", [])
                    if int(item.get("slot", -999)) != slot
                ]
                patches.append(patch)
                patches.sort(key=lambda item: int(item.get("slot", 0)))
                profile = update_device_mode_profile(
                    serial_number,
                    mode,
                    patches=patches,
                    last_calibrated_at=__import__("datetime").datetime.now().isoformat(),
                )
                snapshot["profile"] = profile

        return {
            "ok": True,
            "target": target,
            "results": results,
            "snapshot": snapshot,
            "profile": profile,
        }

    @app.get("/api/devices/{serial_number}/config/traction-out")
    async def get_device_traction_out_config(serial_number: str):
        value = get_device_traction_out_value(
            db_path=db_path,
            serial_number=serial_number,
        )
        if value is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {
            "serial_number": serial_number,
            "traction_out_value": int(value),
        }

    @app.post("/api/devices/{serial_number}/config/traction-out")
    async def update_device_traction_out_config(
        serial_number: str,
        payload: TractionOutValueUpdatePayload,
    ):
        updated = set_device_traction_out_value(
            db_path=db_path,
            serial_number=serial_number,
            traction_out_value=payload.value,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {
            "serial_number": serial_number,
            "traction_out_value": int(updated.get("traction_out_value", 0) or 0),
        }

    @app.post("/api/devices/{serial_number}/traction-out/send")
    async def send_device_traction_out(serial_number: str, payload: TractionOutSendPayload):
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != MESSAGE_TYPE_CONTROL:
            raise HTTPException(status_code=409, detail="set message type to CONTROL first")

        result = send_device_traction_out_once(
            db_path=db_path,
            serial_number=serial_number,
            value=payload.value,
            timeout_sec=1.5,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="device not found")
        if not bool(result.get("ok")):
            detail = str(result.get("error_kind") or "traction_out_send_failed")
            if detail == "traction_out_send_timeout":
                raise HTTPException(status_code=504, detail=detail)
            raise HTTPException(status_code=409, detail=detail)
        return result

    @app.post("/api/devices/{serial_number}/cmd/send")
    async def send_device_cmd(serial_number: str, payload: CmdSendPayload):
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != "CMD":
            raise HTTPException(status_code=409, detail="set message type to CMD first")

        command_text = str(payload.command or "").strip()
        if not command_text:
            raise HTTPException(status_code=400, detail="command is required")

        result = send_device_cmd_once(
            db_path=db_path,
            serial_number=serial_number,
            command=command_text,
            timeout_sec=1.5,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="device not found")
        if not bool(result.get("ok")):
            detail = str(result.get("error_kind") or "cmd_send_failed")
            if detail == "cmd_send_timeout":
                raise HTTPException(status_code=504, detail=detail)
            raise HTTPException(status_code=409, detail=detail)
        return result

    @app.get("/api/config/serial")
    async def get_serial_config():
        return {
            "active_baud_rate": get_active_serial_baud(),
            "supported_baud_rates": supported_serial_baud_rates(),
        }

    @app.post("/api/serial-monitors/pause")
    async def pause_serial_monitors():
        stop_all_keepalive_monitors()
        return {"ok": True}

    @app.post("/api/serial-monitors/resume")
    async def resume_serial_monitors():
        resume_keepalive_monitors(db_path)
        return {"ok": True}

    @app.post("/api/config/serial")
    async def update_serial_config(payload: BaudRateUpdatePayload):
        try:
            requested_baud_rate = _normalize_baud_rate_for_api(payload.baud_rate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        current_baud_rate = get_active_serial_baud()
        device_updates: list[dict] = []
        if requested_baud_rate != current_baud_rate:
            for device in _online_traction_devices(db_path):
                serial_number = str(device.get("serial_number") or "").strip()
                if not serial_number:
                    continue
                previous_type = get_device_message_type(
                    db_path=db_path,
                    serial_number=serial_number,
                )
                if previous_type != MESSAGE_TYPE_CMD:
                    set_device_message_type(
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type=MESSAGE_TYPE_CMD,
                    )
                    time.sleep(0.05)

                result = send_device_cmd_once(
                    db_path=db_path,
                    serial_number=serial_number,
                    command=f"SET BAUD {requested_baud_rate}",
                    timeout_sec=2.0,
                )
                if result is None:
                    raise HTTPException(status_code=404, detail=f"device not found: {serial_number}")
                if not bool(result.get("ok")):
                    raise HTTPException(
                        status_code=409,
                        detail=f"device baud update failed for {serial_number}: {result.get('error_kind') or result.get('response')}",
                    )
                response_text = str(result.get("response") or "").strip()
                if response_text != f"B,{requested_baud_rate}":
                    raise HTTPException(
                        status_code=409,
                        detail=f"unexpected baud response for {serial_number}: {response_text}",
                    )
                device_updates.append({
                    "serial_number": serial_number,
                    "response": response_text,
                    "latency_ms": result.get("latency_ms"),
                })
            time.sleep(0.08)

        active_baud_rate = set_active_serial_baud(requested_baud_rate)
        if requested_baud_rate != current_baud_rate:
            stop_all_keepalive_monitors()
            resume_keepalive_monitors(db_path)
        return {
            "active_baud_rate": active_baud_rate,
            "supported_baud_rates": supported_serial_baud_rates(),
            "device_updates": device_updates,
        }

    @app.post("/api/devices/{serial_number}/telemetry/start")
    async def start_telemetry(serial_number: str):
        device = next(
            (item for item in _load_devices(db_path) if item.get("serial_number") == serial_number),
            None,
        )
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        if bool(device.get("telemetry_requested")) or bool(device.get("telemetry_active")):
            raise HTTPException(
                status_code=409,
                detail="Another program already owns this communication stream. Stop that code and run devices.py for configuration.",
            )
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != "TELEMETRY":
            raise HTTPException(status_code=409, detail="set message type to TELEMETRY for this device")
        updated = set_device_telemetry_requested(
            db_path=db_path,
            serial_number=serial_number,
            enabled=True,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {"ok": True}

    @app.post("/api/devices/{serial_number}/telemetry/stop")
    async def stop_telemetry(serial_number: str):
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != "TELEMETRY":
            raise HTTPException(status_code=409, detail="set message type to TELEMETRY for this device")
        updated = set_device_telemetry_requested(
            db_path=db_path,
            serial_number=serial_number,
            enabled=False,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {"ok": True}

    @app.get("/api/comms")
    async def comms(
        limit: int = Query(default=300, ge=1, le=5000),
        serial: str | None = Query(default=None),
    ):
        if broker is None:
            return {"events": [], "disabled": True}
        return {"events": broker.history(limit=limit, serial_filter=serial)}

    @app.websocket("/ws/comms")
    async def ws_comms(websocket: WebSocket):
        await websocket.accept()
        if broker is None:
            await websocket.send_json(
                {
                    "type": "disabled",
                    "reason": "webview_realtime_stream_disabled",
                }
            )
            await websocket.close()
            return
        subscriber_id, subscriber_queue = broker.register()
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "devices": _load_devices(db_path),
                    "events": broker.history(limit=500, serial_filter=None),
                    "active_message_type": get_active_message_type(),
                    "supported_message_types": supported_message_types(),
                    "active_baud_rate": get_active_serial_baud(),
                    "supported_baud_rates": supported_serial_baud_rates(),
                }
            )
            while True:
                try:
                    event = await asyncio.to_thread(subscriber_queue.get, True, 1.0)
                except queue.Empty:
                    continue
                await websocket.send_json({"type": "comms", "event": event})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            broker.unregister(subscriber_id)

    @app.get("/api/devices/{serial_number}/distance-sensor/snapshot")
    async def distance_sensor_snapshot(serial_number: str):
        return await _distance_snapshot(serial_number)

    @app.get("/api/devices/{serial_number}/control-hub/snapshot")
    async def control_hub_snapshot(serial_number: str):
        return _hub_snapshot(serial_number)

    @app.get("/api/devices/{serial_number}/control-hub/executions")
    async def control_hub_executions(
        serial_number: str, limit: int = Query(default=50, ge=1, le=200),
    ):
        _control_hub_device_or_error(serial_number, require_online=False)
        return {
            "entries": control_hub_executor.execution_history(serial_number, limit),
            "revision": control_hub_executor.execution_history_revision(serial_number),
        }

    @app.post("/api/devices/{serial_number}/control-hub/executions/clear")
    async def control_hub_executions_clear(serial_number: str):
        _control_hub_device_or_error(serial_number, require_online=False)
        await asyncio.to_thread(control_hub_executor.clear_execution_history, serial_number)
        return {"ok": True, "entries": [], "revision": "0"}

    @app.get("/api/devices/{serial_number}/control-hub/imu")
    async def control_hub_imu(serial_number: str):
        return await _hub_imu_snapshot(serial_number)

    @app.post("/api/devices/{serial_number}/control-hub/imu/calibrate")
    async def control_hub_imu_calibrate(serial_number: str):
        await _send_control_hub_cmds(serial_number, ["CALIBRATE IMU"])
        return await _hub_imu_snapshot(serial_number)

    @app.get("/api/control-hub/scripts")
    async def control_hub_scripts():
        return {
            "scripts": control_hub_script_store.list(),
            "directories": control_hub_script_store.directories(),
        }

    @app.get("/api/control-hub/script-directories")
    async def control_hub_script_directories():
        return {"directories": control_hub_script_store.directories()}

    @app.post("/api/control-hub/script-directories")
    async def control_hub_script_directory_add(payload: ControlHubScriptDirectoryPayload):
        try:
            directory = await asyncio.to_thread(
                control_hub_script_store.add_directory, payload.path,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "directory": directory,
            "directories": control_hub_script_store.directories(),
            "scripts": control_hub_script_store.list(),
        }

    @app.post("/api/control-hub/script-directories/remove")
    async def control_hub_script_directory_remove(
        payload: ControlHubScriptDirectoryRemovePayload,
    ):
        try:
            await asyncio.to_thread(
                control_hub_script_store.remove_directory, payload.directory_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="script directory not found") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "directories": control_hub_script_store.directories(),
            "scripts": control_hub_script_store.list(),
        }

    @app.post("/api/control-hub/scripts/upload")
    async def control_hub_script_upload(payload: ControlHubScriptUploadPayload):
        try:
            saved = await asyncio.to_thread(
                control_hub_script_store.save, payload.filename, payload.content,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "script": saved,
            "scripts": control_hub_script_store.list(),
            "directories": control_hub_script_store.directories(),
        }

    @app.post("/api/devices/{serial_number}/control-hub/refresh")
    async def control_hub_refresh(serial_number: str):
        commands = ["GET INFO", "GET CFG"] + [f"GET MENU {index}" for index in range(8)]
        results = await _send_control_hub_cmds(serial_number, commands)
        snapshot = _hub_snapshot(serial_number)
        snapshot["results"] = results
        return snapshot

    @app.post("/api/devices/{serial_number}/control-hub/config")
    async def control_hub_config(serial_number: str, payload: ControlHubConfigPayload):
        current = control_hub_executor.get_profile(serial_number)
        device_name = str(payload.device_name if payload.device_name is not None else current.get("device_name") or "Modulo de Controle").strip()
        if not device_name or len(device_name.encode("utf-8")) > 31:
            raise HTTPException(status_code=400, detail="device_name must contain 1 to 31 UTF-8 bytes")
        source_menu = payload.menu if payload.menu is not None else current.get("menu", [])
        if not isinstance(source_menu, list) or len(source_menu) > 8:
            raise HTTPException(status_code=400, detail="menu must contain at most 8 entries")
        menu: list[dict] = []
        commands = [f"SET CFG NAME {device_name.replace(',', '-')}"]
        for slot in range(8):
            raw = source_menu[slot] if slot < len(source_menu) and isinstance(source_menu[slot], dict) else {}
            name = str(raw.get("name") or "").strip()
            command = str(raw.get("command") or "").strip()
            script = str(raw.get("script") or "").strip()
            kind = str(raw.get("kind") or "command").strip().lower()
            terminal = str(raw.get("shell") or "auto").strip().lower()
            selected_value = script if kind == "python" else command
            enabled = bool(raw.get("enabled", bool(name and selected_value)))
            if kind not in {"command", "python"}:
                raise HTTPException(status_code=400, detail=f"menu slot {slot + 1}: type must be command or python")
            if "\n" in command or "\r" in command or "\x00" in command:
                raise HTTPException(status_code=400, detail=f"menu slot {slot + 1}: command contains an invalid character")
            if len(name.encode("utf-8")) > 30:
                raise HTTPException(status_code=400, detail=f"menu slot {slot + 1}: name is longer than 30 UTF-8 bytes")
            if len(selected_value.encode("utf-8")) > 90:
                raise HTTPException(status_code=400, detail=f"menu slot {slot + 1}: execution value is longer than 90 UTF-8 bytes")
            if enabled and (not name or not selected_value):
                raise HTTPException(status_code=400, detail=f"menu slot {slot + 1}: enabled entries require a name and execution target")
            if kind == "python" and script:
                try:
                    control_hub_script_store.resolve(script)
                except (ValueError, FileNotFoundError) as exc:
                    raise HTTPException(status_code=400, detail=f"menu slot {slot + 1}: Python script is unavailable") from exc
            if terminal not in {"auto", "cmd", "powershell", "sh"}:
                raise HTTPException(status_code=400, detail=f"menu slot {slot + 1}: shell must be auto, cmd, powershell, or sh")
            normalized = {"enabled": enabled, "name": name, "kind": kind,
                          "command": command, "script": script, "shell": terminal}
            menu.append(normalized)
            if enabled:
                mode = 1 if kind == "python" else 0
                commands.append(f"SET MENU {slot} {mode} {_hub_b64(name)} {_hub_b64(selected_value)}")
            else:
                commands.append(f"CLEAR MENU {slot}")
        if payload.save:
            commands.append("SAVE CFG")
        results = await _send_control_hub_cmds(serial_number, commands, timeout_sec=2.5)
        profile = {"device_name": device_name, "menu": menu, "updated_at": time.time()}
        control_hub_executor.set_profile(serial_number, profile)
        snapshot = _hub_snapshot(serial_number)
        snapshot["results"] = results
        return snapshot

    @app.post("/api/devices/{serial_number}/control-hub/servo")
    async def control_hub_servo(serial_number: str, payload: ControlHubServoPayload):
        if payload.channel < 0 or payload.channel >= 6 or payload.angle < 0 or payload.angle > 180:
            raise HTTPException(status_code=400, detail="channel must be 0..5 and angle must be 0..180")
        results = await _send_control_hub_cmds(serial_number, [f"SET SERVO {payload.channel} {payload.angle}"])
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/control-hub/gpio")
    async def control_hub_gpio(serial_number: str, payload: ControlHubGpioPayload):
        if payload.pin < 0 or payload.pin >= 9 or payload.value not in (0, 1):
            raise HTTPException(status_code=400, detail="output pin must be 0..8 and value must be 0 or 1")
        results = await _send_control_hub_cmds(serial_number, [f"SET GPIO {payload.pin} {payload.value}"])
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/distance-sensor/refresh")
    async def distance_sensor_refresh(serial_number: str):
        results = await _send_distance_cmds(
            serial_number,
            ["GET INFO", "GET CFG", "GET DATA"],
            timeout_sec=2.0,
        )
        response_payload = _distance_snapshot_from_results(results)
        if response_payload.get("data") is None:
            raise HTTPException(
                status_code=502,
                detail="invalid distance sensor data response",
            )
        snapshot = await _distance_snapshot(serial_number)
        snapshot.update(response_payload)
        snapshot["results"] = results
        return snapshot

    @app.post("/api/devices/{serial_number}/distance-sensor/config")
    async def distance_sensor_config(
        serial_number: str,
        payload: DistanceSensorConfigPayload,
    ):
        commands: list[str] = []
        if payload.name is not None:
            name = str(payload.name).strip().replace(",", "-")
            if not name:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            commands.append(f"SET CFG NAME {name[:31]}")
        if payload.sample_ms is not None:
            sample_ms = int(payload.sample_ms)
            if sample_ms < 60 or sample_ms > 2000:
                raise HTTPException(
                    status_code=400,
                    detail="sample_ms must be between 60 and 2000",
                )
            commands.append(f"SET CFG SAMPLE_MS {sample_ms}")
        if payload.max_mm is not None:
            max_mm = int(payload.max_mm)
            if max_mm < 20 or max_mm > 4000:
                raise HTTPException(
                    status_code=400,
                    detail="max_mm must be between 20 and 4000",
                )
            commands.append(f"SET CFG MAX_MM {max_mm}")
        if payload.filter_window is not None:
            filter_window = int(payload.filter_window)
            if filter_window not in (1, 3, 5, 7):
                raise HTTPException(
                    status_code=400,
                    detail="filter_window must be 1, 3, 5, or 7",
                )
            commands.append(f"SET CFG FILTER {filter_window}")
        if payload.save and commands:
            commands.append("SAVE CFG")
        commands.extend(["GET CFG", "GET DATA"])

        results = await _send_distance_cmds(
            serial_number,
            commands,
            timeout_sec=2.0,
        )
        response_payload = _distance_snapshot_from_results(results)
        if response_payload.get("cfg") is None:
            raise HTTPException(
                status_code=502,
                detail="invalid distance sensor config response",
            )
        snapshot = await _distance_snapshot(serial_number)
        snapshot.update(response_payload)
        snapshot["results"] = results
        return snapshot

    @app.post("/api/devices/{serial_number}/distance-sensor/selftest")
    async def distance_sensor_selftest(serial_number: str):
        results = await _send_distance_cmds(
            serial_number,
            ["RUN SELFTEST", "GET DATA"],
            timeout_sec=2.0,
        )
        response_payload = _distance_snapshot_from_results(results)
        if response_payload.get("selftest") is None:
            raise HTTPException(
                status_code=502,
                detail="invalid distance sensor selftest response",
            )
        snapshot = await _distance_snapshot(serial_number)
        snapshot.update(response_payload)
        snapshot["results"] = results
        return snapshot

    @app.post("/api/devices/{serial_number}/distance-sensor/stream/start")
    async def distance_sensor_stream_start(serial_number: str):
        async with _distance_command_lock(serial_number):
            _distance_device_or_error(serial_number)
            updated = await asyncio.to_thread(
                set_device_message_type,
                db_path=db_path,
                serial_number=serial_number,
                message_type="TELEMETRY",
            )
            if updated is None:
                raise HTTPException(status_code=404, detail="device not found")
            updated = await asyncio.to_thread(
                set_device_telemetry_requested,
                db_path=db_path,
                serial_number=serial_number,
                enabled=True,
            )
            if updated is None:
                raise HTTPException(status_code=404, detail="device not found")
        return {"ok": True, "device": updated}

    @app.post("/api/devices/{serial_number}/distance-sensor/stream/stop")
    async def distance_sensor_stream_stop(serial_number: str):
        async with _distance_command_lock(serial_number):
            _distance_device_or_error(serial_number, require_online=False)
            updated = await asyncio.to_thread(
                set_device_telemetry_requested,
                db_path=db_path,
                serial_number=serial_number,
                enabled=False,
            )
            if updated is None:
                raise HTTPException(status_code=404, detail="device not found")
        return {"ok": True, "device": updated}

    @app.get("/api/devices/{serial_number}/line-sensor/snapshot")
    async def line_sensor_snapshot(serial_number: str):
        """
        Return the most recent LS / CFG / CAL / INFO messages for this device.
        LS comes from the streaming cache (O(1), lock-free read, never blocks).
        CFG/CAL/INFO come from the broker history via asyncio.to_thread so the
        event loop is never blocked by threading.Lock acquisition.
        """
        result: dict = {}

        # LS: in-process streaming cache — always current, zero contention
        cached = get_latest_ls_frame(serial_number)
        if cached is not None:
            result["ls"] = cached[1]

        # CFG/CAL/INFO: broker history scan — run in thread pool so the asyncio
        # event loop is never blocked by the broker's threading.Lock.
        if broker is not None:
            def _scan_broker():
                out: dict = {}
                for event in reversed(broker.history(limit=200, serial_filter=serial_number)):
                    if str(event.get("direction") or "") != "rx":
                        continue
                    msg = str(event.get("message") or "").strip()
                    if msg.startswith("CFG,") and "cfg" not in out:
                        out["cfg"] = msg
                    elif msg.startswith("CAL,") and "cal" not in out:
                        out["cal"] = msg
                    elif msg.startswith("INFO,") and "info" not in out:
                        out["info"] = msg
                    if len(out) == 3:
                        break
                return out

            cfg_data = await asyncio.to_thread(_scan_broker)
            result.update(cfg_data)

        return {"serial": serial_number, **result}

    @app.post("/api/devices/{serial_number}/line-sensor/stream/start")
    async def line_sensor_stream_start(serial_number: str):
        """Switch an idle device to TELEMETRY mode and enable streaming."""
        async with _line_sensor_command_lock(serial_number):
            device = next(
                (item for item in _load_devices(db_path) if item.get("serial_number") == serial_number),
                None,
            )
            if device is None:
                raise HTTPException(status_code=404, detail="device not found")
            if bool(device.get("telemetry_requested")) or bool(device.get("telemetry_active")):
                raise HTTPException(
                    status_code=409,
                    detail="Another program already owns this communication stream. Stop that code and run devices.py for configuration.",
                )
            await asyncio.to_thread(
                set_device_message_type,
                db_path=db_path, serial_number=serial_number, message_type="TELEMETRY",
            )
            await asyncio.to_thread(
                set_device_telemetry_requested,
                db_path=db_path, serial_number=serial_number, enabled=True,
            )
        return {"ok": True}

    @app.post("/api/devices/{serial_number}/line-sensor/stream/stop")
    async def line_sensor_stream_stop(serial_number: str):
        """Disable streaming (leaves device in TELEMETRY mode, just stops the push)."""
        async with _line_sensor_command_lock(serial_number):
            await asyncio.to_thread(
                set_device_telemetry_requested,
                db_path=db_path, serial_number=serial_number, enabled=False,
            )
        return {"ok": True}

    @app.post("/api/devices/{serial_number}/line-sensor/refresh")
    async def line_sensor_refresh(serial_number: str):
        """
        Queue lightweight read commands (GET INFO / CFG / CAL) so the responses
        appear in the comms stream and update the monitor page.
        """
        results = await _send_line_sensor_cmds(
            serial_number,
            ["GET INFO", "GET CFG", "GET CAL"],
            timeout_sec=2.0,
        )
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/config")
    async def line_sensor_config(serial_number: str, payload: LineSensorConfigPayload):
        commands: list[str] = []
        if payload.track_type is not None:
            track_type = int(payload.track_type)
            if track_type not in (0, 1):
                raise HTTPException(status_code=400, detail="track_type must be 0 or 1")
            commands.append(f"SET CFG TRACK {track_type}")
        if payload.digital_threshold is not None:
            digital_threshold = float(payload.digital_threshold)
            if digital_threshold < 0.05 or digital_threshold > 0.95:
                raise HTTPException(status_code=400, detail="digital_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DIGITAL_TH {digital_threshold:.4f}")
        if payload.detect_threshold is not None:
            detect_threshold = float(payload.detect_threshold)
            if detect_threshold < 0.05 or detect_threshold > 0.95:
                raise HTTPException(status_code=400, detail="detect_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DETECT_TH {detect_threshold:.4f}")
        if payload.calibration_time_ms is not None:
            calibration_time_ms = int(payload.calibration_time_ms)
            if calibration_time_ms < 100:
                raise HTTPException(status_code=400, detail="calibration_time_ms must be at least 100")
            commands.append(f"SET CFG CAL_TIME_MS {calibration_time_ms}")
        if payload.save:
            commands.append("SAVE CFG")
        results = await _send_line_sensor_cmds(serial_number, commands, timeout_sec=2.0)
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/calibration/start")
    async def line_sensor_calibration_start(
        serial_number: str,
        payload: LineSensorCalibrationStartPayload,
    ):
        config_payload = LineSensorConfigPayload(
            track_type=payload.track_type,
            digital_threshold=payload.digital_threshold,
            detect_threshold=payload.detect_threshold,
            calibration_time_ms=payload.calibration_time_ms,
            save=payload.save_config,
        )
        commands: list[str] = []
        if config_payload.track_type is not None:
            track_type = int(config_payload.track_type)
            if track_type not in (0, 1):
                raise HTTPException(status_code=400, detail="track_type must be 0 or 1")
            commands.append(f"SET CFG TRACK {track_type}")
        if config_payload.digital_threshold is not None:
            digital_threshold = float(config_payload.digital_threshold)
            if digital_threshold < 0.05 or digital_threshold > 0.95:
                raise HTTPException(status_code=400, detail="digital_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DIGITAL_TH {digital_threshold:.4f}")
        if config_payload.detect_threshold is not None:
            detect_threshold = float(config_payload.detect_threshold)
            if detect_threshold < 0.05 or detect_threshold > 0.95:
                raise HTTPException(status_code=400, detail="detect_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DETECT_TH {detect_threshold:.4f}")
        if config_payload.calibration_time_ms is not None:
            calibration_time_ms = int(config_payload.calibration_time_ms)
            if calibration_time_ms < 100:
                raise HTTPException(status_code=400, detail="calibration_time_ms must be at least 100")
            commands.append(f"SET CFG CAL_TIME_MS {calibration_time_ms}")
        if commands and config_payload.save:
            commands.append("SAVE CFG")
        commands.append("START CAL")
        results = await _send_line_sensor_cmds(serial_number, commands, timeout_sec=2.0)
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/calibration/stop")
    async def line_sensor_calibration_stop(serial_number: str):
        results = await _send_line_sensor_cmds(
            serial_number,
            ["STOP CAL", "GET CAL"],
            timeout_sec=2.0,
        )
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/calibration")
    async def line_sensor_calibration(serial_number: str, payload: LineSensorCalibrationPayload):
        if len(payload.min_raw) != 5 or len(payload.max_raw) != 5:
            raise HTTPException(status_code=400, detail="min_raw and max_raw must have 5 values")
        commands: list[str] = []
        for idx, (min_value_raw, max_value_raw) in enumerate(zip(payload.min_raw, payload.max_raw)):
            min_value = int(min_value_raw)
            max_value = int(max_value_raw)
            if min_value < 0 or min_value > 4095 or max_value < 0 or max_value > 4095:
                raise HTTPException(status_code=400, detail="calibration values must be between 0 and 4095")
            if max_value <= min_value:
                raise HTTPException(status_code=400, detail="each max_raw value must be greater than min_raw")
            commands.append(f"SET CAL {idx} {min_value} {max_value}")
        if payload.save:
            commands.append("SAVE CAL")
        results = await _send_line_sensor_cmds(serial_number, commands, timeout_sec=2.0)
        return {"ok": True, "results": results}

    app.mount("/static", StaticFiles(directory=os.path.join(static_dir, "static")), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(static_dir, "index.html"))

    @app.get("/distance-sensor", include_in_schema=False)
    async def distance_sensor_page():
        return FileResponse(
            os.path.join(static_dir, "distance-sensor.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/control-hub", include_in_schema=False)
    async def control_hub_page():
        return FileResponse(
            os.path.join(static_dir, "control-hub.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/line-sensor", include_in_schema=False)
    async def line_sensor_page():
        return FileResponse(
            os.path.join(static_dir, "line-sensor.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/traction-motor-config", include_in_schema=False)
    async def traction_motor_config_page():
        return FileResponse(
            os.path.join(static_dir, "traction-motor-config.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/traction-pid-tuner", include_in_schema=False)
    async def traction_pid_tuner_page():
        return FileResponse(
            os.path.join(static_dir, "traction-pid-tuner.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/traction-position-tuner", include_in_schema=False)
    async def traction_position_tuner_page():
        return FileResponse(
            os.path.join(static_dir, "traction-position-tuner.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/color", include_in_schema=False)
    async def color_page():
        return FileResponse(
            os.path.join(static_dir, "color.html"),
            headers={"Cache-Control": "no-store"},
        )

    return app


def start_webview_server(
    db_path: str,
    comms_log_path: str,
    host: str,
    port: int,
    enable_realtime_stream: bool = True,
):
    app = create_webview_app(
        db_path=db_path,
        comms_log_path=comms_log_path,
        enable_realtime_stream=enable_realtime_stream,
    )
    config = uvicorn.Config(
        app=app,
        host=host,
        port=int(port),
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
