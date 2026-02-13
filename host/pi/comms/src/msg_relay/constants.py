import os
from datetime import timedelta, timezone

DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_SERIAL_BAUD = 115200
READ_TIMEOUT_SEC = 1.0
RETRY_DELAY_SEC = 2.0
# Raw handshake bytes (host -> ESP and expected ESP -> host responses).
# 0x01 = SOH, 0x06 = ACK, 0x02 = STX (PING), 0x03 = ETX (PONG).
# 0x04 = EOT (module query), 0x05 = ENQ (module info frame prefix).
FRAME_SYNC_BYTES = b"\xAA\x55\xAA\x55"
HOST_MODULE_ID = 0x00
TEST_MODULE_ID = 0x11
HELLO_MESSAGE_BYTES = b"\x01"
HELLO_ACK_BYTES = b"\x06"
PING_MESSAGE_BYTES = b"\x02"
PONG_MESSAGE_BYTES = b"\x03"
MODULE_QUERY_MESSAGE_BYTES = b"\x04"
MODULE_INFO_PREFIX_BYTE = 0x05
HELLO_ACK_TIMEOUT_SEC = 3.0
PING_PONG_TIMEOUT_SEC = 2.0
MODULE_QUERY_TIMEOUT_SEC = 1.5
HELLO_READ_TIMEOUT_SEC = 0.25
HELLO_OPEN_DELAY_SEC = 0.2
MODULE_TYPE_MAX_BYTES = 64
COMM_HISTORY_MAX_ENTRIES = 20
KEEPALIVE_PING_INTERVAL_SEC = 2.0
KEEPALIVE_PING_TIMEOUT_SEC = 1.0
LOG_MAX_LINES = 20
LOG_TRIM_INTERVAL_SEC = 0.2

DEFAULT_SERVICE_LOG_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "msg-relay.log",
    )
)

# Timezone used for all stored timestamps.
# -180 minutes = UTC-03:00.
HOST_TIMEZONE_OFFSET_MINUTES = -180
HOST_TIMEZONE = timezone(timedelta(minutes=HOST_TIMEZONE_OFFSET_MINUTES))
HOST_TIMESTAMP_FORMAT = "%H:%M:%S"

WEBVIEW_HOST = "0.0.0.0"
WEBVIEW_PORT = 8765
WEBVIEW_REFRESH_SECONDS = 1.0

# Module type shown in DB/webview when firmware does not identify itself.
DEFAULT_MODULE_TYPE = "NOT-RDK-MODULE"
MODULE_ID_TO_TYPE = {
    TEST_MODULE_ID: "test_module",
}

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
