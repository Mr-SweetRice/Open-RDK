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
from pydantic import BaseModel

from .color_support import (
    PALETTE_DEFINITIONS,
    COLOR_MODULE_TYPE,
    default_device_profile,
    get_device_profile,
    merge_device_profile,
    parse_color_cal_line,
    parse_color_cfg_line,
    parse_color_data_line,
    parse_color_info_line,
    parse_color_patch_line,
    parse_color_selftest_line,
    set_device_profile,
    update_device_mode_profile,
)
from .constants import BAUD_RATE_MAX, BAUD_RATE_MIN, MESSAGE_TYPE_CMD, MESSAGE_TYPE_CONTROL
from .functions import (
    clear_devices_registry,
    get_active_message_type,
    get_device_message_type,
    get_device_traction_out_value,
    get_active_serial_baud,
    get_latest_ls_frame,
    resume_keepalive_monitors,
    send_device_cmd_once,
    set_active_message_type,
    set_device_message_type,
    send_device_traction_out_once,
    set_device_traction_out_value,
    set_active_serial_baud,
    set_device_name,
    set_device_telemetry_requested,
    stop_all_keepalive_monitors,
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
    phase = payload.get("phase")
    if isinstance(phase, str) and phase.strip():
        event["phase"] = phase.strip().lower()
    return event


def _normalize_baud_rate_for_api(value: int | str | None) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid baud rate")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid baud rate")
    if parsed < BAUD_RATE_MIN or parsed > BAUD_RATE_MAX:
        raise ValueError(f"baud rate must be between {BAUD_RATE_MIN} and {BAUD_RATE_MAX}")
    return parsed


def _online_traction_devices(db_path: str) -> list[dict]:
    devices = []
    for item in _load_devices(db_path):
        if str(item.get("status") or "").lower() != "online connected":
            continue
        if str(item.get("module_type") or "").lower() != "traction_module":
            continue
        if not item.get("serial_number"):
            continue
        devices.append(item)
    return devices


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


class LineSensorConfigPayload(BaseModel):
    track_type: int | None = None
    digital_threshold: float | None = None
    detect_threshold: float | None = None
    calibration_time_ms: int | None = None
    save: bool = True


class LineSensorCalibrationStartPayload(BaseModel):
    track_type: int | None = None
    digital_threshold: float | None = None
    detect_threshold: float | None = None
    calibration_time_ms: int | None = None
    save_config: bool = True


class LineSensorCalibrationPayload(BaseModel):
    min_raw: list[int]
    max_raw: list[int]
    save: bool = True


class ColorConfigPayload(BaseModel):
    sensor_name: str | None = None
    palette_mode: int | None = None
    sample_period_ms: int | None = None
    led_mode: int | None = None
    gain_mode: int | None = None
    gain: int | None = None
    integration_ms: int | None = None
    classifier: int | None = None
    confidence_milli: int | None = None
    target_clear: int | None = None
    patch_sample_count: int | None = None
    save: bool = False


class ColorSavePayload(BaseModel):
    persist_cfg: bool = False
    persist_cal: bool = False


class ColorProfilePayload(BaseModel):
    profile: dict
    apply_to_firmware: bool = False


class ColorCalibrationTargetPayload(BaseModel):
    target: str | int


