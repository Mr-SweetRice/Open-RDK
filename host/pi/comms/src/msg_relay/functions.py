import time
import json
import os
import tempfile
import threading
import serial
import pyudev
from datetime import datetime
from .constants import (
    SERIAL_NUMBER_ESP32_SHORT,
    ID_VENDOR_ESP32,
    ID_MODEL_ESP32,
    MANUFACTURER_ESP32,
    STATUS_ONLINE_CONNECTED,
    STATUS_OFFLINE_DISCONNECTED,
    LINK_STATUS_LIVE,
    LINK_STATUS_NOT_LIVE,
    DEFAULT_SERIAL_BAUD,
    FRAME_SYNC_BYTES,
    HOST_MODULE_ID,
    HELLO_MESSAGE_BYTES,
    HELLO_ACK_BYTES,
    PING_MESSAGE_BYTES,
    PONG_MESSAGE_BYTES,
    MODULE_QUERY_MESSAGE_BYTES,
    MODULE_INFO_PREFIX_BYTE,
    HELLO_ACK_TIMEOUT_SEC,
    PING_PONG_TIMEOUT_SEC,
    MODULE_QUERY_TIMEOUT_SEC,
    HELLO_READ_TIMEOUT_SEC,
    HELLO_OPEN_DELAY_SEC,
    MODULE_TYPE_MAX_BYTES,
    KEEPALIVE_PING_INTERVAL_SEC,
    KEEPALIVE_PING_TIMEOUT_SEC,
    HOST_TIMEZONE,
    HOST_TIMESTAMP_FORMAT,
    DEFAULT_MODULE_TYPE,
    MODULE_ID_TO_TYPE,
    DEFAULT_COMMS_LOG_PATH,
)

_DB_LOCK = threading.RLock()
_COMMS_LOG_LOCK = threading.Lock()
_MONITOR_LOCK = threading.Lock()
_KEEPALIVE_STOPS: dict[str, threading.Event] = {}
_KEEPALIVE_THREADS: dict[str, threading.Thread] = {}
_COMMS_LOG_PATH = DEFAULT_COMMS_LOG_PATH
_DEVICE_DB_FIELDS = (
    "serial_number",
    "name",
    "status",
    "device_node",
    "module_type",
    "firmware_module",
    "module_id",
    "module_id_hex",
    "link_live",
    "link_status",
    "last_event_at",
    "last_link_check_at",
)

def open_serial(port: str, baud: int, timeout: float):
    return serial.Serial(port, baudrate=baud, timeout=timeout)

def relay_loop(ser: serial.Serial):
    """
    Minimal loop: reads lines from ESP and prints them.
    Extend this later to parse messages and forward to network/MQTT/etc.
    """
    while True:
        line = ser.readline()
        if not line:
            continue
        text = line.decode(errors="ignore").strip()
        if text:
            print(f"[esp] {text}", flush=True)

def run_with_retries(port: str, baud: int, timeout: float, retry_delay: float):
    while True:
        try:
            print(f"[comms] Opening serial {port} @ {baud}", flush=True)
            with open_serial(port, baud, timeout) as ser:
                relay_loop(ser)
        except Exception as exc:
            print(f"[comms] Error: {exc} — retrying in {retry_delay}s", flush=True)
            time.sleep(retry_delay)

def _now_iso() -> str:
    now = datetime.now(HOST_TIMEZONE)
    return now.strftime(HOST_TIMESTAMP_FORMAT)


def configure_comms_log_path(path: str) -> str:
    global _COMMS_LOG_PATH
    resolved = os.path.abspath(path)
    folder = os.path.dirname(resolved) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(resolved):
        with open(resolved, "a", encoding="utf-8"):
            pass
    _COMMS_LOG_PATH = resolved
    print(f"[comms-db] Using comms log path: {resolved}", flush=True)
    return resolved


def _ensure_comms_log_path() -> str:
    path = _COMMS_LOG_PATH or DEFAULT_COMMS_LOG_PATH
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "a", encoding="utf-8"):
            pass
    return path


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

def _device_node(dev: pyudev.Device) -> str | None:
    if dev.device_node:
        return dev.device_node
    if dev.sys_name:
        return f"/dev/{dev.sys_name}"
    return None

