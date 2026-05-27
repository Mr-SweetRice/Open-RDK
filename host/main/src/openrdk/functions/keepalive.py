import threading
import time

import serial

from ..constants import (
    FRAME_KEEPALIVE_INTERVAL_SEC,
    FRAME_MAX_RETRY_ATTEMPTS,
    FRAME_READER_QUEUE_MAX,
    FRAME_RESPONSE_TIMEOUT_SEC,
    FRAME_RETRY_DELAY_SEC,
    FRAME_SEQUENCE_MIN,
    FRAME_TELEMETRY_IDLE_SLEEP_SEC,
    FRAME_TELEMETRY_POLL_INTERVAL_SEC,
    FRAME_TELEMETRY_SYNC_INTERVAL_SEC,
    FRAME_SYNC_BYTES,
    HELLO_ACK_BYTES,
    HELLO_ACK_TIMEOUT_SEC,
    HELLO_MESSAGE_BYTES,
    HELLO_OPEN_DELAY_SEC,
    LINK_STATUS_LIVE,
    LINK_STATUS_NOT_LIVE,
    MESSAGE_TYPE_CMD,
    MESSAGE_TYPE_TELEMETRY,
    MESSAGE_TYPE_TRACTION_OUT,
    MODULE_INFO_PREFIX_BYTE,
    MODULE_QUERY_MESSAGE_BYTES,
    MODULE_QUERY_TIMEOUT_SEC,
    MODULE_TYPE_MAX_BYTES,
    STATUS_ONLINE_CONNECTED,
    STREAM_READ_TIMEOUT_SEC,
    STREAM_WRITE_TIMEOUT_SEC,
    TELEMETRY_STOP_BYTES,
    TRACTION_OUT_DEFAULT_VALUE,
)
from . import _state
from .framing import _SerialFrameReader, _next_sequence_value, _normalize_sequence_value
from .registry import (
    _default_device_message_type,
    _find_device_by_serial,
    _load_db,
    _module_id_to_type,
    _normalize_message_type_name,
    _normalize_module_type,
    _normalize_traction_out_value,
    _update_registry_by_serial,
    get_active_serial_baud,
)
from .transport import (
    _build_traction_out_payload,
    _drain_stream_reader_frames,
    _query_module_type,
    _send_and_wait,
    _send_stream_frame_and_wait,
)


def _complete_traction_out_request(request: dict | None, result: dict):
    if not isinstance(request, dict):
        return
    done_event = request.get("done_event")
    request["result"] = dict(result or {})
    if isinstance(done_event, threading.Event):
        done_event.set()


def _enqueue_traction_out_request(
    serial_number: str,
    value: int | None = None,
    command: str | None = None,
) -> dict:
    normalized_value = _normalize_traction_out_value(value) if value is not None else None
    command_text = str(command or "").strip()
    if not command_text:
        command_text = _build_traction_out_payload(normalized_value).decode("utf-8")
    if normalized_value is None and command_text.upper().startswith("CLR OUT"):
        normalized_value = TRACTION_OUT_DEFAULT_VALUE
    request = {
        "serial_number": serial_number,
        "value": normalized_value,
        "command": command_text,
        "queued_at_monotonic_ns": time.perf_counter_ns(),
        "done_event": threading.Event(),
        "result": None,
    }

    superseded = None
    with _state._TRACTION_OUT_LOCK:
        _state._TRACTION_OUT_REQUEST_ID += 1
        request["request_id"] = int(_state._TRACTION_OUT_REQUEST_ID)
        superseded = _state._TRACTION_OUT_PENDING.get(serial_number)
        _state._TRACTION_OUT_PENDING[serial_number] = request

    if isinstance(superseded, dict):
        superseded_value = superseded.get("value")
        if not isinstance(superseded_value, int):
            superseded_value = TRACTION_OUT_DEFAULT_VALUE
        _complete_traction_out_request(
            superseded,
            {
                "ok": False,
                "serial_number": serial_number,
                "traction_out_value": int(superseded_value),
                "command": str(superseded.get("command") or ""),
                "ack": "SUPERSEDED",
                "error_kind": "traction_out_superseded",
                "latency_ms": None,
            },
        )
    _state._wake_keepalive_monitor(serial_number)
    return request


def _pop_traction_out_request(serial_number: str) -> dict | None:
    with _state._TRACTION_OUT_LOCK:
        return _state._TRACTION_OUT_PENDING.pop(serial_number, None)


def _cancel_traction_out_request(serial_number: str, error_kind: str):
    request = _pop_traction_out_request(serial_number)
    if not isinstance(request, dict):
        return
    request_value = request.get("value")
    if not isinstance(request_value, int):
        request_value = TRACTION_OUT_DEFAULT_VALUE
    _complete_traction_out_request(
        request,
        {
            "ok": False,
            "serial_number": serial_number,
            "traction_out_value": int(request_value),
            "command": str(request.get("command") or ""),
            "ack": "CANCELLED",
            "error_kind": str(error_kind or "traction_out_cancelled"),
            "latency_ms": None,
        },
    )


