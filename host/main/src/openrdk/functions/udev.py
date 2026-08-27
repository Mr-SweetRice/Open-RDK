import os
import sys
import threading
from typing import Any

try:
    import pyudev as _pyudev
    _UDEV_AVAILABLE = sys.platform != "win32"
except ImportError:
    _pyudev = None  # type: ignore[assignment]
    _UDEV_AVAILABLE = False

from ..constants import (
    DEFAULT_MODULE_TYPE,
    ID_MODEL_ESP32,
    ID_VENDOR_ESP32,
    LINK_STATUS_LIVE,
    LINK_STATUS_NOT_LIVE,
    MANUFACTURER_ESP32,
    MESSAGE_TYPE_CONTROL,
    SERIAL_NUMBER_ESP32_SHORT,
    STATUS_OFFLINE_DISCONNECTED,
    STATUS_ONLINE_CONNECTED,
    DEFAULT_USB_DENY_PATH_PREFIXES,
    ESP32_USB_SERIAL_ADAPTER_IDS,
    USB_DENY_PATH_PREFIXES_ENV,
)
from . import _state
from .comms_log import _now_iso
from .keepalive import _start_keepalive_monitor, _stop_keepalive_monitor
from .registry import (
    _find_device_by_serial,
    _load_db,
    _normalize_device_name,
    _normalize_message_type_name,
    _normalize_module_type,
    _normalize_traction_out_value,
    _save_db,
    _update_registry_by_serial,
    get_active_serial_baud,
)
from .transport import _probe_link_via_handshake


class _WindowsSerialDevice:
    def __init__(self, port_info: Any, action: str = "add"):
        self.action = action
        self.device_node = str(getattr(port_info, "device", "") or "")
        self.sys_name = str(getattr(port_info, "name", "") or self.device_node)
        serial_number = str(getattr(port_info, "serial_number", "") or "").strip()
        if not serial_number:
            serial_number = self.device_node
        manufacturer = str(getattr(port_info, "manufacturer", "") or "").strip()
        product = str(getattr(port_info, "product", "") or getattr(port_info, "description", "") or "").strip()
        vid = getattr(port_info, "vid", None)
        pid = getattr(port_info, "pid", None)
        vendor_id = f"{int(vid):04x}" if isinstance(vid, int) else ""
        model_id = f"{int(pid):04x}" if isinstance(pid, int) else ""
        product_norm = product.replace(" ", "_").replace("/", "_") if product else ""
        if manufacturer and product_norm and serial_number:
            serial_full = f"{manufacturer}_{product_norm}_{serial_number}"
        else:
            serial_full = serial_number
        self.properties = {
            "ID_SERIAL_SHORT": serial_number,
            "ID_USB_SERIAL_SHORT": serial_number,
            "ID_SERIAL": serial_full,
            "ID_VENDOR": manufacturer,
            "ID_VENDOR_ID": vendor_id,
            "ID_MODEL_ID": model_id,
        }

    def find_parent(self, *_args, **_kwargs):
        return None

    def with_action(self, action: str):
        clone = object.__new__(_WindowsSerialDevice)
        clone.action = action
        clone.device_node = self.device_node
        clone.sys_name = self.sys_name
        clone.properties = dict(self.properties)
        return clone


def _list_windows_serial_devices() -> list[_WindowsSerialDevice]:
    try:
        from serial.tools import list_ports
    except Exception as exc:
        print(f"[windows-serial] list_ports unavailable: {exc}", flush=True)
        return []

    devices: list[_WindowsSerialDevice] = []
    for port_info in list_ports.comports():
        dev = _WindowsSerialDevice(port_info, action="add")
        if matches(dev):
            devices.append(dev)
    return devices


