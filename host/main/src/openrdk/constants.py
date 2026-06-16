import os
from dataclasses import dataclass
from datetime import timedelta, timezone

DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_SERIAL_BAUD = 512000
READ_TIMEOUT_SEC = 1.0
RETRY_DELAY_SEC = 2.0
# Legacy handshake/control bytes kept for attach probing.
# 0x01 = SOH, 0x06 = ACK, 0x04 = EOT (module query), 0x05 = ENQ (module info prefix).
FRAME_SYNC_BYTES = b"\xAA\x55\xAA\x55"
HOST_MODULE_ID = 0x00
TRACTION_MODULE_ID = 0x11
LINE_SENSOR_MODULE_ID = 0x12
COLOR_MODULE_ID = 0x13
HELLO_MESSAGE_BYTES = b"\x01"
HELLO_ACK_BYTES = b"\x06"
MODULE_QUERY_MESSAGE_BYTES = b"\x04"
MODULE_INFO_PREFIX_BYTE = 0x05
HELLO_ACK_TIMEOUT_SEC = 3.0
MODULE_QUERY_TIMEOUT_SEC = 1.5
HELLO_READ_TIMEOUT_SEC = 0.25
HELLO_OPEN_DELAY_SEC = 0.8
MODULE_TYPE_MAX_BYTES = 64
STREAM_READ_TIMEOUT_SEC = 0.001
STREAM_WRITE_TIMEOUT_SEC = 0.30


@dataclass(frozen=True)
class RelayMessageType:
    name: str
    code: int
    default_content: str
    ack_content: str


MESSAGE_TYPE_CMD = "CMD"
MESSAGE_TYPE_TEST = "TEST"
MESSAGE_TYPE_TELEMETRY = "TELEMETRY"
MESSAGE_TYPE_CONTROL = "CONTROL"
# Backward-compatible alias for older code and device registries.
MESSAGE_TYPE_TRACTION_OUT = MESSAGE_TYPE_CONTROL
MESSAGE_TYPE_ALIASES = {
    "TRACTION_OUT": MESSAGE_TYPE_CONTROL,
    "MODULE_CONTROL": MESSAGE_TYPE_CONTROL,
    "MODULE_CMD": MESSAGE_TYPE_CONTROL,
}

MESSAGE_TYPES = {
    MESSAGE_TYPE_CMD: RelayMessageType(
        name=MESSAGE_TYPE_CMD,
        code=0x01,
        default_content="COMMAND",
        ack_content="I RECIEVED CMD",
    ),
    MESSAGE_TYPE_TEST: RelayMessageType(
        name=MESSAGE_TYPE_TEST,
        code=0x02,
        default_content="TESTING",
        ack_content="I RECIEVED TEST",
    ),
    MESSAGE_TYPE_TELEMETRY: RelayMessageType(
        name=MESSAGE_TYPE_TELEMETRY,
        code=0x03,
        default_content="TELEMETRY",
        ack_content="I RECIEVED TELEMETRY",
    ),
    MESSAGE_TYPE_CONTROL: RelayMessageType(
        name=MESSAGE_TYPE_CONTROL,
        code=0x04,
        default_content="CONTROL",
        ack_content="OK",
    ),
}