def _extract_serial(dev: pyudev.Device) -> str | None:
    props = dev.properties
    for key in ("ID_SERIAL_SHORT", "ID_USB_SERIAL_SHORT", "ID_SERIAL"):
        value = props.get(key)
        if value and value.strip():
            text = value.strip()
            if key == "ID_SERIAL" and "_" in text and ":" in text:
                return text.rsplit("_", 1)[-1]
            return text
    return None

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
    for key in _DEVICE_DB_FIELDS:
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
        os.replace(tmp_path, path)
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
        if item.get("status") == STATUS_ONLINE_CONNECTED:
            return item
    return fallback


def _frame_payload(payload: bytes, sync_bytes: bytes, module_id: int | None = HOST_MODULE_ID) -> bytes:
    if not payload:
        return b""
    module_byte = bytes([(module_id or 0) & 0xFF])
    return (sync_bytes or b"") + module_byte + payload


def _find_framed_payload(
    buffer: bytes,
    sync_bytes: bytes,
    expected_payload: bytes,
) -> tuple[bool, int | None]:
    if not buffer or not expected_payload:
        return False, None

    sync = sync_bytes or b""
    base = len(sync)
    needed = base + 1 + len(expected_payload)
    if len(buffer) < needed:
        return False, None

    for start in range(0, len(buffer) - needed + 1):
        if sync and buffer[start : start + base] != sync:
            continue
        module_id = buffer[start + base]
        payload_start = start + base + 1
        payload_end = payload_start + len(expected_payload)
        if buffer[payload_start:payload_end] == expected_payload:
            return True, module_id
    return False, None


def _append_communication_event(
    db_path: str,
    device_node: str | None,
    phase: str,
    direction: str,
    payload: bytes,
    serial_number: str | None = None,
):
    if not payload:
        return

    resolved_serial = serial_number
    if not resolved_serial and device_node:
        with _DB_LOCK:
            data = _load_db(db_path)
            devices = data["devices"]
            item = _find_active_device_by_node(devices, device_node) or _find_device_by_node(
                devices, device_node
            )
            if item is not None:
                resolved_serial = item.get("serial_number")

    direction_norm = (direction or "").strip().lower()
    if direction_norm == "tx":
        sender = "host"
    elif direction_norm == "rx":
        sender = resolved_serial or device_node or "device"
    else:
        sender = resolved_serial or "unknown"

    event = {
        "sender": sender,
        "raw_hex": payload.hex(),
    }

    log_path = _ensure_comms_log_path()
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    with _COMMS_LOG_LOCK:
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write(line)
            fp.write("\n")

def _update_registry(
    dev: pyudev.Device,
    status: str,
    db_path: str,
    link_live: bool | None = None,
    link_status: str | None = None,
    module_type: str | None = None,
    module_id: int | None = None,
) -> dict | None:
    with _DB_LOCK:
        data = _load_db(db_path)
        devices = data["devices"]
        node = _device_node(dev)
        serial_number = _extract_serial(dev)

        if not serial_number:
            by_node = _find_device_by_node(devices, node)
            if by_node:
                serial_number = by_node.get("serial_number")

        if not serial_number:
            print(f"[db] Could not resolve serial for event {dev.action} ({dev.sys_name})", flush=True)
            return None

        item = _find_device_by_serial(devices, serial_number)
        dirty = False
        link_dirty = False
        if not item:
            item = {"serial_number": serial_number}
            devices.append(item)
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
        if item.get("module_type") != resolved_module_type:
            item["module_type"] = resolved_module_type
            dirty = True
        # Keep legacy key for compatibility with existing tooling.
        if item.get("firmware_module") != resolved_module_type:
            item["firmware_module"] = resolved_module_type
            dirty = True

        current_name = item.get("name")
        if module_changed or not isinstance(current_name, str) or not current_name.strip():
            desired_name = resolved_module_type
            if item.get("name") != desired_name:
                item["name"] = desired_name
                dirty = True

        if module_id is not None:
            module_id_val = int(module_id) & 0xFF
            module_id_hex = _module_id_hex(module_id)
            if item.get("module_id") != module_id_val:
                item["module_id"] = module_id_val
                dirty = True
            if item.get("module_id_hex") != module_id_hex:
                item["module_id_hex"] = module_id_hex
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
        if link_status is not None:
            if item.get("link_status") != link_status:
                item["link_status"] = link_status
                link_dirty = True
                dirty = True

        if link_dirty:
            item["last_link_check_at"] = now
            dirty = True

        if dirty:
            _save_db(db_path, data)
        return item