def send_device_traction_out_once(
    db_path: str,
    serial_number: str,
    value: int | str | None = None,
    timeout_sec: float = 1.5,
) -> dict | None:
    if not serial_number:
        return None

    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        from .registry import _save_db
        normalized_value = _normalize_traction_out_value(
            value if value is not None else item.get("traction_out_value")
        )
        if item.get("traction_out_value") != normalized_value:
            item["traction_out_value"] = normalized_value
            _save_db(db_path, data)
        status_value = str(item.get("status") or "")
        message_type_value = _normalize_message_type_name(item.get("message_type"))
        device_node_value = str(item.get("device_node") or "").strip()

    if message_type_value != MESSAGE_TYPE_TRACTION_OUT:
        return {
            "ok": False, "serial_number": serial_number,
            "traction_out_value": int(normalized_value),
            "ack": "MODE REQUIRED", "error_kind": "traction_out_mode_required",
            "latency_ms": None,
        }
    if status_value != STATUS_ONLINE_CONNECTED:
        return {
            "ok": False, "serial_number": serial_number,
            "traction_out_value": int(normalized_value),
            "ack": "OFFLINE", "error_kind": "device_offline", "latency_ms": None,
        }

    with _state._MONITOR_LOCK:
        monitor_thread = _state._KEEPALIVE_THREADS.get(serial_number)
    if (not monitor_thread or not monitor_thread.is_alive()) and device_node_value:
        _start_keepalive_monitor(serial_number=serial_number, device_node=device_node_value, db_path=db_path)
        time.sleep(0.05)
        with _state._MONITOR_LOCK:
            monitor_thread = _state._KEEPALIVE_THREADS.get(serial_number)
    if not monitor_thread or not monitor_thread.is_alive():
        return {
            "ok": False, "serial_number": serial_number,
            "traction_out_value": int(normalized_value),
            "ack": "NO MONITOR", "error_kind": "keepalive_not_running", "latency_ms": None,
        }

    request = _enqueue_traction_out_request(serial_number, value=normalized_value)
    done_event = request.get("done_event")
    wait_timeout = max(0.1, float(timeout_sec))
    if isinstance(done_event, threading.Event):
        done_event.wait(wait_timeout)

    result = request.get("result")
    if not isinstance(result, dict):
        with _state._TRACTION_OUT_LOCK:
            pending = _state._TRACTION_OUT_PENDING.get(serial_number)
            if pending is request:
                _state._TRACTION_OUT_PENDING.pop(serial_number, None)
        return {
            "ok": False, "serial_number": serial_number,
            "traction_out_value": int(normalized_value),
            "ack": "TIMEOUT", "error_kind": "traction_out_send_timeout", "latency_ms": None,
        }

    merged = dict(result)
    merged.setdefault("serial_number", serial_number)
    merged.setdefault("traction_out_value", int(normalized_value))
    from ..constants import TRACTION_OUT_COMMAND_PREFIX
    merged.setdefault("command", f"{TRACTION_OUT_COMMAND_PREFIX} {int(normalized_value)}")
    return merged


def send_device_traction_command_once(
    db_path: str,
    serial_number: str,
    command: str,
    timeout_sec: float = 1.5,
) -> dict | None:
    if not serial_number:
        return None
    command_text = str(command or "").strip()
    if not command_text:
        return {
            "ok": False, "serial_number": serial_number, "command": "",
            "ack": "EMPTY", "error_kind": "traction_out_empty", "latency_ms": None,
        }

    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        status_value = str(item.get("status") or "")
        message_type_value = _normalize_message_type_name(item.get("message_type"))
        device_node_value = str(item.get("device_node") or "").strip()

    if message_type_value != MESSAGE_TYPE_TRACTION_OUT:
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "ack": "MODE REQUIRED", "error_kind": "traction_out_mode_required", "latency_ms": None,
        }
    if status_value != STATUS_ONLINE_CONNECTED:
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "ack": "OFFLINE", "error_kind": "device_offline", "latency_ms": None,
        }

    with _state._MONITOR_LOCK:
        monitor_thread = _state._KEEPALIVE_THREADS.get(serial_number)
    if (not monitor_thread or not monitor_thread.is_alive()) and device_node_value:
        _start_keepalive_monitor(serial_number=serial_number, device_node=device_node_value, db_path=db_path)
        time.sleep(0.05)
        with _state._MONITOR_LOCK:
            monitor_thread = _state._KEEPALIVE_THREADS.get(serial_number)
    if not monitor_thread or not monitor_thread.is_alive():
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "ack": "NO MONITOR", "error_kind": "keepalive_not_running", "latency_ms": None,
        }

    request = _enqueue_traction_out_request(serial_number=serial_number, command=command_text)
    done_event = request.get("done_event")
    if isinstance(done_event, threading.Event):
        done_event.wait(max(0.1, float(timeout_sec)))

    result = request.get("result")
    if not isinstance(result, dict):
        with _state._TRACTION_OUT_LOCK:
            pending = _state._TRACTION_OUT_PENDING.get(serial_number)
            if pending is request:
                _state._TRACTION_OUT_PENDING.pop(serial_number, None)
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "ack": "TIMEOUT", "error_kind": "traction_out_send_timeout", "latency_ms": None,
        }

    merged = dict(result)
    merged.setdefault("serial_number", serial_number)
    merged.setdefault("command", command_text)
    return merged


