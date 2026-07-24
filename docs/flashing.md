# Flashing

Flash ESP32-C3 firmware from within the SDK. Requires `esptool` installed in the same Python environment.

```bash
pip install "esptool>=4.9,<6"
```

→ [CommsRuntime](runtime.md) · [Errors](errors.md)

---

## BOOT Button Workflow

The ESP32-C3 on this hardware requires manual entry into ROM download mode before flashing. Auto-reset via DTR/RTS is not reliable on this board.

1. Hold the **BOOT** button on the module
2. Press and release **RESET** (or replug USB while holding BOOT)
3. Release **BOOT** — the chip is now in ROM download mode
4. Call `flash_firmware(...)` immediately

The device will reboot automatically with the new firmware after flashing completes.

---

## Supported Firmware Types

As of `openrdk` 0.2.0, the SDK manifest contains:

| Type | Chip | Binary |
|------|------|--------|
| `"traction_module"` | ESP32-C3 | `traction_module.bin` |
| `"line_sensor_module"` | ESP32-C3 | `line_sensor_module.bin` |
| `"distance_sensor_module"` | ESP32-C3 | `distance_sensor_module.bin` |

Firmware binaries are expected under the SDK package's firmware asset directory when using the SDK flashing helpers. Build artifacts under `firmware/esp/modules/*/build/` are not tracked in git; generate them with ESP-IDF and copy package assets only when preparing a release bundle.

For the HC-SR04 module, build and package all three required images with:

```powershell
tools/scripts/package_firmware.ps1 -ModuleName distance_sensor_module
```

```bash
tools/scripts/package_firmware.sh distance_sensor_module
```

Available at runtime:
```python
runtime.supported_firmware_types
# ['traction_module', 'line_sensor_module', 'distance_sensor_module']
```

---

## `flash_firmware`

Flash a device that is already in the registry (identified by serial number).

```python
result = runtime.flash_firmware(
    "98:3D:AE:41:97:C4",
    "distance_sensor_module",
    baud=460800,
    on_output=None,
)
```

| Parameter | Description |
|-----------|-------------|
| `serial_number` | Device serial from registry (e.g. `"98:3D:AE:41:97:C4"`) |
| `firmware_type` | `"traction_module"`, `"line_sensor_module"`, or `"distance_sensor_module"` |
| `baud` | Flash baud rate (default `460800`) |
| `on_output` | Optional callback receiving each esptool output line. If `None`, lines are printed to stdout with `[flash]` prefix |

**Before calling:** put the device in ROM download mode using the BOOT button workflow above.

Internally:
- Locks the device port and serial number so the keepalive and udev cannot interfere
- Stops the keepalive thread for this device and waits for it to exit
- Runs `esptool` as a subprocess
- Uses a watchdog reset after flashing ESP32-C3 targets so native USB
  Serial/JTAG boards start the application instead of remaining in the ROM
  downloader
- Releases the lock after completion (keepalive and udev resume normally)

Returns:

| Key | Type | Description |
|-----|------|-------------|
| `ok` | `bool` | `True` on success |
| `serial_number` | `str` | Echo of the serial |
| `device_node` | `str` | Port used (e.g. `/dev/ttyACM0`) |
| `firmware_type` | `str` | Echo of the firmware type |
| `returncode` | `int` | esptool exit code |
| `output` | `str` | Full esptool stdout |

Raises `FlashError` on failure.

```python
# 1. Hold BOOT, press RESET, release BOOT
# 2. Then:
result = runtime.flash_firmware("98:3D:AE:41:97:C4", "traction_module")
print(result["output"])
```

---

## `flash_firmware_by_port`

Flash a device directly by port path. Use this for brand-new devices not yet in the registry.

```python
result = runtime.flash_firmware_by_port(
    "/dev/ttyACM0",
    "distance_sensor_module",
    baud=460800,
    on_output=None,
)
```

| Parameter | Description |
|-----------|-------------|
| `device_node` | Serial port path (e.g. `"/dev/ttyACM0"`) |
| `firmware_type` | `"traction_module"`, `"line_sensor_module"`, or `"distance_sensor_module"` |
| `baud` | Flash baud rate |
| `on_output` | Optional output callback |

If the port is already associated with a registry entry, the helper stops that
device's keepalive thread before flashing. It also works when the port has no
registry entry.

Returns the same dict as `flash_firmware` (without `serial_number`).

```python
result = runtime.flash_firmware_by_port("/dev/ttyACM0", "line_sensor_module")
```

---

## Capturing Output Programmatically

Pass `on_output` to suppress the default `[flash]` print and handle lines yourself.

```python
lines = []
runtime.flash_firmware(
    "98:3D:AE:41:97:C4",
    "traction_module",
    on_output=lines.append,
)
print("\n".join(lines))
```

---

## Full Example

```python
from openrdk import CommsRuntime, FlashError

runtime = CommsRuntime(auto_start=True, enable_webview=True)
runtime.post("webview_complete")

# Use the webview to find the serial number of the module to flash,
# then hold BOOT, press RESET, release BOOT, and run:

try:
    result = runtime.flash_firmware("98:3D:AE:41:97:C4", "traction_module")
    print("flash OK")
except FlashError as exc:
    print(f"flash failed: {exc}")

# After flashing the device reboots and reconnects automatically.
# The webview will show the updated module_type.

input("press Enter to quit\n")
runtime.stop()
```