def _update_registry_by_serial(
    serial_number: str,
    db_path: str,
    status: str | None = None,
    device_node: str | None = None,
    link_live: bool | None = None,
    link_status: str | None = None,
    module_type: str | None = None,
    module_id: int | None = None,
) -> dict | None:
    if not serial_number:
        return None

    with _DB_LOCK:
        data = _load_db(db_path)
        devices = data["devices"]
        item = _find_device_by_serial(devices, serial_number)
        if not item:
            return None

        now = _now_iso()
        dirty = False
        link_dirty = False

        if status is not None:
            if item.get("status") != status:
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
            if item.get("module_type") != resolved:
                item["module_type"] = resolved
                module_changed = True
                dirty = True
            if item.get("firmware_module") != resolved:
                item["firmware_module"] = resolved
                module_changed = True
                dirty = True
        elif module_id is not None:
            mapped = _module_id_to_type(module_id)
            if mapped:
                resolved = _normalize_module_type(mapped)
                if item.get("module_type") != resolved:
                    item["module_type"] = resolved
                    module_changed = True
                    dirty = True
                if item.get("firmware_module") != resolved:
                    item["firmware_module"] = resolved
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
            module_id_hex = _module_id_hex(module_id)
            if item.get("module_id") != module_id_val:
                item["module_id"] = module_id_val
                dirty = True
            if item.get("module_id_hex") != module_id_hex:
                item["module_id_hex"] = module_id_hex
                dirty = True

        if link_live is not None:
            value = bool(link_live)
            if item.get("link_live") != value:
                item["link_live"] = value
                link_dirty = True
                dirty = True
        if link_status is not None:
            if item.get("link_status") != link_status:
                item["link_status"] = link_status
                link_dirty = True
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
    with _DB_LOCK:
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


def _send_and_wait(
    ser: serial.Serial,
    port: str,
    tx_bytes: bytes,
    expected_bytes: bytes,
    timeout_sec: float,
    phase: str,
    db_path: str,
    serial_number: str | None = None,
    sync_bytes: bytes = FRAME_SYNC_BYTES,
) -> tuple[bool, int | None]:
    if not tx_bytes:
        print(f"[{phase}] skipped: empty TX payload", flush=True)
        return False, None
    if not expected_bytes:
        print(f"[{phase}] skipped: empty expected payload", flush=True)
        return False, None

    framed_tx = _frame_payload(tx_bytes, sync_bytes, module_id=HOST_MODULE_ID)

    ser.reset_input_buffer()
    ser.write(framed_tx)
    ser.flush()
    print(f"[{phase}] TX {port}: {framed_tx.hex()}", flush=True)
    _append_communication_event(
        db_path=db_path,
        device_node=port,
        phase=phase,
        direction="tx",
        payload=framed_tx,
        serial_number=serial_number,
    )

    deadline = time.monotonic() + max(timeout_sec, 0.1)
    rx_buffer = b""
    while time.monotonic() < deadline:
        chunk = ser.read(64)
        if not chunk:
            continue
        rx_buffer += chunk
        if len(rx_buffer) > 256:
            rx_buffer = rx_buffer[-256:]

        print(f"[{phase}] RX {port}: {chunk.hex()}", flush=True)
        _append_communication_event(
            db_path=db_path,
            device_node=port,
            phase=phase,
            direction="rx",
            payload=chunk,
            serial_number=serial_number,
        )
        matched, rx_module_id = _find_framed_payload(rx_buffer, sync_bytes, expected_bytes)
        if matched:
            print(
                f"[{phase}] expected bytes received from {port} "
                f"(module_id={_module_id_hex(rx_module_id) or 'unknown'})",
                flush=True,
            )
            return True, rx_module_id

    print(f"[{phase}] timeout on {port} after {timeout_sec:.2f}s", flush=True)
    return False, None