def create_webview_app(
    db_path: str,
    comms_log_path: str,
    enable_realtime_stream: bool = True,
) -> FastAPI:
    app = FastAPI(title="RDK Msg Relay Webview")
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_new")
    broker = CommsStreamBroker(comms_log_path=comms_log_path) if enable_realtime_stream else None
    color_command_locks: dict[str, asyncio.Lock] = {}

    app.state.db_path = db_path
    app.state.comms_log_path = comms_log_path
    app.state.broker = broker
    app.state.enable_realtime_stream = bool(enable_realtime_stream)

    def _color_device_or_error(serial_number: str) -> dict:
        device = next(
            (
                item for item in _load_devices(db_path)
                if item.get("serial_number") == serial_number
            ),
            None,
        )
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        if str(device.get("module_type") or "").lower() != COLOR_MODULE_TYPE:
            raise HTTPException(status_code=400, detail="device is not a color module")
        if str(device.get("status") or "").lower() != "online connected":
            raise HTTPException(status_code=409, detail="device is not online")
        return device

    def _color_command_lock(serial_number: str) -> asyncio.Lock:
        lock = color_command_locks.get(serial_number)
        if lock is None:
            lock = asyncio.Lock()
            color_command_locks[serial_number] = lock
        return lock

    async def _send_color_cmds(
        serial_number: str,
        commands: list[str],
        timeout_sec: float = 2.0,
    ) -> list[dict]:
        if not commands:
            return []

        async with _color_command_lock(serial_number):
            _color_device_or_error(serial_number)
            previous_type = get_device_message_type(db_path=db_path, serial_number=serial_number) or "CMD"
            if previous_type != "CMD":
                if previous_type == "TELEMETRY":
                    await asyncio.to_thread(
                        set_device_telemetry_requested,
                        db_path=db_path,
                        serial_number=serial_number,
                        enabled=False,
                    )
                    await asyncio.sleep(0.25)
                await asyncio.to_thread(
                    set_device_message_type,
                    db_path=db_path,
                    serial_number=serial_number,
                    message_type="CMD",
                )
                await asyncio.sleep(0.08)

            results: list[dict] = []
            try:
                for command in commands:
                    result = await asyncio.to_thread(
                        send_device_cmd_once,
                        db_path=db_path,
                        serial_number=serial_number,
                        command=command,
                        timeout_sec=timeout_sec,
                    )
                    if result is None:
                        raise HTTPException(status_code=404, detail="device not found")
                    results.append(result)
                    if not bool(result.get("ok")):
                        detail = str(result.get("error_kind") or result.get("response") or "command failed")
                        status_code = 504 if detail == "cmd_send_timeout" else 409
                        raise HTTPException(status_code=status_code, detail=f"{command}: {detail}")
            finally:
                if previous_type != "CMD":
                    await asyncio.to_thread(
                        set_device_message_type,
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type=previous_type,
                    )
                    if previous_type == "TELEMETRY":
                        await asyncio.to_thread(
                            set_device_telemetry_requested,
                            db_path=db_path,
                            serial_number=serial_number,
                            enabled=True,
                        )

            return results

    def _response_for(results: list[dict], prefix: str) -> str | None:
        prefix_text = str(prefix or "")
        for result in reversed(results):
            response = str(result.get("response") or "").strip()
            if response.startswith(prefix_text):
                return response
        return None

    async def _color_snapshot(serial_number: str) -> dict:
        results = await _send_color_cmds(
            serial_number,
            ["GET INFO", "GET CFG", "GET CAL", "GET DATA"],
            timeout_sec=2.0,
        )
        info = parse_color_info_line(_response_for(results, "INFO,"))
        cfg = parse_color_cfg_line(_response_for(results, "CFG,"))
        cal = parse_color_cal_line(_response_for(results, "CAL,"))
        data = parse_color_data_line(_response_for(results, "DATA,"))
        if info is None or cfg is None or cal is None or data is None:
            raise HTTPException(status_code=502, detail="invalid color module response")
        return {
            "serial": serial_number,
            "info": info,
            "cfg": cfg,
            "cal": cal,
            "data": data,
            "profile": get_device_profile(serial_number),
            "results": results,
        }

    async def _color_calibration(serial_number: str) -> dict:
        cfg_result = await _send_color_cmds(serial_number, ["GET CFG"], timeout_sec=2.0)
        cfg = parse_color_cfg_line(_response_for(cfg_result, "CFG,"))
        mode = int(cfg.get("palette_mode") if cfg else 8)
        labels = PALETTE_DEFINITIONS.get(str(mode), [])
        commands = [f"GET CAL {mode}"]
        commands.extend(f"GET CAL PATCH {mode} {int(item['slot'])}" for item in labels)
        results = await _send_color_cmds(serial_number, commands, timeout_sec=2.0)
        cal = parse_color_cal_line(_response_for(results, "CAL,"))
        patches = [
            parsed for parsed in (
                parse_color_patch_line(str(item.get("response") or ""))
                for item in results
            )
            if parsed is not None
        ]
        profile = update_device_mode_profile(
            serial_number,
            mode,
            summary=cal,
            patches=patches,
        )
        return {
            "serial": serial_number,
            "cfg": cfg,
            "cal": cal,
            "patches": patches,
            "profile": profile,
            "results": results,
        }

    def _target_token(target: str | int) -> str:
        text = str(target).strip().upper()
        if text in {"DARK", "BLACK"}:
            return "DARK"
        if text in {"WHITE"}:
            return "WHITE"
        try:
            slot = int(text)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid calibration target")
        if slot < 0 or slot > 15:
            raise HTTPException(status_code=400, detail="invalid calibration target")
        return str(slot)

    def _commands_from_color_config(payload: ColorConfigPayload) -> list[str]:
        commands: list[str] = []
        if payload.sensor_name is not None:
            name = str(payload.sensor_name).strip()
            if name:
                commands.append(f"SET CFG NAME {name[:31]}")
        if payload.sample_period_ms is not None:
            commands.append(f"SET CFG SAMPLE_MS {max(20, int(payload.sample_period_ms))}")
        if payload.led_mode is not None:
            commands.append(f"SET CFG LED {int(payload.led_mode)}")
        if payload.gain_mode is not None:
            commands.append(f"SET CFG GAIN_MODE {int(payload.gain_mode)}")
        if payload.gain is not None:
            commands.append(f"SET CFG GAIN {max(1, int(payload.gain))}")
        if payload.integration_ms is not None:
            commands.append(f"SET CFG INTEGRATION_MS {max(2, int(payload.integration_ms))}")
        if payload.classifier is not None:
            commands.append(f"SET CFG CLASSIFIER {int(payload.classifier)}")
        if payload.confidence_milli is not None:
            threshold = max(0.0, min(1.0, float(payload.confidence_milli) / 1000.0))
            commands.append(f"SET CFG CONF_TH {threshold:.4f}")
        if payload.target_clear is not None:
            commands.append(f"SET CFG TARGET_CLEAR {max(1, int(payload.target_clear))}")
        if payload.palette_mode is not None:
            mode = int(payload.palette_mode)
            if str(mode) not in PALETTE_DEFINITIONS:
                raise HTTPException(status_code=400, detail="palette_mode must be 5, 8, or 16")
            commands.append(f"SET CFG PALETTE_MODE {mode}")
        if payload.patch_sample_count is not None:
            commands.append(f"SET CFG PATCH_SAMPLES {max(1, int(payload.patch_sample_count))}")
        if payload.save:
            commands.append("SAVE CFG")
        return commands

    async def _send_line_sensor_cmds(
        serial_number: str,
        commands: list[str],
        timeout_sec: float = 2.0,
    ) -> list[dict]:
        if not commands:
            return []

        device_before = next(
            (
                item for item in _load_devices(db_path)
                if item.get("serial_number") == serial_number
            ),
            None,
        )
        if device_before is None:
            raise HTTPException(status_code=404, detail="device not found")
        if str(device_before.get("module_type") or "").lower() != "line_sensor_module":
            raise HTTPException(status_code=400, detail="device is not a line sensor module")
        if str(device_before.get("status") or "").lower() != "online connected":
            raise HTTPException(status_code=409, detail="device is not online")

        previous_type = get_device_message_type(db_path=db_path, serial_number=serial_number) or "CMD"
        if previous_type != "CMD":
            if previous_type == "TELEMETRY":
                await asyncio.to_thread(
                    set_device_telemetry_requested,
                    db_path=db_path,
                    serial_number=serial_number,
                    enabled=False,
                )
                await asyncio.sleep(0.25)
            await asyncio.to_thread(
                set_device_message_type,
                db_path=db_path,
                serial_number=serial_number,
                message_type="CMD",
            )
            await asyncio.sleep(0.08)

        results: list[dict] = []
        try:
            for command in commands:
                result = await asyncio.to_thread(
                    send_device_cmd_once,
                    db_path=db_path,
                    serial_number=serial_number,
                    command=command,
                    timeout_sec=timeout_sec,
                )
                if result is None:
                    raise HTTPException(status_code=404, detail="device not found")
                results.append(result)
                if not bool(result.get("ok")):
                    detail = str(result.get("error_kind") or result.get("response") or "command failed")
                    status_code = 504 if detail == "cmd_send_timeout" else 409
                    raise HTTPException(status_code=status_code, detail=f"{command}: {detail}")
        finally:
            if previous_type != "CMD":
                await asyncio.to_thread(
                    set_device_message_type,
                    db_path=db_path,
                    serial_number=serial_number,
                    message_type=previous_type,
                )
                if previous_type == "TELEMETRY":
                    await asyncio.to_thread(
                        set_device_telemetry_requested,
                        db_path=db_path,
                        serial_number=serial_number,
                        enabled=True,
                    )

        return results

    @app.on_event("startup")
    async def _on_startup():
        if broker is not None:
            broker.start()
        print(
            f"[webview] startup db={db_path} comms_log={comms_log_path} "
            f"realtime_stream={'on' if enable_realtime_stream else 'off'}",
            flush=True,
        )

    @app.on_event("shutdown")
    async def _on_shutdown():
        if broker is not None:
            broker.stop()
        print("[webview] shutdown complete", flush=True)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/devices")
    async def devices():
        return {"devices": _load_devices(db_path)}

    @app.get("/api/color/palettes")
    async def color_palettes():
        return {"palettes": PALETTE_DEFINITIONS}

    @app.get("/api/color/devices")
    async def color_devices():
        devices = []
        for device in _load_devices(db_path):
            if str(device.get("module_type") or "").lower() != COLOR_MODULE_TYPE:
                continue
            serial = str(device.get("serial_number") or "").strip()
            item = dict(device)
            item["color_profile"] = get_device_profile(serial)
            devices.append(item)
        return {"devices": devices, "palettes": PALETTE_DEFINITIONS}

    @app.post("/api/devices/clear")
    async def clear_devices():
        cleared = clear_devices_registry(db_path=db_path)
        return {
            "ok": True,
            "cleared": int(cleared),
            "devices": _load_devices(db_path),
        }

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

    @app.get("/api/devices/{serial_number}/color/profile")
    async def color_profile(serial_number: str):
        _color_device_or_error(serial_number)
        return {
            "serial": serial_number,
            "profile": get_device_profile(serial_number),
            "palettes": PALETTE_DEFINITIONS,
        }

    @app.post("/api/devices/{serial_number}/color/profile")
    async def update_color_profile(serial_number: str, payload: ColorProfilePayload):
        _color_device_or_error(serial_number)
        profile = set_device_profile(serial_number, merge_device_profile(serial_number, payload.profile))

        firmware_results: list[dict] = []
        if payload.apply_to_firmware:
            commands: list[str] = []
            for mode_key, mode_profile in dict(profile.get("modes") or {}).items():
                patches = mode_profile.get("patches") if isinstance(mode_profile, dict) else None
                if not isinstance(patches, list):
                    continue
                try:
                    mode = int(mode_key)
                except (TypeError, ValueError):
                    continue
                if str(mode) not in PALETTE_DEFINITIONS:
                    continue
                for patch in patches:
                    if not isinstance(patch, dict) or not bool(patch.get("valid", True)):
                        continue
                    try:
                        slot = int(patch.get("slot"))
                        norm = patch.get("norm_rgb_milli") or {}
                        lab = patch.get("lab") or {}
                        nr = int(norm.get("r"))
                        ng = int(norm.get("g"))
                        nb = int(norm.get("b"))
                        luma = int(patch.get("luma_milli", 0) or 0)
                        ll = int(lab.get("l_centi"))
                        la = int(lab.get("a_centi"))
                        lb = int(lab.get("b_centi"))
                        samples = int(patch.get("sample_count", 1) or 1)
                    except (TypeError, ValueError):
                        continue
                    commands.append(
                        f"SET CAL PROTO {mode} {slot} {nr} {ng} {nb} {luma} {ll} {la} {lb} {samples}"
                    )
            if commands:
                commands.append("SAVE CAL")
                firmware_results = await _send_color_cmds(serial_number, commands, timeout_sec=2.0)

        return {
            "serial": serial_number,
            "profile": profile,
            "firmware_results": firmware_results,
        }

    @app.get("/api/devices/{serial_number}/color/snapshot")
    async def color_snapshot(serial_number: str):
        return await _color_snapshot(serial_number)

    @app.get("/api/devices/{serial_number}/color/calibration")
    async def color_calibration(serial_number: str):
        return await _color_calibration(serial_number)

    @app.post("/api/devices/{serial_number}/color/config")
    async def color_config(serial_number: str, payload: ColorConfigPayload):
        commands = _commands_from_color_config(payload)
        if commands:
            commands.append("GET CFG")
            await _send_color_cmds(serial_number, commands, timeout_sec=2.0)
        return await _color_snapshot(serial_number)

    @app.post("/api/devices/{serial_number}/color/save")
    async def color_save(serial_number: str, payload: ColorSavePayload):
        commands: list[str] = []
        if payload.persist_cfg:
            commands.append("SAVE CFG")
        if payload.persist_cal:
            commands.append("SAVE CAL")
        results = await _send_color_cmds(serial_number, commands, timeout_sec=2.0) if commands else []
        return {
            "ok": True,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/selftest")
    async def color_selftest(serial_number: str):
        results = await _send_color_cmds(serial_number, ["RUN SELFTEST"], timeout_sec=2.0)
        result = parse_color_selftest_line(_response_for(results, "SELFTEST,"))
        return {
            "ok": bool(result.get("ok")) if result else False,
            "result": result,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/restore-defaults")
    async def color_restore_defaults(serial_number: str):
        results = await _send_color_cmds(
            serial_number,
            ["RESET CFG", "RESET CAL ALL", "SAVE CFG", "SAVE CAL"],
            timeout_sec=2.0,
        )
        profile = set_device_profile(serial_number, default_device_profile(serial_number))
        return {
            "ok": True,
            "results": results,
            "profile": profile,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/start")
    async def color_calibration_start(serial_number: str):
        results = await _send_color_cmds(serial_number, ["START CAL", "GET DATA"], timeout_sec=2.0)
        return {
            "ok": True,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/stop")
    async def color_calibration_stop(serial_number: str):
        results = await _send_color_cmds(serial_number, ["STOP CAL", "GET CAL"], timeout_sec=2.0)
        return {
            "ok": True,
            "results": results,
            "calibration": await _color_calibration(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/select")
    async def color_calibration_select(serial_number: str, payload: ColorCalibrationTargetPayload):
        target = _target_token(payload.target)
        results = await _send_color_cmds(serial_number, [f"SET CAL PATCH {target}", "GET DATA"], timeout_sec=2.0)
        return {
            "ok": True,
            "target": target,
            "results": results,
            "snapshot": await _color_snapshot(serial_number),
        }

    @app.post("/api/devices/{serial_number}/color/calibration/commit")
    async def color_calibration_commit(serial_number: str, payload: ColorCalibrationTargetPayload):
        target = _target_token(payload.target)
        results = await _send_color_cmds(serial_number, [f"COMMIT CAL PATCH {target}"], timeout_sec=2.0)

        snapshot = await _color_snapshot(serial_number)
        profile = snapshot.get("profile") or get_device_profile(serial_number)
        try:
            slot = int(target)
        except (TypeError, ValueError):
            slot = None
        if slot is not None:
            mode = int(snapshot.get("cfg", {}).get("palette_mode", 8))
            patch_results = await _send_color_cmds(serial_number, [f"GET CAL PATCH {mode} {slot}"], timeout_sec=2.0)
            patch = parse_color_patch_line(_response_for(patch_results, "PATCH,"))
            if patch is not None:
                mode_profile = profile["modes"].setdefault(str(mode), {
                    "mode": str(mode),
                    "labels": PALETTE_DEFINITIONS.get(str(mode), []),
                    "summary": None,
                    "patches": [],
                    "last_calibrated_at": None,
                    "updated_at": None,
                })
                patches = [
                    item for item in mode_profile.get("patches", [])
                    if int(item.get("slot", -999)) != slot
                ]
                patches.append(patch)
                patches.sort(key=lambda item: int(item.get("slot", 0)))
                profile = update_device_mode_profile(
                    serial_number,
                    mode,
                    patches=patches,
                    last_calibrated_at=__import__("datetime").datetime.now().isoformat(),
                )
                snapshot["profile"] = profile

        return {
            "ok": True,
            "target": target,
            "results": results,
            "snapshot": snapshot,
            "profile": profile,
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
        if message_type != MESSAGE_TYPE_CONTROL:
            raise HTTPException(status_code=409, detail="set message type to CONTROL first")

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

    @app.post("/api/serial-monitors/pause")
    async def pause_serial_monitors():
        stop_all_keepalive_monitors()
        return {"ok": True}

    @app.post("/api/serial-monitors/resume")
    async def resume_serial_monitors():
        resume_keepalive_monitors(db_path)
        return {"ok": True}

    @app.post("/api/config/serial")
    async def update_serial_config(payload: BaudRateUpdatePayload):
        try:
            requested_baud_rate = _normalize_baud_rate_for_api(payload.baud_rate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        current_baud_rate = get_active_serial_baud()
        device_updates: list[dict] = []
        if requested_baud_rate != current_baud_rate:
            for device in _online_traction_devices(db_path):
                serial_number = str(device.get("serial_number") or "").strip()
                if not serial_number:
                    continue
                previous_type = get_device_message_type(
                    db_path=db_path,
                    serial_number=serial_number,
                )
                if previous_type != MESSAGE_TYPE_CMD:
                    set_device_message_type(
                        db_path=db_path,
                        serial_number=serial_number,
                        message_type=MESSAGE_TYPE_CMD,
                    )
                    time.sleep(0.05)

                result = send_device_cmd_once(
                    db_path=db_path,
                    serial_number=serial_number,
                    command=f"SET BAUD {requested_baud_rate}",
                    timeout_sec=2.0,
                )
                if result is None:
                    raise HTTPException(status_code=404, detail=f"device not found: {serial_number}")
                if not bool(result.get("ok")):
                    raise HTTPException(
                        status_code=409,
                        detail=f"device baud update failed for {serial_number}: {result.get('error_kind') or result.get('response')}",
                    )
                response_text = str(result.get("response") or "").strip()
                if response_text != f"B,{requested_baud_rate}":
                    raise HTTPException(
                        status_code=409,
                        detail=f"unexpected baud response for {serial_number}: {response_text}",
                    )
                device_updates.append({
                    "serial_number": serial_number,
                    "response": response_text,
                    "latency_ms": result.get("latency_ms"),
                })
            time.sleep(0.08)

        active_baud_rate = set_active_serial_baud(requested_baud_rate)
        if requested_baud_rate != current_baud_rate:
            stop_all_keepalive_monitors()
            resume_keepalive_monitors(db_path)
        return {
            "active_baud_rate": active_baud_rate,
            "supported_baud_rates": supported_serial_baud_rates(),
            "device_updates": device_updates,
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

    @app.get("/api/comms")
    async def comms(
        limit: int = Query(default=300, ge=1, le=5000),
        serial: str | None = Query(default=None),
    ):
        if broker is None:
            return {"events": [], "disabled": True}
        return {"events": broker.history(limit=limit, serial_filter=serial)}

    @app.websocket("/ws/comms")
    async def ws_comms(websocket: WebSocket):
        await websocket.accept()
        if broker is None:
            await websocket.send_json(
                {
                    "type": "disabled",
                    "reason": "webview_realtime_stream_disabled",
                }
            )
            await websocket.close()
            return
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

    @app.get("/api/devices/{serial_number}/line-sensor/snapshot")
    async def line_sensor_snapshot(serial_number: str):
        """
        Return the most recent LS / CFG / CAL / INFO messages for this device.
        LS comes from the streaming cache (O(1), lock-free read, never blocks).
        CFG/CAL/INFO come from the broker history via asyncio.to_thread so the
        event loop is never blocked by threading.Lock acquisition.
        """
        result: dict = {}

        # LS: in-process streaming cache — always current, zero contention
        cached = get_latest_ls_frame(serial_number)
        if cached is not None:
            result["ls"] = cached[1]

        # CFG/CAL/INFO: broker history scan — run in thread pool so the asyncio
        # event loop is never blocked by the broker's threading.Lock.
        if broker is not None:
            def _scan_broker():
                out: dict = {}
                for event in reversed(broker.history(limit=200, serial_filter=serial_number)):
                    if str(event.get("direction") or "") != "rx":
                        continue
                    msg = str(event.get("message") or "").strip()
                    if msg.startswith("CFG,") and "cfg" not in out:
                        out["cfg"] = msg
                    elif msg.startswith("CAL,") and "cal" not in out:
                        out["cal"] = msg
                    elif msg.startswith("INFO,") and "info" not in out:
                        out["info"] = msg
                    if len(out) == 3:
                        break
                return out

            cfg_data = await asyncio.to_thread(_scan_broker)
            result.update(cfg_data)

        return {"serial": serial_number, **result}

    @app.post("/api/devices/{serial_number}/line-sensor/stream/start")
    async def line_sensor_stream_start(serial_number: str):
        """Switch device to TELEMETRY mode and enable streaming. Idempotent."""
        await asyncio.to_thread(
            set_device_message_type,
            db_path=db_path, serial_number=serial_number, message_type="TELEMETRY",
        )
        await asyncio.to_thread(
            set_device_telemetry_requested,
            db_path=db_path, serial_number=serial_number, enabled=True,
        )
        return {"ok": True}

    @app.post("/api/devices/{serial_number}/line-sensor/stream/stop")
    async def line_sensor_stream_stop(serial_number: str):
        """Disable streaming (leaves device in TELEMETRY mode, just stops the push)."""
        await asyncio.to_thread(
            set_device_telemetry_requested,
            db_path=db_path, serial_number=serial_number, enabled=False,
        )
        return {"ok": True}

    @app.post("/api/devices/{serial_number}/line-sensor/refresh")
    async def line_sensor_refresh(serial_number: str):
        """
        Queue lightweight read commands (GET INFO / CFG / CAL) so the responses
        appear in the comms stream and update the monitor page.
        """
        results = await _send_line_sensor_cmds(
            serial_number,
            ["GET INFO", "GET CFG", "GET CAL"],
            timeout_sec=2.0,
        )
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/config")
    async def line_sensor_config(serial_number: str, payload: LineSensorConfigPayload):
        commands: list[str] = []
        if payload.track_type is not None:
            track_type = int(payload.track_type)
            if track_type not in (0, 1):
                raise HTTPException(status_code=400, detail="track_type must be 0 or 1")
            commands.append(f"SET CFG TRACK {track_type}")
        if payload.digital_threshold is not None:
            digital_threshold = float(payload.digital_threshold)
            if digital_threshold < 0.05 or digital_threshold > 0.95:
                raise HTTPException(status_code=400, detail="digital_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DIGITAL_TH {digital_threshold:.4f}")
        if payload.detect_threshold is not None:
            detect_threshold = float(payload.detect_threshold)
            if detect_threshold < 0.05 or detect_threshold > 0.95:
                raise HTTPException(status_code=400, detail="detect_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DETECT_TH {detect_threshold:.4f}")
        if payload.calibration_time_ms is not None:
            calibration_time_ms = int(payload.calibration_time_ms)
            if calibration_time_ms < 100:
                raise HTTPException(status_code=400, detail="calibration_time_ms must be at least 100")
            commands.append(f"SET CFG CAL_TIME_MS {calibration_time_ms}")
        if payload.save:
            commands.append("SAVE CFG")
        commands.append("GET CFG")
        results = await _send_line_sensor_cmds(serial_number, commands, timeout_sec=2.0)
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/calibration/start")
    async def line_sensor_calibration_start(
        serial_number: str,
        payload: LineSensorCalibrationStartPayload,
    ):
        config_payload = LineSensorConfigPayload(
            track_type=payload.track_type,
            digital_threshold=payload.digital_threshold,
            detect_threshold=payload.detect_threshold,
            calibration_time_ms=payload.calibration_time_ms,
            save=payload.save_config,
        )
        commands: list[str] = []
        if config_payload.track_type is not None:
            track_type = int(config_payload.track_type)
            if track_type not in (0, 1):
                raise HTTPException(status_code=400, detail="track_type must be 0 or 1")
            commands.append(f"SET CFG TRACK {track_type}")
        if config_payload.digital_threshold is not None:
            digital_threshold = float(config_payload.digital_threshold)
            if digital_threshold < 0.05 or digital_threshold > 0.95:
                raise HTTPException(status_code=400, detail="digital_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DIGITAL_TH {digital_threshold:.4f}")
        if config_payload.detect_threshold is not None:
            detect_threshold = float(config_payload.detect_threshold)
            if detect_threshold < 0.05 or detect_threshold > 0.95:
                raise HTTPException(status_code=400, detail="detect_threshold must be between 0.05 and 0.95")
            commands.append(f"SET CFG DETECT_TH {detect_threshold:.4f}")
        if config_payload.calibration_time_ms is not None:
            calibration_time_ms = int(config_payload.calibration_time_ms)
            if calibration_time_ms < 100:
                raise HTTPException(status_code=400, detail="calibration_time_ms must be at least 100")
            commands.append(f"SET CFG CAL_TIME_MS {calibration_time_ms}")
        if commands and config_payload.save:
            commands.append("SAVE CFG")
        commands.extend(["START CAL", "GET CFG"])
        results = await _send_line_sensor_cmds(serial_number, commands, timeout_sec=2.0)
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/calibration/stop")
    async def line_sensor_calibration_stop(serial_number: str):
        results = await _send_line_sensor_cmds(
            serial_number,
            ["STOP CAL", "GET CAL"],
            timeout_sec=2.0,
        )
        return {"ok": True, "results": results}

    @app.post("/api/devices/{serial_number}/line-sensor/calibration")
    async def line_sensor_calibration(serial_number: str, payload: LineSensorCalibrationPayload):
        if len(payload.min_raw) != 5 or len(payload.max_raw) != 5:
            raise HTTPException(status_code=400, detail="min_raw and max_raw must have 5 values")
        commands: list[str] = []
        for idx, (min_value_raw, max_value_raw) in enumerate(zip(payload.min_raw, payload.max_raw)):
            min_value = int(min_value_raw)
            max_value = int(max_value_raw)
            if min_value < 0 or min_value > 4095 or max_value < 0 or max_value > 4095:
                raise HTTPException(status_code=400, detail="calibration values must be between 0 and 4095")
            if max_value <= min_value:
                raise HTTPException(status_code=400, detail="each max_raw value must be greater than min_raw")
            commands.append(f"SET CAL {idx} {min_value} {max_value}")
        if payload.save:
            commands.append("SAVE CAL")
        commands.append("GET CAL")
        results = await _send_line_sensor_cmds(serial_number, commands, timeout_sec=2.0)
        return {"ok": True, "results": results}

    app.mount("/static", StaticFiles(directory=os.path.join(static_dir, "static")), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(static_dir, "index.html"))

    @app.get("/line-sensor", include_in_schema=False)
    async def line_sensor_page():
        return FileResponse(
            os.path.join(static_dir, "line-sensor.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/traction-motor-config", include_in_schema=False)
    async def traction_motor_config_page():
        return FileResponse(
            os.path.join(static_dir, "traction-motor-config.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/traction-pid-tuner", include_in_schema=False)
    async def traction_pid_tuner_page():
        return FileResponse(
            os.path.join(static_dir, "traction-pid-tuner.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/traction-position-tuner", include_in_schema=False)
    async def traction_position_tuner_page():
        return FileResponse(
            os.path.join(static_dir, "traction-position-tuner.html"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/color", include_in_schema=False)
    async def color_page():
        return FileResponse(
            os.path.join(static_dir, "color.html"),
            headers={"Cache-Control": "no-store"},
        )

    return app


def start_webview_server(
    db_path: str,
    comms_log_path: str,
    host: str,
    port: int,
    enable_realtime_stream: bool = True,
):
    app = create_webview_app(
        db_path=db_path,
        comms_log_path=comms_log_path,
        enable_realtime_stream=enable_realtime_stream,
    )
    config = uvicorn.Config(
        app=app,
        host=host,
        port=int(port),
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