def _complete_cmd_request(request: dict | None, result: dict):
    if not isinstance(request, dict):
        return
    done_event = request.get("done_event")
    request["result"] = dict(result or {})
    if isinstance(done_event, threading.Event):
        done_event.set()


def _enqueue_cmd_request(serial_number: str, command: str) -> dict:
    text = str(command or "").strip()
    request = {
        "serial_number": serial_number,
        "command": text,
        "queued_at_monotonic_ns": time.perf_counter_ns(),
        "done_event": threading.Event(),
        "result": None,
    }

    superseded = None
    with _state._CMD_REQUEST_LOCK:
        _state._CMD_REQUEST_ID += 1
        request["request_id"] = int(_state._CMD_REQUEST_ID)
        superseded = _state._CMD_REQUEST_PENDING.get(serial_number)
        _state._CMD_REQUEST_PENDING[serial_number] = request

    if isinstance(superseded, dict):
        _complete_cmd_request(
            superseded,
            {
                "ok": False, "serial_number": serial_number,
                "command": str(superseded.get("command") or ""),
                "response": "SUPERSEDED", "error_kind": "cmd_superseded", "latency_ms": None,
            },
        )
    _state._wake_keepalive_monitor(serial_number)
    return request


def _pop_cmd_request(serial_number: str) -> dict | None:
    with _state._CMD_REQUEST_LOCK:
        return _state._CMD_REQUEST_PENDING.pop(serial_number, None)


def _cancel_cmd_request(serial_number: str, error_kind: str):
    request = _pop_cmd_request(serial_number)
    if not isinstance(request, dict):
        return
    _complete_cmd_request(
        request,
        {
            "ok": False, "serial_number": serial_number,
            "command": str(request.get("command") or ""),
            "response": "CANCELLED",
            "error_kind": str(error_kind or "cmd_cancelled"), "latency_ms": None,
        },
    )


def send_device_cmd_once(
    db_path: str,
    serial_number: str,
    command: str,
    timeout_sec: float = 1.5,
) -> dict | None:
    if not serial_number:
        return None
    command_text = str(command or "").strip()
    if not command_text:
        return {
            "ok": False, "serial_number": serial_number, "command": "",
            "response": "EMPTY", "error_kind": "cmd_empty", "latency_ms": None,
        }

    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)
        if not item:
            return None
        status_value = str(item.get("status") or "")
        message_type_value = _normalize_message_type_name(item.get("message_type"))
        device_node_value = str(item.get("device_node") or "").strip()

    if message_type_value != MESSAGE_TYPE_CMD:
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "response": "MODE REQUIRED", "error_kind": "cmd_mode_required", "latency_ms": None,
        }
    if status_value != STATUS_ONLINE_CONNECTED:
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "response": "OFFLINE", "error_kind": "device_offline", "latency_ms": None,
        }

    with _state._MONITOR_LOCK:
        monitor_thread = _state._KEEPALIVE_THREADS.get(serial_number)
    if (not monitor_thread or not monitor_thread.is_alive()) and device_node_value:
        _start_keepalive_monitor(serial_number=serial_number, device_node=device_node_value, db_path=db_path)
        time.sleep(0.05)
        with _state._MONITOR_LOCK:
            monitor_thread = _state._KEEPALIVE_THREADS.get(serial_number)
    if not monitor_thread or not monitor_thread.is_alive():
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "response": "NO MONITOR", "error_kind": "keepalive_not_running", "latency_ms": None,
        }

    request = _enqueue_cmd_request(serial_number, command_text)
    done_event = request.get("done_event")
    if isinstance(done_event, threading.Event):
        done_event.wait(max(0.1, float(timeout_sec)))

    result = request.get("result")
    if not isinstance(result, dict):
        with _state._CMD_REQUEST_LOCK:
            pending = _state._CMD_REQUEST_PENDING.get(serial_number)
            if pending is request:
                _state._CMD_REQUEST_PENDING.pop(serial_number, None)
        return {
            "ok": False, "serial_number": serial_number, "command": command_text,
            "response": "TIMEOUT", "error_kind": "cmd_send_timeout", "latency_ms": None,
        }

    merged = dict(result)
    merged.setdefault("serial_number", serial_number)
    merged.setdefault("command", command_text)
    return merged


