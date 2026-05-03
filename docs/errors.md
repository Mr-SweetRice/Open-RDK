# Errors

All SDK exceptions. Import from the top-level package.

```python
from openrdk import (
    RelayError,
    RuntimeNotStartedError,
    DeviceNotFoundError,
    DeviceOfflineError,
    UnsupportedModuleTypeError,
    ModuleTypeMismatchError,
    CommandFailedError,
    FlashError,
)
```

→ [CommsRuntime](runtime.md) · [BaseModule](base-module.md) · [Flashing](flashing.md)

---

## Hierarchy

```
RelayError
├── RuntimeNotStartedError
├── DeviceNotFoundError
├── DeviceOfflineError
├── UnsupportedModuleTypeError
├── ModuleTypeMismatchError
├── CommandFailedError
└── FlashError
```

Catching `RelayError` catches all SDK exceptions.

---

## `RelayError`
Base class for all openrdk exceptions. Catch this to handle any SDK error.

```python
from openrdk import RelayError
try:
    motor.forward(50)
except RelayError as exc:
    print(f"sdk error: {exc}")
```

---

## `RuntimeNotStartedError`
The runtime or webview thread is not alive when an operation that requires it is attempted.

**Raised by:** `ensure_running()`, module constructors (which call `ensure_running()` internally).

```python
runtime = CommsRuntime()
# runtime.start() not called yet
try:
    motor = runtime.traction("98:3D:AE:41:97:C4")
except RuntimeNotStartedError:
    runtime.start()
```

---

## `DeviceNotFoundError`
The requested serial number is not in the device registry, or a `serial_number` argument was empty.

**Raised by:** `require_device()`, module constructors, `send_raw_cmd()`, `send_raw_traction()`, `refresh()`, `flash_firmware()`.

```python
try:
    motor = runtime.traction("AA:BB:CC:DD:EE:FF")
except DeviceNotFoundError:
    print("device not seen yet — is it connected?")
```

---

## `DeviceOfflineError`
The device is in the registry but its status is not `"online connected"` at the time of an operation.

**Raised by:** `send_raw_cmd()`, `send_raw_traction()`, and any method that calls `_ensure_online()` internally (all movement and query methods on `TractionModule` and `LineSensorModule`).

```python
try:
    motor.forward(50)
except DeviceOfflineError:
    print("device is offline — check USB connection")
```

---

## `UnsupportedModuleTypeError`
The device's `module_type` is not recognized by `CommsRuntime.module()`.

**Raised by:** `CommsRuntime.module()`.

```python
try:
    mod = runtime.module("98:3D:AE:41:97:C4")
except UnsupportedModuleTypeError as exc:
    print(f"unknown firmware type: {exc}")
    # Flash the correct firmware first
```

---

## `ModuleTypeMismatchError`
A typed module class was instantiated for a device whose `module_type` does not match `EXPECTED_MODULE_TYPE`.

**Raised by:** `TractionModule.__init__()`, `LineSensorModule.__init__()`, `refresh()` if the type changes while the module is held.

```python
try:
    # This device is a line_sensor_module, not traction_module
    motor = runtime.traction("98:3D:AE:41:97:C4")
except ModuleTypeMismatchError as exc:
    print(f"wrong firmware: {exc}")
    # Use runtime.module() to get the correct type, or flash the right firmware
```

---

## `CommandFailedError`
The firmware returned an error response (`ERR ...`) or the response could not be parsed.

**Raised by:** any method that sends a command and checks the response — `send_raw_cmd()`, `send_raw_traction()`, `get_position_telemetry()`, `get_position_pid()`, `get_pid_rpm()`.

The exception message includes the `error_kind` and the raw firmware response.

```python
try:
    result = motor.send_raw_cmd("INVALID CMD")
except CommandFailedError as exc:
    print(f"command failed: {exc}")
```

---

## `FlashError`
A firmware flash operation failed — `esptool` not installed, binary missing, esptool non-zero exit, or device port unavailable.

**Raised by:** `flash_firmware()`, `flash_firmware_by_port()`.

```python
from openrdk import FlashError

try:
    runtime.flash_firmware("98:3D:AE:41:97:C4", "traction_module")
except FlashError as exc:
    print(f"flash failed: {exc}")
    # Check: esptool installed? BOOT button pressed? Device connected?
```
