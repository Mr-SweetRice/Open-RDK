import queue
import threading

from ..constants import (
    COMMS_LOG_QUEUE_MAX,
    DEFAULT_ACTIVE_MESSAGE_TYPE,
    DEFAULT_COMMS_LOG_PATH,
    DEFAULT_SERIAL_BAUD,
)

_DB_LOCK = threading.RLock()
_COMMS_LOG_LOCK = threading.Lock()
_MONITOR_LOCK = threading.Lock()

_KEEPALIVE_STOPS: dict[str, threading.Event] = {}
_KEEPALIVE_THREADS: dict[str, threading.Thread] = {}
_KEEPALIVE_WAKES: dict[str, threading.Event] = {}

_TRACTION_OUT_LOCK = threading.Lock()
_TRACTION_OUT_PENDING: dict[str, dict] = {}
_TRACTION_OUT_REQUEST_ID = 0

_CMD_REQUEST_LOCK = threading.Lock()
_CMD_REQUEST_PENDING: dict[str, dict] = {}
_CMD_REQUEST_ID = 0

_COMMS_LOG_PATH: str = DEFAULT_COMMS_LOG_PATH
_COMMS_LOG_QUEUE: queue.Queue[str] = queue.Queue(maxsize=max(256, int(COMMS_LOG_QUEUE_MAX)))
_COMMS_LOG_WRITER_THREAD: threading.Thread | None = None
_COMMS_LOG_WRITER_STOP = threading.Event()

_FLASH_LOCK = threading.Lock()
_FLASH_LOCKED_SERIALS: set[str] = set()
_FLASH_LOCKED_NODES: set[str] = set()

_LATEST_LS_LOCK = threading.Lock()
_LATEST_LS_FRAMES: dict[str, tuple[float, str]] = {}  # serial -> (monotonic_ts, raw_text)

_LATEST_DS_LOCK = threading.Lock()
_LATEST_DS_FRAMES: dict[str, tuple[float, str]] = {}  # serial -> (monotonic_ts, raw_text)

_ACTIVE_MESSAGE_TYPE: str = DEFAULT_ACTIVE_MESSAGE_TYPE
_ACTIVE_SERIAL_BAUD: int = DEFAULT_SERIAL_BAUD
_ACTIVE_MESSAGE_LOCK = threading.Lock()
_ACTIVE_SERIAL_LOCK = threading.Lock()

_DEVICE_DB_FIELDS = (
    "serial_number",
    "name",
    "status",
    "device_node",
    "message_type",
    "module_type",
    "firmware_module",
    "firmware_version",
    "expected_page",
    "expected_page_version",
    "module_id",
    "module_id_hex",
    "link_live",
    "link_status",
    "last_event_at",
    "last_link_check_at",
    "error_count",
    "last_error_kind",
    "last_error_at",
    "telemetry_requested",
    "telemetry_active",
    "traction_out_value",
)


def _wake_keepalive_monitor(serial_number: str | None):
    if not serial_number:
        return
    with _MONITOR_LOCK:
        wake_event = _KEEPALIVE_WAKES.get(serial_number)
    if wake_event is not None:
        wake_event.set()
