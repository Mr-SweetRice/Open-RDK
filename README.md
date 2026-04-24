# Embedded Workspace (Pi + ESP-IDF)

## Layout
- .devcontainer/ : ESP-IDF dev container (build/flash/monitor)
- firmware/esp/modules/ : ESP-IDF firmware projects (one per module/device)
- host/pi/comms/ : Raspberry Pi runtime service (serial relay)
- docker-compose.yml : runtime stack on the Pi
- tools/scripts/ : convenience scripts

## Firmware (ESP)
Open the repo on the Raspberry Pi with VS Code Remote-SSH, then "Open Folder in Container".

VS Code workspace:
- Open the repository root `Open-RDK`.
- The workspace is preconfigured for `firmware/esp/modules/traction_module`.
- On Windows, set `IDF_PATH` in your environment before opening VS Code so the workspace resolves the ESP-IDF installation automatically.

Build:
- cd firmware/esp/modules/traction_module
- . $IDF_PATH/export.sh
- idf.py set-target esp32c3
- idf.py build

Flash + monitor:
- idf.py -p /dev/ttyUSB0 flash monitor

Helper scripts:
- Linux/macOS: `./tools/scripts/build_firmware.sh` and `./tools/scripts/flash.sh`
- Windows PowerShell: `.\tools\scripts\build.ps1` and `.\tools\scripts\flash.ps1 -Port COM11`

The flash helpers always enter `firmware/esp/modules/traction_module`, recreate `sdkconfig` when it is missing, and delete a stale `build/` directory if it still points at an older absolute path.

## Pi Runtime
From repo root on the Pi:
- docker compose build
- docker compose up -d

Logs:
- docker compose logs -f comms

## Host Comms Architecture (Current Direction)
- The host comms system remains the single protocol engine for serial, handshake, keepalive, telemetry, and command transport.
- The next architecture step is to expose this engine as an importable Python package (SDK-first), so a new Pi can run user code without a pre-installed system service.
- The webview stays supported as a fallback/debug control surface, not as the primary control path.

Planned operation modes:
- Embedded mode (primary): user imports the package, starts the runtime in-process, and controls modules through Python classes.
- Webview mode (fallback): optional FastAPI/websocket UI uses the same core engine and remains compatible.

Planned SDK surface:
- Raw/expert access for direct command-level control.
- Sanitized module classes for application code (`TractionModule`, `LineSensorModule`, etc.), with safe methods and validation.

Development workflow lock:
- First define sanitized class methods and behavior contracts.
- Then implement backend wiring to match those contracts.
- Keep protocol behavior and existing webview compatibility stable while adding SDK support.

Detailed host-side architecture and workflow live in:
- `host/pi/comms/README.md`
