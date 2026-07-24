import json
import time

import serial

from ..constants import (
    DEFAULT_MODULE_TYPE,
    DEFAULT_SERIAL_BAUD,
    FRAME_LEN_MAX,
    FRAME_MAX_RETRY_ATTEMPTS,
    FRAME_RESPONSE_TIMEOUT_SEC,
    FRAME_RETRY_DELAY_SEC,
    FRAME_SYNC_BYTES,
    FRAME_TELEMETRY_POLL_INTERVAL_SEC,
    HELLO_ACK_BYTES,
    HELLO_ACK_TIMEOUT_SEC,
    HELLO_MESSAGE_BYTES,
    HELLO_OPEN_DELAY_SEC,
    HELLO_READ_TIMEOUT_SEC,
    HOST_MODULE_ID,
    MESSAGE_TYPE_DEFAULT_BYTES,
    MESSAGE_TYPES,
    MODULE_INFO_PREFIX_BYTE,
    MODULE_QUERY_MESSAGE_BYTES,
    MODULE_QUERY_TIMEOUT_SEC,
    MODULE_TYPE_MAX_BYTES,
    STREAM_WRITE_TIMEOUT_SEC,
    TELEMETRY_START_COMMAND,
    TELEMETRY_SYNC_COMMAND,
    TRACTION_OUT_COMMAND_PREFIX,
)
from . import _state
from .comms_log import _enqueue_comms_log_line, _start_comms_log_writer
from .framing import (
    _SerialFrameReader,
    _build_stream_frame,
    _find_framed_payload,
    _frame_payload,
    _next_sequence_value,
    _normalize_sequence_value,
)
from .registry import (
    _find_active_device_by_node,
    _find_device_by_node,
    _load_db,
    _module_id_hex,
    _module_id_to_type,
    _normalize_message_type_name,
    _normalize_module_type,
    _normalize_traction_out_value,
)


def open_serial(port: str, baud: int, timeout: float):
    return serial.Serial(port, baudrate=baud, timeout=timeout)



