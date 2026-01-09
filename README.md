# Embedded Workspace (Pi + ESP-IDF)

## Layout
- .devcontainer/ : ESP-IDF dev container (build/flash/monitor)
- firmware/esp/modules/ : ESP-IDF firmware projects (one per module/device)
- host/pi/comms/ : Raspberry Pi runtime service (serial relay)
- docker-compose.yml : runtime stack on the Pi
- tools/scripts/ : convenience scripts

## Firmware (ESP)
Open the repo on the Raspberry Pi with VS Code Remote-SSH, then "Open Folder in Container".

Build:
- cd firmware/esp/modules/color_module
- . $IDF_PATH/export.sh
- idf.py set-target esp32
- idf.py build

Flash + monitor:
- idf.py -p /dev/ttyUSB0 flash monitor

## Pi Runtime
From repo root on the Pi:
- docker compose build
- docker compose up -d

Logs:
- docker compose logs -f comms