def _mark_all_devices_offline(db_path: str):
    data = _load_db(db_path)
    now = _now_iso()
    for item in data["devices"]:
        item["status"] = STATUS_OFFLINE_DISCONNECTED
        item["last_event_at"] = now
        item["link_live"] = False
        item["link_status"] = LINK_STATUS_NOT_LIVE
        item["last_link_check_at"] = now
        item["message_type"] = _normalize_message_type_name(item.get("message_type"))
        item["traction_out_value"] = _normalize_traction_out_value(item.get("traction_out_value"))
        item["telemetry_requested"] = False
        item["telemetry_active"] = False
        if not isinstance(item.get("error_count"), int) or int(item.get("error_count", 0)) < 0:
            item["error_count"] = 0
        item["name"] = _normalize_device_name(
            item.get("name"), item.get("module_type") or item.get("firmware_module"),
        )
    _save_db(db_path, data)


def _device_node(dev: Any) -> str | None:
    if dev.device_node:
        return dev.device_node
    if dev.sys_name:
        return f"/dev/{dev.sys_name}"
    return None


def _split_prefixes(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_USB_DENY_PATH_PREFIXES
    prefixes: list[str] = []
    for raw in value.replace(";", ",").split(","):
        cleaned = raw.strip().strip("/")
        if cleaned:
            prefixes.append(cleaned)
    return tuple(prefixes)


def _configured_usb_deny_path_prefixes() -> tuple[str, ...]:
    return _split_prefixes(os.getenv(USB_DENY_PATH_PREFIXES_ENV))


def _sys_path(dev: Any) -> str:
    for attr in ("sys_path", "device_path"):
        value = getattr(dev, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _usb_path_tokens(dev: Any) -> set[str]:
    tokens: set[str] = set()
    path = _sys_path(dev)
    for part in path.replace("\\", "/").split("/"):
        if part.startswith("usb"):
            tokens.add(part)
        if "-" in part and part[:1].isdigit():
            tokens.add(part)
    for key in ("ID_PATH", "ID_PATH_TAG"):
        value = dev.properties.get(key)
        if isinstance(value, str):
            normalized = value.replace("_", ".").replace(":", ".")
            for part in normalized.split("-"):
                if part[:1].isdigit():
                    tokens.add(part)
    return tokens


def _usb_token_matches_prefix(token: str, prefix: str) -> bool:
    safe_token = str(token or "").strip().strip("/")
    safe_prefix = str(prefix or "").strip().strip("/")
    if not safe_token or not safe_prefix:
        return False
    return (
        safe_token == safe_prefix
        or safe_token.startswith(f"{safe_prefix}.")
        or safe_token.startswith(f"{safe_prefix}:")
    )


def _sys_path_text_for_match(value: str) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _is_denied_usb_path(dev: Any) -> bool:
    prefixes = _configured_usb_deny_path_prefixes()
    if not prefixes:
        return False
    sys_path = _sys_path_text_for_match(_sys_path(dev))
    tokens = _usb_path_tokens(dev)
    for prefix in prefixes:
        safe_prefix = _sys_path_text_for_match(prefix)
        if not safe_prefix:
            continue
        if "/" in safe_prefix and safe_prefix in sys_path:
            return True
        for token in tokens:
            if _usb_token_matches_prefix(token, safe_prefix):
                return True
    return False


def _usb_parent_device(dev: Any) -> Any | None:
    try:
        return dev.find_parent("usb", "usb_device")
    except Exception:
        return None


def _read_usb_attr_text(dev: Any | None, attr_name: str) -> str | None:
    if dev is None:
        return None
    try:
        raw = dev.attributes.get(attr_name)
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode(errors="ignore").strip()
    else:
        text = str(raw).strip()
    return text or None


def _device_property_with_fallback(dev: Any, key: str) -> str | None:
    value = dev.properties.get(key)
    if value and str(value).strip():
        return str(value).strip()

    usb_parent = _usb_parent_device(dev)
    if key in ("ID_SERIAL_SHORT", "ID_USB_SERIAL_SHORT"):
        return _read_usb_attr_text(usb_parent, "serial")
    if key == "ID_SERIAL":
        serial = _read_usb_attr_text(usb_parent, "serial")
        manufacturer = _read_usb_attr_text(usb_parent, "manufacturer")
        product = _read_usb_attr_text(usb_parent, "product")
        if serial and manufacturer and product:
            product_norm = product.replace(" ", "_").replace("/", "_")
            return f"{manufacturer}_{product_norm}_{serial}"
        return serial
    if key == "ID_VENDOR":
        return _read_usb_attr_text(usb_parent, "manufacturer")
    if key == "ID_VENDOR_ID":
        value = _read_usb_attr_text(usb_parent, "idVendor")
        return value.lower() if value else None
    if key == "ID_MODEL_ID":
        value = _read_usb_attr_text(usb_parent, "idProduct")
        return value.lower() if value else None
    return None


def _extract_serial(dev: Any) -> str | None:
    for key in ("ID_SERIAL_SHORT", "ID_USB_SERIAL_SHORT", "ID_SERIAL"):
        value = _device_property_with_fallback(dev, key)
        if value:
            text = value.strip()
            if key == "ID_SERIAL" and "_" in text and ":" in text:
                return text.rsplit("_", 1)[-1]
            return text
    return None


def _update_registry(
    dev: Any,
    status: str,
    db_path: str,
    link_live: bool | None = None,
    link_status: str | None = None,
    module_type: str | None = None,
    module_id: int | None = None,
) -> dict | None:
    from .registry import (
        _ensure_device_message_type,
        _ensure_device_traction_out_value,
        _module_id_hex,
        _module_id_to_type,
    )

    with _state._DB_LOCK:
        data = _load_db(db_path)
        devices = data["devices"]
        node = _device_node(dev)
        serial_number = _extract_serial(dev)

        if not serial_number:
            by_node = _find_device_by_serial(devices, node) if node else None
            if by_node:
                serial_number = by_node.get("serial_number")

        if not serial_number:
            return None

        item = _find_device_by_serial(devices, serial_number)
        dirty = False
        link_dirty = False
        if not item:
            item = {"serial_number": serial_number}
            devices.append(item)
            dirty = True

        from .registry import _default_device_message_type
        if not isinstance(item.get("message_type"), str):
            item["message_type"] = _default_device_message_type()
            dirty = True
        elif _ensure_device_message_type(item):
            dirty = True
        if _ensure_device_traction_out_value(item):
            dirty = True

        for field, default in (("error_count", 0), ("last_error_kind", ""), ("last_error_at", "")):
            if not isinstance(item.get(field), type(default)) or (
                field == "error_count" and int(item.get(field, 0)) < 0
            ):
                item[field] = default
                dirty = True
        for bool_field in ("telemetry_requested", "telemetry_active"):
            if not isinstance(item.get(bool_field), bool):
                item[bool_field] = False
                dirty = True

        now = _now_iso()
        if item.get("status") != status:
            item["status"] = status
            item["last_event_at"] = now
            dirty = True

        previous_module_type = _normalize_module_type(
            item.get("module_type") or item.get("firmware_module")
        )
        resolved_module_type = _normalize_module_type(
            module_type
            or _module_id_to_type(module_id)
            or item.get("module_type")
            or item.get("firmware_module")
        )
        module_changed = previous_module_type != resolved_module_type
        for key in ("module_type", "firmware_module"):
            if item.get(key) != resolved_module_type:
                item[key] = resolved_module_type
                dirty = True

        current_name = item.get("name")
        if module_changed or not isinstance(current_name, str) or not current_name.strip():
            if item.get("name") != resolved_module_type:
                item["name"] = resolved_module_type
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

        if node and item.get("device_node") != node:
            item["device_node"] = node
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

        if link_dirty:
            item["last_link_check_at"] = now
            dirty = True

        if dirty:
            _save_db(db_path, data)
        return item


def matches(dev: Any) -> bool:
    if _is_denied_usb_path(dev):
        return False
    serial_short = _device_property_with_fallback(dev, "ID_SERIAL_SHORT")
    if SERIAL_NUMBER_ESP32_SHORT and serial_short == SERIAL_NUMBER_ESP32_SHORT:
        return True
    vendor = _device_property_with_fallback(dev, "ID_VENDOR") or ""
    if MANUFACTURER_ESP32 and MANUFACTURER_ESP32.lower() in vendor.lower():
        return True
    vendor_id = _device_property_with_fallback(dev, "ID_VENDOR_ID")
    model_id = _device_property_with_fallback(dev, "ID_MODEL_ID")
    if (str(vendor_id or "").lower(), str(model_id or "").lower()) in ESP32_USB_SERIAL_ADAPTER_IDS:
        return True
    if ID_VENDOR_ESP32 and vendor_id == ID_VENDOR_ESP32:
        return True
    if ID_VENDOR_ESP32 and ID_MODEL_ESP32:
        if vendor_id == ID_VENDOR_ESP32 and model_id == ID_MODEL_ESP32:
            return True
    return False


def on_attach(dev: Any, db_path: str):
    serial_number = _extract_serial(dev)
    node = dev.device_node
    with _state._FLASH_LOCK:
        if serial_number and serial_number in _state._FLASH_LOCKED_SERIALS:
            return
        if node and node in _state._FLASH_LOCKED_NODES:
            return

    known_message_type = None
    known_module_type = None
    if serial_number:
        with _state._DB_LOCK:
            _data = _load_db(db_path)
            _item = _find_device_by_serial(_data["devices"], serial_number)
            if _item:
                known_message_type = _normalize_message_type_name(_item.get("message_type"))
                known_module_type = _normalize_module_type(
                    _item.get("module_type") or _item.get("firmware_module")
                )

    result = _update_registry(dev, STATUS_ONLINE_CONNECTED, db_path, link_live=False, link_status=LINK_STATUS_NOT_LIVE)
    if serial_number:
        _update_registry_by_serial(serial_number=serial_number, db_path=db_path, telemetry_active=False)

    # Skip the blocking probe when the device is already known with a valid module type.
    # The keepalive does its own HELLO + module query on connect — the probe is only
    # needed the very first time a device is seen.
    skip_handshake_probe = bool(node) and (
        known_message_type == MESSAGE_TYPE_CONTROL
        or bool(known_module_type and known_module_type != DEFAULT_MODULE_TYPE)
    )
    if node and not skip_handshake_probe:
        link_live, module_type, module_id = _probe_link_via_handshake(
            port=node, db_path=db_path, serial_number=serial_number,
            baud=get_active_serial_baud(),
        )
        reported_module_type = module_type
        if not link_live and module_id is None and _normalize_module_type(module_type) == DEFAULT_MODULE_TYPE:
            reported_module_type = None
        link_status = LINK_STATUS_LIVE if link_live else LINK_STATUS_NOT_LIVE
        result = _update_registry(
            dev, STATUS_ONLINE_CONNECTED, db_path,
            link_live=link_live, link_status=link_status,
            module_type=reported_module_type, module_id=module_id,
        )

    resolved_serial = serial_number or (result or {}).get("serial_number")
    _stop_keepalive_monitor(resolved_serial)
    _start_keepalive_monitor(serial_number=resolved_serial, device_node=node, db_path=db_path)


def on_detach(dev: Any, db_path: str):
    result = _update_registry(dev, STATUS_OFFLINE_DISCONNECTED, db_path, link_live=False, link_status=LINK_STATUS_NOT_LIVE)
    if (result or {}).get("serial_number"):
        _update_registry_by_serial(
            serial_number=(result or {}).get("serial_number"),
            db_path=db_path, telemetry_active=False,
        )
    _stop_keepalive_monitor((result or {}).get("serial_number"))


def bootstrap_connected_devices(context: Any, db_path: str):
    _mark_all_devices_offline(db_path)

    boot_count = 0
    for dev in context.list_devices(subsystem="tty"):
        if matches(dev):
            _update_registry(dev, STATUS_ONLINE_CONNECTED, db_path, link_live=False, link_status=LINK_STATUS_NOT_LIVE)
            link_live = False
            module_type = DEFAULT_MODULE_TYPE
            module_id = None
            node = _device_node(dev)
            serial_number = _extract_serial(dev)
            known_message_type = None
            known_module_type = None
            if serial_number:
                with _state._DB_LOCK:
                    data = _load_db(db_path)
                    known = _find_device_by_serial(data["devices"], serial_number)
                    if known:
                        known_message_type = _normalize_message_type_name(
                            known.get("message_type")
                        )
                        known_module_type = _normalize_module_type(
                            known.get("module_type") or known.get("firmware_module")
                        )
            skip_handshake_probe = bool(node) and (
                known_message_type == MESSAGE_TYPE_CONTROL
                or bool(
                    known_module_type
                    and known_module_type != DEFAULT_MODULE_TYPE
                )
            )
            if node and not skip_handshake_probe:
                link_live, module_type, module_id = _probe_link_via_handshake(
                    port=node, db_path=db_path, serial_number=serial_number,
                    baud=get_active_serial_baud(),
                )
            reported_module_type = module_type
            if not link_live and module_id is None and _normalize_module_type(module_type) == DEFAULT_MODULE_TYPE:
                reported_module_type = None
            link_status = LINK_STATUS_LIVE if link_live else LINK_STATUS_NOT_LIVE
            if _update_registry(
                dev, STATUS_ONLINE_CONNECTED, db_path,
                link_live=link_live, link_status=link_status,
                module_type=reported_module_type, module_id=module_id,
            ):
                boot_count += 1
            # Start each monitor as soon as its device is bootstrapped. Waiting
            # until every serial probe completes creates a startup window where
            # the registry says "online" but no worker can service requests.
            _start_keepalive_monitor(
                serial_number=serial_number,
                device_node=node,
                db_path=db_path,
            )


def _run_windows_serial_loop(
    db_path: str,
    stop_event: threading.Event | None = None,
    poll_timeout_sec: float = 0.25,
):
    _mark_all_devices_offline(db_path)
    known_by_node: dict[str, _WindowsSerialDevice] = {}

    while True:
        if stop_event is not None and stop_event.is_set():
            break

        current_by_node = {
            dev.device_node: dev
            for dev in _list_windows_serial_devices()
            if dev.device_node
        }

        for node, dev in current_by_node.items():
            if node not in known_by_node:
                on_attach(dev, db_path)

        for node, dev in list(known_by_node.items()):
            if node not in current_by_node:
                on_detach(dev.with_action("remove"), db_path)

        known_by_node = current_by_node

        if stop_event is not None and stop_event.wait(max(0.05, float(poll_timeout_sec))):
            break


def _handle_monitor_device(dev: Any, db_path: str):
    action = dev.action
    if action not in ("add", "remove"):
        return
    if not matches(dev):
        return
    if action == "add":
        on_attach(dev, db_path)
    elif action == "remove":
        on_detach(dev, db_path)


def run_conex_loop(
    db_path: str,
    stop_event: threading.Event | None = None,
    poll_timeout_sec: float = 0.25,
):
    if not _UDEV_AVAILABLE:
        if sys.platform == "win32":
            _run_windows_serial_loop(
                db_path=db_path,
                stop_event=stop_event,
                poll_timeout_sec=poll_timeout_sec,
            )
            return
        import time
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(max(0.05, float(poll_timeout_sec)))
        return

    import time
    context = _pyudev.Context()
    bootstrap_connected_devices(context, db_path)
    monitor = _pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="tty")

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        timeout = max(0.05, float(poll_timeout_sec))
        dev = monitor.poll(timeout)
        if dev is None:
            continue
        _handle_monitor_device(dev, db_path)
        time.sleep(0.01)


def conex(db_path: str):
    run_conex_loop(db_path=db_path, stop_event=None, poll_timeout_sec=0.25)
