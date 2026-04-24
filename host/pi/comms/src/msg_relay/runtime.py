import os
import threading
import time

from .constants import (
    DEFAULT_COMMS_LOG_PATH,
    DEFAULT_DEVICE_DB_PATH,
    WEBVIEW_HOST,
    WEBVIEW_PORT,
)
from .errors import DeviceNotFoundError, RuntimeNotStartedError, UnsupportedModuleTypeError
from .functions import (
    configure_comms_log_path,
    get_device_snapshot,
    list_device_snapshots,
    run_conex_loop,
    stop_all_keepalive_monitors,
)


def _normalize_module_type(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


class RelayRuntime:
    """
    In-process runtime entrypoint for SDK-first usage.

    This class starts:
    - udev/serial monitor loop in a background thread
    - optional webview server in a background thread

    so users can import and control modules from their own Python app without
    requiring a pre-installed system service.
    """

    def __init__(
        self,
        db_path: str | None = None,
        comms_log_path: str | None = None,
        poll_timeout_sec: float = 0.25,
        enable_webview: bool = True,
        enable_webview_updates: bool = True,
        webview_host: str | None = None,
        webview_port: int | None = None,
        auto_start: bool = False,
    ):
        self._db_path = os.path.abspath(db_path or DEFAULT_DEVICE_DB_PATH)
        self._comms_log_path = os.path.abspath(comms_log_path or DEFAULT_COMMS_LOG_PATH)
        self._poll_timeout_sec = max(0.05, float(poll_timeout_sec))
        self._enable_webview = bool(enable_webview)
        self._enable_webview_updates = bool(enable_webview_updates)
        self._webview_host = str(webview_host or WEBVIEW_HOST)
        self._webview_port = int(webview_port or WEBVIEW_PORT)
        self._runtime_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._runtime_error: Exception | None = None
        self._webview_thread: threading.Thread | None = None
        self._webview_server = None
        self._webview_error: Exception | None = None
        self._runtime_lock = threading.Lock()

        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(self._comms_log_path) or ".", exist_ok=True)
        configure_comms_log_path(self._comms_log_path)

        if auto_start:
            self.start()

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def comms_log_path(self) -> str:
        return self._comms_log_path

    @property
    def is_running(self) -> bool:
        thread = self._runtime_thread
        return bool(thread and thread.is_alive())

    @property
    def webview_enabled(self) -> bool:
        return self._enable_webview

    @property
    def webview_updates_enabled(self) -> bool:
        return self._enable_webview_updates

    @property
    def is_webview_running(self) -> bool:
        thread = self._webview_thread
        return bool(thread and thread.is_alive())

    @property
    def webview_url(self) -> str:
        return f"http://{self._webview_host}:{self._webview_port}"

    @property
    def last_error(self) -> Exception | None:
        return self._runtime_error

    @property
    def last_webview_error(self) -> Exception | None:
        return self._webview_error

    def _runtime_worker(self, stop_event: threading.Event):
        try:
            run_conex_loop(
                db_path=self._db_path,
                stop_event=stop_event,
                poll_timeout_sec=self._poll_timeout_sec,
            )
        except Exception as exc:
            self._runtime_error = exc

    def _webview_worker(self, server):
        try:
            server.run()
        except Exception as exc:
            self._webview_error = exc

    def _start_webview_locked(self):
        if not self._enable_webview:
            return
        if self.is_webview_running:
            return

        self._webview_error = None
        try:
            import uvicorn
            from .webview import create_webview_app
        except Exception as exc:
            self._webview_error = exc
            return

        app = create_webview_app(
            db_path=self._db_path,
            comms_log_path=self._comms_log_path,
            enable_realtime_stream=self._enable_webview_updates,
        )
        config = uvicorn.Config(
            app=app,
            host=self._webview_host,
            port=int(self._webview_port),
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=self._webview_worker,
            args=(server,),
            name="msg-relay-webview",
            daemon=True,
        )
        thread.start()
        self._webview_server = server
        self._webview_thread = thread

    def start(self):
        with self._runtime_lock:
            if self.is_running:
                self._start_webview_locked()
                return self
            configure_comms_log_path(self._comms_log_path)
            stop_event = threading.Event()
            self._runtime_error = None
            thread = threading.Thread(
                target=self._runtime_worker,
                args=(stop_event,),
                name="msg-relay-runtime",
                daemon=True,
            )
            thread.start()
            self._stop_event = stop_event
            self._runtime_thread = thread
            self._start_webview_locked()
        return self

    def ensure_running(self):
        self.start()
        if self.is_running:
            if self._enable_webview and not self.is_webview_running:
                err = self.last_webview_error
                if err is not None:
                    raise RuntimeNotStartedError(
                        f"webview failed to start: {err}"
                    ) from err
                raise RuntimeNotStartedError("webview is not running")
            return self
        err = self.last_error
        if err is not None:
            raise RuntimeNotStartedError(f"runtime failed to start: {err}") from err
        raise RuntimeNotStartedError("runtime is not running")

    def stop(self, timeout_sec: float = 2.0):
        with self._runtime_lock:
            stop_event = self._stop_event
            thread = self._runtime_thread
            webview_server = self._webview_server
            webview_thread = self._webview_thread
            self._stop_event = None
            self._runtime_thread = None
            self._webview_server = None
            self._webview_thread = None
        if stop_event is not None:
            stop_event.set()
        if webview_server is not None:
            try:
                webview_server.should_exit = True
            except Exception:
                pass
        stop_all_keepalive_monitors()
        if thread and thread.is_alive():
            thread.join(max(0.1, float(timeout_sec)))
        if webview_thread and webview_thread.is_alive():
            webview_thread.join(max(0.1, float(timeout_sec)))

    def list_devices(self) -> list[dict]:
        return list_device_snapshots(self._db_path)

    def get_device(self, serial_number: str) -> dict | None:
        return get_device_snapshot(self._db_path, serial_number)

    def require_device(self, serial_number: str, wait_timeout_sec: float = 1.0) -> dict:
        deadline = time.monotonic() + max(0.0, float(wait_timeout_sec))
        while True:
            snapshot = self.get_device(serial_number)
            if isinstance(snapshot, dict):
                return snapshot
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        raise DeviceNotFoundError(f"device not found: {serial_number}")

    def module(self, serial_number: str):
        self.ensure_running()
        snapshot = self.require_device(serial_number)
        module_type = _normalize_module_type(
            str(snapshot.get("module_type") or snapshot.get("firmware_module") or "")
        )
        if module_type == "traction_module":
            from .modules import TractionModule

            return TractionModule(self, serial_number=serial_number, snapshot=snapshot)
        if module_type == "line_sensor_module":
            from .modules import LineSensorModule

            return LineSensorModule(self, serial_number=serial_number, snapshot=snapshot)
        raise UnsupportedModuleTypeError(
            f"unsupported module_type '{module_type or 'unknown'}' for {serial_number}"
        )

    def traction(self, serial_number: str):
        self.ensure_running()
        from .modules import TractionModule

        return TractionModule(self, serial_number=serial_number, snapshot=self.require_device(serial_number))

    def line_sensor(self, serial_number: str):
        self.ensure_running()
        from .modules import LineSensorModule

        return LineSensorModule(self, serial_number=serial_number, snapshot=self.require_device(serial_number))
