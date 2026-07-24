# RDK Host Comms (`host/main`)

Current Python package version: `openrdk` 0.2.0.

## Purpose
This service runs on the **host (Raspberry Pi)** and handles:
- ESP device attach/detach tracking (`pyudev`)
- Serial handshake + keepalive (`pyserial`)
- Device registry persistence (`espressif_devices.json`)
- Raw communication persistence (`comms-raw.log`)
- Live monitoring UI (`FastAPI` + WebSocket + browser frontend)

## Two Ways to Run

### SDK-first (embedded in your own Python script)
```python
from openrdk import CommsRuntime

runtime = CommsRuntime(
    auto_start=True,
    enable_webview=True,
    enable_webview_updates=True,
)
devices = runtime.list_devices()
traction = runtime.traction(devices[0]["serial_number"])
traction.forward(30)
traction.stop()
```

### Standalone process (CLI / service)
```bash
cd host/main
pip install .
PYTHONPATH=src python3 -m openrdk.ordk_run
# or after install:
openrdk
```

## Architecture
At runtime `ordk_run.py`:
1. Resolves paths and starts log trimmer (`msg-relay.log`, size-capped).
2. Writes one run-access snapshot to `runpy-access.log`.
3. Configures comms log path.
4. Starts FastAPI webview in a background thread.
5. Starts udev/serial relay loop in main thread.

Web stream:
- `log thread`: tails `comms-raw.log`
- `UI stream thread`: fans events to WebSocket clients
- Frontend: subscribes to `/ws/comms`, updates live

## SDK Layers
- **Sanitized layer**: `TractionModule`, `LineSensorModule`, `DistanceSensorModule` — typed classes, safe methods.
- **Raw layer**: `send_raw_cmd`, `send_raw_control` — for advanced use. `send_raw_traction` remains as a compatibility alias.

## Web UI (`src/openrdk/web_new`)

### Files
- `index.html`: shell layout (device panel + comms panel)
- `static/app.js`: state, websocket consumer, device selection, render logic
- `static/theme.css`: shared UI theme
- `line-sensor.html`, `distance-sensor.html`, `traction-motor-config.html`, `traction-pid-tuner.html`, `traction-position-tuner.html`, `color.html`: module tools served by the host relay

### Behavior
- Device list shows editable display name, serial number, module type, link/status, tty port (`device_node`).
- Rename updates host-side JSON (`name` field) — no firmware change required.
- Clicking a device filters comms panel to that device's events + host TX.
- Comms panel shows line number, direction (TX/RX), sender, raw hex.
- Auto-scrolls when viewer is already at bottom.

### Frontend constraints
- Dependency-free: vanilla JS/CSS/HTML only.
- Incremental rendering — no full-page refresh loops.

## Data Contracts
### Devices DB (`src/openrdk/espressif_devices.json`)
```json
{
  "devices": [
    {
      "serial_number": "98:3D:AE:41:97:C4",
      "name": "left-front-esp",
      "status": "online connected",
      "device_node": "/dev/ttyACM0",
      "module_type": "line_sensor_module",
      "firmware_module": "line_sensor_module",
      "module_id": 17,
      "module_id_hex": "0x11",
      "link_live": true,
      "link_status": "live",
      "last_event_at": "12:34:56",
      "last_link_check_at": "12:34:57"
    }
  ]
}
```
`name` persists per serial number while module type is unchanged. Resets on firmware type change.

### Comms DB (`comms-raw.log`)
NDJSON, one event per line:
```json
{"sender":"host","raw_hex":"aa55aa550002"}
{"sender":"98:3D:AE:41:97:C4","raw_hex":"aa55aa551103"}
```

## API Surface
Base URL: `http://<host-ip>:8765`

- `GET /api/health`
- `GET /api/devices`
- `POST /api/devices/{serial_number}/name` — body `{"name":"label"}`
- `POST /api/devices/{serial_number}/config/message-type` — `CMD`, `TEST`, `TELEMETRY`, or `CONTROL`
- `POST /api/devices/{serial_number}/cmd/send`
- `POST /api/devices/{serial_number}/traction-out/send` — sends motor/control payloads through `CONTROL (0x04)`
- `POST /api/devices/{serial_number}/telemetry/start`
- `POST /api/devices/{serial_number}/telemetry/stop`
- `POST /api/devices/{serial_number}/line-sensor/config`
- `POST /api/devices/{serial_number}/line-sensor/calibration/start`
- `POST /api/devices/{serial_number}/line-sensor/calibration`
- `GET /api/devices/{serial_number}/distance-sensor/snapshot`
- `POST /api/devices/{serial_number}/distance-sensor/refresh`
- `POST /api/devices/{serial_number}/distance-sensor/config`
- `POST /api/devices/{serial_number}/distance-sensor/selftest`
- `POST /api/devices/{serial_number}/distance-sensor/stream/start`
- `POST /api/devices/{serial_number}/distance-sensor/stream/stop`
- `GET /api/comms?limit=300&serial=<optional>`
- `WS /ws/comms`
- `GET /` — web UI

## Directory Structure
```text
host/main/
├── pyproject.toml
├── msg-relay.log
├── comms-raw.log
├── runpy-access.log
├── track_runpy_access.sh
└── src/openrdk/
    ├── __init__.py
    ├── ordk_run.py
    ├── ordk_runtime.py
    ├── constants.py
    ├── functions/
    │   ├── _state.py
    │   ├── comms_log.py
    │   ├── registry.py
    │   ├── framing.py
    │   ├── transport.py
    │   ├── keepalive.py
    │   └── udev.py
    ├── modules.py
    ├── errors.py
    ├── webview.py
    ├── tls.py
    └── web_new/
        ├── index.html
        ├── line-sensor.html
        ├── traction-motor-config.html
        └── static/
```

## Environment Variables
- `ESPRESSIF_DEVICE_DB_PATH`
- `MSG_RELAY_COMMS_LOG_PATH`
- `MSG_RELAY_LOG_PATH`
- `MSG_RELAY_LOG_MAX_BYTES`
- `MSG_RELAY_LOG_TRIM_INTERVAL_SEC`
- `MSG_RELAY_WEB_HOST`
- `MSG_RELAY_WEB_PORT`
- `MSG_RELAY_ENABLE_WEBVIEW` (`true`/`false`, default `true`)
- `MSG_RELAY_ENABLE_WEBVIEW_UPDATES` (`true`/`false`, default `true`)
- `MSG_RELAY_ENABLE_MDNS` (`true`/`false`, default `true`)
- `MSG_RELAY_MDNS_NAME` (default `rdk`, advertises `http://rdk.local:8765`)
- `MSG_RELAY_ENABLE_HTTP_REDIRECT` (`true`/`false`, default `true`; redirects `http://rdk.local` to the webview port when port 80 is available)
- `MSG_RELAY_ENABLE_HTTPS` (`true`/`false`, default `false`; serves the web UI over HTTPS when enabled)
- `MSG_RELAY_TLS_CERT_FILE` and `MSG_RELAY_TLS_KEY_FILE` (optional existing cert/key paths; otherwise a local self-signed certificate for `rdk.local`, `localhost`, and local IPs is generated under `host/main/certs/`)
- `OPENRDK_USB_DENY_PATH_PREFIXES` (comma-separated Linux USB topology prefixes that Open-RDK must never probe; defaults to `1-1.1` to avoid the camera branch on the top-right Raspberry Pi USB port. Set to an empty value to disable.)

## Notes
- Does not require Docker. Legacy Docker setup is in `host/legacy_runtime/`.
- Device JSON is not used for raw comms history.
- Raw comms is append-only NDJSON optimized for stream consumption.
