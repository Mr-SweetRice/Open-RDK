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

from .functions import set_device_name


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
    return {
        "sender": sender.strip(),
        "raw_hex": raw_hex.strip().lower(),
    }


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
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = module_type
        out.append(
            {
                "serial_number": serial.strip(),
                "name": name,
                "status": item.get("status", ""),
                "module_type": module_type,
                "link_status": item.get("link_status", ""),
                "device_node": item.get("device_node"),
                "last_event_at": item.get("last_event_at"),
                "last_link_check_at": item.get("last_link_check_at"),
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

            event = {
                "line": self._next_line(),
                "sender": event.get("sender"),
                "raw_hex": event.get("raw_hex"),
            }
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
            events = [item for item in events if item.get("sender") in (serial, "host")]
        if len(events) > keep:
            events = events[-keep:]
        return events


class DeviceNameUpdatePayload(BaseModel):
    name: str = ""


def create_webview_app(db_path: str, comms_log_path: str) -> FastAPI:
    app = FastAPI(title="RDK Msg Relay Webview")
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    broker = CommsStreamBroker(comms_log_path=comms_log_path)

    app.state.db_path = db_path
    app.state.comms_log_path = comms_log_path
    app.state.broker = broker

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
