# LineSensorModule

Controls a line sensor module. Inherits all properties and raw helpers from [BaseModule](base-module.md).

```python
sensor = runtime.line_sensor("98:3D:AE:41:97:C4")
# or
sensor = runtime.module("98:3D:AE:41:97:C4")  # auto-detects type
```

→ [CommsRuntime](runtime.md) · [BaseModule](base-module.md) · [Errors](errors.md)

---

## Class Constant

### `EXPECTED_MODULE_TYPE = "line_sensor_module"`

---

## Methods

### `get_info(timeout_sec=1.5) → dict`
Send `GET INFO` in CMD mode. Returns firmware identification and configuration info.

Return shape matches `send_raw_cmd`: `ok`, `response`, `command`, `latency_ms`.

```python
info = sensor.get_info()
print(info["response"])
```

---

### `get_data(timeout_sec=1.5) → dict`
Send `GET DATA` in CMD mode. Returns a single sensor reading snapshot.

```python
data = sensor.get_data()
print(data["response"])
```

---

### `start_telemetry() → dict`
Switch the device to `TELEMETRY` message type and request continuous telemetry streaming.

The keepalive loop will begin requesting telemetry frames from the firmware automatically once this is called.

Returns the updated registry dict for this device.

```python
sensor.start_telemetry()
```

---

### `stop_telemetry() → dict`
Stop the telemetry stream and keep the device in `TELEMETRY` message type.

To send commands again after stopping telemetry, call `send_raw_cmd` (which will switch the device back to `CMD` mode automatically).

Returns the updated registry dict.

```python
sensor.stop_telemetry()
```

---

## Example

```python
import time
from openrdk import CommsRuntime

runtime = CommsRuntime(auto_start=True, enable_webview=False)
sensor = runtime.line_sensor("98:3D:AE:41:97:C4")

print(sensor.get_info()["response"])

# One-shot read
data = sensor.get_data()
print(data["response"])

# Start streaming
sensor.start_telemetry()
time.sleep(3)
sensor.stop_telemetry()

runtime.stop()
```
