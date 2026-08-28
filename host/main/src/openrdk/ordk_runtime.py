import os
import socket
import threading
import time

from .constants import (
    DEFAULT_COMMS_LOG_PATH,
    DEFAULT_DEVICE_DB_PATH,
    STATUS_ONLINE_CONNECTED,
    WEBVIEW_HOST,
    WEBVIEW_PORT,
)
from .errors import DeviceNotFoundError, DeviceOfflineError, RuntimeNotStartedError, UnsupportedModuleTypeError
from .functions import (
    configure_comms_log_path,
    get_device_snapshot,
    list_device_snapshots,
    run_conex_loop,
    set_device_telemetry_requested,
    set_device_name,
    stop_all_keepalive_monitors,
)


def _normalize_module_type(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


class CommsRuntime:
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
        enable_mdns: bool = True,
        mdns_name: str = "rdk",
        enable_http_redirect: bool = True,
        enable_https: bool = False,
        tls_cert_file: str | None = None,
        tls_key_file: str | None = None,
        webview_host: str | None = None,
        webview_port: int | None = None,
        auto_start: bool = False,
    ):
        self._db_path = os.path.abspath(db_path or DEFAULT_DEVICE_DB_PATH)
        self._comms_log_path = os.path.abspath(comms_log_path or DEFAULT_COMMS_LOG_PATH)
        self._poll_timeout_sec = max(0.05, float(poll_timeout_sec))
        self._enable_webview = bool(enable_webview)
        self._enable_webview_updates = bool(enable_webview_updates)
        self._enable_mdns = bool(enable_mdns)
        self._mdns_name = str(mdns_name or "rdk")
        self._enable_http_redirect = bool(enable_http_redirect)
        self._enable_https = bool(enable_https)
        self._tls_cert_file = tls_cert_file
        self._tls_key_file = tls_key_file
        self._webview_host = str(webview_host or WEBVIEW_HOST)
        self._webview_port = int(webview_port or WEBVIEW_PORT)
        self._runtime_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._runtime_error: Exception | None = None
        self._webview_thread: threading.Thread | None = None
        self._webview_server = None
        self._webview_error: Exception | None = None
        self._mdns_publisher = None
        self._http_redirect = None
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
        host = self._webview_host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"{self._webview_scheme}://{host}:{self._webview_port}"

    @property
    def mdns_url(self) -> str:
        return f"{self._webview_scheme}://{self._mdns_name}.local:{self._webview_port}"

    @property
    def _webview_scheme(self) -> str:
        return "https" if self._enable_https else "http"

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
        ssl_certfile = None
        ssl_keyfile = None
        if self._enable_https:
            try:
                from .tls import ensure_self_signed_cert

                ssl_certfile, ssl_keyfile = ensure_self_signed_cert(
                    cert_file=self._tls_cert_file,
                    key_file=self._tls_key_file,
                    hosts=[
                        f"{self._mdns_name}.local",
                        "localhost",
                        "127.0.0.1",
                    ],
                )
                print(f"[tls] HTTPS certificate: {ssl_certfile}", flush=True)
            except Exception as exc:
                self._webview_error = exc
                return
        config = uvicorn.Config(
            app=app,
            host=self._webview_host,
            port=int(self._webview_port),
            log_level="warning",
            access_log=False,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=self._webview_worker,
            args=(server,),
            name="openrdk-webview",
            daemon=True,
        )
        thread.start()
        self._webview_server = server
        self._webview_thread = thread
        if self._enable_mdns:
            try:
                from .mdns import MdnsPublisher

                mdns = MdnsPublisher(
                    name=self._mdns_name,
                    port=int(self._webview_port),
                    scheme=self._webview_scheme,
                )
                if mdns.start():
                    self._mdns_publisher = mdns
            except Exception as exc:
                print(f"[mdns] disabled: {exc}", flush=True)
        if self._enable_http_redirect:
            try:
                from .http_redirect import HttpRedirectServer

                redirect = HttpRedirectServer(
                    target_port=int(self._webview_port),
                    target_scheme=self._webview_scheme,
                )
                if redirect.start():
                    self._http_redirect = redirect
            except Exception as exc:
                print(f"[redirect] disabled: {exc}", flush=True)

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
                name="openrdk-runtime",
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
            mdns_publisher = self._mdns_publisher
            http_redirect = self._http_redirect
            self._stop_event = None
            self._runtime_thread = None
            self._webview_server = None
            self._webview_thread = None
            self._mdns_publisher = None
            self._http_redirect = None
        if stop_event is not None:
            stop_event.set()
        if webview_server is not None:
            try:
                webview_server.should_exit = True
            except Exception:
                pass
        if mdns_publisher is not None:
            try:
                mdns_publisher.stop()
            except Exception:
                pass
        if http_redirect is not None:
            try:
                http_redirect.stop()
            except Exception:
                pass
        telemetry_serials = [
            str(device.get("serial_number") or "")
            for device in list_device_snapshots(self._db_path)
            if bool(device.get("telemetry_requested"))
        ]
        for serial_number in telemetry_serials:
            if serial_number:
                set_device_telemetry_requested(
                    db_path=self._db_path,
                    serial_number=serial_number,
                    enabled=False,
                )
        telemetry_stop_deadline = time.monotonic() + min(
            0.75, max(0.1, float(timeout_sec))
        )
        while telemetry_serials and time.monotonic() < telemetry_stop_deadline:
            active_serials = {
                str(device.get("serial_number") or "")
                for device in list_device_snapshots(self._db_path)
                if bool(device.get("telemetry_active"))
            }
            if not active_serials.intersection(telemetry_serials):
                break
            time.sleep(0.01)
        stop_all_keepalive_monitors()
        if thread and thread.is_alive():
            thread.join(max(0.1, float(timeout_sec)))
        if webview_thread and webview_thread.is_alive():
            webview_thread.join(max(0.1, float(timeout_sec)))

    def list_devices(self, verbose: str | bool | None = None) -> list[dict]:
        devices = list_device_snapshots(self._db_path)
        if verbose:
            mode = str(verbose).lower() if isinstance(verbose, str) else "full"
            print(f"{len(devices)} device(s):")
            for d in devices:
                name = d.get("name") or d.get("module_type") or "?"
                serial = d.get("serial_number", "?")
                if mode == "serials":
                    print(f"  {serial}")
                elif mode == "names":
                    print(f"  {name}")
                elif mode == "status":
                    print(f"  [{name}]  {d.get('status','?')}")
                else:
                    print(
                        f"  [{name}]"
                        f"  serial={serial}"
                        f"  type={d.get('module_type','?')}"
                        f"  status={d.get('status','?')}"
                        f"  port={d.get('device_node','?')}"
                    )
        return devices

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

    def wait_online(
        self,
        serial_number: str,
        timeout_sec: float = 15.0,
        stable_sec: float = 1.0,
    ) -> dict:
        """
        Block until a device has been continuously 'online connected' for stable_sec
        seconds, then return its snapshot. Resets the stability counter on any blip.

        Waits for the runtime (and webview, if enabled) to be fully started before
        polling — avoids false instability caused by system startup load.
        Raises DeviceOfflineError if timeout_sec expires.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_sec))

        # Phase 1 — wait for runtime + webview to be ready before polling devices
        while True:
            runtime_up  = self.is_running
            webview_up  = (not self._enable_webview) or self.is_webview_running
            if runtime_up and webview_up:
                break
            if time.monotonic() >= deadline:
                raise DeviceOfflineError(
                    f"runtime/webview did not start within {timeout_sec}s "
                    f"(runtime={runtime_up}, webview={webview_up})"
                )
            time.sleep(0.1)

        # Phase 2 — wait for stable 'online connected' status
        stable_since: float | None = None
        while True:
            now = time.monotonic()
            snapshot = self.get_device(serial_number)
            is_online = (
                isinstance(snapshot, dict)
                and str(snapshot.get("status") or "") == STATUS_ONLINE_CONNECTED
            )
            if is_online:
                if stable_since is None:
                    stable_since = now
                elif (now - stable_since) >= stable_sec:
                    return snapshot
            else:
                stable_since = None
            if now >= deadline:
                break
            time.sleep(0.1)
        raise DeviceOfflineError(
            f"{serial_number} did not stay online for {stable_sec}s within {timeout_sec}s"
        )

    @property
    def lan_ip(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return str(sock.getsockname()[0])
        except Exception:
            return "127.0.0.1"

    def post(self, post_option: str = "default") -> None:
        hostname = socket.gethostname()
        match post_option:
            case "run":
                print(f"openrdk running: {self.is_running}")
            case "webview":
                print(f"webview running: {self.is_webview_running}")
                print(f"webview url (hostname): http://{hostname}:{self._webview_port}")
            case "webview_complete":
                print(f"webview running: {self.is_webview_running}")
                print(f"webview url (host): {self.webview_url}")
                print(f"webview url (lan): http://{self.lan_ip}:{self._webview_port}")
            case _:
                print(f"openrdk running: {self.is_running}")
                print(f"webview running: {self.is_webview_running}")

    def find_device_by_serial(self, serial: str) -> dict | None:
        for dev in self.list_devices():
            if str(dev.get("serial_number") or "").strip() == serial:
                return dev
        return None

    def find_device_by_name(self, name: str) -> dict | None:
        target = str(name or "").strip().lower()
        for dev in self.list_devices():
            if str(dev.get("name") or "").strip().lower() == target:
                return dev
        return None

    def get_serial_by_name(self, name: str) -> str | None:
        dev = self.find_device_by_name(name)
        return str(dev["serial_number"]).strip() if dev else None

    def rename_device(self, serial: str, name: str) -> dict | None:
        return set_device_name(self._db_path, serial, name)

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

        if module_type == "color_module":
            from .modules import ColorSensorModule

            return ColorSensorModule(self, serial_number=serial_number, snapshot=snapshot)

        if module_type == "distance_sensor_module":
            from .modules import DistanceSensorModule

            return DistanceSensorModule(self, serial_number=serial_number, snapshot=snapshot)

        raise UnsupportedModuleTypeError(
            f"unsupported module_type '{module_type or 'unknown'}' for {serial_number}"
        )

    @property
    def supported_firmware_types(self) -> list[str]:
        from .functions.flasher import SUPPORTED_FIRMWARE_TYPES
        return list(SUPPORTED_FIRMWARE_TYPES)

    def flash_firmware(
        self,
        serial_number: str,
        firmware_type: str,
        baud: int = 460800,
        on_output=None,
    ) -> dict:
        from .functions.flasher import flash_firmware as _flash
        return _flash(
            serial_number=serial_number,
            firmware_type=firmware_type,
            db_path=self._db_path,
            baud=baud,
            on_output=on_output,
        )

    def flash_firmware_by_port(
        self,
        device_node: str,
        firmware_type: str,
        baud: int = 460800,
        on_output=None,
    ) -> dict:
        from .functions.flasher import flash_firmware_by_node
        return flash_firmware_by_node(
            device_node=device_node,
            firmware_type=firmware_type,
            db_path=self._db_path,
            baud=baud,
            on_output=on_output,
        )

    def traction(self, serial_number: str):
        self.ensure_running()
        from .modules import TractionModule

        return TractionModule(self, serial_number=serial_number, snapshot=self.require_device(serial_number))

    def motors(self, motors: dict[str, str], inverted=None):
        """
        Factory for a Motors group.

        Args:
            motors:   mapping of name → serial_number, e.g.
                      {"left": "AA:BB:CC:...", "right": "DD:EE:FF:..."}
            inverted: motor name(s) whose direction should be flipped, e.g.
                      "right" or {"right"} when that motor is wired in reverse.
        """
        self.ensure_running()
        from .modules import Motors, TractionModule

        return Motors(
            inverted=inverted,
            **{name: TractionModule(self, serial) for name, serial in motors.items()},
        )

    def line_sensor(self, serial_number: str):
        self.ensure_running()
        from .modules import LineSensorModule

        return LineSensorModule(self, serial_number=serial_number, snapshot=self.require_device(serial_number))

    def distance_sensor(self, serial_number: str):
        self.ensure_running()
        from .modules import DistanceSensorModule

        return DistanceSensorModule(
            self,
            serial_number=serial_number,
            snapshot=self.require_device(serial_number),
        )
    def color_sensor(self, serial_number: str):
        """Return the typed SDK wrapper for a ``color_module`` device."""
        self.ensure_running()
        from .modules import ColorSensorModule

        return ColorSensorModule(
            self,
            serial_number=serial_number,
            snapshot=self.require_device(serial_number),
        )