def _build_telemetry_start_payload() -> bytes:
    host_epoch_ms = int(time.time_ns() // 1_000_000)
    return f"{TELEMETRY_START_COMMAND}:{host_epoch_ms}".encode("utf-8")


def _build_telemetry_sync_payload() -> bytes:
    host_epoch_ms = int(time.time_ns() // 1_000_000)
    return f"{TELEMETRY_SYNC_COMMAND}:{host_epoch_ms}".encode("utf-8")


def _build_traction_out_payload(value: int | str | None) -> bytes:
    normalized = _normalize_traction_out_value(value)
    return f"{TRACTION_OUT_COMMAND_PREFIX} {normalized}".encode("utf-8")


def _classify_line_ack(line_text: str) -> str | None:
    text = str(line_text or "").strip().upper()
    if not text:
        return None
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text)
    tokens = [t for t in cleaned.split() if t]
    for token in tokens:
        compact = token.lstrip("0123456789")
        if compact == "OK":
            return "OK"
        if compact == "ERR":
            return "ERR"
    return None


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
    from ..constants import MESSAGE_TYPE_TELEMETRY
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
    if one_way_ms < 0 or one_way_ms > 60_000:
        return
    parsed_frame["latency_ms"] = round(one_way_ms, 3)


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
        with _state._DB_LOCK:
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

    event: dict = {
        "sender": sender,
        "device_serial": resolved_serial,
        "raw_hex": payload.hex(),
        "direction": direction_norm or "unknown",
        "phase": str(phase or "").strip() or None,
    }
    if parsed_frame:
        event.update({
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
        })
    event = {k: v for k, v in event.items() if v is not None}

    _start_comms_log_writer()
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    _enqueue_comms_log_line(line)


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
    # Cache sensor telemetry regardless of which code path receives it
    # (drain, SYNC wait, START wait — all paths log through here).
    if serial_number:
        msg_text = str(parsed.get("message_text") or "")
        if msg_text.startswith("LS,"):
            with _state._LATEST_LS_LOCK:
                _state._LATEST_LS_FRAMES[serial_number] = (time.monotonic(), msg_text)
        elif msg_text.startswith("DS,"):
            with _state._LATEST_DS_LOCK:
                _state._LATEST_DS_FRAMES[serial_number] = (time.monotonic(), msg_text)
    return parsed_for_log


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
    if not tx_bytes or not expected_bytes:
        return False, None

    framed_tx = _frame_payload(tx_bytes, sync_bytes, module_id=HOST_MODULE_ID)
    ser.reset_input_buffer()
    ser.write(framed_tx)
    ser.flush()
    _append_communication_event(
        db_path=db_path, device_node=port, phase=phase,
        direction="tx", payload=framed_tx, serial_number=serial_number,
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
        _append_communication_event(
            db_path=db_path, device_node=port, phase=phase,
            direction="rx", payload=chunk, serial_number=serial_number,
        )
        matched, rx_module_id = _find_framed_payload(rx_buffer, sync_bytes, expected_bytes)
        if matched:
            return True, rx_module_id

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
    _append_communication_event(
        db_path=db_path, device_node=port, phase="module",
        direction="tx", payload=framed_query, serial_number=serial_number,
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
            db_path=db_path, device_node=port, phase="module",
            direction="rx", payload=chunk, serial_number=serial_number,
        )

        sync = sync_bytes or b""
        base = len(sync)
        min_needed = base + 3
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
            return module_type, module_id

    return DEFAULT_MODULE_TYPE, None


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
        with serial.Serial(
            port,
            baudrate=baud,
            timeout=read_timeout_sec,
            write_timeout=read_timeout_sec,
            dsrdtr=False,
            rtscts=False,
        ) as ser:
            try:
                ser.dtr = True
                ser.rts = False
            except Exception:
                pass
            if open_delay_sec > 0:
                time.sleep(open_delay_sec)

            ack_ok, ack_module_id = _send_and_wait(
                ser=ser, port=port, tx_bytes=hello_message,
                expected_bytes=ack_message, timeout_sec=ack_timeout_sec,
                phase="hello", db_path=db_path, serial_number=serial_number,
                sync_bytes=sync_bytes,
            )
            if not ack_ok:
                return False, DEFAULT_MODULE_TYPE, ack_module_id

            detected_module_id = ack_module_id
            module_type, query_module_id = _query_module_type(
                ser=ser, port=port, query_bytes=module_query_message,
                response_prefix_byte=module_info_prefix_byte,
                timeout_sec=module_timeout_sec,
                max_payload_bytes=MODULE_TYPE_MAX_BYTES,
                db_path=db_path, serial_number=serial_number,
                sync_bytes=sync_bytes,
            )
            if query_module_id is not None:
                detected_module_id = query_module_id
            if module_type == DEFAULT_MODULE_TYPE:
                module_type = _normalize_module_type(_module_id_to_type(detected_module_id))

            return True, module_type, detected_module_id
    except Exception as exc:
        print(f"[link] probe error on {port}: {exc}", flush=True)
        return False, DEFAULT_MODULE_TYPE, None


def _send_line_command_and_wait(
    ser: serial.Serial,
    port: str,
    db_path: str,
    serial_number: str | None,
    command_text: str,
    message_type_name: str,
    sequence_abs: int | None = None,
    timeout_sec: float = FRAME_RESPONSE_TIMEOUT_SEC,
    max_attempts: int = FRAME_MAX_RETRY_ATTEMPTS,
) -> tuple[bool, dict | None, int]:
    normalized_type = _normalize_message_type_name(message_type_name)
    command = str(command_text or "").strip()
    if not command:
        return False, None, 1

    attempts = max(1, int(max_attempts or 1))
    timeout_errors = 0
    seq_abs_val = int(sequence_abs) if sequence_abs is not None else None

    for attempt_idx in range(attempts):
        is_retry = attempt_idx > 0
        tx_bytes = (command + "\n").encode("utf-8")
        tx_frame = {
            "len": len(command),
            "message_text": command,
            "message_type": normalized_type,
            "seq_abs": seq_abs_val,
            "retry": is_retry,
            "retry_count": attempt_idx,
            "error_kind": "timeout_retry" if is_retry else None,
        }
        tx_started_ns = time.perf_counter_ns()
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        ser.write(tx_bytes)
        ser.flush()
        _append_communication_event(
            db_path=db_path, device_node=port, phase="line",
            direction="tx", payload=tx_bytes, serial_number=serial_number,
            parsed_frame=tx_frame,
        )

        deadline = time.monotonic() + max(float(timeout_sec), 0.1)
        rx_buffer = bytearray()
        last_err_parsed: dict | None = None
        while time.monotonic() < deadline:
            chunk = ser.read(64)
            if not chunk:
                continue
            rx_buffer.extend(chunk)
            if len(rx_buffer) > 1024:
                del rx_buffer[:-1024]

            while True:
                newline_index = -1
                for idx, ch in enumerate(rx_buffer):
                    if ch in (0x0A, 0x0D):
                        newline_index = idx
                        break
                if newline_index < 0:
                    break
                line_bytes = bytes(rx_buffer[: newline_index + 1])
                del rx_buffer[: newline_index + 1]
                line_text = line_bytes.decode("utf-8", errors="replace").strip()
                if not line_text:
                    continue

                parsed = {
                    "len": len(line_text),
                    "message_text": line_text,
                    "message_type": normalized_type,
                    "seq_abs": seq_abs_val,
                    "latency_ms": round(
                        max(0.0, (time.perf_counter_ns() - tx_started_ns) / 1_000_000.0), 3
                    ),
                }
                _append_communication_event(
                    db_path=db_path, device_node=port, phase="line",
                    direction="rx", payload=line_bytes, serial_number=serial_number,
                    parsed_frame=parsed,
                )

                ack_kind = _classify_line_ack(line_text)
                if ack_kind == "OK":
                    return True, parsed, timeout_errors
                if ack_kind == "ERR":
                    last_err_parsed = parsed
                    continue

        if last_err_parsed is not None:
            return False, last_err_parsed, timeout_errors + 1

        timeout_errors += 1
        if attempt_idx < attempts - 1 and FRAME_RETRY_DELAY_SEC > 0:
            time.sleep(FRAME_RETRY_DELAY_SEC)

    return False, None, timeout_errors


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
            db_path=db_path, device_node=port, phase="stream",
            direction="tx", payload=tx_bytes, serial_number=serial_number,
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
            parsed = stream_reader.get_frame(
                timeout=min(remaining, FRAME_TELEMETRY_POLL_INTERVAL_SEC)
            )
            if parsed is None:
                continue

            received_seq = int(parsed.get("seq", -1))
            received_type_code = int(parsed.get("message_type_code", -1))
            seq_valid = (received_seq == seq_val) and (received_type_code == int(spec.code))
            parsed_for_log = _log_stream_rx_frame(
                db_path=db_path, port=port, serial_number=serial_number,
                parsed=parsed, expected_seq=seq_val, seq_valid=seq_valid,
                seq_abs=seq_abs_val if seq_valid else None,
                rtt_started_ns=tx_started_ns if seq_valid else None,
            )
            if seq_valid:
                return True, parsed_for_log, timeout_errors

        timeout_errors += 1
        if attempt_idx < attempts - 1 and FRAME_RETRY_DELAY_SEC > 0:
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
            db_path=db_path, port=port, serial_number=serial_number,
            parsed=parsed, seq_valid=True,
        )
        received += 1
    return received


def get_latest_ls_frame(serial_number: str) -> tuple[float, str] | None:
    """Return (monotonic_ts, raw_text) of the last cached LS telemetry frame, or None."""
    with _state._LATEST_LS_LOCK:
        return _state._LATEST_LS_FRAMES.get(serial_number)


def get_latest_ds_frame(serial_number: str) -> tuple[float, str] | None:
    """Return (monotonic_ts, raw_text) of the last cached DS telemetry frame, or None."""
    with _state._LATEST_DS_LOCK:
        return _state._LATEST_DS_FRAMES.get(serial_number)
