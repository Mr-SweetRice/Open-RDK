from __future__ import annotations

import argparse
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .executor import ExecutionManager
from .serial_runtime import SerialRuntime
from .storage import JsonStore, ScriptStore, default_state_dir


class Payload(BaseModel):
    model_config = {"extra": "allow"}


class ServiceState:
    LOG_LIMIT = 500

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.scripts = ScriptStore(self.state_dir)
        self.activity_store = JsonStore(
            self.state_dir / "activity_log.json", {"version": 1, "entries": []}
        )
        self.log_lock = threading.RLock()
        self.serial = SerialRuntime(self.state_dir)
        self.executor = ExecutionManager(self.state_dir, self.scripts, notify=self.send_command)
        self.serial.event_callback = self.executor.handle_frame
        self.auto_connect_stop = threading.Event()
        self.auto_connect_thread: threading.Thread | None = None
        self._migrate_legacy_configuration()

    def start_auto_connect(self) -> None:
        if self.auto_connect_thread and self.auto_connect_thread.is_alive():
            return
        self.auto_connect_stop.clear()
        self.auto_connect_thread = threading.Thread(
            target=self._auto_connect_loop, name="control-hub-auto-connect", daemon=True,
        )
        self.auto_connect_thread.start()

    def _auto_connect_loop(self) -> None:
        while not self.auto_connect_stop.is_set():
            status = self.serial.status()
            if status["connected"] or status["state"] == "connecting":
                self.auto_connect_stop.wait(1.0)
                continue
            configured = self.executor.config()["connection"].get("port", "")
            available = [item["device"] for item in self.serial.ports()]
            candidates = ([configured] if configured and configured in available else []) + [
                port for port in available if port != configured
            ]
            connected = False
            for port in candidates:
                if self.auto_connect_stop.is_set():
                    return
                try:
                    self.serial.connect(port)
                    deadline = time.monotonic() + 12.5
                    while time.monotonic() < deadline and not self.auto_connect_stop.is_set():
                        current = self.serial.status()
                        if current["state"] != "connecting":
                            break
                        self.auto_connect_stop.wait(0.1)
                    current = self.serial.status()
                    if current["connected"]:
                        config = self.executor.config()
                        config["connection"] = {"port": port, "auto_connect": True}
                        self.executor.save_config(config)
                        self.log("service", "auto_connect", response=port)
                        connected = True
                        break
                except (ValueError, RuntimeError, OSError):
                    pass
            if not connected:
                self.auto_connect_stop.wait(2.0)

    def _migrate_legacy_configuration(self) -> None:
        """Import the former WebView profile once without depending on its runtime."""
        if self.executor.config_store.path.exists():
            return
        candidates: list[Path] = []
        configured = os.environ.get("OPENRDK_LEGACY_STATE_DIR", "").strip()
        if configured:
            candidates.append(Path(os.path.expandvars(os.path.expanduser(configured))).resolve())
        try:
            candidates.append(Path(__file__).resolve().parents[4] / "host" / "main" / "src" / "openrdk")
        except IndexError:
            pass
        for legacy in candidates:
            profiles_path = legacy / "control_hub_commands.json"
            if not profiles_path.is_file():
                continue
            try:
                profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            profile_pair = next(
                ((str(key), value) for key, value in profiles.items() if isinstance(value, dict)),
                None,
            ) if isinstance(profiles, dict) else None
            profile = profile_pair[1] if profile_pair else None
            if profile is None:
                continue
            folders = [legacy / "control_hub_scripts"]
            directories_path = legacy / "control_hub_script_directories.json"
            try:
                directory_data = json.loads(directories_path.read_text(encoding="utf-8"))
                folders.extend(Path(item) for item in directory_data.get("directories", []) if isinstance(item, str))
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            for folder in folders:
                try:
                    self.scripts.add_directory(str(folder.resolve()))
                except (ValueError, OSError):
                    pass
            available = self.scripts.list()
            migrated = self.executor.config()
            migrated["device_name"] = str(profile.get("device_name") or migrated["device_name"])
            try:
                registry = json.loads((legacy / "espressif_devices.json").read_text(encoding="utf-8"))
                devices = registry.get("devices", []) if isinstance(registry, dict) else []
                legacy_serial = profile_pair[0] if profile_pair else ""
                device = next((item for item in devices if isinstance(item, dict) and (
                    str(item.get("serial_number") or "") == legacy_serial
                    or str(item.get("module_type") or "") == "control_hub_module"
                )), None)
                if device and device.get("device_node"):
                    migrated["connection"]["port"] = str(device["device_node"])
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            raw_menu = profile.get("menu") if isinstance(profile.get("menu"), list) else []
            for index, raw in enumerate(raw_menu[:8]):
                if not isinstance(raw, dict):
                    continue
                item = {**migrated["menu"][index], **raw, "timeout_sec": 30}
                if str(item.get("kind")) == "python" and item.get("script"):
                    reference = str(item["script"])
                    if not any(script["reference"] == reference for script in available):
                        same_name = next((script for script in available if script["name"] == reference), None)
                        if same_name:
                            item["script"] = same_name["reference"]
                migrated["menu"][index] = item
            try:
                self.executor.save_config(migrated)
                self.log("migration", "legacy_webview", response=str(profiles_path))
            except (ValueError, OSError):
                pass
            return

    def log(self, kind: str, command: str, response: str = "", error: str = "", **extra) -> None:
        entry = {"id": str(time.time_ns()), "timestamp": time.time(), "kind": kind,
                 "command": command, "response": response, "error": error, **extra}
        with self.log_lock:
            value = self.activity_store.load()
            entries = value.get("entries", []) if isinstance(value.get("entries"), list) else []
            entries.append(entry)
            self.activity_store.save({"version": 1, "entries": entries[-self.LOG_LIMIT:]})

    def activity(self, limit: int = 100) -> list[dict]:
        entries = self.activity_store.load().get("entries", [])
        entries = entries if isinstance(entries, list) else []
        return list(reversed(entries[-max(1, min(int(limit), self.LOG_LIMIT)):]))

    def send_command(self, command: str, timeout: float = 2.0) -> str:
        try:
            response = self.serial.send(command, timeout=timeout)
            self.log("module_command", command, response=response)
            return response
        except Exception as exc:
            self.log("module_command", command, error=str(exc))
            raise

    def sync_module(self) -> list[str]:
        config = self.executor.config()
        commands = [f"SET CFG NAME {config['device_name']}"]
        for index, item in enumerate(config["menu"]):
            if not item["enabled"]:
                commands.append(f"CLEAR MENU {index}")
                continue
            target = item["script"] if item["kind"] == "python" else item["command"]
            name_b64 = self.executor.encode(item["name"])
            target_b64 = self.executor.encode(target)
            if len(name_b64) > 43 or len(target_b64) > 131:
                raise ValueError(f"menu option {index + 1} is too long for the firmware")
            commands.append(
                f"SET MENU {index} {1 if item['kind'] == 'python' else 0} {name_b64} {target_b64}"
            )
        commands.append("SAVE CFG")
        responses = []
        for command in commands:
            response = self.send_command(command)
            responses.append(response)
            if response != "OK":
                raise RuntimeError(f"firmware rejected '{command}': {response}")
        return responses

    def shutdown(self) -> None:
        self.auto_connect_stop.set()
        if self.auto_connect_thread and self.auto_connect_thread.is_alive():
            self.auto_connect_thread.join(timeout=3)
        self.executor.shutdown()
        self.serial.shutdown()


