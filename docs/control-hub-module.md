# ControlHubModule

Controls the GPIO pins, MPU6050 IMU, and six servo outputs of the ESP32
control hub.

```python
hub = runtime.control_hub("5945007747")
# or:
hub = runtime.module("5945007747")
```

## Pinout

| Function | Physical ESP32 GPIOs |
|----------|----------------------|
| Servo 1..6 | 13, 12, 23, 5, 2, 15 |
| Digital input/output | 0, 21, 16, 17, 18, 19, 4, 22, 25 |
| Digital input only | 34, 35, 39 |
| MPU6050/OLED I2C | SDA 33, SCL 32 |
| Encoder | CLK 14, DT 27, SW 26 |

The high-level pin methods receive the physical ESP32 GPIO number:

```python
hub.write_pin(4, 1)       # GPIO4 HIGH
reading = hub.read_pin(35)
print(reading["value"])  # 0 or 1
print(reading["mode"])   # "input" or "output"
hub.write_pin(4, 0)       # GPIO4 LOW
```

`digital_write()` and `digital_read()` are aliases. GPIO34, GPIO35, and
GPIO39 can be read but cannot be written. The older `set_gpio(index, value)`
and `get_gpio(index)` calls remain available for firmware-indexed code.

## Servos

The high-level API numbers servos from 1 through 6:

```python
hub.set_servo_angle(1, 45)
hub.set_servo_pulse_us(2, 1500)
hub.center_servo(1)
```

Angles are limited to 0..180 degrees and pulse widths to 500..2500 us.
The compatibility methods `set_servo(channel, angle)` and
`set_servo_us(channel, pulse_us)` use zero-based channels 0..5.

## IMU

```python
imu = hub.read_imu()
print(imu["roll_deg"], imu["pitch_deg"], imu["yaw_deg"])
print(imu["gyro_dps"])
print(imu["calibrated"], imu["calibration_progress"])

raw = hub.read_imu_raw()
print(raw["ax"], raw["ay"], raw["az"])
```

To calibrate yaw drift, leave the board completely stationary for about five
seconds:

```python
imu = hub.calibrate_imu(wait=True)
print(imu["yaw_deg"])
```

`calibrate_imu(wait=False)` only starts calibration. Use
`wait_for_imu_calibration()` later if the script needs to wait for it.

## Complete example

The runnable example is
[`host/main/examples/control_hub_demo.py`](../host/main/examples/control_hub_demo.py).
It reads all pins and the IMU by default. The `--actuate` option is required
before it changes a GPIO output or moves a servo.

```bash
cd host/main
python examples/control_hub_demo.py
python examples/control_hub_demo.py --calibrate
python examples/control_hub_demo.py --actuate --gpio 4 --servo 1
```

To move Servo 1 progressively from 0 to 180 degrees in three seconds, use:

```bash
python examples/servo1_0_a_180.py
```

## Script directories in the WebView

The Control Hub page always includes the Open-RDK managed script directory.
Additional absolute host paths can be registered under **Biblioteca Python →
Diretórios simultâneos**. Every direct child ending in `.py` from every
available registered directory is shown in the module's Python script selector.

Directory registrations persist in `control_hub_script_directories.json` next
to the device registry. Removing a registration does not delete the directory
or any script. If two directories contain the same filename, the selector keeps
both entries grouped by directory and the host stores a unique reference so the
correct file is executed.

The same page shows a per-module execution log for shell commands and Python
scripts. The newest entries include the selected target, terminal, start/end
times, duration, result, return code, stdout, stderr, and host-side errors. Up to
200 entries per module persist in `control_hub_execution_log.json`; the WebView
loads the newest 50 and provides a clear action without affecting scripts or
menu configuration.
