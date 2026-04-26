import threading

import pyudev

from ..constants import (
    DEFAULT_MODULE_TYPE,
    ID_MODEL_ESP32,
    ID_VENDOR_ESP32,
    LINK_STATUS_LIVE,
    LINK_STATUS_NOT_LIVE,
    MANUFACTURER_ESP32,
    MESSAGE_TYPE_TRACTION_OUT,
    SERIAL_NUMBER_ESP32_SHORT,
    STATUS_OFFLINE_DISCONNECTED,
    STATUS_ONLINE_CONNECTED,
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
    get_device_message_type,
)
from .transport import _probe_link_via_handshake


def _device_node(dev: pyudev.Device) -> str | None:
    if dev.device_node:
        return dev.device_node
    if dev.sys_name:
        return f"/dev/{dev.sys_name}"
    return None


def _usb_parent_device(dev: pyudev.Device) -> pyudev.Device | None:
    try:
        return dev.find_parent("usb", "usb_device")
    except Exception:
        return None


def _read_usb_attr_text(dev: pyudev.Device | None, attr_name: str) -> str | None:
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


def _device_property_with_fallback(dev: pyudev.Device, key: str) -> str | None:
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


def _extract_serial(dev: pyudev.Device) -> str | None:
    for key in ("ID_SERIAL_SHORT", "ID_USB_SERIAL_SHORT", "ID_SERIAL"):
        value = _device_property_with_fallback(dev, key)
        if value:
            text = value.strip()
            if key == "ID_SERIAL" and "_" in text and ":" in text:
                return text.rsplit("_", 1)[-1]
            return text
    return None


def _update_registry(
    dev: pyudev.Device,
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


def matches(dev: pyudev.Device) -> bool:
    serial_short = _device_property_with_fallback(dev, "ID_SERIAL_SHORT")
    if SERIAL_NUMBER_ESP32_SHORT and serial_short == SERIAL_NUMBER_ESP32_SHORT:
        return True
    vendor = _device_property_with_fallback(dev, "ID_VENDOR") or ""
    if MANUFACTURER_ESP32 and MANUFACTURER_ESP32.lower() in vendor.lower():
        return True
    vendor_id = _device_property_with_fallback(dev, "ID_VENDOR_ID")
    model_id = _device_property_with_fallback(dev, "ID_MODEL_ID")
    if ID_VENDOR_ESP32 and vendor_id == ID_VENDOR_ESP32:
        return True
    if ID_VENDOR_ESP32 and ID_MODEL_ESP32:
        if vendor_id == ID_VENDOR_ESP32 and model_id == ID_MODEL_ESP32:
            return True
    return False


def on_attach(dev: pyudev.Device, db_path: str):
    node = dev.device_node
    serial_number = _extract_serial(dev)
    known_message_type = None
    if serial_number:
        known_message_type = get_device_message_type(db_path=db_path, serial_number=serial_number)
    result = _update_registry(dev, STATUS_ONLINE_CONNECTED, db_path, link_live=False, link_status=LINK_STATUS_NOT_LIVE)
    if serial_number:
        _update_registry_by_serial(serial_number=serial_number, db_path=db_path, telemetry_active=False)

    skip_handshake_probe = (
        bool(node)
        and _normalize_message_type_name(known_message_type) == MESSAGE_TYPE_TRACTION_OUT
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


def on_detach(dev: pyudev.Device, db_path: str):
    result = _update_registry(dev, STATUS_OFFLINE_DISCONNECTED, db_path, link_live=False, link_status=LINK_STATUS_NOT_LIVE)
    if (result or {}).get("serial_number"):
        _update_registry_by_serial(
            serial_number=(result or {}).get("serial_number"),
            db_path=db_path, telemetry_active=False,
        )
    _stop_keepalive_monitor((result or {}).get("serial_number"))


def bootstrap_connected_devices(context: pyudev.Context, db_path: str):
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

    boot_count = 0
    monitor_targets: list[tuple[str | None, str | None]] = []
    for dev in context.list_devices(subsystem="tty"):
        if matches(dev):
            _update_registry(dev, STATUS_ONLINE_CONNECTED, db_path, link_live=False, link_status=LINK_STATUS_NOT_LIVE)
            link_live = False
            module_type = DEFAULT_MODULE_TYPE
            module_id = None
            node = _device_node(dev)
            serial_number = _extract_serial(dev)
            if node:
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
            monitor_targets.append((serial_number, node))

    for serial_number, node in monitor_targets:
        _start_keepalive_monitor(serial_number=serial_number, device_node=node, db_path=db_path)


def _handle_monitor_device(dev: pyudev.Device, db_path: str):
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
    import time
    context = pyudev.Context()
    bootstrap_connected_devices(context, db_path)
    monitor = pyudev.Monitor.from_netlink(context)
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
