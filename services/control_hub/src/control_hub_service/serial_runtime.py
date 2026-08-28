from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import serial
from serial.tools import list_ports

from .protocol import (
    MODULE_NAME, TYPE_CMD, Frame, StreamParser, build_control, build_stream, control_response,
)
from .storage import reserve_device


@dataclass
class Request:
    sequence: int
    command: str
    message_type: int
    timeout: float
    completed: threading.Event = field(default_factory=threading.Event)
    response: Frame | None = None
    error: str = ""


class SerialRuntime:
    BAUD_RATE = 512000
    BOOT_DELAY_SEC = 4.0

    def __init__(self, state_dir, event_callback: Callable[[Frame], None] | None = None):
        self.state_dir = state_dir
        self.event_callback = event_callback
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.disconnect_event = threading.Event()
        self.requests: queue.Queue[Request] = queue.Queue()
        self.pending: dict[int, Request] = {}
        self.worker: threading.Thread | None = None
        self.ser: serial.Serial | None = None
        self.sequence = 0x400000
        self._status = {
            "state": "disconnected", "connected": False, "port": "",
            "serial_number": "", "error": "", "connected_at": None,
        }

    @staticmethod
    def ports() -> list[dict]:
        return [{
            "device": item.device, "description": item.description or "",
            "serial_number": item.serial_number or "", "manufacturer": item.manufacturer or "",
            "vid": item.vid, "pid": item.pid,
        } for item in sorted(list_ports.comports(), key=lambda value: value.device)]

    def status(self) -> dict:
        with self.lock:
            return dict(self._status)

    def _set_status(self, **changes) -> None:
        with self.lock:
            self._status.update(changes)

    def connect(self, port: str) -> None:
        selected = str(port or "").strip()
        if not selected:
            raise ValueError("select a serial port")
        with self.lock:
            if self.worker and self.worker.is_alive():
                if self._status.get("connected") and self._status.get("port") == selected:
                    return
                raise RuntimeError("disconnect the current port first")
            self.stop_event.clear()
            self.disconnect_event.clear()
            self._set_status(state="connecting", connected=False, port=selected, error="")
            self.worker = threading.Thread(
                target=self._run, args=(selected,), name="control-hub-serial", daemon=True,
            )
            self.worker.start()

    def disconnect(self) -> None:
        self.disconnect_event.set()
        current = self.ser
        if current is not None:
            try:
                current.cancel_read()
            except (AttributeError, OSError, serial.SerialException):
                pass
        worker = self.worker
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=3)
        self._fail_pending("serial connection was closed")
        self._set_status(state="disconnected", connected=False, connected_at=None)

    def shutdown(self) -> None:
        self.stop_event.set()
        self.disconnect()

    def send(self, command: str, message_type: int = TYPE_CMD, timeout: float = 2.0) -> str:
        if not self.status()["connected"]:
            raise RuntimeError("control module is not connected")
        with self.lock:
            self.sequence = (self.sequence + 1) & 0xFFFFFF
            if self.sequence == 0:
                self.sequence = 1
            request = Request(self.sequence, str(command), int(message_type), max(0.1, float(timeout)))
            self.pending[request.sequence] = request
        self.requests.put(request)
        if not request.completed.wait(request.timeout + 0.5):
            with self.lock:
                self.pending.pop(request.sequence, None)
            raise TimeoutError(f"timeout waiting for: {command}")
        if request.error:
            raise RuntimeError(request.error)
        if request.response is None:
            raise RuntimeError("empty response from control module")
        return request.response.message

    def _handshake(self, ser: serial.Serial, timeout: float = 7.0) -> None:
        ser.reset_input_buffer()
        received = bytearray()
        deadline = time.monotonic() + timeout
        next_hello = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_hello:
                ser.write(build_control(0x01))
                ser.flush()
                next_hello = time.monotonic() + 0.35
            received.extend(ser.read(128))
            ok, _ = control_response(bytes(received), 0x06)
            if ok:
                break
        else:
            raise RuntimeError("the selected port did not answer as a control module")
        ser.reset_input_buffer()
        received.clear()
        ser.write(build_control(0x04))
        ser.flush()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            received.extend(ser.read(128))
            ok, name = control_response(bytes(received), 0x05)
            if ok:
                if name != MODULE_NAME:
                    raise RuntimeError(f"unexpected module type: {name or 'unknown'}")
                return
        raise RuntimeError("the module identity query timed out")

    def _run(self, port: str) -> None:
        error = ""
        try:
            ser = serial.Serial(
                port=None, baudrate=self.BAUD_RATE, timeout=0.05, write_timeout=1,
                dsrdtr=False, rtscts=False,
            )
            # CH9102 auto-reset wiring uses RTS for EN and DTR for BOOT. Set
            # inactive levels before opening, then perform a normal boot reset.
            ser.dtr = False
            ser.rts = False
            ser.port = port
            ser.open()
            self.ser = ser
            ser.dtr = False
            ser.rts = True
            time.sleep(0.15)
            ser.rts = False
            time.sleep(self.BOOT_DELAY_SEC)
            self._handshake(ser)
            info = next((item for item in self.ports() if item["device"] == port), {})
            serial_number = str(info.get("serial_number") or "")
            reserve_device(self.state_dir, serial_number, port)
            self._set_status(
                state="connected", connected=True, port=port, serial_number=serial_number,
                error="", connected_at=time.time(),
            )
            parser = StreamParser()
            while not self.stop_event.is_set() and not self.disconnect_event.is_set():
                self._flush_requests(ser)
                chunk = ser.read(256)
                for frame in parser.feed(chunk):
                    with self.lock:
                        pending = self.pending.pop(frame.sequence, None)
                    if pending is not None:
                        pending.response = frame
                        pending.completed.set()
                    elif self.event_callback is not None:
                        try:
                            self.event_callback(frame)
                        except Exception:
                            pass
        except Exception as exc:
            error = str(exc)
        finally:
            current, self.ser = self.ser, None
            if current is not None:
                try:
                    current.close()
                except Exception:
                    pass
            self._fail_pending(error or "serial connection was closed")
            self._set_status(
                state="error" if error and not self.disconnect_event.is_set() else "disconnected",
                connected=False, error=error if not self.disconnect_event.is_set() else "",
                connected_at=None,
            )

    def _flush_requests(self, ser: serial.Serial) -> None:
        while True:
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                return
            with self.lock:
                if request.sequence not in self.pending:
                    continue
            try:
                packet = build_stream(request.command, request.message_type, request.sequence)
                with self.write_lock:
                    ser.write(packet)
                    ser.flush()
            except Exception as exc:
                with self.lock:
                    self.pending.pop(request.sequence, None)
                request.error = str(exc)
                request.completed.set()

    def _fail_pending(self, reason: str) -> None:
        with self.lock:
            values = list(self.pending.values())
            self.pending.clear()
        for request in values:
            request.error = reason
            request.completed.set()
