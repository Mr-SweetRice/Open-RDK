# BaseModule

Base class for all module types. Holds the device reference, validates online state, and exposes raw serial command helpers.

Inherited by [TractionModule](traction-module.md) and [LineSensorModule](line-sensor-module.md).

→ [CommsRuntime](runtime.md) · [Errors](errors.md)

---

## Constructor

Not instantiated directly. Use [`CommsRuntime.traction()`](runtime.md#tractionserialnumber--tractionmodule), [`CommsRuntime.line_sensor()`](runtime.md#line_sensorserialnumber--linesensormodule), or [`CommsRuntime.module()`](runtime.md#moduleserialnumber--tractionmodule--linesensormodule).

```python
# Subclass signature (same for all module types):
TractionModule(runtime, serial_number, snapshot=None)
LineSensorModule(runtime, serial_number, snapshot=None)
```

| Parameter | Description |
|-----------|-------------|
| `runtime` | The running `CommsRuntime` instance |
| `serial_number` | Device serial (MAC format, e.g. `"98:3D:AE:41:97:C4"`) |
| `snapshot` | Optional pre-fetched registry dict — skips a registry read if provided |

Raises `DeviceNotFoundError` if the serial is not in the registry.  
Raises `ModuleTypeMismatchError` if the detected `module_type` does not match `EXPECTED_MODULE_TYPE`.

---

## Class Attribute

### `EXPECTED_MODULE_TYPE: str | None`
Set on each subclass. The registry's `module_type` must match when the device is online.

| Class | Value |
|-------|-------|
| `BaseModule` | `None` (no check) |
| `TractionModule` | `"traction_module"` |
| `LineSensorModule` | `"line_sensor_module"` |

---

## Properties

### `serial_number → str`
The device serial number this instance is bound to.

### `module_type → str`
The `module_type` field from the last registry snapshot.

### `status → str`
The `status` field from the last registry snapshot. Typical values: `"online connected"`, `"offline disconnected"`.

### `is_online → bool`
`True` when `status == "online connected"`.

---

## Methods

### `refresh(snapshot: dict | None = None) → dict`
Re-read the registry and update the internal snapshot. Returns the updated snapshot dict.

- If `snapshot` is provided it is used directly (no registry read).
- Raises `DeviceNotFoundError` if the serial is no longer in the registry.
- Raises `ModuleTypeMismatchError` if the type changed to an incompatible value.

```python
state = motor.refresh()
print(state["link_live"])
```

---

### `send_raw_cmd(command, timeout_sec=1.5, retries=2, retry_delay_sec=0.08) → dict`
Send a text command in CMD mode and wait for the firmware ACK.

Switches the device to `CMD` message type before sending.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `command` | — | Command string to send (e.g. `"GET INFO"`) |
| `timeout_sec` | `1.5` | Per-attempt wait for ACK |
| `retries` | `2` | Total attempts (1 = no retry) |
| `retry_delay_sec` | `0.08` | Delay between retries |

Returns a dict with at least:

| Key | Type | Description |
|-----|------|-------------|
| `ok` | `bool` | `True` on success |
| `response` | `str` | Firmware ACK text |
| `command` | `str` | Echo of the sent command |
| `latency_ms` | `float \| None` | Round-trip time |
| `error_kind` | `str` | Present only on failure |

Raises `CommandFailedError` on all failures after retries are exhausted.

```python
result = motor.send_raw_cmd("GET INFO")
print(result["response"])
```

---

### `send_raw_traction(command, timeout_sec=1.5) → dict`
Send a text command in TRACTION_OUT mode and wait for ACK.

Switches the device to `TRACTION_OUT` message type before sending. No retry logic.

```python
result = motor.send_raw_traction("CLR OUT")
```

Return shape is the same as `send_raw_cmd`.

---

## Example

```python
from openrdk import CommsRuntime

runtime = CommsRuntime(auto_start=True, enable_webview=False)

motor = runtime.traction("98:3D:AE:41:97:C4")

print(motor.serial_number)   # 98:3D:AE:41:97:C4
print(motor.module_type)     # traction_module
print(motor.is_online)       # True

state = motor.refresh()
print(state["link_live"])

result = motor.send_raw_cmd("GET INFO")
print(result["response"])
```
