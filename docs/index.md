# openrdk SDK

Python SDK for controlling ESP32-C3 modules over USB serial on Linux.

## Run

```bash
PYTHONPATH=host/main/src python3 your_script.py
```

## Quickstart

```python
from openrdk import CommsRuntime

runtime = CommsRuntime(auto_start=True, enable_webview=True)
runtime.post("webview_complete")

runtime.rename_device("98:3D:AE:41:97:C4", "motor_left")
motor = runtime.traction(runtime.get_serial_by_name("motor_left"))

motor.forward(60)

input("press Enter to quit\n")
runtime.stop()
```

## Reference

| Document | Contents |
|----------|----------|
| [CommsRuntime](runtime.md) | Constructor, lifecycle, device discovery, module factory |
| [BaseModule](base-module.md) | Shared properties and raw command helpers |
| [TractionModule](traction-module.md) | Motor control, PID, angle movement |
| [LineSensorModule](line-sensor-module.md) | Sensor reads and telemetry |
| [Flashing](flashing.md) | Firmware flash via serial, BOOT button workflow |
| [Errors](errors.md) | All exception types and when they are raised |
