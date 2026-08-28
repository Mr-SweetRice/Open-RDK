import json
import os
import sys
import tempfile
import threading


CONTROL_HUB_MODULE_TYPE = "control_hub_module"
CONTROL_HUB_STATE_DIR_ENV = "CONTROL_HUB_SERVICE_STATE_DIR"
_LOCK = threading.Lock()


def control_hub_state_dir() -> str:
    configured = os.environ.get(CONTROL_HUB_STATE_DIR_ENV, "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(configured)))
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "state"
        )
    return os.path.abspath(os.path.join(base, "openrdk", "control-hub-service"))


def reservation_path() -> str:
    return os.path.join(control_hub_state_dir(), "reserved_devices.json")


def configured_port() -> str:
    try:
        with open(os.path.join(control_hub_state_dir(), "config.json"), "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return ""
    connection = payload.get("connection", {}) if isinstance(payload, dict) else {}
    return str(connection.get("port") or "").strip() if isinstance(connection, dict) else ""


def _load() -> list[dict]:
    try:
        with open(reservation_path(), "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return []
    devices = payload.get("devices", []) if isinstance(payload, dict) else []
    return [dict(item) for item in devices if isinstance(item, dict)]


def _save(devices: list[dict]) -> None:
    path = reservation_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".reserved_devices_", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump({"version": 1, "devices": devices}, fp, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def reserve_control_hub(serial_number: str | None, device_node: str | None) -> None:
    serial = str(serial_number or "").strip()
    node = str(device_node or "").strip()
    if not serial and not node:
        return
    with _LOCK:
        devices = _load()
        match = next((item for item in devices if (
            serial and str(item.get("serial_number") or "") == serial
        ) or (
            node and str(item.get("device_node") or "") == node
        )), None)
        if match is None:
            match = {}
            devices.append(match)
        if serial:
            match["serial_number"] = serial
        if node:
            match["device_node"] = node
        match["module_type"] = CONTROL_HUB_MODULE_TYPE
        _save(devices)


def is_reserved_control_hub(serial_number: str | None, device_node: str | None) -> bool:
    serial = str(serial_number or "").strip()
    node = str(device_node or "").strip()
    with _LOCK:
        devices = _load()
        selected_port = configured_port()
    if node and selected_port and os.path.normcase(node) == os.path.normcase(selected_port):
        return True
    return any(
        (serial and str(item.get("serial_number") or "") == serial)
        or (node and str(item.get("device_node") or "") == node)
        for item in devices
    )
