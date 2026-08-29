from __future__ import annotations

import base64
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .protocol import TYPE_CONTROL, Frame
from .storage import JsonStore, ScriptStore


def default_config() -> dict:
    return {
        "version": 1,
        "connection": {"port": "", "auto_connect": True},
        "device_name": "Modulo de controle",
        "menu": [{
            "enabled": False, "name": f"Opcao {index + 1}", "kind": "command",
            "command": "", "script": "", "python_env": "", "shell": "auto",
            "timeout_sec": 30,
        } for index in range(8)],
        "stop_action": {"enabled": True, "kind": "builtin_openrdk", "readonly": True,
                        "timeout_sec": 15},
    }


class ExecutionManager:
    HISTORY_LIMIT = 200
    OUTPUT_LIMIT = 16_000

    def __init__(
        self, state_dir: Path, scripts: ScriptStore,
        notify: Callable[[str], str] | None = None,
    ):
        self.scripts = scripts
        self.notify = notify
        self.config_store = JsonStore(state_dir / "config.json", default_config())
        self.history_store = JsonStore(state_dir / "execution_log.json", {"version": 1, "entries": []})
        self.lock = threading.RLock()
        self.active: dict | None = None
        self._status: dict = {"state": "idle"}

    def config(self) -> dict:
        current = default_config()
        stored = self.config_store.load()
        for key in ("device_name", "connection", "menu"):
            if key in stored:
                current[key] = stored[key]
        stored_menu = current["menu"] if isinstance(current.get("menu"), list) else []
        defaults = default_config()["menu"]
        current["menu"] = [
            {**defaults[index], **(stored_menu[index] if index < len(stored_menu) and isinstance(stored_menu[index], dict) else {})}
            for index in range(8)
        ]
        current["connection"]["auto_connect"] = True
        current["stop_action"] = dict(default_config()["stop_action"])
        return current

    def save_config(self, value: dict) -> dict:
        checked = self.validate_config(value)
        self.config_store.save(checked)
        return checked

    @staticmethod
    def validate_config(value: dict) -> dict:
        if not isinstance(value, dict):
            raise ValueError("invalid configuration")
        checked = default_config()
        checked["device_name"] = str(value.get("device_name") or "Modulo de controle").strip()
        if not checked["device_name"] or len(checked["device_name"].encode("utf-8")) > 31:
            raise ValueError("device name must contain between 1 and 31 UTF-8 bytes")
        incoming_connection = value.get("connection") if isinstance(value.get("connection"), dict) else {}
        checked["connection"] = {
            "port": str(incoming_connection.get("port") or "").strip(),
            "auto_connect": True,
        }
        incoming_menu = value.get("menu") if isinstance(value.get("menu"), list) else []
        menu = []
        for index in range(8):
            raw = incoming_menu[index] if index < len(incoming_menu) and isinstance(incoming_menu[index], dict) else {}
            menu.append(ExecutionManager._validate_action(raw, menu=True, index=index))
        checked["menu"] = menu
        checked["stop_action"] = dict(default_config()["stop_action"])
        return checked

    @staticmethod
    def _validate_action(raw: dict, menu: bool, index: int = 0) -> dict:
        kind = str(raw.get("kind") or "command").strip().lower()
        if kind not in ("command", "python"):
            raise ValueError("action type must be command or python")
        shell = str(raw.get("shell") or "auto").strip().lower()
        if shell not in ("auto", "cmd", "powershell", "sh"):
            raise ValueError("invalid command shell")
        try:
            timeout = int(raw.get("timeout_sec") or (30 if menu else 15))
        except (TypeError, ValueError):
            raise ValueError("timeout must be an integer") from None
        if not 1 <= timeout <= 3600:
            raise ValueError("timeout must be between 1 and 3600 seconds")
        action = {
            "enabled": bool(raw.get("enabled")), "kind": kind,
            "command": str(raw.get("command") or "").strip(),
            "script": str(raw.get("script") or "").strip(),
            "python_env": str(raw.get("python_env") or "").strip(),
            "shell": shell, "timeout_sec": timeout,
        }
        if menu:
            action["name"] = str(raw.get("name") or f"Opcao {index + 1}").strip()[:30]
        target = action["script"] if kind == "python" else action["command"]
        if action["enabled"] and not target:
            raise ValueError("every enabled action needs a command or script")
        if any(character in target for character in ("\x00", "\r", "\n")):
            raise ValueError("an action target contains an invalid character")
        if any(character in action["python_env"] for character in ("\x00", "\r", "\n")):
            raise ValueError("the Python environment path contains an invalid character")
        if len(action["python_env"].encode("utf-8")) > 2048:
            raise ValueError("the Python environment path is too long")
        if menu and len(action["name"].encode("utf-8")) > 30:
            raise ValueError("menu names are limited to 30 UTF-8 bytes")
        if menu and action["enabled"] and not action["name"]:
            raise ValueError("every enabled menu action needs a name")
        if menu and len(target.encode("utf-8")) > 90:
            raise ValueError("menu execution values are limited to 90 UTF-8 bytes")
        if not menu and len(target.encode("utf-8")) > 8192:
            raise ValueError("motor stop action is too long")
        return action

    def status(self) -> dict:
        with self.lock:
            return dict(self._status)

    def history(self, limit: int = 50) -> list[dict]:
        entries = self.history_store.load().get("entries", [])
        entries = entries if isinstance(entries, list) else []
        return [dict(item) for item in reversed(entries[-max(1, min(int(limit), self.HISTORY_LIMIT)):])]

    def clear_history(self) -> None:
        self.history_store.save({"version": 1, "entries": []})

    def _append_history(self, entry: dict) -> None:
        with self.lock:
            value = self.history_store.load()
            entries = value.get("entries", []) if isinstance(value.get("entries"), list) else []
            entries.append(dict(entry))
            self.history_store.save({"version": 1, "entries": entries[-self.HISTORY_LIMIT:]})

    @staticmethod
    def decode(value: str) -> str:
        text = str(value or "").strip()
        padding = "=" * ((4 - len(text) % 4) % 4)
        return base64.urlsafe_b64decode((text + padding).encode("ascii")).decode("utf-8")

    @staticmethod
    def encode(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")

    def handle_frame(self, frame: Frame) -> None:
        if frame.message_type != TYPE_CONTROL:
            return
        message = frame.message.strip()
        if message.startswith("STOP,"):
            try:
                self.request_stop(int(message.split(",", 1)[1]))
            except ValueError:
                pass
            return
        if not message.startswith("EXEC,"):
            return
        parts = message.split(",", 3)
        try:
            slot, mode, requested = int(parts[1]), int(parts[2]), self.decode(parts[3])
        except (IndexError, TypeError, ValueError, UnicodeError):
            return
        self.execute_slot(slot, mode, requested, source="module")

    def execute_slot(self, slot: int, mode: int | None = None, requested: str | None = None, source: str = "web") -> None:
        profile = self.config()
        stop_action = self._required_stop_action(profile)
        menu = profile["menu"]
        if not 0 <= int(slot) < len(menu):
            raise ValueError("invalid menu slot")
        action = dict(menu[int(slot)])
        expected_mode = 1 if action["kind"] == "python" else 0
        expected = action["script"] if expected_mode else action["command"]
        if not action["enabled"]:
            raise RuntimeError("this menu action is disabled")
        if mode is not None and (int(mode) != expected_mode or str(requested or "") != expected):
            self._rejected(slot, action, "module request does not match the service configuration")
            return
        with self.lock:
            if self.active is not None:
                raise RuntimeError("another action is already running")
            stop_event = threading.Event()
            self.active = {"slot": slot, "process": None, "stop_event": stop_event,
                           "stop_timeout": stop_action["timeout_sec"]}
            self._status = {
                "state": "starting", "slot": slot, "name": action["name"],
                "kind": action["kind"], "target": expected, "source": source,
                "started_at": time.time(),
            }
        threading.Thread(
            target=self._run, args=(slot, action, source, stop_event, stop_action),
            name=f"control-hub-action-{slot}", daemon=True,
        ).start()

    def execute_direct(self, action: dict, source: str = "web") -> None:
        checked = self._validate_action({**action, "enabled": True, "name": "Comando manual"}, menu=True)
        stop_action = self._required_stop_action(self.config())
        with self.lock:
            if self.active is not None:
                raise RuntimeError("another action is already running")
            stop_event = threading.Event()
            self.active = {"slot": -1, "process": None, "stop_event": stop_event,
                           "stop_timeout": stop_action["timeout_sec"]}
            self._status = {"state": "starting", "slot": -1, "name": checked["name"],
                            "kind": checked["kind"], "source": source, "started_at": time.time()}
        threading.Thread(
            target=self._run, args=(-1, checked, source, stop_event, stop_action), daemon=True,
        ).start()

    def _required_stop_action(self, profile: dict) -> dict:
        del profile
        return dict(default_config()["stop_action"])

    def _rejected(self, slot: int, action: dict, reason: str) -> None:
        status = {"state": "rejected", "slot": slot, "name": action.get("name", ""),
                  "error": reason, "finished_at": time.time()}
        with self.lock:
            self._status = status
        self._append_history(status)
        self._notify_state(slot, "FAILED")

    def request_stop(self, slot: int | None = None) -> None:
        process = None
        with self.lock:
            if self.active is None or (slot is not None and self.active["slot"] != slot):
                return
            self.active["stop_event"].set()
            process = self.active.get("process")
            self._status = {**self._status, "state": "stopping"}
        if process is not None:
            self.terminate_process(process)

    def shutdown(self) -> None:
        self.request_stop()
        with self.lock:
            timeout = int((self.active or {}).get("stop_timeout") or 15) + 8
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if self.active is None:
                    return
            time.sleep(0.05)

    @staticmethod
    def command_argv(command: str, shell: str) -> tuple[list[str], str]:
        selected = str(shell or "auto").lower()
        if selected == "auto":
            selected = "cmd" if os.name == "nt" else "sh"
        if selected == "cmd":
            if os.name != "nt":
                raise RuntimeError("cmd is available only on Windows")
            return [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c", command], "cmd"
        if selected == "powershell":
            executable = shutil.which("pwsh") or shutil.which("powershell")
            if not executable:
                raise RuntimeError("PowerShell was not found")
            return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], "powershell"
        if selected == "sh":
            executable = shutil.which("sh")
            if not executable:
                raise RuntimeError("sh was not found")
            return [executable, "-lc", command], "sh"
        raise RuntimeError(f"unsupported shell: {shell}")

    @staticmethod
    def terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    @staticmethod
    def _venv_interpreter(root: Path) -> Path | None:
        candidates = (
            root / "Scripts" / "python.exe",
            root / "bin" / "python",
            root / "bin" / "python3",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    @classmethod
    def python_runtime(cls, script: Path, configured: str = "") -> tuple[Path, dict[str, str], Path | None]:
        interpreter: Path | None = None
        venv_root: Path | None = None
        value = os.path.expandvars(os.path.expanduser(str(configured or "").strip()))
        if value:
            selected = Path(value).resolve()
            if selected.is_dir():
                interpreter = cls._venv_interpreter(selected)
                venv_root = selected
                if interpreter is None:
                    raise FileNotFoundError(f"Python interpreter was not found in virtual environment: {selected}")
            elif selected.is_file():
                interpreter = selected
                venv_root = selected.parent.parent if selected.parent.name.lower() in ("bin", "scripts") else selected.parent
            else:
                raise FileNotFoundError(f"Python environment does not exist: {selected}")
        else:
            for parent in (script.parent, *script.parents):
                for name in (".venv", "venv"):
                    candidate = parent / name
                    found = cls._venv_interpreter(candidate)
                    if found is not None:
                        interpreter, venv_root = found, candidate
                        break
                if interpreter is not None:
                    break
        interpreter = interpreter or Path(sys.executable).resolve()
        environment = os.environ.copy()
        if venv_root is not None:
            environment["VIRTUAL_ENV"] = str(venv_root)
            environment.pop("PYTHONHOME", None)
            executable_dir = str(interpreter.parent)
            environment["PATH"] = executable_dir + os.pathsep + environment.get("PATH", "")
        return interpreter, environment, venv_root

    def _execute_action(self, action: dict, stop_event: threading.Event | None = None) -> dict:
        started = time.time()
        kind = action["kind"]
        builtin = kind == "builtin_openrdk"
        target = "builtin:openrdk-stop-all-motors" if builtin else (
            action["script"] if kind == "python" else action["command"]
        )
        resolved_shell = "python" if kind in ("python", "builtin_openrdk") else action["shell"]
        working_directory = Path.cwd()
        process_environment = None
        python_interpreter = ""
        python_environment = ""
        process = None
        try:
            if builtin:
                path = Path(__file__).with_name("builtin_motor_stop.py")
                interpreter, process_environment, venv_root = self.python_runtime(
                    path, action.get("python_env", ""),
                )
                argv = [str(interpreter), "-u", str(path)]
                python_interpreter = str(interpreter)
                python_environment = str(venv_root or "")
            elif kind == "python":
                path = self.scripts.resolve(target)
                interpreter, process_environment, venv_root = self.python_runtime(
                    path, action.get("python_env", ""),
                )
                argv = [str(interpreter), "-u", str(path)]
                working_directory = path.parent
                python_interpreter = str(interpreter)
                python_environment = str(venv_root or "")
            else:
                argv, resolved_shell = self.command_argv(target, action["shell"])
            options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                       "stderr": subprocess.PIPE, "text": True, "shell": False,
                       "cwd": str(working_directory)}
            if process_environment is not None:
                options["env"] = process_environment
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                options["start_new_session"] = True
            process = subprocess.Popen(argv, **options)
            if stop_event is not None:
                with self.lock:
                    if self.active is not None:
                        self.active["process"] = process
                if stop_event.is_set():
                    self.terminate_process(process)
            stdout, stderr = process.communicate(timeout=action["timeout_sec"])
            stopped = bool(stop_event and stop_event.is_set())
            return {"state": "stopped" if stopped else ("completed" if process.returncode == 0 else "failed"),
                    "kind": kind, "target": target, "shell": resolved_shell,
                    "returncode": process.returncode, "stdout": (stdout or "")[-self.OUTPUT_LIMIT:],
                    "stderr": (stderr or "")[-self.OUTPUT_LIMIT:], "started_at": started,
                    "finished_at": time.time(), "python_interpreter": python_interpreter,
                    "python_env": python_environment}
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                self.terminate_process(process)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except Exception:
                    stdout, stderr = exc.stdout, exc.stderr
            else:
                stdout, stderr = exc.stdout, exc.stderr
            return {"state": "timeout", "kind": kind, "target": target, "shell": resolved_shell,
                    "stdout": str(stdout or "")[-self.OUTPUT_LIMIT:],
                    "stderr": str(stderr or "")[-self.OUTPUT_LIMIT:], "started_at": started,
                    "finished_at": time.time(), "error": "execution timeout",
                    "python_interpreter": python_interpreter, "python_env": python_environment}
        except Exception as exc:
            return {"state": "failed", "kind": kind, "target": target, "shell": resolved_shell,
                    "started_at": started, "finished_at": time.time(), "error": str(exc),
                    "python_interpreter": python_interpreter, "python_env": python_environment}

    def _run(
        self, slot: int, action: dict, source: str, stop_event: threading.Event,
        stop_action: dict,
    ) -> None:
        if slot >= 0:
            self._notify_state(slot, "RUNNING")
        result = self._execute_action(action, stop_event)

        # Safety invariant: this action runs in a finally-equivalent path after every exit.
        stop_runtime = dict(stop_action)
        if action.get("kind") == "python" and action.get("python_env"):
            stop_runtime["python_env"] = action["python_env"]
        motor_stop = self._execute_action(stop_runtime)
        status = {
            **result, "slot": slot, "name": action.get("name", ""), "source": source,
            "motor_stop": motor_stop,
        }
        if motor_stop["state"] not in ("completed",) and status["state"] == "completed":
            status["state"] = "failed"
            status["error"] = "main action ended, but the motor stop action failed"
        with self.lock:
            self._status = status
            self.active = None
        self._append_history(status)
        if slot >= 0:
            firmware_state = "STOPPED" if result["state"] == "stopped" else (
                "DONE" if status["state"] == "completed" else "FAILED"
            )
            self._notify_state(slot, firmware_state)

    def _notify_state(self, slot: int, state: str) -> None:
        if self.notify is None:
            return
        try:
            self.notify(f"RUN STATE {slot} {state}")
        except Exception:
            pass
