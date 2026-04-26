import queue
import threading

import serial

from ..constants import (
    FRAME_LEN_MAX,
    FRAME_READER_QUEUE_MAX,
    FRAME_RX_BUFFER_MAX_BYTES,
    FRAME_SEQUENCE_BYTES,
    FRAME_SEQUENCE_MAX,
    FRAME_SEQUENCE_MIN,
    FRAME_SYNC_BYTES,
    MESSAGE_TYPE_CODE_TO_NAME,
)


def _frame_payload(payload: bytes, sync_bytes: bytes, module_id: int | None = None) -> bytes:
    from ..constants import HOST_MODULE_ID
    mid = HOST_MODULE_ID if module_id is None else module_id
    if not payload:
        return b""
    module_byte = bytes([(mid or 0) & 0xFF])
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


def _next_sequence_value(current: int) -> int:
    safe = _normalize_sequence_value(current)
    if safe >= FRAME_SEQUENCE_MAX:
        return FRAME_SEQUENCE_MIN
    return safe + 1


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
        out.append({
            "frame_bytes": frame_bytes,
            "len": msg_len,
            "message_bytes": msg_bytes,
            "message_text": msg_bytes.decode("utf-8", errors="replace"),
            "message_type_code": msg_type_code,
            "message_type": _stream_message_type_name(msg_type_code),
            "seq": seq,
        })

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
            name="openrdk-stream-reader",
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
        import time
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
