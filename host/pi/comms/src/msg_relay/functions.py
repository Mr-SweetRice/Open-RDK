import time
import json
import os
import tempfile
import threading
import queue
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
    MODULE_QUERY_MESSAGE_BYTES,
    MODULE_INFO_PREFIX_BYTE,
    HELLO_ACK_TIMEOUT_SEC,
    MODULE_QUERY_TIMEOUT_SEC,
    HELLO_READ_TIMEOUT_SEC,
    HELLO_OPEN_DELAY_SEC,
    MODULE_TYPE_MAX_BYTES,
    STREAM_READ_TIMEOUT_SEC,
    STREAM_WRITE_TIMEOUT_SEC,
    MESSAGE_TYPES,
    MESSAGE_TYPE_CODE_TO_NAME,
    MESSAGE_TYPE_DEFAULT_BYTES,
    MESSAGE_TYPE_TELEMETRY,
    DEFAULT_ACTIVE_MESSAGE_TYPE,
    TELEMETRY_START_COMMAND,
    TELEMETRY_SYNC_COMMAND,
    TELEMETRY_STOP_BYTES,
    FRAME_LEN_MAX,
    FRAME_RX_BUFFER_MAX_BYTES,
    FRAME_KEEPALIVE_INTERVAL_SEC,
    FRAME_RESPONSE_TIMEOUT_SEC,
    FRAME_MAX_RETRY_ATTEMPTS,
    FRAME_RETRY_DELAY_SEC,
    FRAME_TELEMETRY_POLL_INTERVAL_SEC,
    FRAME_TELEMETRY_IDLE_SLEEP_SEC,
    FRAME_TELEMETRY_SYNC_INTERVAL_SEC,
    FRAME_SEQUENCE_MIN,
    FRAME_SEQUENCE_MAX,
    FRAME_SEQUENCE_BYTES,
    FRAME_READER_QUEUE_MAX,
    COMMS_LOG_QUEUE_MAX,
    COMMS_LOG_WRITER_POLL_SEC,
    BAUD_RATE_MIN,
    BAUD_RATE_MAX,
    COMMON_BAUD_RATES,
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
_COMMS_LOG_QUEUE: queue.Queue[str] = queue.Queue(maxsize=max(256, int(COMMS_LOG_QUEUE_MAX)))
_COMMS_LOG_WRITER_THREAD: threading.Thread | None = None
_COMMS_LOG_WRITER_STOP = threading.Event()
_ACTIVE_MESSAGE_TYPE = DEFAULT_ACTIVE_MESSAGE_TYPE
_ACTIVE_SERIAL_BAUD = DEFAULT_SERIAL_BAUD
_ACTIVE_MESSAGE_LOCK = threading.Lock()
_ACTIVE_SERIAL_LOCK = threading.Lock()
_DEVICE_DB_FIELDS = (
    "serial_number",
    "name",
    "status",
    "device_node",
    "message_type",
    "module_type",
    "firmware_module",
    "module_id",
    "module_id_hex",
    "link_live",
    "link_status",
    "last_event_at",
    "last_link_check_at",
    "error_count",
    "last_error_kind",
    "last_error_at",
    "telemetry_requested",
    "telemetry_active",
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
            # print(f"[esp] {text}", flush=True)
            pass

def run_with_retries(port: str, baud: int, timeout: float, retry_delay: float):
    while True:
        try:
            # print(f"[comms] Opening serial {port} @ {baud}", flush=True)
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
    _start_comms_log_writer()
    # print(f"[comms-db] Using comms log path: {resolved}", flush=True)
    return resolved


def _ensure_comms_log_path() -> str:
    path = _COMMS_LOG_PATH or DEFAULT_COMMS_LOG_PATH
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
        _COMMS_LOG_QUEUE.put_nowait(text)
        return
    except queue.Full:
        pass

    # Drop oldest line and keep newest event.
    try:
        _COMMS_LOG_QUEUE.get_nowait()
    except queue.Empty:
        pass
    try:
        _COMMS_LOG_QUEUE.put_nowait(text)
    except queue.Full:
        pass


def _comms_log_writer_loop():
    fp = None
    opened_path = ""
    poll_sec = max(0.01, float(COMMS_LOG_WRITER_POLL_SEC))
    try:
        while not _COMMS_LOG_WRITER_STOP.is_set():
            try:
                line = _COMMS_LOG_QUEUE.get(timeout=poll_sec)
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
    global _COMMS_LOG_WRITER_THREAD
    with _COMMS_LOG_LOCK:
        if _COMMS_LOG_WRITER_THREAD and _COMMS_LOG_WRITER_THREAD.is_alive():
            return
        _COMMS_LOG_WRITER_STOP.clear()
        _COMMS_LOG_WRITER_THREAD = threading.Thread(
            target=_comms_log_writer_loop,
            name="msg-relay-comms-log-writer",
            daemon=True,
        )
        _COMMS_LOG_WRITER_THREAD.start()


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
    if candidate in MESSAGE_TYPES:
        return candidate
    return DEFAULT_ACTIVE_MESSAGE_TYPE


def get_active_message_type() -> str:
    with _ACTIVE_MESSAGE_LOCK:
        return _ACTIVE_MESSAGE_TYPE


def set_active_message_type(name: str | None) -> str:
    global _ACTIVE_MESSAGE_TYPE
    normalized = _normalize_message_type_name(name)
    with _ACTIVE_MESSAGE_LOCK:
        _ACTIVE_MESSAGE_TYPE = normalized
    return normalized


def _default_device_message_type() -> str:
    with _ACTIVE_MESSAGE_LOCK:
        return _normalize_message_type_name(_ACTIVE_MESSAGE_TYPE)


def _ensure_device_message_type(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    normalized = _normalize_message_type_name(item.get("message_type"))
    if item.get("message_type") != normalized:
        item["message_type"] = normalized
        return True
    return False


def get_device_message_type(db_path: str, serial_number: str) -> str | None:
    if not serial_number:
        return None
    with _DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        normalized = _normalize_message_type_name(item.get("message_type"))
        if item.get("message_type") != normalized:
            item["message_type"] = normalized
            _save_db(db_path, data)
        return normalized


def set_device_message_type(
    db_path: str,
    serial_number: str,
    message_type: str | None,
) -> dict | None:
    if not serial_number:
        return None
    with _DB_LOCK:
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
        return dict(item)


def supported_message_types() -> list[dict]:
    items: list[dict] = []
    for key, spec in MESSAGE_TYPES.items():
        items.append(
            {
                "name": key,
                "code": spec.code,
                "default_content": spec.default_content,
                "ack_content": spec.ack_content,
            }
        )
    return sorted(items, key=lambda item: item["name"])


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
    with _ACTIVE_SERIAL_LOCK:
        return _ACTIVE_SERIAL_BAUD


def set_active_serial_baud(value: int | str | None) -> int:
    global _ACTIVE_SERIAL_BAUD
    normalized = _normalize_serial_baud(value)
    with _ACTIVE_SERIAL_LOCK:
        _ACTIVE_SERIAL_BAUD = normalized
    return normalized


def supported_serial_baud_rates() -> list[int]:
    out = sorted({int(rate) for rate in COMMON_BAUD_RATES if BAUD_RATE_MIN <= int(rate) <= BAUD_RATE_MAX})
    default = int(DEFAULT_SERIAL_BAUD)
    if BAUD_RATE_MIN <= default <= BAUD_RATE_MAX and default not in out:
        out.append(default)
        out.sort()
    return out


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


def _stream_message_type_name(code: int) -> str:
    return MESSAGE_TYPE_CODE_TO_NAME.get(code, f"0x{code:02X}")


def _normalize_sequence_value(value: int | None) -> int:
    span = int(FRAME_SEQUENCE_MAX) - int(FRAME_SEQUENCE_MIN) + 1
    if span <= 0:
        return int(FRAME_SEQUENCE_MIN)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(FRAME_SEQUENCE_MIN)
    return ((parsed - int(FRAME_SEQUENCE_MIN)) % span) + int(FRAME_SEQUENCE_MIN)


def _build_stream_frame(
    message_bytes: bytes,
    message_type_code: int,
    sequence: int,
    sync_bytes: bytes = FRAME_SYNC_BYTES,
) -> bytes:
    safe_message = bytes(message_bytes or b"")[:FRAME_LEN_MAX]
    if not safe_message:
        safe_message = b"\x20"
    msg_len = len(safe_message) & 0xFF
    seq_val = _normalize_sequence_value(sequence)
    seq_bytes = seq_val.to_bytes(int(FRAME_SEQUENCE_BYTES), byteorder="big", signed=False)
    return (
        (sync_bytes or b"")
        + bytes([msg_len])
        + safe_message
        + bytes([int(message_type_code) & 0xFF])
        + seq_bytes
    )


def _trim_to_sync_prefix(buffer: bytearray, sync_bytes: bytes):
    if not buffer:
        return
    sync = sync_bytes or b""
    if not sync:
        buffer.clear()
        return

    keep = 0
    max_prefix = min(len(buffer), len(sync) - 1)
    for prefix_len in range(max_prefix, 0, -1):
        if buffer[-prefix_len:] == sync[:prefix_len]:
            keep = prefix_len
            break
    if keep > 0:
        del buffer[:-keep]
    else:
        buffer.clear()


def _consume_stream_frames(
    rx_buffer: bytearray,
    sync_bytes: bytes = FRAME_SYNC_BYTES,
) -> list[dict]:
    out: list[dict] = []
    sync = sync_bytes or b""
    sync_len = len(sync)
    if sync_len == 0:
        rx_buffer.clear()
        return out

    while True:
        start = rx_buffer.find(sync)
        if start < 0:
            _trim_to_sync_prefix(rx_buffer, sync)
            return out
        if start > 0:
            del rx_buffer[:start]

        if len(rx_buffer) < sync_len + 1:
            return out

        msg_len = rx_buffer[sync_len]
        if msg_len <= 0 or msg_len > FRAME_LEN_MAX:
            # Resync: discard first sync byte and scan again.
            del rx_buffer[0]
            continue

        frame_len = sync_len + 1 + msg_len + 1 + int(FRAME_SEQUENCE_BYTES)
        if len(rx_buffer) < frame_len:
            return out

        frame_bytes = bytes(rx_buffer[:frame_len])
        del rx_buffer[:frame_len]

        msg_start = sync_len + 1
        msg_end = msg_start + msg_len
        msg_bytes = frame_bytes[msg_start:msg_end]
        msg_type_code = frame_bytes[msg_end]
        seq_start = msg_end + 1
        seq_end = seq_start + int(FRAME_SEQUENCE_BYTES)
        seq = int.from_bytes(frame_bytes[seq_start:seq_end], byteorder="big", signed=False)
        out.append(
            {
                "frame_bytes": frame_bytes,
                "len": msg_len,
                "message_bytes": msg_bytes,
                "message_text": msg_bytes.decode("utf-8", errors="replace"),
                "message_type_code": msg_type_code,
                "message_type": _stream_message_type_name(msg_type_code),
                "seq": seq,
            }
        )

        if len(rx_buffer) > FRAME_RX_BUFFER_MAX_BYTES:
            del rx_buffer[:-FRAME_RX_BUFFER_MAX_BYTES]


class _SerialFrameReader:
    def __init__(
        self,
        ser: serial.Serial,
        sync_bytes: bytes = FRAME_SYNC_BYTES,
        queue_size: int = FRAME_READER_QUEUE_MAX,
    ):
        self._ser = ser
        self._sync_bytes = sync_bytes or FRAME_SYNC_BYTES
        self._rx_buffer = bytearray()
        self._stop = threading.Event()
        self._frames: queue.Queue[dict] = queue.Queue(maxsize=max(128, int(queue_size)))
        self._thread: threading.Thread | None = None
        self._last_error: Exception | None = None
        self._error_lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="msg-relay-stream-reader",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def last_error(self) -> Exception | None:
        with self._error_lock:
            return self._last_error

    def get_frame(self, timeout: float = 0.0) -> dict | None:
        timeout_sec = max(0.0, float(timeout))
        if timeout_sec <= 0:
            try:
                return self._frames.get_nowait()
            except queue.Empty:
                return None
        try:
            return self._frames.get(timeout=timeout_sec)
        except queue.Empty:
            return None

    def clear_frames(self):
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break

    def _push_frame(self, frame: dict):
        try:
            self._frames.put_nowait(frame)
            return
        except queue.Full:
            pass
        try:
            self._frames.get_nowait()
        except queue.Empty:
            pass
        try:
            self._frames.put_nowait(frame)
        except queue.Full:
            pass

    def _run(self):
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except Exception as exc:
                with self._error_lock:
                    self._last_error = exc
                break

            if not chunk:
                continue

            self._rx_buffer.extend(chunk)
            if len(self._rx_buffer) > FRAME_RX_BUFFER_MAX_BYTES:
                del self._rx_buffer[:-FRAME_RX_BUFFER_MAX_BYTES]

            parsed_frames = _consume_stream_frames(self._rx_buffer, sync_bytes=self._sync_bytes)
            for frame in parsed_frames:
                frame["rx_host_epoch_ms"] = float(time.time_ns()) / 1_000_000.0
                self._push_frame(frame)

        self._stop.set()


def _append_communication_event(
    db_path: str,
    device_node: str | None,
    phase: str,
    direction: str,
    payload: bytes,
    serial_number: str | None = None,
    parsed_frame: dict | None = None,
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
        "device_serial": resolved_serial,
        "raw_hex": payload.hex(),
        "direction": direction_norm or "unknown",
    }
    if parsed_frame:
        event.update(
            {
                "message_type": parsed_frame.get("message_type"),
                "message_type_code": parsed_frame.get("message_type_code"),
                "message": parsed_frame.get("message_text"),
                "seq": parsed_frame.get("seq"),
                "seq_abs": parsed_frame.get("seq_abs"),
                "len": parsed_frame.get("len"),
                "latency_ms": parsed_frame.get("latency_ms"),
                "retry": parsed_frame.get("retry"),
                "retry_count": parsed_frame.get("retry_count"),
                "error_kind": parsed_frame.get("error_kind"),
                "expected_seq": parsed_frame.get("expected_seq"),
                "seq_valid": parsed_frame.get("seq_valid"),
            }
        )
    event = {key: value for key, value in event.items() if value is not None}

    _start_comms_log_writer()
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    _enqueue_comms_log_line(line)

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
            # print(f"[db] Could not resolve serial for event {dev.action} ({dev.sys_name})", flush=True)
            return None

        item = _find_device_by_serial(devices, serial_number)
        dirty = False
        link_dirty = False
        if not item:
            item = {"serial_number": serial_number}
            devices.append(item)
            dirty = True
        if not isinstance(item.get("message_type"), str):
            item["message_type"] = _default_device_message_type()
            dirty = True
        elif _ensure_device_message_type(item):
            dirty = True
        if not isinstance(item.get("error_count"), int) or int(item.get("error_count", 0)) < 0:
            item["error_count"] = 0
            dirty = True
        if not isinstance(item.get("last_error_kind"), str):
            item["last_error_kind"] = ""
            dirty = True
        if not isinstance(item.get("last_error_at"), str):
            item["last_error_at"] = ""
            dirty = True
        if not isinstance(item.get("telemetry_requested"), bool):
            item["telemetry_requested"] = False
            dirty = True
        if not isinstance(item.get("telemetry_active"), bool):
            item["telemetry_active"] = False
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
    telemetry_requested: bool | None = None,
    telemetry_active: bool | None = None,
    error_count_delta: int = 0,
    error_count_reset: bool = False,
    error_kind: str | None = None,
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

        if not isinstance(item.get("error_count"), int) or int(item.get("error_count", 0)) < 0:
            item["error_count"] = 0
            dirty = True
        if not isinstance(item.get("last_error_kind"), str):
            item["last_error_kind"] = ""
            dirty = True
        if not isinstance(item.get("last_error_at"), str):
            item["last_error_at"] = ""
            dirty = True
        if not isinstance(item.get("message_type"), str):
            item["message_type"] = _default_device_message_type()
            dirty = True
        elif _ensure_device_message_type(item):
            dirty = True
        if not isinstance(item.get("telemetry_requested"), bool):
            item["telemetry_requested"] = False
            dirty = True
        if not isinstance(item.get("telemetry_active"), bool):
            item["telemetry_active"] = False
            dirty = True

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

        if telemetry_requested is not None:
            requested_value = bool(telemetry_requested)
            if item.get("telemetry_requested") != requested_value:
                item["telemetry_requested"] = requested_value
                dirty = True

        if telemetry_active is not None:
            active_value = bool(telemetry_active)
            if item.get("telemetry_active") != active_value:
                item["telemetry_active"] = active_value
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


def clear_devices_registry(db_path: str) -> int:
    with _DB_LOCK:
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
    return _update_registry_by_serial(
        serial_number=serial_number,
        db_path=db_path,
        telemetry_requested=bool(enabled),
        # Keep current telemetry_active state untouched here.
        # Keepalive thread handles START/STOP sequencing and state transitions.
        telemetry_active=None,
    )


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
        # print(f"[{phase}] skipped: empty TX payload", flush=True)
        return False, None
    if not expected_bytes:
        # print(f"[{phase}] skipped: empty expected payload", flush=True)
        return False, None

    framed_tx = _frame_payload(tx_bytes, sync_bytes, module_id=HOST_MODULE_ID)

    ser.reset_input_buffer()
    ser.write(framed_tx)
    ser.flush()
    # print(f"[{phase}] TX {port}: {framed_tx.hex()}", flush=True)
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

        # print(f"[{phase}] RX {port}: {chunk.hex()}", flush=True)
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
            # print(
            #     f"[{phase}] expected bytes received from {port} "
            #     f"(module_id={_module_id_hex(rx_module_id) or 'unknown'})",
            #     flush=True,
            # )
            return True, rx_module_id

    # print(f"[{phase}] timeout on {port} after {timeout_sec:.2f}s", flush=True)
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
    # print(f"[module] TX {port}: {framed_query.hex()}", flush=True)
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
            # print(
            #     f"[module] RX {port}: {module_type} "
            #     f"(module_id={_module_id_hex(module_id) or 'unknown'})",
            #     flush=True,
            # )
            return module_type, module_id

    # print(f"[module] timeout on {port} after {timeout_sec:.2f}s", flush=True)
    return DEFAULT_MODULE_TYPE, None


def _next_sequence_value(current: int) -> int:
    safe = _normalize_sequence_value(current)
    if safe >= FRAME_SEQUENCE_MAX:
        return FRAME_SEQUENCE_MIN
    return safe + 1


def _build_telemetry_start_payload() -> bytes:
    host_epoch_ms = int(time.time_ns() // 1_000_000)
    return f"{TELEMETRY_START_COMMAND}:{host_epoch_ms}".encode("utf-8")


def _build_telemetry_sync_payload() -> bytes:
    host_epoch_ms = int(time.time_ns() // 1_000_000)
    return f"{TELEMETRY_SYNC_COMMAND}:{host_epoch_ms}".encode("utf-8")


def _extract_telemetry_tx_epoch_ms(message_text: str | None) -> int | None:
    if not isinstance(message_text, str):
        return None
    parts = message_text.strip().split()
    if len(parts) < 3:
        return None
    try:
        ts_ms = int(parts[-1])
    except (TypeError, ValueError):
        return None
    if ts_ms <= 0:
        return None
    return ts_ms


def _attach_telemetry_one_way_latency(parsed_frame: dict):
    if not isinstance(parsed_frame, dict):
        return
    if parsed_frame.get("message_type") != MESSAGE_TYPE_TELEMETRY:
        return
    tx_epoch_ms = _extract_telemetry_tx_epoch_ms(parsed_frame.get("message_text"))
    if tx_epoch_ms is None:
        return
    now_epoch_ms_raw = parsed_frame.get("rx_host_epoch_ms")
    if isinstance(now_epoch_ms_raw, (int, float)):
        now_epoch_ms = float(now_epoch_ms_raw)
    else:
        now_epoch_ms = float(time.time_ns()) / 1_000_000.0
    one_way_ms = now_epoch_ms - float(tx_epoch_ms)
    if one_way_ms < 0:
        return
    if one_way_ms > 60_000:
        return
    parsed_frame["latency_ms"] = round(one_way_ms, 3)


def _log_stream_rx_frame(
    db_path: str,
    port: str,
    serial_number: str | None,
    parsed: dict,
    expected_seq: int | None = None,
    seq_valid: bool | None = None,
    seq_abs: int | None = None,
    rtt_started_ns: int | None = None,
) -> dict:
    parsed_for_log = dict(parsed)
    if expected_seq is not None:
        parsed_for_log["expected_seq"] = int(expected_seq)
    if seq_valid is not None:
        parsed_for_log["seq_valid"] = bool(seq_valid)
    _attach_telemetry_one_way_latency(parsed_for_log)
    if seq_abs is not None:
        parsed_for_log["seq_abs"] = int(seq_abs)
    if rtt_started_ns is not None:
        latency_ms = max(0.0, (time.perf_counter_ns() - rtt_started_ns) / 1_000_000.0)
        parsed_for_log["latency_ms"] = round(latency_ms, 3)
    _append_communication_event(
        db_path=db_path,
        device_node=port,
        phase="stream",
        direction="rx",
        payload=parsed.get("frame_bytes") or b"",
        serial_number=serial_number,
        parsed_frame=parsed_for_log,
    )
    return parsed_for_log


def _send_stream_frame_and_wait(
    ser: serial.Serial,
    stream_reader: _SerialFrameReader,
    port: str,
    db_path: str,
    serial_number: str | None,
    message_type_name: str,
    sequence: int,
    sequence_abs: int | None,
    message_bytes: bytes | None = None,
    timeout_sec: float = FRAME_RESPONSE_TIMEOUT_SEC,
    max_attempts: int = FRAME_MAX_RETRY_ATTEMPTS,
    sync_bytes: bytes = FRAME_SYNC_BYTES,
) -> tuple[bool, dict | None, int]:
    normalized_type = _normalize_message_type_name(message_type_name)
    spec = MESSAGE_TYPES[normalized_type]
    payload = bytes(message_bytes) if message_bytes is not None else (
        MESSAGE_TYPE_DEFAULT_BYTES.get(normalized_type) or spec.default_content.encode("utf-8")
    )
    seq_val = _normalize_sequence_value(sequence)
    seq_abs_val = int(sequence_abs) if sequence_abs is not None else seq_val
    attempts = max(1, int(max_attempts or 1))
    timeout_errors = 0

    for attempt_idx in range(attempts):
        is_retry = attempt_idx > 0
        tx_frame = {
            "len": min(len(payload), FRAME_LEN_MAX),
            "message_bytes": payload[:FRAME_LEN_MAX],
            "message_text": payload[:FRAME_LEN_MAX].decode("utf-8", errors="replace"),
            "message_type_code": spec.code,
            "message_type": spec.name,
            "seq": seq_val,
            "seq_abs": seq_abs_val,
            "retry": is_retry,
            "retry_count": attempt_idx,
            "error_kind": "timeout_retry" if is_retry else None,
        }
        tx_bytes = _build_stream_frame(
            message_bytes=tx_frame["message_bytes"],
            message_type_code=tx_frame["message_type_code"],
            sequence=tx_frame["seq"],
            sync_bytes=sync_bytes,
        )
        tx_frame["frame_bytes"] = tx_bytes

        tx_started_ns = time.perf_counter_ns()
        ser.write(tx_bytes)
        ser.flush()
        _append_communication_event(
            db_path=db_path,
            device_node=port,
            phase="stream",
            direction="tx",
            payload=tx_bytes,
            serial_number=serial_number,
            parsed_frame=tx_frame,
        )

        deadline = time.monotonic() + max(timeout_sec, 0.1)
        while time.monotonic() < deadline:
            reader_error = stream_reader.last_error()
            if reader_error is not None:
                raise RuntimeError(f"stream reader stopped: {reader_error}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            parsed = stream_reader.get_frame(timeout=min(remaining, FRAME_TELEMETRY_POLL_INTERVAL_SEC))
            if parsed is None:
                continue

            received_seq = int(parsed.get("seq", -1))
            received_type_code = int(parsed.get("message_type_code", -1))
            seq_valid = (received_seq == seq_val) and (received_type_code == int(spec.code))
            parsed_for_log = _log_stream_rx_frame(
                db_path=db_path,
                port=port,
                serial_number=serial_number,
                parsed=parsed,
                expected_seq=seq_val,
                seq_valid=seq_valid,
                seq_abs=seq_abs_val if seq_valid else None,
                rtt_started_ns=tx_started_ns if seq_valid else None,
            )
            if seq_valid:
                return True, parsed_for_log, timeout_errors

        timeout_errors += 1
        if attempt_idx < attempts - 1:
            # print(
            #     f"[stream] timeout on {port} seq={seq_val} retry={attempt_idx + 1}/{attempts - 1}",
            #     flush=True,
            # )
            if FRAME_RETRY_DELAY_SEC > 0:
                time.sleep(FRAME_RETRY_DELAY_SEC)

    return False, None, timeout_errors


def _drain_stream_reader_frames(
    stream_reader: _SerialFrameReader,
    port: str,
    db_path: str,
    serial_number: str | None,
    max_frames: int = 512,
) -> int:
    received = 0
    for _ in range(max(1, int(max_frames))):
        reader_error = stream_reader.last_error()
        if reader_error is not None:
            raise RuntimeError(f"stream reader stopped: {reader_error}")
        parsed = stream_reader.get_frame(timeout=0.0)
        if parsed is None:
            break
        _log_stream_rx_frame(
            db_path=db_path,
            port=port,
            serial_number=serial_number,
            parsed=parsed,
            seq_valid=True,
        )
        received += 1
    return received


def _probe_link_via_handshake(
    port: str,
    db_path: str,
    serial_number: str | None = None,
    baud: int = DEFAULT_SERIAL_BAUD,
    hello_message: bytes = HELLO_MESSAGE_BYTES,
    ack_message: bytes = HELLO_ACK_BYTES,
    module_query_message: bytes = MODULE_QUERY_MESSAGE_BYTES,
    sync_bytes: bytes = FRAME_SYNC_BYTES,
    module_info_prefix_byte: int = MODULE_INFO_PREFIX_BYTE,
    ack_timeout_sec: float = HELLO_ACK_TIMEOUT_SEC,
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

            detected_module_id = ack_module_id
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

            # print(f"[link] handshake confirmed on {port}", flush=True)
            return True, module_type, detected_module_id
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
        # print(f"[keepalive] stop requested for {serial_number}", flush=True)


def _keepalive_loop(
    serial_number: str,
    initial_node: str | None,
    db_path: str,
    stop_event: threading.Event,
):
    device_node = initial_node
    keepalive_serial: serial.Serial | None = None
    keepalive_port: str | None = None
    stream_reader: _SerialFrameReader | None = None
    stream_seq = FRAME_SEQUENCE_MIN
    stream_seq_abs = 0
    last_telemetry_rx_at = 0.0
    last_telemetry_sync_at = 0.0
    last_telemetry_timeout_error_at = 0.0
    telemetry_requested = False
    telemetry_active_state = False
    device_message_type = _default_device_message_type()
    last_db_refresh_at = 0.0
    last_registry_sync_at = 0.0
    last_registry_link_live: bool | None = None
    last_registry_telemetry_requested: bool | None = None
    last_registry_telemetry_active: bool | None = None
    # print(f"[keepalive] monitor started for {serial_number}", flush=True)

    try:
        while not stop_event.is_set():
            telemetry_mode = device_message_type == MESSAGE_TYPE_TELEMETRY
            now_mono = time.monotonic()
            db_refresh_interval = 0.05 if telemetry_mode else 0.25
            if (now_mono - last_db_refresh_at) >= db_refresh_interval:
                with _DB_LOCK:
                    data = _load_db(db_path)
                    item = _find_device_by_serial(data["devices"], serial_number)
                    if item is None:
                        break
                    if item.get("status") != STATUS_ONLINE_CONNECTED:
                        break
                    device_node = item.get("device_node") or device_node
                    device_message_type = _normalize_message_type_name(item.get("message_type"))
                    telemetry_requested = bool(item.get("telemetry_requested", False))
                    telemetry_active_state = bool(item.get("telemetry_active", False))
                last_db_refresh_at = now_mono
            telemetry_mode = device_message_type == MESSAGE_TYPE_TELEMETRY

            if not device_node:
                if stream_reader is not None:
                    stream_reader.clear_frames()
                    stream_reader.stop()
                    stream_reader = None
                if keepalive_serial is not None:
                    try:
                        keepalive_serial.reset_input_buffer()
                    except Exception:
                        pass
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
                    telemetry_active=False,
                    error_count_reset=True,
                )
                if stop_event.wait(FRAME_KEEPALIVE_INTERVAL_SEC):
                    break
                continue

            active_baud = get_active_serial_baud()
            if keepalive_serial is not None:
                try:
                    current_baud = int(getattr(keepalive_serial, "baudrate", active_baud))
                except Exception:
                    current_baud = active_baud
                if current_baud != active_baud:
                    if stream_reader is not None:
                        stream_reader.stop()
                        stream_reader = None
                    try:
                        keepalive_serial.close()
                    except Exception:
                        pass
                    keepalive_serial = None
                    keepalive_port = None
                    stream_seq = FRAME_SEQUENCE_MIN
                    stream_seq_abs = 0
                    last_telemetry_rx_at = 0.0
                    last_telemetry_sync_at = 0.0
                    last_telemetry_timeout_error_at = 0.0

            if (
                keepalive_serial is None
                or keepalive_port != device_node
                or not keepalive_serial.is_open
            ):
                if stream_reader is not None:
                    stream_reader.clear_frames()
                    stream_reader.stop()
                    stream_reader = None
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
                        baudrate=active_baud,
                        timeout=STREAM_READ_TIMEOUT_SEC,
                        write_timeout=STREAM_WRITE_TIMEOUT_SEC,
                        dsrdtr=False,
                        rtscts=False,
                    )
                    keepalive_port = device_node
                    stream_seq = FRAME_SEQUENCE_MIN
                    stream_seq_abs = 0
                    last_telemetry_rx_at = 0.0
                    last_telemetry_sync_at = 0.0
                    last_telemetry_timeout_error_at = 0.0

                    ack_ok, rx_module_id = _send_and_wait(
                        ser=keepalive_serial,
                        port=device_node,
                        tx_bytes=HELLO_MESSAGE_BYTES,
                        expected_bytes=HELLO_ACK_BYTES,
                        timeout_sec=HELLO_ACK_TIMEOUT_SEC,
                        phase="hello",
                        db_path=db_path,
                        serial_number=serial_number,
                        sync_bytes=FRAME_SYNC_BYTES,
                    )
                    if not ack_ok:
                        raise RuntimeError("hello handshake timeout")

                    module_type, query_module_id = _query_module_type(
                        ser=keepalive_serial,
                        port=device_node,
                        query_bytes=MODULE_QUERY_MESSAGE_BYTES,
                        response_prefix_byte=MODULE_INFO_PREFIX_BYTE,
                        timeout_sec=MODULE_QUERY_TIMEOUT_SEC,
                        max_payload_bytes=MODULE_TYPE_MAX_BYTES,
                        db_path=db_path,
                        serial_number=serial_number,
                        sync_bytes=FRAME_SYNC_BYTES,
                    )
                    if query_module_id is not None:
                        rx_module_id = query_module_id
                    if module_type == DEFAULT_MODULE_TYPE:
                        module_type = _normalize_module_type(_module_id_to_type(rx_module_id))
                    _update_registry_by_serial(
                        serial_number=serial_number,
                        db_path=db_path,
                        device_node=device_node,
                        module_type=module_type,
                        module_id=rx_module_id,
                        telemetry_active=False,
                    )
                    stream_reader = _SerialFrameReader(
                        ser=keepalive_serial,
                        sync_bytes=FRAME_SYNC_BYTES,
                        queue_size=FRAME_READER_QUEUE_MAX,
                    )
                    stream_reader.start()
                    last_telemetry_rx_at = 0.0
                    last_telemetry_sync_at = 0.0
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
                        telemetry_active=False,
                        error_count_delta=1,
                        error_kind="serial_open_failed",
                    )
                    if stream_reader is not None:
                        stream_reader.clear_frames()
                        stream_reader.stop()
                        stream_reader = None
                    if keepalive_serial is not None:
                        try:
                            keepalive_serial.close()
                        except Exception:
                            pass
                    keepalive_serial = None
                    keepalive_port = None
                    if stop_event.wait(FRAME_KEEPALIVE_INTERVAL_SEC):
                        break
                    continue

            try:
                link_live = False
                rx_frame = None
                error_delta = 0
                error_kind: str | None = None
                if stream_reader is None:
                    error_kind = "stream_reader_unavailable"
                    raise RuntimeError("stream reader unavailable")

                if telemetry_mode:
                    if telemetry_requested and not telemetry_active_state:
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass
                        start_ok, start_ack, start_errors = _send_stream_frame_and_wait(
                            ser=keepalive_serial,
                            stream_reader=stream_reader,
                            port=device_node,
                            db_path=db_path,
                            serial_number=serial_number,
                            message_type_name=MESSAGE_TYPE_TELEMETRY,
                            sequence=stream_seq,
                            sequence_abs=stream_seq_abs,
                            message_bytes=_build_telemetry_start_payload(),
                            timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.05), 0.25),
                            max_attempts=1,
                            sync_bytes=FRAME_SYNC_BYTES,
                        )
                        error_delta += start_errors
                        if (not start_ok) and start_errors:
                            error_kind = error_kind or "telemetry_start_timeout"
                        if start_ok:
                            stream_seq = _next_sequence_value(stream_seq)
                            stream_seq_abs += 1
                        start_ack_text = str((start_ack or {}).get("message_text") or "").strip().upper()
                        if start_ok and start_ack_text == "TELEMETRY STARTED":
                            telemetry_active_state = True
                            last_telemetry_rx_at = time.monotonic()
                            last_telemetry_sync_at = 0.0
                            rx_frame = start_ack
                            link_live = True
                        elif start_ok:
                            error_delta += 1
                            error_kind = error_kind or "telemetry_start_unexpected_ack"
                            telemetry_requested = False
                    elif (not telemetry_requested) and telemetry_active_state:
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass
                        stop_ok, stop_ack, stop_errors = _send_stream_frame_and_wait(
                            ser=keepalive_serial,
                            stream_reader=stream_reader,
                            port=device_node,
                            db_path=db_path,
                            serial_number=serial_number,
                            message_type_name=MESSAGE_TYPE_TELEMETRY,
                            sequence=stream_seq,
                            sequence_abs=stream_seq_abs,
                            message_bytes=TELEMETRY_STOP_BYTES,
                            timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.05), 0.25),
                            max_attempts=1,
                            sync_bytes=FRAME_SYNC_BYTES,
                        )
                        error_delta += stop_errors
                        if (not stop_ok) and stop_errors:
                            error_kind = error_kind or "telemetry_stop_timeout"
                        if stop_ok:
                            stream_seq = _next_sequence_value(stream_seq)
                            stream_seq_abs += 1
                        stop_ack_text = str((stop_ack or {}).get("message_text") or "").strip().upper()
                        if stop_ok and stop_ack_text == "TELEMETRY STOPPED":
                            telemetry_active_state = False
                            last_telemetry_rx_at = 0.0
                            last_telemetry_sync_at = 0.0
                            rx_frame = stop_ack
                            link_live = True
                        elif stop_ok:
                            error_delta += 1
                            error_kind = error_kind or "telemetry_stop_unexpected_ack"
                        else:
                            telemetry_active_state = False
                            last_telemetry_rx_at = 0.0
                            last_telemetry_sync_at = 0.0
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass

                    if telemetry_active_state and telemetry_requested:
                        telemetry_frames = _drain_stream_reader_frames(
                            stream_reader=stream_reader,
                            port=device_node,
                            db_path=db_path,
                            serial_number=serial_number,
                        )
                        if telemetry_frames > 0:
                            last_telemetry_rx_at = time.monotonic()
                            last_telemetry_timeout_error_at = 0.0
                        now_mono = time.monotonic()
                        if (
                            FRAME_TELEMETRY_SYNC_INTERVAL_SEC > 0
                            and (now_mono - last_telemetry_sync_at) >= FRAME_TELEMETRY_SYNC_INTERVAL_SEC
                        ):
                            last_telemetry_sync_at = now_mono
                            sync_ok, sync_ack, sync_errors = _send_stream_frame_and_wait(
                                ser=keepalive_serial,
                                stream_reader=stream_reader,
                                port=device_node,
                                db_path=db_path,
                                serial_number=serial_number,
                                message_type_name=MESSAGE_TYPE_TELEMETRY,
                                sequence=stream_seq,
                                sequence_abs=stream_seq_abs,
                                message_bytes=_build_telemetry_sync_payload(),
                                timeout_sec=FRAME_RESPONSE_TIMEOUT_SEC,
                                max_attempts=1,
                                sync_bytes=FRAME_SYNC_BYTES,
                            )
                            error_delta += sync_errors
                            if sync_ok:
                                stream_seq = _next_sequence_value(stream_seq)
                                stream_seq_abs += 1
                                sync_ack_text = str((sync_ack or {}).get("message_text") or "").strip().upper()
                                if sync_ack_text != "TELEMETRY SYNCED":
                                    error_delta += 1
                                    error_kind = error_kind or "telemetry_sync_unexpected_ack"
                            else:
                                if sync_errors <= 0:
                                    error_delta += 1
                                error_kind = error_kind or "telemetry_sync_timeout"
                        link_live = (time.monotonic() - last_telemetry_rx_at) <= 1.0
                        if (not link_live) and (
                            last_telemetry_timeout_error_at <= 0.0
                            or (time.monotonic() - last_telemetry_timeout_error_at) >= 1.0
                        ):
                            error_delta += 1
                            error_kind = error_kind or "telemetry_rx_timeout"
                            last_telemetry_timeout_error_at = time.monotonic()
                    elif (not telemetry_requested) and (not telemetry_active_state):
                        link_live = bool(keepalive_serial and keepalive_serial.is_open)
                else:
                    if telemetry_requested:
                        telemetry_requested = False
                    if telemetry_active_state:
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass
                        stop_ok, _stop_ack, stop_errors = _send_stream_frame_and_wait(
                            ser=keepalive_serial,
                            stream_reader=stream_reader,
                            port=device_node,
                            db_path=db_path,
                            serial_number=serial_number,
                            message_type_name=MESSAGE_TYPE_TELEMETRY,
                            sequence=stream_seq,
                            sequence_abs=stream_seq_abs,
                            message_bytes=TELEMETRY_STOP_BYTES,
                            timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.05), 0.25),
                            max_attempts=1,
                            sync_bytes=FRAME_SYNC_BYTES,
                        )
                        error_delta += stop_errors
                        if stop_ok:
                            telemetry_active_state = False
                            stream_seq = _next_sequence_value(stream_seq)
                            stream_seq_abs += 1
                        else:
                            if stop_errors <= 0:
                                error_delta += 1
                            error_kind = error_kind or "telemetry_stop_timeout"
                            telemetry_active_state = False
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass

                    active_type = device_message_type
                    stream_reader.clear_frames()
                    link_live, rx_frame, frame_errors = _send_stream_frame_and_wait(
                        ser=keepalive_serial,
                        stream_reader=stream_reader,
                        port=device_node,
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type_name=active_type,
                        sequence=stream_seq,
                        sequence_abs=stream_seq_abs,
                        timeout_sec=FRAME_RESPONSE_TIMEOUT_SEC,
                        max_attempts=FRAME_MAX_RETRY_ATTEMPTS,
                        sync_bytes=FRAME_SYNC_BYTES,
                    )
                    error_delta += frame_errors
                    if link_live and rx_frame is not None:
                        stream_seq = _next_sequence_value(stream_seq)
                        stream_seq_abs += 1
                        last_telemetry_timeout_error_at = 0.0
                    elif not link_live:
                        if frame_errors <= 0:
                            error_delta += 1
                        error_kind = error_kind or "stream_exchange_timeout"
            except Exception as exc:
                print(f"[keepalive] probe error on {device_node}: {exc}", flush=True)
                link_live = False
                rx_frame = None
                error_delta = 1
                error_kind = error_kind or "keepalive_exception"
                telemetry_active_state = False
                if keepalive_serial is not None:
                    try:
                        keepalive_serial.reset_input_buffer()
                    except Exception:
                        pass
                    try:
                        keepalive_serial.close()
                    except Exception:
                        pass
                keepalive_serial = None
                keepalive_port = None
                if stream_reader is not None:
                    stream_reader.clear_frames()
                    stream_reader.stop()
                    stream_reader = None
                stream_seq = FRAME_SEQUENCE_MIN
                stream_seq_abs = 0
                last_telemetry_rx_at = 0.0
                last_telemetry_sync_at = 0.0
                last_telemetry_timeout_error_at = 0.0

            now_mono = time.monotonic()
            registry_changed = (
                last_registry_link_live is None
                or link_live != last_registry_link_live
                or telemetry_requested != last_registry_telemetry_requested
                or telemetry_active_state != last_registry_telemetry_active
            )
            registry_interval = 0.25 if telemetry_mode else FRAME_KEEPALIVE_INTERVAL_SEC
            registry_due = (now_mono - last_registry_sync_at) >= registry_interval
            if error_delta or registry_changed or registry_due:
                if error_delta and not error_kind:
                    error_kind = "comm_error"
                _update_registry_by_serial(
                    serial_number=serial_number,
                    db_path=db_path,
                    device_node=device_node,
                    link_live=link_live,
                    link_status=LINK_STATUS_LIVE if link_live else LINK_STATUS_NOT_LIVE,
                    telemetry_requested=telemetry_requested,
                    telemetry_active=telemetry_active_state,
                    error_count_delta=error_delta,
                    error_count_reset=bool(link_live and error_delta == 0),
                    error_kind=error_kind,
                )
                last_registry_sync_at = now_mono
                last_registry_link_live = link_live
                last_registry_telemetry_requested = telemetry_requested
                last_registry_telemetry_active = telemetry_active_state

            if telemetry_mode and telemetry_requested and telemetry_active_state:
                wait_sec = FRAME_TELEMETRY_IDLE_SLEEP_SEC
            elif telemetry_mode:
                wait_sec = FRAME_TELEMETRY_POLL_INTERVAL_SEC
            else:
                wait_sec = FRAME_KEEPALIVE_INTERVAL_SEC
            if stop_event.wait(wait_sec):
                break
    finally:
        if stream_reader is not None:
            stream_reader.stop()
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
        # print(f"[keepalive] monitor stopped for {serial_number}", flush=True)


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
    if serial_number:
        _update_registry_by_serial(
            serial_number=serial_number,
            db_path=db_path,
            telemetry_active=False,
        )
    # print(f"[ATTACH] {node}  ID_SERIAL_SHORT={props.get('ID_SERIAL_SHORT')}  DB={result}", flush=True)
    if node:
        link_live, module_type, module_id = _probe_link_via_handshake(
            port=node,
            db_path=db_path,
            serial_number=serial_number,
            baud=get_active_serial_baud(),
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
        # print(
        #     f"[LINK] {node}  link_status={link_status}  module_type={module_type} "
        #     f"module_id={_module_id_hex(module_id) or 'unknown'}  DB={result}",
        #     flush=True,
        # )
    else:
        # print("[link] Skipping probe: missing device node", flush=True)
        pass

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
    # print(f"[DETACH] {dev.sys_name}  DB={result}", flush=True)
    if (result or {}).get("serial_number"):
        _update_registry_by_serial(
            serial_number=(result or {}).get("serial_number"),
            db_path=db_path,
            telemetry_active=False,
        )
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
        item["message_type"] = _normalize_message_type_name(item.get("message_type"))
        item["telemetry_requested"] = False
        item["telemetry_active"] = False
        if not isinstance(item.get("error_count"), int) or int(item.get("error_count", 0)) < 0:
            item["error_count"] = 0
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
                    baud=get_active_serial_baud(),
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

    # print(f"[db] bootstrap complete. online_devices={boot_count} db_path={db_path}", flush=True)

def conex(db_path: str):
    context = pyudev.Context()
    bootstrap_connected_devices(context, db_path)
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="tty")  # serial devices

    # print("Monitoring udev for tty devices... (plug/unplug ESP32)")

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