def _query_module_type(
    ser: serial.Serial,
    port: str,
    query_bytes: bytes,
    response_prefix_byte: int,
    timeout_sec: float,
    max_payload_bytes: int,
    db_path: str,
    serial_number: str | None = None,
    sync_bytes: bytes = FRAME_SYNC_BYTES,
) -> tuple[str, int | None]:
    if not query_bytes:
        return DEFAULT_MODULE_TYPE, None

    framed_query = _frame_payload(query_bytes, sync_bytes, module_id=HOST_MODULE_ID)
    ser.reset_input_buffer()
    ser.write(framed_query)
    ser.flush()
    print(f"[module] TX {port}: {framed_query.hex()}", flush=True)
    _append_communication_event(
        db_path=db_path,
        device_node=port,
        phase="module",
        direction="tx",
        payload=framed_query,
        serial_number=serial_number,
    )

    deadline = time.monotonic() + max(timeout_sec, 0.2)
    rx_buffer = b""
    while time.monotonic() < deadline:
        chunk = ser.read(64)
        if not chunk:
            continue

        rx_buffer += chunk
        if len(rx_buffer) > 512:
            rx_buffer = rx_buffer[-512:]
        _append_communication_event(
            db_path=db_path,
            device_node=port,
            phase="module",
            direction="rx",
            payload=chunk,
            serial_number=serial_number,
        )

        sync = sync_bytes or b""
        base = len(sync)
        min_needed = base + 3  # [SYNC][MODULE_ID][PREFIX][LEN]
        for start in range(0, len(rx_buffer) - min_needed + 1):
            if sync and rx_buffer[start : start + base] != sync:
                continue

            module_id = rx_buffer[start + base]
            prefix = rx_buffer[start + base + 1]
            if prefix != response_prefix_byte:
                continue

            name_len = rx_buffer[start + base + 2]
            if name_len <= 0 or name_len > max_payload_bytes:
                print(f"[module] invalid length from {port}: {name_len}", flush=True)
                return DEFAULT_MODULE_TYPE, module_id

            payload_start = start + base + 3
            end = payload_start + name_len
            if len(rx_buffer) < end:
                continue

            payload = rx_buffer[payload_start:end]
            module_type = _normalize_module_type(payload.decode("utf-8", errors="ignore"))
            print(
                f"[module] RX {port}: {module_type} "
                f"(module_id={_module_id_hex(module_id) or 'unknown'})",
                flush=True,
            )
            return module_type, module_id

    print(f"[module] timeout on {port} after {timeout_sec:.2f}s", flush=True)
    return DEFAULT_MODULE_TYPE, None


