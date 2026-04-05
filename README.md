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
