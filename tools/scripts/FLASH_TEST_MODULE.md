# Flash Runbook (ESP32-C3 Line Sensor Module)

This is the reproducible sequence used to flash `firmware/esp/modules/line_sensor_module` on an ESP32-C3 from this repo.

## 1) Confirm device port

```bash
ls -l /dev/ttyACM* /dev/ttyUSB*
```

Expected in this setup: `/dev/ttyACM0`.

## 2) Stop host comms service (free the serial port)

From repo root:

```bash
docker compose stop comms
```

## 3) Verify ESP-IDF container image

This workspace already had `rdk-idf-dev:latest` with ESP-IDF v5.2.2.

```bash
docker images | grep rdk-idf-dev
docker run --rm --privileged --network=host \
  -v /dev:/dev -v "$PWD":/work -w /work \
  rdk-idf-dev:latest \
  bash -lc 'source /opt/esp/idf/export.sh >/dev/null && idf.py --version'
```

## 4) Flash line sensor firmware (ESP32-C3)

```bash
docker run --rm --privileged --network=host \
  -v /dev:/dev -v "$PWD":/work -w /work \
  rdk-idf-dev:latest \
  bash -lc 'source /opt/esp/idf/export.sh >/dev/null && \
    bash tools/scripts/flash.sh firmware/esp/modules/line_sensor_module /dev/ttyACM0'
```

Successful flash indicator from output:
- `Hash of data verified.`
- `Hard resetting via RTS pin...`

Note: in non-interactive shells, `flash.sh` may report a monitor TTY warning after flashing. Flash is still complete.

## 5) Optional monitor (interactive, short check)

```bash
docker run --rm -it --privileged --network=host \
  -v /dev:/dev -v "$PWD":/work -w /work \
  rdk-idf-dev:latest \
  bash -lc 'source /opt/esp/idf/export.sh >/dev/null && \
    cd firmware/esp/modules/line_sensor_module && idf.py -p /dev/ttyACM0 monitor'
```

Exit monitor with `Ctrl+]`.

## 6) Bring host comms/webview back

```bash
docker compose up -d comms
curl http://127.0.0.1:8765/api/health
```

Expected health response:

```json
{"ok":true}
```