def _probe_link_via_handshake(
    port: str,
    db_path: str,
    serial_number: str | None = None,
    baud: int = DEFAULT_SERIAL_BAUD,
    hello_message: bytes = HELLO_MESSAGE_BYTES,
    ack_message: bytes = HELLO_ACK_BYTES,
    ping_message: bytes = PING_MESSAGE_BYTES,
    pong_message: bytes = PONG_MESSAGE_BYTES,
    module_query_message: bytes = MODULE_QUERY_MESSAGE_BYTES,
    sync_bytes: bytes = FRAME_SYNC_BYTES,
    module_info_prefix_byte: int = MODULE_INFO_PREFIX_BYTE,
    ack_timeout_sec: float = HELLO_ACK_TIMEOUT_SEC,
    ping_timeout_sec: float = PING_PONG_TIMEOUT_SEC,
    module_timeout_sec: float = MODULE_QUERY_TIMEOUT_SEC,
    read_timeout_sec: float = HELLO_READ_TIMEOUT_SEC,
    open_delay_sec: float = HELLO_OPEN_DELAY_SEC,
) -> tuple[bool, str, int | None]:
    try:
        with serial.Serial(port, baudrate=baud, timeout=read_timeout_sec, write_timeout=read_timeout_sec) as ser:
            if open_delay_sec > 0:
                time.sleep(open_delay_sec)

            ack_ok, ack_module_id = _send_and_wait(
                ser=ser,
                port=port,
                tx_bytes=hello_message,
                expected_bytes=ack_message,
                timeout_sec=ack_timeout_sec,
                phase="hello",
                db_path=db_path,
                serial_number=serial_number,
                sync_bytes=sync_bytes,
            )
            if not ack_ok:
                return False, DEFAULT_MODULE_TYPE, ack_module_id

            ping_ok, ping_module_id = _send_and_wait(
                ser=ser,
                port=port,
                tx_bytes=ping_message,
                expected_bytes=pong_message,
                timeout_sec=ping_timeout_sec,
                phase="ping",
                db_path=db_path,
                serial_number=serial_number,
                sync_bytes=sync_bytes,
            )
            detected_module_id = ping_module_id if ping_module_id is not None else ack_module_id
            if ping_ok:
                print(f"[link] live confirmed on {port}", flush=True)
                module_type, query_module_id = _query_module_type(
                    ser=ser,
                    port=port,
                    query_bytes=module_query_message,
                    response_prefix_byte=module_info_prefix_byte,
                    timeout_sec=module_timeout_sec,
                    max_payload_bytes=MODULE_TYPE_MAX_BYTES,
                    db_path=db_path,
                    serial_number=serial_number,
                    sync_bytes=sync_bytes,
                )
                if query_module_id is not None:
                    detected_module_id = query_module_id
                if module_type == DEFAULT_MODULE_TYPE:
                    module_type = _normalize_module_type(_module_id_to_type(detected_module_id))
                return True, module_type, detected_module_id

            print(f"[link] not live on {port}", flush=True)
            module_type = _normalize_module_type(_module_id_to_type(detected_module_id))
            return False, module_type, detected_module_id
    except Exception as exc:
        print(f"[link] probe error on {port}: {exc}", flush=True)
        return False, DEFAULT_MODULE_TYPE, None


def _stop_keepalive_monitor(serial_number: str | None):
    if not serial_number:
        return
    with _MONITOR_LOCK:
        stop_event = _KEEPALIVE_STOPS.pop(serial_number, None)
        _KEEPALIVE_THREADS.pop(serial_number, None)
    if stop_event:
        stop_event.set()
        print(f"[keepalive] stop requested for {serial_number}", flush=True)


def _keepalive_loop(
    serial_number: str,
    initial_node: str | None,
    db_path: str,
    stop_event: threading.Event,
):
    device_node = initial_node
    keepalive_serial: serial.Serial | None = None
    keepalive_port: str | None = None
    print(f"[keepalive] monitor started for {serial_number}", flush=True)

    try:
        while not stop_event.is_set():
            with _DB_LOCK:
                data = _load_db(db_path)
                item = _find_device_by_serial(data["devices"], serial_number)
                if item is None:
                    break
                if item.get("status") != STATUS_ONLINE_CONNECTED:
                    break
                device_node = item.get("device_node") or device_node

            if not device_node:
                if keepalive_serial is not None:
                    try:
                        keepalive_serial.close()
                    except Exception:
                        pass
                    keepalive_serial = None
                    keepalive_port = None
                _update_registry_by_serial(
                    serial_number=serial_number,
                    db_path=db_path,
                    link_live=False,
                    link_status=LINK_STATUS_NOT_LIVE,
                )
                if stop_event.wait(KEEPALIVE_PING_INTERVAL_SEC):
                    break
                continue

            if (
                keepalive_serial is None
                or keepalive_port != device_node
                or not keepalive_serial.is_open
            ):
                if keepalive_serial is not None:
                    try:
                        keepalive_serial.close()
                    except Exception:
                        pass
                keepalive_serial = None
                keepalive_port = None

                try:
                    keepalive_serial = serial.Serial(
                        device_node,
                        baudrate=DEFAULT_SERIAL_BAUD,
                        timeout=HELLO_READ_TIMEOUT_SEC,
                        write_timeout=HELLO_READ_TIMEOUT_SEC,
                        dsrdtr=False,
                        rtscts=False,
                    )
                    keepalive_port = device_node
                except Exception as exc:
                    print(
                        f"[keepalive] serial open failed on {device_node}: {exc}",
                        flush=True,
                    )
                    _update_registry_by_serial(
                        serial_number=serial_number,
                        db_path=db_path,
                        device_node=device_node,
                        link_live=False,
                        link_status=LINK_STATUS_NOT_LIVE,
                    )
                    if stop_event.wait(KEEPALIVE_PING_INTERVAL_SEC):
                        break
                    continue

            try:
                link_live, rx_module_id = _send_and_wait(
                    ser=keepalive_serial,
                    port=device_node,
                    tx_bytes=PING_MESSAGE_BYTES,
                    expected_bytes=PONG_MESSAGE_BYTES,
                    timeout_sec=KEEPALIVE_PING_TIMEOUT_SEC,
                    phase="keepalive",
                    db_path=db_path,
                    serial_number=serial_number,
                    sync_bytes=FRAME_SYNC_BYTES,
                )
            except Exception as exc:
                print(f"[keepalive] probe error on {device_node}: {exc}", flush=True)
                link_live = False
                rx_module_id = None
                if keepalive_serial is not None:
                    try:
                        keepalive_serial.close()
                    except Exception:
                        pass
                keepalive_serial = None
                keepalive_port = None

            _update_registry_by_serial(
                serial_number=serial_number,
                db_path=db_path,
                device_node=device_node,
                link_live=link_live,
                link_status=LINK_STATUS_LIVE if link_live else LINK_STATUS_NOT_LIVE,
                module_id=rx_module_id,
            )

            if stop_event.wait(KEEPALIVE_PING_INTERVAL_SEC):
                break
    finally:
        if keepalive_serial is not None:
            try:
                keepalive_serial.close()
            except Exception:
                pass
        with _MONITOR_LOCK:
            existing_stop = _KEEPALIVE_STOPS.get(serial_number)
            if existing_stop is stop_event:
                _KEEPALIVE_STOPS.pop(serial_number, None)
                _KEEPALIVE_THREADS.pop(serial_number, None)
        print(f"[keepalive] monitor stopped for {serial_number}", flush=True)


