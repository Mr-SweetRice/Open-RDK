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
      "module_type": "test_module",
      "firmware_module": "test_module",
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

## Notes
- This project is host-side and does not require Docker for normal operation.
- Device JSON is intentionally not used for raw comms history.
- Raw comms is append-only NDJSON and optimized for stream consumption.