def create_app(state_dir: Path | None = None) -> FastAPI:
    state = ServiceState(state_dir or default_state_dir())
    web_dir = Path(__file__).with_name("web")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.start_auto_connect()
        yield
        state.shutdown()

    app = FastAPI(title="Open-RDK Control Hub Service", version="1.0.0", lifespan=lifespan)
    app.state.control_hub = state
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(web_dir / "index.html")

    @app.get("/api/status")
    def status():
        return {"service": "online", "state_dir": str(state.state_dir),
                "connection": state.serial.status(), "execution": state.executor.status()}

    @app.get("/api/ports")
    def ports():
        return {"ports": state.serial.ports()}

    @app.post("/api/connect")
    def connect(payload: Payload):
        data = payload.model_dump()
        port = str(data.get("port") or state.executor.config()["connection"].get("port") or "")
        previous_config = state.executor.config()
        try:
            claimed_config = state.executor.config()
            claimed_config["connection"]["port"] = port
            state.executor.save_config(claimed_config)
            # Give an already-running Open-RDK host one discovery cycle to release this port.
            time.sleep(0.35)
            state.serial.connect(port)
            deadline = time.monotonic() + 9.5
            while time.monotonic() < deadline:
                current = state.serial.status()
                if current["state"] != "connecting":
                    if not current["connected"]:
                        raise RuntimeError(current.get("error") or "connection failed")
                    break
                time.sleep(0.05)
            current = state.serial.status()
            if not current["connected"]:
                raise RuntimeError(current.get("error") or "connection timed out")
            state.log("service", "connect", response=port)
            return {"ok": True, "connection": current}
        except (ValueError, RuntimeError, OSError) as exc:
            state.executor.save_config(previous_config)
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/disconnect")
    def disconnect():
        state.serial.disconnect()
        state.log("service", "disconnect")
        return {"ok": True, "connection": state.serial.status()}

    @app.get("/api/config")
    def get_config():
        return state.executor.config()

    @app.put("/api/config")
    def put_config(payload: Payload):
        try:
            config = state.executor.save_config(payload.model_dump())
            synced = False
            warning = ""
            if state.serial.status()["connected"]:
                state.sync_module()
                synced = True
            else:
                warning = "configuration saved; it will synchronize when the module connects automatically"
            state.log("configuration", "save", response="synced" if synced else "saved")
            return {"ok": True, "config": config, "synced": synced, "warning": warning}
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/sync")
    def sync():
        try:
            responses = state.sync_module()
            return {"ok": True, "responses": responses}
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/scripts")
    def scripts():
        return {"scripts": state.scripts.list(), "directories": state.scripts.directories()}

    @app.post("/api/scripts")
    def upload_script(payload: Payload):
        data = payload.model_dump()
        try:
            return {"ok": True, "script": state.scripts.save(data.get("filename", ""), data.get("content", ""))}
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/script-directories/select")
    def select_directory():
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(
                parent=root, title="Selecionar diretorio de scripts", mustexist=True,
            )
            root.destroy()
            return {"ok": True, "path": selected or ""}
        except Exception as exc:
            raise HTTPException(400, f"seletor de diretorio indisponivel: {exc}") from exc

    @app.post("/api/script-directories")
    def add_directory(payload: Payload):
        try:
            return {"ok": True, "directory": state.scripts.add_directory(payload.model_dump().get("path", ""))}
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/script-directories/{directory_id}")
    def remove_directory(directory_id: str):
        try:
            state.scripts.remove_directory(directory_id)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(404, "directory not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/executions/menu/{slot}")
    def execute_menu(slot: int):
        try:
            state.executor.execute_slot(slot, source="web")
            return {"ok": True}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/executions/command")
    def execute_command(payload: Payload):
        try:
            state.executor.execute_direct(payload.model_dump(), source="web")
            return {"ok": True}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/executions/stop")
    def stop_execution():
        state.executor.request_stop()
        return {"ok": True}

    @app.get("/api/executions")
    def executions(limit: int = 50):
        return {"status": state.executor.status(), "entries": state.executor.history(limit)}

    @app.delete("/api/executions")
    def clear_executions():
        state.executor.clear_history()
        return {"ok": True}

    @app.get("/api/activity")
    def activity(limit: int = 100):
        return {"entries": state.activity(limit)}

    @app.delete("/api/activity")
    def clear_activity():
        state.activity_store.save({"version": 1, "entries": []})
        return {"ok": True}

    @app.post("/api/module/command")
    def module_command(payload: Payload):
        command = str(payload.model_dump().get("command") or "").strip()
        if not command:
            raise HTTPException(400, "command is required")
        try:
            return {"ok": True, "response": state.send_command(command)}
        except (RuntimeError, TimeoutError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/module/imu")
    def imu():
        try:
            return {"response": state.send_command("GET IMU")}
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/module/imu/calibrate")
    def calibrate_imu():
        try:
            return {"response": state.send_command("CALIBRATE IMU")}
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/module/servo")
    def servo(payload: Payload):
        data = payload.model_dump()
        try:
            channel, angle = int(data.get("channel")), int(data.get("angle"))
            if not 0 <= channel < 6 or not 0 <= angle <= 180:
                raise ValueError("servo channel or angle is out of range")
            return {"response": state.send_command(f"SET SERVO {channel} {angle}")}
        except (TypeError, ValueError, RuntimeError, TimeoutError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/module/gpio")
    def gpio(payload: Payload):
        data = payload.model_dump()
        try:
            channel, value = int(data.get("channel")), int(data.get("value"))
            if not 0 <= channel < 9 or value not in (0, 1):
                raise ValueError("GPIO channel or value is out of range")
            return {"response": state.send_command(f"SET GPIO {channel} {value}")}
        except (TypeError, ValueError, RuntimeError, TimeoutError) as exc:
            raise HTTPException(400, str(exc)) from exc

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone service for the Open-RDK control module")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--serial-port", default="")
    parser.add_argument("--auto-connect", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir).resolve() if args.state_dir else default_state_dir()
    app = create_app(state_dir)
    if args.serial_port:
        config = app.state.control_hub.executor.config()
        config["connection"] = {"port": args.serial_port, "auto_connect": args.auto_connect}
        app.state.control_hub.executor.save_config(config)
    uvicorn.run(app, host=args.host, port=args.port, log_level=os.environ.get("LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