def _stop_keepalive_monitor(serial_number: str | None):
    if not serial_number:
        return
    with _state._MONITOR_LOCK:
        stop_event = _state._KEEPALIVE_STOPS.pop(serial_number, None)
        _state._KEEPALIVE_THREADS.pop(serial_number, None)
        wake_event = _state._KEEPALIVE_WAKES.pop(serial_number, None)
    if stop_event:
        stop_event.set()
    if wake_event:
        wake_event.set()
    _cancel_traction_out_request(serial_number, "keepalive_stopped")
    _cancel_cmd_request(serial_number, "keepalive_stopped")


def stop_all_keepalive_monitors():
    with _state._MONITOR_LOCK:
        serial_numbers = list(_state._KEEPALIVE_STOPS.keys())
    for serial_number in serial_numbers:
        _stop_keepalive_monitor(serial_number)


def resume_keepalive_monitors(db_path: str):
    with _state._DB_LOCK:
        data = _load_db(db_path)
        devices = list(data["devices"])
    for item in devices:
        if item.get("status") != STATUS_ONLINE_CONNECTED:
            continue
        serial_number = item.get("serial_number")
        device_node = item.get("device_node")
        if serial_number and device_node:
            _start_keepalive_monitor(
                serial_number=serial_number,
                device_node=device_node,
                db_path=db_path,
            )


