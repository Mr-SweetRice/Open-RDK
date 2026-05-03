# CommsRuntime

Main SDK entrypoint. Manages USB device discovery (udev), per-device keepalive threads, device registry, and the optional webview dashboard.

→ [BaseModule](base-module.md) · [TractionModule](traction-module.md) · [LineSensorModule](line-sensor-module.md) · [Flashing](flashing.md) · [Errors](errors.md)

---

## Constructor

```python
CommsRuntime(
    db_path: str | None = None,
    comms_log_path: str | None = None,
    poll_timeout_sec: float = 0.25,
    enable_webview: bool = True,
    enable_webview_updates: bool = True,
    webview_host: str | None = None,
    webview_port: int | None = None,
    auto_start: bool = False,
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `db_path` | SDK internal path | JSON file used as device registry |
| `comms_log_path` | SDK internal path | JSONL file for raw comms logging |
| `poll_timeout_sec` | `0.25` | udev monitor poll interval in seconds |
| `enable_webview` | `True` | Start the HTTP/WebSocket dashboard |
| `enable_webview_updates` | `True` | Enable realtime comms stream in dashboard |
| `webview_host` | `"0.0.0.0"` | Bind host for the webview server |
| `webview_port` | `8765` | Bind port for the webview server |
| `auto_start` | `False` | Call `start()` automatically in the constructor |

---

## Properties

### `is_running → bool`
`True` if the runtime background thread is alive.

### `is_webview_running → bool`
`True` if the uvicorn webview thread is alive.

### `webview_url → str`
URL built from the configured host and port: `http://<host>:<port>`.

### `webview_enabled → bool`
Whether the webview is configured (mirrors `enable_webview`).

### `webview_updates_enabled → bool`
Whether the realtime comms stream is enabled (mirrors `enable_webview_updates`).

### `db_path → str`
Absolute path to the device registry JSON file.

### `comms_log_path → str`
Absolute path to the comms log JSONL file.

### `lan_ip → str`
Best-guess LAN IP of the host machine. Falls back to `127.0.0.1`.

### `last_error → Exception | None`
Last unhandled exception from the runtime thread, or `None`.

### `last_webview_error → Exception | None`
Last unhandled exception from the webview thread, or `None`.

### `supported_firmware_types → list[str]`
Firmware types available for flashing. Currently `["traction_module", "line_sensor_module"]`.

---

## Lifecycle

### `start() → CommsRuntime`
Start the runtime thread and webview. Safe to call multiple times — no-op if already running.

```python
runtime = CommsRuntime()
runtime.start()
```

---

### `stop(timeout_sec: float = 2.0)`
Stop the runtime and webview. Cancels all keepalive threads and waits up to `timeout_sec` for them to exit.

```python
runtime.stop()
```

---

### `ensure_running() → CommsRuntime`
Calls `start()` then raises `RuntimeNotStartedError` if the runtime or webview is not alive. Used internally by module constructors.

```python
runtime.ensure_running()
```

---

### `post(post_option: str = "default")`
Print status to stdout.

| Value | Prints |
|-------|--------|
| `"default"` | runtime running, webview running |
| `"run"` | runtime running only |
| `"webview"` | webview running + hostname URL |
| `"webview_complete"` | webview running + all URLs (host, LAN) |

```python
runtime.post("webview_complete")
# webview running: True
# webview url (host): http://0.0.0.0:8765
# webview url (lan): http://192.168.1.10:8765
```

---

## Device Discovery

### `list_devices(verbose=None) → list[dict]`
Return all devices in the registry. Pass `verbose` to also print a summary.

| `verbose` value | Output |
|-----------------|--------|
| `None` / `False` | silent — returns list only |
| `True` / `"full"` | name, serial, type, status, port |
| `"names"` | assigned names only |
| `"serials"` | serial numbers only |
| `"status"` | name + status |

```python
devices = runtime.list_devices()                  # silent
devices = runtime.list_devices(verbose="full")    # full table
devices = runtime.list_devices(verbose="names")   # names only
devices = runtime.list_devices(verbose="serials") # serials only
devices = runtime.list_devices(verbose="status")  # name + status
```

Each dict contains: `serial_number`, `name`, `status`, `module_type`, `device_node`, `link_status`, `link_live`, `message_type`, `traction_out_value`, `error_count`, `last_error_kind`, `last_event_at`, `telemetry_requested`, `telemetry_active`.

---

### `get_device(serial_number: str) → dict | None`
Return a single device snapshot by serial number, or `None` if not found.

```python
device = runtime.get_device("98:3D:AE:41:97:C4")
if device:
    print(device["status"])
```

---

### `require_device(serial_number: str, wait_timeout_sec: float = 1.0) → dict`
Like `get_device` but polls until found or the timeout expires. Raises `DeviceNotFoundError` on timeout.

```python
device = runtime.require_device("98:3D:AE:41:97:C4", wait_timeout_sec=3.0)
```

---

### `find_device_by_serial(serial: str) → dict | None`
Exact match by serial number. Same as `get_device`.

---

### `find_device_by_name(name: str) → dict | None`
Case-insensitive match by assigned name.

```python
device = runtime.find_device_by_name("motor_left")
```

---

### `get_serial_by_name(name: str) → str | None`
Return the serial number of the first device matching `name`, or `None`.

```python
serial = runtime.get_serial_by_name("motor_left")
```

---

### `rename_device(serial: str, name: str) → dict | None`
Assign a persistent human-readable name to a device. Survives restarts.

```python
runtime.rename_device("98:3D:AE:41:97:C4", "motor_left")
```

---

## Module Factory

### `module(serial_number: str) → TractionModule | LineSensorModule`
Return the correct typed module based on the device's detected `module_type`. Raises `UnsupportedModuleTypeError` if the type is not recognized.

```python
mod = runtime.module("98:3D:AE:41:97:C4")
```

---

### `traction(serial_number: str) → TractionModule`
Return a `TractionModule` without checking `module_type`.

```python
motor = runtime.traction("98:3D:AE:41:97:C4")
```

---

### `line_sensor(serial_number: str) → LineSensorModule`
Return a `LineSensorModule` without checking `module_type`.

```python
sensor = runtime.line_sensor("98:3D:AE:41:97:C4")
```

---

## Flashing

### `flash_firmware(serial_number, firmware_type, baud=460800, on_output=None) → dict`
### `flash_firmware_by_port(device_node, firmware_type, baud=460800, on_output=None) → dict`

See [Flashing](flashing.md) for full reference and the required BOOT button workflow.

---

## Full Example

```python
from openrdk import CommsRuntime, DeviceNotFoundError

runtime = CommsRuntime(
    auto_start=True,
    enable_webview=True,
    enable_webview_updates=True,
)
runtime.post("webview_complete")

try:
    # First-time setup: assign name
    runtime.rename_device("98:3D:AE:41:97:C4", "motor_left")

    motor = runtime.traction(runtime.get_serial_by_name("motor_left"))
    motor.forward(50)

    input("press Enter to quit\n")

except DeviceNotFoundError as exc:
    print(f"device not found: {exc}")
finally:
    runtime.stop()
```