def _start_keepalive_monitor(
    serial_number: str | None,
    device_node: str | None,
    db_path: str,
):
    if not serial_number:
        return

    with _MONITOR_LOCK:
        thread = _KEEPALIVE_THREADS.get(serial_number)
        if thread and thread.is_alive():
            return

        stop_event = threading.Event()
        monitor = threading.Thread(
            target=_keepalive_loop,
            args=(serial_number, device_node, db_path, stop_event),
            daemon=True,
            name=f"keepalive-{serial_number.replace(':', '')[-8:]}",
        )
        _KEEPALIVE_STOPS[serial_number] = stop_event
        _KEEPALIVE_THREADS[serial_number] = monitor
        monitor.start()


def matches(dev: pyudev.Device) -> bool:
    props = dev.properties

    serial_short = props.get("ID_SERIAL_SHORT")
    if SERIAL_NUMBER_ESP32_SHORT and serial_short == SERIAL_NUMBER_ESP32_SHORT:
        return True

    vendor = props.get("ID_VENDOR") or ""
    if MANUFACTURER_ESP32 and MANUFACTURER_ESP32.lower() in vendor.lower():
        return True

    if ID_VENDOR_ESP32 and props.get("ID_VENDOR_ID") == ID_VENDOR_ESP32:
        return True

    if ID_VENDOR_ESP32 and ID_MODEL_ESP32:
        if (props.get("ID_VENDOR_ID") == ID_VENDOR_ESP32 and
                props.get("ID_MODEL_ID") == ID_MODEL_ESP32):
            return True

    return False

