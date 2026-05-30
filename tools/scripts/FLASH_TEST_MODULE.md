# Flash Runbook (ESP32-C3 Line Sensor Module)

This is the current direct ESP-IDF sequence for flashing `firmware/esp/modules/line_sensor_module`.

## 1. Stop the host relay

The host relay owns the serial port while it is running. Stop it before flashing.

```bash
# If running from a foreground shell, press Ctrl+C.
# If running as a service, stop that service first.
```

## 2. Confirm the device port

Linux:

```bash
ls -l /dev/ttyACM* /dev/ttyUSB*
```

Windows:

```powershell
python -m serial.tools.list_ports
```

## 3. Build and flash

```bash
cd firmware/esp/modules/line_sensor_module
idf.py set-target esp32c3
idf.py build
idf.py -p <PORT> flash
```

Replace `<PORT>` with the detected port, for example `/dev/ttyACM0` or `COM11`.

## 4. Restart the host relay

```bash
cd host/main
openrdk
```

Then check the UI:

```text
http://rdk.local:8765
```
