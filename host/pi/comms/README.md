# RDK Host Message Relay (`host/pi/comms`)

## Purpose
This service runs on the **host (Raspberry Pi)** and handles:
- ESP device attach/detach tracking (`pyudev`)
- Serial handshake + keepalive (`pyserial`)
- Device registry persistence (`espressif_devices.json`)
- Raw communication persistence (`comms-raw.log`)
- Live monitoring UI (`FastAPI` + WebSocket + browser frontend)

## Runtime Architecture
At runtime (`python3 -m msg_relay.run`):
1. Starts service log trimming (`msg-relay.log`, size based).
2. Writes one run-access snapshot to `runpy-access.log`.
3. Resolves comms log path and configures comms DB writer.
4. Starts FastAPI web server in a thread.
5. Starts udev/serial relay loop in main thread.

Web stream architecture:
- `log thread`: tails `comms-raw.log`
- `UI stream thread`: distributes events to websocket clients using queues
- frontend: subscribes to `/ws/comms` and updates view live

## Architecture Direction (SDK-First, Webview as Fallback)
This project is moving to an SDK-first architecture while preserving current behavior:
- Core protocol engine stays in `msg_relay.functions` (serial attach/detach, handshake, framed comms, keepalive, command send/ack).
- Webview remains available as a fallback test/control UI, using the same engine.
- New primary integration path will be importable Python classes for external code on a fresh Pi.

Target usage on a new Pi:
- Install package.
- Import `msg_relay`.
- Start runtime from user code (no mandatory pre-installed system service).
- Instantiate module-specific objects and call sanitized methods.

Compatibility requirement:
- Existing webview routes and current protocol behavior must not break during SDK introduction.

## Planned Public SDK Layers
Two public layers will coexist:
- Expert/raw layer: direct command send helpers for advanced developers.
- Sanitized module layer: typed classes with safe methods per module type.

Initial module-class concept:
- `TractionModule`
- `LineSensorModule`

Class behavior contract:
- Resolve/validate module identity at object creation.
- Ensure correct message type before sending.
- Expose only relevant methods for that module.
- Parse and normalize responses.

Example sanitized traction methods:
- `forward(value)` maps to `SET OUT <value>` in `TRACTION_OUT` mode.
- `forward_raw(value)` maps to `SET OUT RAW <value>`.
- `stop()` maps to `CLR OUT` (or equivalent safe zero-output behavior).

## Implementation Workflow Lock
From now on, changes follow this sequence:
1. Define sanitized class method list and method contracts first.
2. Freeze method names, argument rules, return shape, and error behavior.
3. Implement backend wiring to satisfy those contracts.
4. Keep webview as validation/fallback path to detect regressions.

Current status:
- Documentation and architecture alignment phase.
- SDK bootstrap implementation started:
  - in-process runtime class (`src/msg_relay/runtime.py`)
  - sanitized module classes (`src/msg_relay/modules.py`)
  - installable package metadata (`pyproject.toml`)

## SDK Quick Start (In-Process, No System Service Required)
Install from this folder:
```bash
cd host/pi/comms
pip install .
```

Minimal usage:
```python
from msg_relay import RelayRuntime

runtime = RelayRuntime(
    auto_start=True,            # starts comms runtime thread
    enable_webview=True,        # starts webview server thread
    enable_webview_updates=True # enables /api/comms + /ws/comms realtime broker
)
devices = runtime.list_devices()
serial = devices[0]["serial_number"]

traction = runtime.traction(serial)
traction.forward(30)
traction.backward(20)
traction.move_angle("forward", 45)
traction.forward_raw(15)
traction.stop()
```

`move_angle(...)` behavior:
- If position PID is disabled, angle is added to current position.
- If position PID is already enabled, angle is added to current target (incremental chaining).

Resource-saving options:
- `enable_webview=False`: comms runtime only, no web server.
- `enable_webview_updates=False`: keep HTTP webview alive, disable realtime stream broker.

Legacy webview entrypoint remains available:
```bash
cd host/pi/comms
PYTHONPATH=src python3 -m msg_relay.run
```

## Data Contracts
### Devices DB (`src/msg_relay/espressif_devices.json`)
Only device state is stored here:
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

`name` behavior:
- Default value is firmware/module type.
- You can rename from web UI.
- Name persists per serial number while firmware/module type stays the same.
- If firmware/module type changes, name is reset to the new module type.

### Comms DB (`comms-raw.log`)
NDJSON; one event per line:
```json
{"sender":"host","raw_hex":"aa55aa550002"}
{"sender":"98:3D:AE:41:97:C4","raw_hex":"aa55aa551103"}
```

## API Surface
Base URL: `http://<host-ip>:8765`

- `GET /api/health`
- `GET /api/devices`
- `POST /api/devices/{serial_number}/name` with body `{"name":"custom-label"}`
- `GET /api/comms?limit=300&serial=<optional>`
- `WS /ws/comms`
- `GET /` (web UI)

## Directory Structure
```text
host/pi/comms/
├── requirements.txt
├── msg-relay.log
├── comms-raw.log
├── runpy-access.log
├── track_runpy_access.sh
└── src/msg_relay/
    ├── run.py
    ├── constants.py
    ├── functions.py
    ├── webview.py
    ├── espressif_devices.json
    └── web/
        ├── index.html
        ├── app.js
        └── styles.css
```

## Service / Run
Local run:
```bash
cd host/pi/comms
PYTHONPATH=src python3 -m msg_relay.run
```

User systemd service:
```bash
systemctl --user restart msg-relay.service
systemctl --user status msg-relay.service
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

## Notes
- This project is host-side and does not require Docker for normal operation.
- Device JSON is intentionally not used for raw comms history.
- Raw comms is append-only NDJSON and optimized for stream consumption.
