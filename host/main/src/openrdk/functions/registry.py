import json
import os
import tempfile
import time

from ..constants import (
    BAUD_RATE_MAX,
    BAUD_RATE_MIN,
    COMMON_BAUD_RATES,
    DEFAULT_ACTIVE_MESSAGE_TYPE,
    DEFAULT_MODULE_TYPE,
    DEFAULT_SERIAL_BAUD,
    MESSAGE_TYPE_TELEMETRY,
    MESSAGE_TYPE_ALIASES,
    MESSAGE_TYPES,
    MODULE_ID_TO_TYPE,
    TRACTION_OUT_DEFAULT_VALUE,
    TRACTION_OUT_MAX_VALUE,
    TRACTION_OUT_MIN_VALUE,
)
from . import _state
from .comms_log import _now_iso


def _normalize_module_type(value: str | None) -> str:
    if value:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return DEFAULT_MODULE_TYPE


def _normalize_device_name(value: str | None, module_type: str) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return _normalize_module_type(module_type)


def _module_id_to_type(module_id: int | None) -> str | None:
    if module_id is None:
        return None
    return MODULE_ID_TO_TYPE.get(module_id)


def _module_id_hex(module_id: int | None) -> str | None:
    if module_id is None:
        return None
    return f"0x{module_id:02X}"


def _normalize_message_type_name(name: str | None) -> str:
    if not isinstance(name, str):
        return DEFAULT_ACTIVE_MESSAGE_TYPE
    candidate = name.strip().upper()
    candidate = MESSAGE_TYPE_ALIASES.get(candidate, candidate)
    if candidate in MESSAGE_TYPES:
        return candidate
    return DEFAULT_ACTIVE_MESSAGE_TYPE


def get_active_message_type() -> str:
    with _state._ACTIVE_MESSAGE_LOCK:
        return _state._ACTIVE_MESSAGE_TYPE


def set_active_message_type(name: str | None) -> str:
    normalized = _normalize_message_type_name(name)
    with _state._ACTIVE_MESSAGE_LOCK:
        _state._ACTIVE_MESSAGE_TYPE = normalized
    return normalized


def _default_device_message_type() -> str:
    with _state._ACTIVE_MESSAGE_LOCK:
        return _normalize_message_type_name(_state._ACTIVE_MESSAGE_TYPE)