def on_attach(dev: pyudev.Device, db_path: str):
    node = dev.device_node  # like /dev/ttyUSB0 or /dev/ttyACM0
    props = dict(dev.properties)
    serial_number = _extract_serial(dev)
    result = _update_registry(
        dev,
        STATUS_ONLINE_CONNECTED,
        db_path,
        link_live=False,
        link_status=LINK_STATUS_NOT_LIVE,
    )
    print(f"[ATTACH] {node}  ID_SERIAL_SHORT={props.get('ID_SERIAL_SHORT')}  DB={result}", flush=True)
    if node:
        link_live, module_type, module_id = _probe_link_via_handshake(
            port=node,
            db_path=db_path,
            serial_number=serial_number,
        )
        reported_module_type = module_type
        if (
            not link_live
            and module_id is None
            and _normalize_module_type(module_type) == DEFAULT_MODULE_TYPE
        ):
            reported_module_type = None
        link_status = LINK_STATUS_LIVE if link_live else LINK_STATUS_NOT_LIVE
        result = _update_registry(
            dev,
            STATUS_ONLINE_CONNECTED,
            db_path,
            link_live=link_live,
            link_status=link_status,
            module_type=reported_module_type,
            module_id=module_id,
        )
        print(
            f"[LINK] {node}  link_status={link_status}  module_type={module_type} "
            f"module_id={_module_id_hex(module_id) or 'unknown'}  DB={result}",
            flush=True,
        )
    else:
        print("[link] Skipping probe: missing device node", flush=True)

    resolved_serial = serial_number or (result or {}).get("serial_number")
    _stop_keepalive_monitor(resolved_serial)
    _start_keepalive_monitor(
        serial_number=resolved_serial,
        device_node=node,
        db_path=db_path,
    )

    # TODO: your control logic here
    # e.g. open serial, start your worker, restart your app, etc.


def on_detach(dev: pyudev.Device, db_path: str):
    # remove events sometimes don't have device_node; still log what we can
    result = _update_registry(
        dev,
        STATUS_OFFLINE_DISCONNECTED,
        db_path,
        link_live=False,
        link_status=LINK_STATUS_NOT_LIVE,
    )
    print(f"[DETACH] {dev.sys_name}  DB={result}", flush=True)
    _stop_keepalive_monitor((result or {}).get("serial_number"))

    # TODO: your teardown logic here
    # e.g. stop worker, close serial, clean up state

def bootstrap_connected_devices(context: pyudev.Context, db_path: str):
    data = _load_db(db_path)
    now = _now_iso()
    for item in data["devices"]:
        item["status"] = STATUS_OFFLINE_DISCONNECTED
        item["last_event_at"] = now
        item["link_live"] = False
        item["link_status"] = LINK_STATUS_NOT_LIVE
        item["last_link_check_at"] = now
        item["name"] = _normalize_device_name(
            item.get("name"),
            item.get("module_type") or item.get("firmware_module"),
        )
    _save_db(db_path, data)

    boot_count = 0
    monitor_targets: list[tuple[str | None, str | None]] = []
    for dev in context.list_devices(subsystem="tty"):
        if matches(dev):
            _update_registry(
                dev,
                STATUS_ONLINE_CONNECTED,
                db_path,
                link_live=False,
                link_status=LINK_STATUS_NOT_LIVE,
            )
            link_live = False
            module_type = DEFAULT_MODULE_TYPE
            module_id = None
            node = _device_node(dev)
            serial_number = _extract_serial(dev)
            if node:
                link_live, module_type, module_id = _probe_link_via_handshake(
                    port=node,
                    db_path=db_path,
                    serial_number=serial_number,
                )
            reported_module_type = module_type
            if (
                not link_live
                and module_id is None
                and _normalize_module_type(module_type) == DEFAULT_MODULE_TYPE
            ):
                reported_module_type = None
            link_status = LINK_STATUS_LIVE if link_live else LINK_STATUS_NOT_LIVE
            if _update_registry(
                dev,
                STATUS_ONLINE_CONNECTED,
                db_path,
                link_live=link_live,
                link_status=link_status,
                module_type=reported_module_type,
                module_id=module_id,
            ):
                boot_count += 1
            monitor_targets.append((serial_number, node))

    for serial_number, node in monitor_targets:
        _start_keepalive_monitor(
            serial_number=serial_number,
            device_node=node,
            db_path=db_path,
        )

    print(f"[db] bootstrap complete. online_devices={boot_count} db_path={db_path}", flush=True)

def conex(db_path: str):
    context = pyudev.Context()
    bootstrap_connected_devices(context, db_path)
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="tty")  # serial devices

    print("Monitoring udev for tty devices... (plug/unplug ESP32)")

    for dev in iter(monitor.poll, None):
        action = dev.action  # "add", "remove", sometimes "change"
        if action not in ("add", "remove"):
            continue

        # Only act on the target device
        if matches(dev):
            if action == "add":
                on_attach(dev, db_path)
            elif action == "remove":
                on_detach(dev, db_path)

        # small sleep avoids busy loops if you add heavier logic later
        time.sleep(0.01)