MESSAGE_TYPE_CODE_TO_NAME = {
    spec.code: spec.name for spec in MESSAGE_TYPES.values()
}
MESSAGE_TYPE_DEFAULT_BYTES = {
    name: spec.default_content.encode("utf-8")
    for name, spec in MESSAGE_TYPES.items()
}
DEFAULT_ACTIVE_MESSAGE_TYPE = MESSAGE_TYPE_CMD
TELEMETRY_START_COMMAND = "TELEMETRY_START"
TELEMETRY_SYNC_COMMAND = "TELEMETRY_SYNC"
TELEMETRY_STOP_COMMAND = "TELEMETRY_STOP"
TELEMETRY_START_BYTES = TELEMETRY_START_COMMAND.encode("utf-8")
TELEMETRY_SYNC_BYTES = TELEMETRY_SYNC_COMMAND.encode("utf-8")
TELEMETRY_STOP_BYTES = TELEMETRY_STOP_COMMAND.encode("utf-8")
TRACTION_OUT_MIN_VALUE = -100
TRACTION_OUT_MAX_VALUE = 100
TRACTION_OUT_DEFAULT_VALUE = 0
TRACTION_OUT_COMMAND_PREFIX = "SET OUT"
FRAME_LEN_MAX = 200
FRAME_RX_BUFFER_MAX_BYTES = 4096
FRAME_KEEPALIVE_INTERVAL_SEC = 2.0
FRAME_RESPONSE_TIMEOUT_SEC = 1.0
FRAME_MAX_RETRY_ATTEMPTS = 3
FRAME_RETRY_DELAY_SEC = 0.05
FRAME_TELEMETRY_POLL_INTERVAL_SEC = 0.005
FRAME_TELEMETRY_IDLE_SLEEP_SEC = 0.002
FRAME_TELEMETRY_SYNC_INTERVAL_SEC = 2.0
FRAME_SEQUENCE_MIN = 0
FRAME_SEQUENCE_MAX = 16_777_215
FRAME_SEQUENCE_BYTES = 3
FRAME_READER_QUEUE_MAX = 4096
COMMS_LOG_QUEUE_MAX = 8192
COMMS_LOG_WRITER_POLL_SEC = 0.05
BAUD_RATE_MIN = 1200
BAUD_RATE_MAX = 3000000
COMMON_BAUD_RATES = (
    9600,
    19200,
    38400,
    57600,
    115200,
    230400,
    460800,
    512000,
    921600,
    1000000,
    1500000,
    2000000,
    2500000,
    3000000,
)
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_TRIM_INTERVAL_SEC = 1.0

DEFAULT_SERVICE_LOG_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "msg-relay.log",
    )
)

DEFAULT_COMMS_LOG_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "comms-raw.log",
    )
)

# Timezone used for all stored timestamps.
# -180 minutes = UTC-03:00.
HOST_TIMEZONE_OFFSET_MINUTES = -180
HOST_TIMEZONE = timezone(timedelta(minutes=HOST_TIMEZONE_OFFSET_MINUTES))
HOST_TIMESTAMP_FORMAT = "%H:%M:%S"
# Module type shown in DB when firmware does not identify itself.
DEFAULT_MODULE_TYPE = "NOT-RDK-MODULE"
MODULE_ID_TO_TYPE = {
    TRACTION_MODULE_ID: "traction_module",
    LINE_SENSOR_MODULE_ID: "line_sensor_module",
    COLOR_MODULE_ID: "color_module",
}

WEBVIEW_HOST = "0.0.0.0"
WEBVIEW_PORT = 8765

DEFAULT_DEVICE_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "espressif_devices.json",
)
STATUS_ONLINE_CONNECTED = "online connected"
STATUS_OFFLINE_DISCONNECTED = "offline disconnected"
LINK_STATUS_LIVE = "live"
LINK_STATUS_NOT_LIVE = "not live"

# NÚMERO SERIAL ESP-32 98:3D:AE:41:97:C4
SERIAL_NUMBER_ESP32_COMPLETE = "Espressif_USB_JTAG_serial_debug_unit_98:3D:AE:41:97:C4"
SERIAL_NUMBER_ESP32_SHORT = "98:3D:AE:41:97:C4"
ID_VENDOR_ESP32 = "303a"
ID_MODEL_ESP32 = "1001"
MANUFACTURER_ESP32 = "Espressif"

# Linux USB topology branches that Open-RDK must not probe for RDK modules.
# Camera branch reported on the Raspberry Pi top-right USB port:
# usb1/1-1/1-1.1/1-1.1:1.0/video4linux/video0
DEFAULT_USB_DENY_PATH_PREFIXES = ("1-1.1",)
USB_DENY_PATH_PREFIXES_ENV = "OPENRDK_USB_DENY_PATH_PREFIXES"