def _ensure_device_message_type(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    normalized = _normalize_message_type_name(item.get("message_type"))
    if item.get("message_type") != normalized:
        item["message_type"] = normalized
        return True
    return False


def _normalize_traction_out_value(value: int | str | None) -> int:
    if isinstance(value, bool):
        parsed = TRACTION_OUT_DEFAULT_VALUE
    else:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = TRACTION_OUT_DEFAULT_VALUE
    if parsed < TRACTION_OUT_MIN_VALUE:
        return TRACTION_OUT_MIN_VALUE
    if parsed > TRACTION_OUT_MAX_VALUE:
        return TRACTION_OUT_MAX_VALUE
    return parsed


def _ensure_device_traction_out_value(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    normalized = _normalize_traction_out_value(item.get("traction_out_value"))
    if item.get("traction_out_value") != normalized:
        item["traction_out_value"] = normalized
        return True
    return False


def _normalize_serial_baud(value: int | str | None) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid baud rate")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid baud rate")
    if parsed < BAUD_RATE_MIN or parsed > BAUD_RATE_MAX:
        raise ValueError(f"baud rate must be between {BAUD_RATE_MIN} and {BAUD_RATE_MAX}")
    return parsed


def get_active_serial_baud() -> int:
    with _state._ACTIVE_SERIAL_LOCK:
        return _state._ACTIVE_SERIAL_BAUD


def set_active_serial_baud(value: int | str | None) -> int:
    normalized = _normalize_serial_baud(value)
    with _state._ACTIVE_SERIAL_LOCK:
        _state._ACTIVE_SERIAL_BAUD = normalized
    return normalized


def supported_serial_baud_rates() -> list[int]:
    out = sorted({int(r) for r in COMMON_BAUD_RATES if BAUD_RATE_MIN <= int(r) <= BAUD_RATE_MAX})
    default = int(DEFAULT_SERIAL_BAUD)
    if BAUD_RATE_MIN <= default <= BAUD_RATE_MAX and default not in out:
        out.append(default)
        out.sort()
    return out


def supported_message_types() -> list[dict]:
    items: list[dict] = []
    for key, spec in MESSAGE_TYPES.items():
        items.append({
            "name": key,
            "code": spec.code,
            "default_content": spec.default_content,
            "ack_content": spec.ack_content,
        })
    return sorted(items, key=lambda item: item["name"])


def _ensure_db(path: str):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fp:
            json.dump({"devices": []}, fp, indent=2, sort_keys=True)
            fp.write("\n")


def _sanitize_device_entry(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    serial_number = item.get("serial_number")
    if not isinstance(serial_number, str):
        return None
    serial_number = serial_number.strip()
    if not serial_number:
        return None
    cleaned: dict = {"serial_number": serial_number}
    for key in _state._DEVICE_DB_FIELDS:
        if key == "serial_number":
            continue
        if key in item:
            cleaned[key] = item[key]
    return cleaned


def _canonicalize_db(data: dict) -> dict:
    devices_raw = data.get("devices") if isinstance(data, dict) else None
    if not isinstance(devices_raw, list):
        devices_raw = []
    devices: list[dict] = []
    for item in devices_raw:
        sanitized = _sanitize_device_entry(item)
        if sanitized is not None:
            devices.append(sanitized)
    return {"devices": devices}


def _load_db(path: str) -> dict:
    _ensure_db(path)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (json.JSONDecodeError, OSError):
        data = {"devices": []}
    return _canonicalize_db(data)


def _save_db(path: str, data: dict):
    _ensure_db(path)
    folder = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".espressif_devices_", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(_canonicalize_db(data), fp, indent=2, sort_keys=True)
            fp.write("\n")
        for attempt in range(8):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _find_device_by_serial(devices: list, serial_number: str) -> dict | None:
    for item in devices:
        if item.get("serial_number") == serial_number:
            return item
    return None


def _find_device_by_node(devices: list, node: str | None) -> dict | None:
    if not node:
        return None
    for item in devices:
        if item.get("device_node") == node:
            return item
    return None


def _find_active_device_by_node(devices: list, node: str | None) -> dict | None:
    if not node:
        return None
    fallback = None
    for item in devices:
        if item.get("device_node") != node:
            continue
        if fallback is None:
            fallback = item
        if item.get("status") == "online connected":
            return item
    return fallback


def get_device_message_type(db_path: str, serial_number: str) -> str | None:
    if not serial_number:
        return None
    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        normalized = _normalize_message_type_name(item.get("message_type"))
        if item.get("message_type") != normalized:
            item["message_type"] = normalized
            _save_db(db_path, data)
        return normalized


def get_device_traction_out_value(db_path: str, serial_number: str) -> int | None:
    if not serial_number:
        return None
    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        normalized = _normalize_traction_out_value(item.get("traction_out_value"))
        if item.get("traction_out_value") != normalized:
            item["traction_out_value"] = normalized
            _save_db(db_path, data)
        return normalized


def set_device_message_type(
    db_path: str,
    serial_number: str,
    message_type: str | None,
) -> dict | None:
    if not serial_number:
        return None
    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        normalized = _normalize_message_type_name(message_type)
        dirty = False
        if normalized != MESSAGE_TYPE_TELEMETRY:
            if item.get("telemetry_requested") is not False:
                item["telemetry_requested"] = False
                dirty = True
            if item.get("telemetry_active") is not False:
                item["telemetry_active"] = False
                dirty = True
        if item.get("message_type") != normalized:
            item["message_type"] = normalized
            dirty = True
        if dirty:
            _save_db(db_path, data)
        updated = dict(item)
    _state._wake_keepalive_monitor(serial_number)
    return updated


def set_device_traction_out_value(
    db_path: str,
    serial_number: str,
    traction_out_value: int | str | None,
) -> dict | None:
    if not serial_number:
        return None
    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        normalized = _normalize_traction_out_value(traction_out_value)
        if item.get("traction_out_value") != normalized:
            item["traction_out_value"] = normalized
            _save_db(db_path, data)
        return dict(item)


def get_device_snapshot(db_path: str, serial_number: str) -> dict | None:
    if not serial_number:
        return None
    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        return dict(item)


def list_device_snapshots(db_path: str) -> list[dict]:
    with _state._DB_LOCK:
        data = _load_db(db_path)
        return [dict(item) for item in data.get("devices", []) if isinstance(item, dict)]


def _update_registry_by_serial(
    serial_number: str,
    db_path: str,
    status: str | None = None,
    device_node: str | None = None,
    link_live: bool | None = None,
    link_status: str | None = None,
    module_type: str | None = None,
    module_id: int | None = None,
    firmware_version: str | None = None,
    expected_page: str | None = None,
    expected_page_version: str | None = None,
    telemetry_requested: bool | None = None,
    telemetry_active: bool | None = None,
    error_count_delta: int = 0,
    error_count_reset: bool = False,
    error_kind: str | None = None,
) -> dict | None:
    if not serial_number:
        return None

    with _state._DB_LOCK:
        data = _load_db(db_path)
        devices = data["devices"]
        item = _find_device_by_serial(devices, serial_number)
        if not item:
            return None

        now = _now_iso()
        dirty = False
        link_dirty = False

        for field, default in (
            ("error_count", 0),
            ("last_error_kind", ""),
            ("last_error_at", ""),
        ):
            if not isinstance(item.get(field), type(default)) or (
                field == "error_count" and int(item.get(field, 0)) < 0
            ):
                item[field] = default
                dirty = True

        if not isinstance(item.get("message_type"), str):
            item["message_type"] = _default_device_message_type()
            dirty = True
        elif _ensure_device_message_type(item):
            dirty = True
        if _ensure_device_traction_out_value(item):
            dirty = True
        for bool_field in ("telemetry_requested", "telemetry_active"):
            if not isinstance(item.get(bool_field), bool):
                item[bool_field] = False
                dirty = True

        if status is not None and item.get("status") != status:
            item["status"] = status
            item["last_event_at"] = now
            dirty = True
        if device_node and item.get("device_node") != device_node:
            item["device_node"] = device_node
            dirty = True

        previous_module_type = _normalize_module_type(
            item.get("module_type") or item.get("firmware_module")
        )
        module_changed = False

        if module_type is not None:
            resolved = _normalize_module_type(module_type)
            for key in ("module_type", "firmware_module"):
                if item.get(key) != resolved:
                    item[key] = resolved
                    module_changed = True
                    dirty = True
        elif module_id is not None:
            mapped = _module_id_to_type(module_id)
            if mapped:
                resolved = _normalize_module_type(mapped)
                for key in ("module_type", "firmware_module"):
                    if item.get(key) != resolved:
                        item[key] = resolved
                        module_changed = True
                        dirty = True

        current_module_type = _normalize_module_type(
            item.get("module_type") or item.get("firmware_module")
        )
        if previous_module_type != current_module_type:
            module_changed = True

        current_name = item.get("name")
        if module_changed or not isinstance(current_name, str) or not current_name.strip():
            desired_name = current_module_type
            if item.get("name") != desired_name:
                item["name"] = desired_name
                dirty = True

        if module_id is not None:
            module_id_val = int(module_id) & 0xFF
            module_id_hex_val = _module_id_hex(module_id)
            if item.get("module_id") != module_id_val:
                item["module_id"] = module_id_val
                dirty = True
            if item.get("module_id_hex") != module_id_hex_val:
                item["module_id_hex"] = module_id_hex_val
                dirty = True

        for key, value in (
            ("firmware_version", firmware_version),
            ("expected_page", expected_page),
            ("expected_page_version", expected_page_version),
        ):
            if value is not None and item.get(key) != str(value).strip():
                item[key] = str(value).strip()
                dirty = True
        if link_live is not None:
            value = bool(link_live)
            if item.get("link_live") != value:
                item["link_live"] = value
                link_dirty = True
                dirty = True
        if link_status is not None and item.get("link_status") != link_status:
            item["link_status"] = link_status
            link_dirty = True
            dirty = True

        if telemetry_requested is not None:
            val = bool(telemetry_requested)
            if item.get("telemetry_requested") != val:
                item["telemetry_requested"] = val
                dirty = True
        if telemetry_active is not None:
            val = bool(telemetry_active)
            if item.get("telemetry_active") != val:
                item["telemetry_active"] = val
                dirty = True

        if error_count_delta:
            try:
                delta = int(error_count_delta)
            except (TypeError, ValueError):
                delta = 0
            if delta:
                next_count = max(0, int(item.get("error_count", 0)) + delta)
                if item.get("error_count") != next_count:
                    item["error_count"] = next_count
                    dirty = True
                normalized_kind = str(error_kind or "").strip()
                if normalized_kind:
                    if item.get("last_error_kind") != normalized_kind:
                        item["last_error_kind"] = normalized_kind
                        dirty = True
                    item["last_error_at"] = now
                    dirty = True
        elif error_count_reset:
            if int(item.get("error_count", 0)) != 0:
                item["error_count"] = 0
                dirty = True

        if link_dirty:
            item["last_link_check_at"] = now
            dirty = True

        if dirty:
            _save_db(db_path, data)
        return item


def set_device_name(db_path: str, serial_number: str, name: str | None) -> dict | None:
    if not serial_number:
        return None
    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        module_type = _normalize_module_type(
            item.get("module_type") or item.get("firmware_module")
        )
        desired_name = _normalize_device_name(name, module_type)
        if item.get("name") != desired_name:
            item["name"] = desired_name
            _save_db(db_path, data)
        return dict(item)


def clear_devices_registry(db_path: str) -> int:
    with _state._DB_LOCK:
        data = _load_db(db_path)
        devices = data.get("devices")
        if not isinstance(devices, list):
            data["devices"] = []
            _save_db(db_path, data)
            return 0
        cleared = len(devices)
        if cleared > 0:
            data["devices"] = []
            _save_db(db_path, data)
        return cleared


def set_device_telemetry_requested(
    db_path: str,
    serial_number: str,
    enabled: bool,
) -> dict | None:
    if not serial_number:
        return None
    updated = _update_registry_by_serial(
        serial_number=serial_number,
        db_path=db_path,
        telemetry_requested=bool(enabled),
        telemetry_active=None,
    )
    _state._wake_keepalive_monitor(serial_number)
    return updated