def _keepalive_loop(
    serial_number: str,
    initial_node: str | None,
    db_path: str,
    stop_event: threading.Event,
    wake_event: threading.Event,
):
    device_node = initial_node
    keepalive_serial: serial.Serial | None = None
    keepalive_port: str | None = None
    stream_reader: _SerialFrameReader | None = None
    keepalive_line_mode = False
    stream_seq = FRAME_SEQUENCE_MIN
    stream_seq_abs = 0
    last_telemetry_rx_at = 0.0
    last_telemetry_sync_at = 0.0
    last_telemetry_timeout_error_at = 0.0
    telemetry_requested = False
    telemetry_active_state = False
    device_message_type = _default_device_message_type()
    device_traction_out_value = TRACTION_OUT_DEFAULT_VALUE
    last_traction_heartbeat_at = 0.0
    last_db_refresh_at = 0.0
    last_registry_sync_at = 0.0
    last_registry_link_live: bool | None = None
    last_registry_telemetry_requested: bool | None = None
    last_registry_telemetry_active: bool | None = None

    try:
        while not stop_event.is_set():
            with _state._FLASH_LOCK:
                if serial_number in _state._FLASH_LOCKED_SERIALS:
                    break
            telemetry_mode = device_message_type == MESSAGE_TYPE_TELEMETRY
            now_mono = time.monotonic()
            db_refresh_interval = 0.05 if telemetry_mode else 0.25
            if (now_mono - last_db_refresh_at) >= db_refresh_interval:
                with _state._DB_LOCK:
                    data = _load_db(db_path)
                    item = _find_device_by_serial(data["devices"], serial_number)
                    if item is None:
                        break
                    if item.get("status") != STATUS_ONLINE_CONNECTED:
                        break
                    device_node = item.get("device_node") or device_node
                    device_message_type = _normalize_message_type_name(item.get("message_type"))
                    device_traction_out_value = _normalize_traction_out_value(item.get("traction_out_value"))
                    telemetry_requested = bool(item.get("telemetry_requested", False))
                    telemetry_active_state = bool(item.get("telemetry_active", False))
                last_db_refresh_at = now_mono

            telemetry_mode = device_message_type == MESSAGE_TYPE_TELEMETRY
            traction_out_mode = device_message_type == MESSAGE_TYPE_TRACTION_OUT
            cmd_mode = device_message_type == MESSAGE_TYPE_CMD
            if not traction_out_mode:
                _cancel_traction_out_request(serial_number, "traction_out_mode_inactive")
            if not cmd_mode:
                _cancel_cmd_request(serial_number, "cmd_mode_inactive")

            if not device_node:
                _cancel_traction_out_request(serial_number, "device_node_missing")
                _cancel_cmd_request(serial_number, "device_node_missing")
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
                    keepalive_line_mode = False
                _update_registry_by_serial(
                    serial_number=serial_number, db_path=db_path,
                    link_live=False, link_status=LINK_STATUS_NOT_LIVE,
                    telemetry_active=False, telemetry_requested=False, error_count_reset=True,
                )
                wake_event.wait(FRAME_KEEPALIVE_INTERVAL_SEC)
                wake_event.clear()
                last_db_refresh_at = 0.0
                if stop_event.is_set():
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
                    keepalive_line_mode = False
                    stream_seq = FRAME_SEQUENCE_MIN
                    stream_seq_abs = 0
                    last_telemetry_rx_at = last_telemetry_sync_at = last_telemetry_timeout_error_at = 0.0

            if (
                keepalive_serial is None
                or keepalive_port != device_node
                or not keepalive_serial.is_open
                or keepalive_line_mode != traction_out_mode
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
                keepalive_line_mode = False

                try:
                    keepalive_serial = serial.Serial(
                        device_node,
                        baudrate=active_baud,
                        timeout=0.05 if traction_out_mode else STREAM_READ_TIMEOUT_SEC,
                        write_timeout=STREAM_WRITE_TIMEOUT_SEC,
                        dsrdtr=False,
                        rtscts=False,
                    )
                    try:
                        keepalive_serial.dtr = True
                        keepalive_serial.rts = False
                    except Exception:
                        pass
                    if HELLO_OPEN_DELAY_SEC > 0:
                        time.sleep(HELLO_OPEN_DELAY_SEC)
                    keepalive_port = device_node
                    keepalive_line_mode = traction_out_mode
                    stream_seq = FRAME_SEQUENCE_MIN
                    stream_seq_abs = 0
                    last_telemetry_rx_at = last_telemetry_sync_at = 0.0

                    ack_ok, rx_module_id = _send_and_wait(
                        ser=keepalive_serial, port=device_node,
                        tx_bytes=HELLO_MESSAGE_BYTES, expected_bytes=HELLO_ACK_BYTES,
                        timeout_sec=HELLO_ACK_TIMEOUT_SEC, phase="hello",
                        db_path=db_path, serial_number=serial_number,
                        sync_bytes=FRAME_SYNC_BYTES,
                    )
                    if not ack_ok:
                        raise RuntimeError("hello handshake timeout")

                    module_type, query_module_id = _query_module_type(
                        ser=keepalive_serial, port=device_node,
                        query_bytes=MODULE_QUERY_MESSAGE_BYTES,
                        response_prefix_byte=MODULE_INFO_PREFIX_BYTE,
                        timeout_sec=MODULE_QUERY_TIMEOUT_SEC,
                        max_payload_bytes=MODULE_TYPE_MAX_BYTES,
                        db_path=db_path, serial_number=serial_number,
                        sync_bytes=FRAME_SYNC_BYTES,
                    )
                    if query_module_id is not None:
                        rx_module_id = query_module_id
                    from ..constants import DEFAULT_MODULE_TYPE
                    if module_type == DEFAULT_MODULE_TYPE:
                        module_type = _normalize_module_type(_module_id_to_type(rx_module_id))
                    _update_registry_by_serial(
                        serial_number=serial_number, db_path=db_path, device_node=device_node,
                        module_type=module_type, module_id=rx_module_id,
                        telemetry_active=False,
                        telemetry_requested=False if traction_out_mode else None,
                    )
                    stream_reader = _SerialFrameReader(
                        ser=keepalive_serial, sync_bytes=FRAME_SYNC_BYTES,
                        queue_size=FRAME_READER_QUEUE_MAX,
                    )
                    stream_reader.start()
                    last_telemetry_rx_at = last_telemetry_sync_at = 0.0
                except Exception as exc:
                    print(f"[keepalive] serial open failed on {device_node}: {exc}", flush=True)
                    _update_registry_by_serial(
                        serial_number=serial_number, db_path=db_path, device_node=device_node,
                        link_live=False, link_status=LINK_STATUS_NOT_LIVE,
                        telemetry_active=False, error_count_delta=1, error_kind="serial_open_failed",
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
                    keepalive_line_mode = False
                    _cancel_traction_out_request(serial_number, "serial_open_failed")
                    _cancel_cmd_request(serial_number, "serial_open_failed")
                    wake_event.wait(FRAME_KEEPALIVE_INTERVAL_SEC)
                    wake_event.clear()
                    last_db_refresh_at = 0.0
                    if stop_event.is_set():
                        break
                    continue

            try:
                link_live = False
                rx_frame = None
                error_delta = 0
                error_kind: str | None = None
                current_traction_request = None
                current_cmd_request = None

                if traction_out_mode:
                    if telemetry_requested:
                        telemetry_requested = False
                    if telemetry_active_state:
                        telemetry_active_state = False
                    current_traction_request = _pop_traction_out_request(serial_number)
                    if not isinstance(current_traction_request, dict):
                        link_live = bool(keepalive_serial and keepalive_serial.is_open)
                        now_mono = time.monotonic()
                        if link_live and stream_reader is not None and (now_mono - last_traction_heartbeat_at) >= 1.0:
                            # Heartbeat: resend current traction value so the firmware
                            # link watchdog (1200 ms) never fires and resets setpoint_rpm=0,
                            # which would flip direction for inverted motors mid-move.
                            heartbeat_cmd = _build_traction_out_payload(device_traction_out_value)
                            hb_ok, _, _ = _send_stream_frame_and_wait(
                                ser=keepalive_serial, stream_reader=stream_reader,
                                port=device_node, db_path=db_path, serial_number=serial_number,
                                message_type_name=MESSAGE_TYPE_TRACTION_OUT,
                                sequence=stream_seq, sequence_abs=stream_seq_abs,
                                message_bytes=heartbeat_cmd,
                                timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.15), 0.75),
                                max_attempts=1, sync_bytes=FRAME_SYNC_BYTES,
                            )
                            if hb_ok:
                                stream_seq = _next_sequence_value(stream_seq)
                                stream_seq_abs += 1
                            last_traction_heartbeat_at = now_mono
                    else:
                        if stream_reader is None:
                            error_kind = "stream_reader_unavailable"
                            raise RuntimeError("stream reader unavailable")
                        request_value_raw = current_traction_request.get("value")
                        request_value = (
                            _normalize_traction_out_value(request_value_raw)
                            if request_value_raw is not None else None
                        )
                        request_command = str(current_traction_request.get("command") or "").strip()
                        if not request_command:
                            request_command = _build_traction_out_payload(request_value).decode("utf-8")
                        command_bytes = request_command.encode("utf-8")
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass
                        tx_seq = stream_seq
                        tx_seq_abs = stream_seq_abs
                        frame_ok, frame_ack, frame_errors = _send_stream_frame_and_wait(
                            ser=keepalive_serial, stream_reader=stream_reader,
                            port=device_node, db_path=db_path, serial_number=serial_number,
                            message_type_name=MESSAGE_TYPE_TRACTION_OUT,
                            sequence=tx_seq, sequence_abs=tx_seq_abs,
                            message_bytes=command_bytes,
                            timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.15), 0.75),
                            max_attempts=1, sync_bytes=FRAME_SYNC_BYTES,
                        )
                        error_delta += frame_errors
                        ack_text = str((frame_ack or {}).get("message_text") or "").strip().upper()
                        frame_latency_ms = (frame_ack or {}).get("latency_ms")
                        if frame_ok:
                            stream_seq = _next_sequence_value(stream_seq)
                            stream_seq_abs += 1
                        if frame_ok and ack_text == "OK":
                            link_live = True
                            rx_frame = frame_ack
                            _complete_traction_out_request(
                                current_traction_request,
                                {
                                    "ok": True, "serial_number": serial_number,
                                    "traction_out_value": (int(request_value) if request_value is not None else None),
                                    "command": request_command, "ack": ack_text or "OK",
                                    "latency_ms": float(frame_latency_ms) if isinstance(frame_latency_ms, (int, float)) else None,
                                    "seq_abs": int(tx_seq_abs),
                                },
                            )
                            current_traction_request = None
                        else:
                            if (not frame_ok) and frame_errors <= 0:
                                error_delta += 1
                            if ack_text.startswith("ERR"):
                                error_kind = error_kind or "traction_out_error"
                            elif frame_ok and ack_text:
                                error_kind = error_kind or "traction_out_unexpected_ack"
                            else:
                                error_kind = error_kind or "traction_out_timeout"
                            link_live = bool(keepalive_serial and keepalive_serial.is_open)
                            _complete_traction_out_request(
                                current_traction_request,
                                {
                                    "ok": False, "serial_number": serial_number,
                                    "traction_out_value": (int(request_value) if request_value is not None else None),
                                    "command": request_command, "ack": ack_text or "TIMEOUT",
                                    "error_kind": error_kind,
                                    "latency_ms": float(frame_latency_ms) if isinstance(frame_latency_ms, (int, float)) else None,
                                    "seq_abs": int(tx_seq_abs),
                                },
                            )
                            current_traction_request = None

                elif telemetry_mode:
                    if stream_reader is None:
                        error_kind = "stream_reader_unavailable"
                        raise RuntimeError("stream reader unavailable")
                    if telemetry_requested and not telemetry_active_state:
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass
                        from .transport import _build_telemetry_start_payload
                        start_ok, start_ack, start_errors = _send_stream_frame_and_wait(
                            ser=keepalive_serial, stream_reader=stream_reader,
                            port=device_node, db_path=db_path, serial_number=serial_number,
                            message_type_name=MESSAGE_TYPE_TELEMETRY,
                            sequence=stream_seq, sequence_abs=stream_seq_abs,
                            message_bytes=_build_telemetry_start_payload(),
                            timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.05), 0.25),
                            max_attempts=1, sync_bytes=FRAME_SYNC_BYTES,
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
                            ser=keepalive_serial, stream_reader=stream_reader,
                            port=device_node, db_path=db_path, serial_number=serial_number,
                            message_type_name=MESSAGE_TYPE_TELEMETRY,
                            sequence=stream_seq, sequence_abs=stream_seq_abs,
                            message_bytes=TELEMETRY_STOP_BYTES,
                            timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.05), 0.25),
                            max_attempts=1, sync_bytes=FRAME_SYNC_BYTES,
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
                            last_telemetry_rx_at = last_telemetry_sync_at = 0.0
                            rx_frame = stop_ack
                            link_live = True
                        elif stop_ok:
                            error_delta += 1
                            error_kind = error_kind or "telemetry_stop_unexpected_ack"
                        else:
                            telemetry_active_state = False
                            last_telemetry_rx_at = last_telemetry_sync_at = 0.0
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass

                    if telemetry_active_state and telemetry_requested:
                        telemetry_frames = _drain_stream_reader_frames(
                            stream_reader=stream_reader, port=device_node,
                            db_path=db_path, serial_number=serial_number,
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
                            from .transport import _build_telemetry_sync_payload
                            sync_ok, sync_ack, sync_errors = _send_stream_frame_and_wait(
                                ser=keepalive_serial, stream_reader=stream_reader,
                                port=device_node, db_path=db_path, serial_number=serial_number,
                                message_type_name=MESSAGE_TYPE_TELEMETRY,
                                sequence=stream_seq, sequence_abs=stream_seq_abs,
                                message_bytes=_build_telemetry_sync_payload(),
                                timeout_sec=FRAME_RESPONSE_TIMEOUT_SEC,
                                max_attempts=1, sync_bytes=FRAME_SYNC_BYTES,
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
                        # Refresh last_telemetry_rx_at from the LS cache — covers frames
                        # received during the SYNC wait (they are cached via _log_stream_rx_frame
                        # even when drain is blocked), so a slow/failed SYNC doesn't falsely
                        # mark the link as dead.
                        with _state._LATEST_LS_LOCK:
                            _cached_ls = _state._LATEST_LS_FRAMES.get(serial_number)
                        if _cached_ls is not None:
                            _cached_ts = _cached_ls[0]
                            if _cached_ts > last_telemetry_rx_at:
                                last_telemetry_rx_at = _cached_ts
                                last_telemetry_timeout_error_at = 0.0

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
                        if stream_reader is None:
                            error_kind = "stream_reader_unavailable"
                            raise RuntimeError("stream reader unavailable")
                        stream_reader.clear_frames()
                        try:
                            keepalive_serial.reset_input_buffer()
                        except Exception:
                            pass
                        stop_ok, _stop_ack, stop_errors = _send_stream_frame_and_wait(
                            ser=keepalive_serial, stream_reader=stream_reader,
                            port=device_node, db_path=db_path, serial_number=serial_number,
                            message_type_name=MESSAGE_TYPE_TELEMETRY,
                            sequence=stream_seq, sequence_abs=stream_seq_abs,
                            message_bytes=TELEMETRY_STOP_BYTES,
                            timeout_sec=min(max(FRAME_RESPONSE_TIMEOUT_SEC, 0.05), 0.25),
                            max_attempts=1, sync_bytes=FRAME_SYNC_BYTES,
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
                    if stream_reader is None:
                        error_kind = "stream_reader_unavailable"
                        raise RuntimeError("stream reader unavailable")
                    stream_reader.clear_frames()
                    active_payload = None
                    if active_type == MESSAGE_TYPE_TRACTION_OUT:
                        active_payload = _build_traction_out_payload(device_traction_out_value)
                    elif active_type == MESSAGE_TYPE_CMD:
                        current_cmd_request = _pop_cmd_request(serial_number)
                        if isinstance(current_cmd_request, dict):
                            command_text = str(current_cmd_request.get("command") or "").strip()
                            if command_text:
                                active_payload = command_text.encode("utf-8")
                            else:
                                _complete_cmd_request(
                                    current_cmd_request,
                                    {
                                        "ok": False, "serial_number": serial_number,
                                        "command": command_text, "response": "EMPTY",
                                        "error_kind": "cmd_empty", "latency_ms": None,
                                    },
                                )
                                current_cmd_request = None

                    link_live, rx_frame, frame_errors = _send_stream_frame_and_wait(
                        ser=keepalive_serial, stream_reader=stream_reader,
                        port=device_node, db_path=db_path, serial_number=serial_number,
                        message_type_name=active_type,
                        sequence=stream_seq, sequence_abs=stream_seq_abs,
                        message_bytes=active_payload,
                        timeout_sec=FRAME_RESPONSE_TIMEOUT_SEC,
                        max_attempts=FRAME_MAX_RETRY_ATTEMPTS, sync_bytes=FRAME_SYNC_BYTES,
                    )
                    error_delta += frame_errors
                    if link_live and rx_frame is not None:
                        stream_seq = _next_sequence_value(stream_seq)
                        stream_seq_abs += 1
                        last_telemetry_timeout_error_at = 0.0
                        if isinstance(current_cmd_request, dict):
                            ack_text = str((rx_frame or {}).get("message_text") or "").strip()
                            ack_upper = ack_text.upper()
                            frame_latency_ms = (rx_frame or {}).get("latency_ms")
                            result_base = {
                                "serial_number": serial_number,
                                "command": str(current_cmd_request.get("command") or ""),
                                "latency_ms": float(frame_latency_ms) if isinstance(frame_latency_ms, (int, float)) else None,
                                "seq_abs": int(stream_seq_abs - 1),
                            }
                            if ack_upper.startswith("ERR"):
                                _complete_cmd_request(current_cmd_request, {**result_base, "ok": False, "response": ack_text or "ERR", "error_kind": "cmd_error"})
                            else:
                                _complete_cmd_request(current_cmd_request, {**result_base, "ok": True, "response": ack_text})
                            current_cmd_request = None
                    elif not link_live:
                        if frame_errors <= 0:
                            error_delta += 1
                        error_kind = error_kind or "stream_exchange_timeout"
                        if isinstance(current_cmd_request, dict):
                            _complete_cmd_request(
                                current_cmd_request,
                                {
                                    "ok": False, "serial_number": serial_number,
                                    "command": str(current_cmd_request.get("command") or ""),
                                    "response": "TIMEOUT", "error_kind": "cmd_timeout",
                                    "latency_ms": None, "seq_abs": int(stream_seq_abs),
                                },
                            )
                            current_cmd_request = None

            except Exception as exc:
                print(f"[keepalive] probe error on {device_node}: {exc}", flush=True)
                link_live = False
                rx_frame = None
                error_delta = 1
                error_kind = error_kind or "keepalive_exception"
                telemetry_active_state = False
                if isinstance(current_traction_request, dict):
                    _complete_traction_out_request(
                        current_traction_request,
                        {
                            "ok": False, "serial_number": serial_number,
                            "traction_out_value": int(_normalize_traction_out_value(current_traction_request.get("value"))),
                            "ack": "EXCEPTION", "error_kind": "keepalive_exception", "latency_ms": None,
                        },
                    )
                    current_traction_request = None
                _cancel_traction_out_request(serial_number, "keepalive_exception")
                if isinstance(current_cmd_request, dict):
                    _complete_cmd_request(
                        current_cmd_request,
                        {
                            "ok": False, "serial_number": serial_number,
                            "command": str(current_cmd_request.get("command") or ""),
                            "response": "EXCEPTION", "error_kind": "keepalive_exception", "latency_ms": None,
                        },
                    )
                    current_cmd_request = None
                _cancel_cmd_request(serial_number, "keepalive_exception")
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
                keepalive_line_mode = False
                if stream_reader is not None:
                    stream_reader.clear_frames()
                    stream_reader.stop()
                    stream_reader = None
                stream_seq = FRAME_SEQUENCE_MIN
                stream_seq_abs = 0
                last_telemetry_rx_at = last_telemetry_sync_at = last_telemetry_timeout_error_at = 0.0

            now_mono = time.monotonic()
            registry_changed = (
                last_registry_link_live is None
                or link_live != last_registry_link_live
                or telemetry_requested != last_registry_telemetry_requested
                or telemetry_active_state != last_registry_telemetry_active
            )
            registry_interval = 0.25 if (traction_out_mode or telemetry_mode) else FRAME_KEEPALIVE_INTERVAL_SEC
            registry_due = (now_mono - last_registry_sync_at) >= registry_interval
            if error_delta or registry_changed or registry_due:
                if error_delta and not error_kind:
                    error_kind = "comm_error"
                _update_registry_by_serial(
                    serial_number=serial_number, db_path=db_path, device_node=device_node,
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

            if traction_out_mode:
                wait_sec = 0.2
            elif telemetry_mode and telemetry_requested and telemetry_active_state:
                wait_sec = FRAME_TELEMETRY_IDLE_SLEEP_SEC
            elif telemetry_mode:
                wait_sec = FRAME_TELEMETRY_POLL_INTERVAL_SEC
            else:
                wait_sec = FRAME_KEEPALIVE_INTERVAL_SEC
            wake_event.wait(wait_sec)
            wake_event.clear()
            last_db_refresh_at = 0.0
            if stop_event.is_set():
                break

    finally:
        _cancel_traction_out_request(serial_number, "keepalive_stopped")
        _cancel_cmd_request(serial_number, "keepalive_stopped")
        if stream_reader is not None:
            stream_reader.stop()
        if keepalive_serial is not None:
            try:
                keepalive_serial.close()
            except Exception:
                pass
        with _state._MONITOR_LOCK:
            existing_stop = _state._KEEPALIVE_STOPS.get(serial_number)
            if existing_stop is stop_event:
                _state._KEEPALIVE_STOPS.pop(serial_number, None)
                _state._KEEPALIVE_THREADS.pop(serial_number, None)
                _state._KEEPALIVE_WAKES.pop(serial_number, None)


def _start_keepalive_monitor(
    serial_number: str | None,
    device_node: str | None,
    db_path: str,
):
    if not serial_number:
        return
    with _state._MONITOR_LOCK:
        thread = _state._KEEPALIVE_THREADS.get(serial_number)
        if thread and thread.is_alive():
            _state._KEEPALIVE_WAKES.setdefault(serial_number, threading.Event())
            return
        stop_event = threading.Event()
        wake_event = threading.Event()
        monitor = threading.Thread(
            target=_keepalive_loop,
            args=(serial_number, device_node, db_path, stop_event, wake_event),
            daemon=True,
            name=f"keepalive-{serial_number.replace(':', '')[-8:]}",
        )
        _state._KEEPALIVE_STOPS[serial_number] = stop_event
        _state._KEEPALIVE_THREADS[serial_number] = monitor
        _state._KEEPALIVE_WAKES[serial_number] = wake_event
        monitor.start()
