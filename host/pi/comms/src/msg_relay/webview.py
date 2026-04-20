import asyncio
import json
import os
import queue
import threading
import time
from collections import deque

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .color_support import (
    COLOR_MODULE_TYPE,
    PALETTE_DEFINITIONS,
    default_device_profile,
    get_device_profile,
    now_iso,
    parse_color_cal_line,
    parse_color_cfg_line,
    parse_color_data_line,
    parse_color_info_line,
    parse_color_patch_line,
    parse_color_selftest_line,
    set_device_profile,
    update_device_mode_profile,
)
from .functions import (
    clear_devices_registry,
    get_active_message_type,
    get_device_message_type,
    get_device_traction_out_value,
    get_active_serial_baud,
    send_device_cmd_once,
    set_active_message_type,
    set_device_message_type,
    send_device_traction_out_once,
    set_device_traction_out_value,
    set_active_serial_baud,
    set_device_name,
    set_device_telemetry_requested,
    supported_message_types,
    supported_serial_baud_rates,
)


def _ensure_file(path: str):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "a", encoding="utf-8"):
            pass


def _parse_comms_line(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    sender = payload.get("sender")
    raw_hex = payload.get("raw_hex")
    if not isinstance(sender, str) or not sender.strip():
        return None
    if not isinstance(raw_hex, str) or not raw_hex.strip():
        return None
    event = {
        "sender": sender.strip(),
        "raw_hex": raw_hex.strip().lower(),
    }
    device_serial = payload.get("device_serial")
    if isinstance(device_serial, str) and device_serial.strip():
        event["device_serial"] = device_serial.strip()
    direction = payload.get("direction")
    if isinstance(direction, str) and direction.strip():
        event["direction"] = direction.strip().lower()
    message_type = payload.get("message_type")
    if isinstance(message_type, str) and message_type.strip():
        event["message_type"] = message_type.strip().upper()
    message = payload.get("message")
    if isinstance(message, str):
        event["message"] = message
    seq = payload.get("seq")
    if isinstance(seq, int):
        event["seq"] = seq
    seq_abs = payload.get("seq_abs")
    if isinstance(seq_abs, int):
        event["seq_abs"] = seq_abs
    latency_ms = payload.get("latency_ms")
    if isinstance(latency_ms, (int, float)):
        event["latency_ms"] = float(latency_ms)
    retry = payload.get("retry")
    if isinstance(retry, bool):
        event["retry"] = retry
    retry_count = payload.get("retry_count")
    if isinstance(retry_count, int):
        event["retry_count"] = retry_count
    error_kind = payload.get("error_kind")
    if isinstance(error_kind, str) and error_kind.strip():
        event["error_kind"] = error_kind.strip()
    seq_valid = payload.get("seq_valid")
    if isinstance(seq_valid, bool):
        event["seq_valid"] = seq_valid
    expected_seq = payload.get("expected_seq")
    if isinstance(expected_seq, int):
        event["expected_seq"] = expected_seq
    length = payload.get("len")
    if isinstance(length, int):
        event["len"] = length
    return event


def _load_devices(db_path: str) -> list[dict]:
    try:
        with open(db_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return []

    devices = data.get("devices")
    if not isinstance(devices, list):
        return []
    out = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        serial = item.get("serial_number")
        if not isinstance(serial, str) or not serial.strip():
            continue
        module_type = item.get("module_type", item.get("firmware_module", ""))
        message_type = str(item.get("message_type", "CMD") or "CMD").strip().upper()
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = module_type
        out.append(
            {
                "serial_number": serial.strip(),
                "name": name,
                "status": item.get("status", ""),
                "module_type": module_type,
                "message_type": message_type,
                "traction_out_value": int(item.get("traction_out_value", 0) or 0),
                "link_status": item.get("link_status", ""),
                "device_node": item.get("device_node"),
                "last_event_at": item.get("last_event_at"),
                "last_link_check_at": item.get("last_link_check_at"),
                "error_count": int(item.get("error_count", 0) or 0),
                "last_error_kind": str(item.get("last_error_kind", "") or ""),
                "last_error_at": str(item.get("last_error_at", "") or ""),
                "telemetry_requested": bool(item.get("telemetry_requested", False)),
                "telemetry_active": bool(item.get("telemetry_active", False)),
            }
        )
    out.sort(key=lambda dev: dev.get("serial_number", ""))
    return out


class CommsStreamBroker:
    def __init__(self, comms_log_path: str, history_size: int = 5000):
        self._comms_log_path = os.path.abspath(comms_log_path)
        self._history = deque(maxlen=max(100, history_size))
        self._history_lock = threading.Lock()
        self._subscribers: dict[int, queue.Queue] = {}
        self._subscribers_lock = threading.Lock()
        self._ingest_queue: queue.Queue = queue.Queue(maxsize=4096)
        self._line_counter = 0
        self._line_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._log_thread: threading.Thread | None = None
        self._stream_thread: threading.Thread | None = None
        self._next_subscriber_id = 1

    def start(self):
        _ensure_file(self._comms_log_path)
        self._bootstrap_history()
        self._stop_event.clear()
        self._log_thread = threading.Thread(
            target=self._log_tail_worker,
            name="msg-relay-log-thread",
            daemon=True,
        )
        self._stream_thread = threading.Thread(
            target=self._ui_stream_worker,
            name="msg-relay-ui-stream-thread",
            daemon=True,
        )
        self._log_thread.start()
        self._stream_thread.start()

    def stop(self):
        self._stop_event.set()
        if self._log_thread and self._log_thread.is_alive():
            self._log_thread.join(timeout=2)
        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=2)

    def _next_line(self) -> int:
        with self._line_lock:
            self._line_counter += 1
            return self._line_counter

    def _bootstrap_history(self):
        with self._history_lock:
            self._history.clear()

        count = 0
        try:
            with open(self._comms_log_path, "r", encoding="utf-8", errors="ignore") as fp:
                for raw_line in fp:
                    parsed = _parse_comms_line(raw_line)
                    if parsed is None:
                        continue
                    count += 1
                    parsed["line"] = count
                    with self._history_lock:
                        self._history.append(parsed)
        except OSError:
            pass

        with self._line_lock:
            self._line_counter = count

    def _enqueue_event(self, event: dict):
        try:
            self._ingest_queue.put_nowait(event)
            return
        except queue.Full:
            pass
        try:
            self._ingest_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._ingest_queue.put_nowait(event)
        except queue.Full:
            pass

    def _log_tail_worker(self):
        _ensure_file(self._comms_log_path)
        try:
            fp = open(self._comms_log_path, "r", encoding="utf-8", errors="ignore")
        except OSError:
            return

        try:
            fp.seek(0, os.SEEK_END)
            while not self._stop_event.is_set():
                line = fp.readline()
                if not line:
                    try:
                        if os.path.getsize(self._comms_log_path) < fp.tell():
                            fp.seek(0)
                    except OSError:
                        pass
                    time.sleep(0.2)
                    continue

                parsed = _parse_comms_line(line)
                if parsed is None:
                    continue
                self._enqueue_event(parsed)
        finally:
            fp.close()

    def _ui_stream_worker(self):
        while not self._stop_event.is_set():
            try:
                event = self._ingest_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            event = dict(event)
            event["line"] = self._next_line()
            event.setdefault("direction", "unknown")
            with self._history_lock:
                self._history.append(event)

            dead: list[int] = []
            with self._subscribers_lock:
                subscribers = list(self._subscribers.items())
            for subscriber_id, subscriber_queue in subscribers:
                try:
                    subscriber_queue.put_nowait(event)
                except queue.Full:
                    try:
                        subscriber_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        subscriber_queue.put_nowait(event)
                    except queue.Full:
                        pass
                except Exception:
                    dead.append(subscriber_id)

            if dead:
                with self._subscribers_lock:
                    for subscriber_id in dead:
                        self._subscribers.pop(subscriber_id, None)

    def register(self) -> tuple[int, queue.Queue]:
        with self._subscribers_lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            subscriber_queue: queue.Queue = queue.Queue(maxsize=2048)
            self._subscribers[subscriber_id] = subscriber_queue
            return subscriber_id, subscriber_queue

    def unregister(self, subscriber_id: int):
        with self._subscribers_lock:
            self._subscribers.pop(subscriber_id, None)

    def history(self, limit: int, serial_filter: str | None = None) -> list[dict]:
        keep = max(1, min(int(limit), 5000))
        serial = (serial_filter or "").strip()
        with self._history_lock:
            events = list(self._history)

        if serial:
            events = [
                item
                for item in events
                if item.get("sender") == serial or item.get("device_serial") == serial
            ]
        if len(events) > keep:
            events = events[-keep:]
        return events


class DeviceNameUpdatePayload(BaseModel):
    name: str = ""


class MessageTypeUpdatePayload(BaseModel):
    message_type: str = ""


class BaudRateUpdatePayload(BaseModel):
    baud_rate: int


class TractionOutValueUpdatePayload(BaseModel):
    value: int


class TractionOutSendPayload(BaseModel):
    value: int | None = None


class CmdSendPayload(BaseModel):
    command: str = ""


class ColorConfigPayload(BaseModel):
    sensor_name: str | None = None
    sample_period_ms: int | None = None
    led_mode: int | None = None
    gain_mode: int | None = None
    gain: int | None = None
    integration_ms: int | None = None
    classifier: int | None = None
    confidence_milli: int | None = None
    target_clear: int | None = None
    palette_mode: int | None = None
    patch_sample_count: int | None = None


class ColorCalibrationTargetPayload(BaseModel):
    target: str = ""


class ColorProfilePayload(BaseModel):
    profile: dict = Field(default_factory=dict)
    apply_to_firmware: bool = False


class ColorSavePayload(BaseModel):
    persist_cfg: bool = False
    persist_cal: bool = True


def create_webview_app(db_path: str, comms_log_path: str) -> FastAPI:
    app = FastAPI(title="RDK Msg Relay Webview")
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    broker = CommsStreamBroker(comms_log_path=comms_log_path)

    app.state.db_path = db_path
    app.state.comms_log_path = comms_log_path
    app.state.broker = broker

    def _color_devices_list() -> list[dict]:
        devices = _load_devices(db_path)
        out: list[dict] = []
        for device in devices:
            if str(device.get("module_type") or "") != COLOR_MODULE_TYPE:
                continue
            serial_number = str(device.get("serial_number") or "").strip()
            profile = get_device_profile(serial_number) if serial_number else None
            item = dict(device)
            item["color_profile"] = profile
            out.append(item)
        return out

    def _ensure_color_cmd_mode(serial_number: str):
        updated = set_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
            message_type="CMD",
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        time.sleep(0.08)

    def _ensure_color_telemetry_mode(serial_number: str):
        updated = set_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
            message_type="TELEMETRY",
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        time.sleep(0.08)

    def _run_color_cmd(serial_number: str, command: str, timeout_sec: float = 2.0) -> str:
        _ensure_color_cmd_mode(serial_number)
        result = send_device_cmd_once(
            db_path=db_path,
            serial_number=serial_number,
            command=command,
            timeout_sec=timeout_sec,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="device not found")
        if not bool(result.get("ok")):
            detail = str(result.get("error_kind") or "color_cmd_failed")
            if detail == "cmd_send_timeout":
                raise HTTPException(status_code=504, detail=detail)
            raise HTTPException(status_code=409, detail=detail)
        return str(result.get("response") or "").strip()

    def _fetch_color_snapshot(serial_number: str) -> dict:
        info_line = _run_color_cmd(serial_number, "GET INFO")
        cfg_line = _run_color_cmd(serial_number, "GET CFG")
        data_line = _run_color_cmd(serial_number, "GET DATA")

        info = parse_color_info_line(info_line)
        cfg = parse_color_cfg_line(cfg_line)
        data = parse_color_data_line(data_line)
        if not info or not cfg or not data:
            raise HTTPException(status_code=502, detail="invalid_color_snapshot")
        profile = get_device_profile(serial_number)
        return {
            "serial_number": serial_number,
            "info": info,
            "cfg": cfg,
            "data": data,
            "profile": profile,
        }

    def _fetch_color_calibration(serial_number: str, mode: int | str) -> dict:
        mode_key = str(mode)
        if mode_key not in PALETTE_DEFINITIONS:
            raise HTTPException(status_code=400, detail="invalid_palette_mode")
        summary_line = _run_color_cmd(serial_number, f"GET CAL {mode_key}")
        summary = parse_color_cal_line(summary_line)
        if not summary:
            raise HTTPException(status_code=502, detail="invalid_color_calibration")

        patches: list[dict] = []
        for item in PALETTE_DEFINITIONS[mode_key]:
            slot = int(item.get("slot", 0))
            patch_line = _run_color_cmd(serial_number, f"GET CAL PATCH {mode_key} {slot}")
            patch = parse_color_patch_line(patch_line)
            if not patch:
                continue
            patches.append(patch)
        return {"mode": int(mode_key), "summary": summary, "patches": patches}

    def _apply_color_profile_to_firmware(serial_number: str, profile: dict):
        if not isinstance(profile, dict):
            raise HTTPException(status_code=400, detail="invalid_profile")
        modes = profile.get("modes")
        if not isinstance(modes, dict):
            raise HTTPException(status_code=400, detail="invalid_profile")

        for mode_key in ("4", "8", "16"):
            mode_profile = modes.get(mode_key)
            if not isinstance(mode_profile, dict):
                continue
            summary = mode_profile.get("summary")
            if isinstance(summary, dict):
                dark = summary.get("dark") if isinstance(summary.get("dark"), dict) else None
                white = summary.get("white") if isinstance(summary.get("white"), dict) else None
                if summary.get("dark_valid") and dark:
                    _run_color_cmd(
                        serial_number,
                        f"SET CAL DARK {mode_key} {int(dark.get('r', 0))} {int(dark.get('g', 0))} "
                        f"{int(dark.get('b', 0))} {int(dark.get('c', 0))}",
                    )
                if summary.get("white_valid") and white:
                    _run_color_cmd(
                        serial_number,
                        f"SET CAL WHITE {mode_key} {int(white.get('r', 0))} {int(white.get('g', 0))} "
                        f"{int(white.get('b', 0))} {int(white.get('c', 0))}",
                    )

            patches = mode_profile.get("patches")
            if isinstance(patches, list):
                for patch in patches:
                    if not isinstance(patch, dict) or not patch.get("valid"):
                        continue
                    norm_rgb = patch.get("norm_rgb_milli") if isinstance(patch.get("norm_rgb_milli"), dict) else {}
                    lab = patch.get("lab") if isinstance(patch.get("lab"), dict) else {}
                    _run_color_cmd(
                        serial_number,
                        f"SET CAL PROTO {mode_key} {int(patch.get('slot', 0))} "
                        f"{int(norm_rgb.get('r', 0))} {int(norm_rgb.get('g', 0))} {int(norm_rgb.get('b', 0))} "
                        f"{int(patch.get('luma_milli', 0))} {int(lab.get('l_centi', 0))} "
                        f"{int(lab.get('a_centi', 0))} {int(lab.get('b_centi', 0))} "
                        f"{int(patch.get('sample_count', 0))}",
                    )

    @app.on_event("startup")
    async def _on_startup():
        broker.start()
        print(f"[webview] startup db={db_path} comms_log={comms_log_path}", flush=True)

    @app.on_event("shutdown")
    async def _on_shutdown():
        broker.stop()
        print("[webview] shutdown complete", flush=True)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/devices")
    async def devices():
        return {"devices": _load_devices(db_path)}

    @app.post("/api/devices/clear")
    async def clear_devices():
        cleared = clear_devices_registry(db_path=db_path)
        return {
            "ok": True,
            "cleared": int(cleared),
            "devices": _load_devices(db_path),
        }

    @app.get("/api/color/palettes")
    async def get_color_palettes():
        return {"palettes": PALETTE_DEFINITIONS}

    @app.get("/api/color/devices")
    async def get_color_devices():
        return {"devices": _color_devices_list()}

    @app.post("/api/devices/{serial_number}/name")
    async def update_device_name(serial_number: str, payload: DeviceNameUpdatePayload):
        updated = set_device_name(
            db_path=db_path,
            serial_number=serial_number,
            name=payload.name,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")

        for device in _load_devices(db_path):
            if device.get("serial_number") == serial_number:
                return {"device": device}

        raise HTTPException(status_code=500, detail="updated device not found")

    @app.get("/api/config/message-type")
    async def get_message_type_config():
        # Legacy global default for new sessions; per-device mode is authoritative.
        return {
            "active_message_type": get_active_message_type(),
            "supported_message_types": supported_message_types(),
        }

    @app.post("/api/config/message-type")
    async def update_message_type_config(payload: MessageTypeUpdatePayload):
        # Legacy global default for new sessions; existing devices keep their own mode.
        active = set_active_message_type(payload.message_type)
        return {
            "active_message_type": active,
            "supported_message_types": supported_message_types(),
        }

    @app.get("/api/devices/{serial_number}/config/message-type")
    async def get_device_message_type_config(serial_number: str):
        active = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if active is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {
            "serial_number": serial_number,
            "active_message_type": active,
            "supported_message_types": supported_message_types(),
        }

    @app.post("/api/devices/{serial_number}/config/message-type")
    async def update_device_message_type_config(
        serial_number: str,
        payload: MessageTypeUpdatePayload,
    ):
        updated = set_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
            message_type=payload.message_type,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        active = str(updated.get("message_type") or "").strip().upper()
        if not active:
            active = get_device_message_type(db_path=db_path, serial_number=serial_number) or "CMD"

        for device in _load_devices(db_path):
            if device.get("serial_number") == serial_number:
                return {
                    "serial_number": serial_number,
                    "active_message_type": active,
                    "supported_message_types": supported_message_types(),
                    "device": device,
                }

        return {
            "serial_number": serial_number,
            "active_message_type": active,
            "supported_message_types": supported_message_types(),
        }

    @app.get("/api/devices/{serial_number}/config/traction-out")
    async def get_device_traction_out_config(serial_number: str):
        value = get_device_traction_out_value(
            db_path=db_path,
            serial_number=serial_number,
        )
        if value is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {
            "serial_number": serial_number,
            "traction_out_value": int(value),
        }

    @app.post("/api/devices/{serial_number}/config/traction-out")
    async def update_device_traction_out_config(
        serial_number: str,
        payload: TractionOutValueUpdatePayload,
    ):
        updated = set_device_traction_out_value(
            db_path=db_path,
            serial_number=serial_number,
            traction_out_value=payload.value,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {
            "serial_number": serial_number,
            "traction_out_value": int(updated.get("traction_out_value", 0) or 0),
        }

    @app.post("/api/devices/{serial_number}/traction-out/send")
    async def send_device_traction_out(serial_number: str, payload: TractionOutSendPayload):
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != "TRACTION_OUT":
            raise HTTPException(status_code=409, detail="set message type to TRACTION_OUT first")

        result = send_device_traction_out_once(
            db_path=db_path,
            serial_number=serial_number,
            value=payload.value,
            timeout_sec=1.5,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="device not found")
        if not bool(result.get("ok")):
            detail = str(result.get("error_kind") or "traction_out_send_failed")
            if detail == "traction_out_send_timeout":
                raise HTTPException(status_code=504, detail=detail)
            raise HTTPException(status_code=409, detail=detail)
        return result

    @app.post("/api/devices/{serial_number}/cmd/send")
    async def send_device_cmd(serial_number: str, payload: CmdSendPayload):
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != "CMD":
            raise HTTPException(status_code=409, detail="set message type to CMD first")

        command_text = str(payload.command or "").strip()
        if not command_text:
            raise HTTPException(status_code=400, detail="command is required")

        result = send_device_cmd_once(
            db_path=db_path,
            serial_number=serial_number,
            command=command_text,
            timeout_sec=1.5,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="device not found")
        if not bool(result.get("ok")):
            detail = str(result.get("error_kind") or "cmd_send_failed")
            if detail == "cmd_send_timeout":
                raise HTTPException(status_code=504, detail=detail)
            raise HTTPException(status_code=409, detail=detail)
        return result

    @app.get("/api/config/serial")
    async def get_serial_config():
        return {
            "active_baud_rate": get_active_serial_baud(),
            "supported_baud_rates": supported_serial_baud_rates(),
        }

    @app.post("/api/config/serial")
    async def update_serial_config(payload: BaudRateUpdatePayload):
        try:
            active_baud_rate = set_active_serial_baud(payload.baud_rate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "active_baud_rate": active_baud_rate,
            "supported_baud_rates": supported_serial_baud_rates(),
        }

    @app.post("/api/devices/{serial_number}/telemetry/start")
    async def start_telemetry(serial_number: str):
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != "TELEMETRY":
            raise HTTPException(status_code=409, detail="set message type to TELEMETRY for this device")
        updated = set_device_telemetry_requested(
            db_path=db_path,
            serial_number=serial_number,
            enabled=True,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {"ok": True}

    @app.post("/api/devices/{serial_number}/telemetry/stop")
    async def stop_telemetry(serial_number: str):
        message_type = get_device_message_type(
            db_path=db_path,
            serial_number=serial_number,
        )
        if message_type is None:
            raise HTTPException(status_code=404, detail="device not found")
        if message_type != "TELEMETRY":
            raise HTTPException(status_code=409, detail="set message type to TELEMETRY for this device")
        updated = set_device_telemetry_requested(
            db_path=db_path,
            serial_number=serial_number,
            enabled=False,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="device not found")
        return {"ok": True}

    @app.get("/api/devices/{serial_number}/color/profile")
    async def get_color_profile(serial_number: str):
        return {"profile": get_device_profile(serial_number)}

    @app.post("/api/devices/{serial_number}/color/profile")
    async def save_color_profile(serial_number: str, payload: ColorProfilePayload):
        profile = set_device_profile(serial_number, payload.profile)
        if payload.apply_to_firmware:
            _apply_color_profile_to_firmware(serial_number, profile)
            _run_color_cmd(serial_number, "SAVE CAL")
        return {"profile": profile}

    @app.get("/api/devices/{serial_number}/color/snapshot")
    async def get_color_snapshot(serial_number: str):
        return _fetch_color_snapshot(serial_number)

    @app.get("/api/devices/{serial_number}/color/calibration")
    async def get_color_calibration(serial_number: str):
        modes: list[dict] = []
        for mode_key in ("4", "8", "16"):
            modes.append(_fetch_color_calibration(serial_number, mode_key))
        return {
            "serial_number": serial_number,
            "modes": modes,
            "profile": get_device_profile(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/config")
    async def update_color_config(serial_number: str, payload: ColorConfigPayload):
        commands: list[str] = []
        if payload.sensor_name is not None:
            commands.append(f"SET CFG NAME {payload.sensor_name.strip()}")
        if payload.sample_period_ms is not None:
            commands.append(f"SET CFG SAMPLE_MS {int(payload.sample_period_ms)}")
        if payload.led_mode is not None:
            commands.append(f"SET CFG LED {int(payload.led_mode)}")
        if payload.gain_mode is not None:
            commands.append(f"SET CFG GAIN_MODE {int(payload.gain_mode)}")
        if payload.gain is not None:
            commands.append(f"SET CFG GAIN {int(payload.gain)}")
        if payload.integration_ms is not None:
            commands.append(f"SET CFG INTEGRATION_MS {int(payload.integration_ms)}")
        if payload.classifier is not None:
            commands.append(f"SET CFG CLASSIFIER {int(payload.classifier)}")
        if payload.confidence_milli is not None:
            commands.append(f"SET CFG CONF_TH {float(int(payload.confidence_milli)) / 1000.0:.3f}")
        if payload.target_clear is not None:
            commands.append(f"SET CFG TARGET_CLEAR {int(payload.target_clear)}")
        if payload.palette_mode is not None:
            commands.append(f"SET CFG PALETTE_MODE {int(payload.palette_mode)}")
        if payload.patch_sample_count is not None:
            commands.append(f"SET CFG PATCH_SAMPLES {int(payload.patch_sample_count)}")

        if not commands:
            raise HTTPException(status_code=400, detail="no_config_changes")
        for command in commands:
            _run_color_cmd(serial_number, command)
        return _fetch_color_snapshot(serial_number)

    @app.post("/api/devices/{serial_number}/color/calibration/start")
    async def start_color_calibration(serial_number: str):
        _run_color_cmd(serial_number, "START CAL")
        return _fetch_color_snapshot(serial_number)

    @app.post("/api/devices/{serial_number}/color/calibration/stop")
    async def stop_color_calibration(serial_number: str):
        _run_color_cmd(serial_number, "STOP CAL")
        return _fetch_color_snapshot(serial_number)

    @app.post("/api/devices/{serial_number}/color/calibration/select")
    async def select_color_calibration_target(
        serial_number: str,
        payload: ColorCalibrationTargetPayload,
    ):
        target = str(payload.target or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="target_required")
        _run_color_cmd(serial_number, f"SET CAL PATCH {target}")
        return _fetch_color_snapshot(serial_number)

    @app.post("/api/devices/{serial_number}/color/calibration/commit")
    async def commit_color_calibration_target(
        serial_number: str,
        payload: ColorCalibrationTargetPayload,
    ):
        target = str(payload.target or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="target_required")
        _run_color_cmd(serial_number, f"COMMIT CAL PATCH {target}")
        snapshot = _fetch_color_snapshot(serial_number)
        calibration = _fetch_color_calibration(serial_number, snapshot["cfg"]["palette_mode"])
        update_device_mode_profile(
            serial_number,
            snapshot["cfg"]["palette_mode"],
            summary=calibration["summary"],
            patches=calibration["patches"],
            last_calibrated_at=now_iso(),
        )
        return {
            "snapshot": snapshot,
            "calibration": calibration,
            "profile": get_device_profile(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/save")
    async def save_color_state(serial_number: str, payload: ColorSavePayload):
        if payload.persist_cfg:
            _run_color_cmd(serial_number, "SAVE CFG")
        if payload.persist_cal:
            _run_color_cmd(serial_number, "SAVE CAL")
        return {"ok": True}

    @app.post("/api/devices/{serial_number}/color/selftest")
    async def run_color_selftest(serial_number: str):
        line = _run_color_cmd(serial_number, "RUN SELFTEST")
        parsed = parse_color_selftest_line(line)
        if not parsed:
            raise HTTPException(status_code=502, detail="invalid_selftest_response")
        return {"result": parsed, "snapshot": _fetch_color_snapshot(serial_number)}

    @app.post("/api/devices/{serial_number}/color/restore-defaults")
    async def restore_color_defaults(serial_number: str):
        _run_color_cmd(serial_number, "RESET CFG")
        _run_color_cmd(serial_number, "RESET CAL ALL")
        _run_color_cmd(serial_number, "SAVE CFG")
        _run_color_cmd(serial_number, "SAVE CAL")
        profile = set_device_profile(serial_number, default_device_profile(serial_number))
        return {"snapshot": _fetch_color_snapshot(serial_number), "profile": profile}

    @app.get("/api/comms")
    async def comms(
        limit: int = Query(default=300, ge=1, le=5000),
        serial: str | None = Query(default=None),
    ):
        return {"events": broker.history(limit=limit, serial_filter=serial)}

    @app.websocket("/ws/comms")
    async def ws_comms(websocket: WebSocket):
        await websocket.accept()
        subscriber_id, subscriber_queue = broker.register()
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "devices": _load_devices(db_path),
                    "events": broker.history(limit=500, serial_filter=None),
                    "active_message_type": get_active_message_type(),
                    "supported_message_types": supported_message_types(),
                    "active_baud_rate": get_active_serial_baud(),
                    "supported_baud_rates": supported_serial_baud_rates(),
                }
            )
            while True:
                try:
                    event = await asyncio.to_thread(subscriber_queue.get, True, 1.0)
                except queue.Empty:
                    continue
                await websocket.send_json({"type": "comms", "event": event})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            broker.unregister(subscriber_id)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(static_dir, "index.html"))

    @app.get("/color", include_in_schema=False)
    async def color_page():
        return FileResponse(os.path.join(static_dir, "color.html"))

    return app


def start_webview_server(
    db_path: str,
    comms_log_path: str,
    host: str,
    port: int,
):
    app = create_webview_app(db_path=db_path, comms_log_path=comms_log_path)
    config = uvicorn.Config(
        app=app,
        host=host,
        port=int(port),
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
