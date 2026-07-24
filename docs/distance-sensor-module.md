# DistanceSensorModule

Controls an HC-SR04 distance sensor connected to an ESP32-C3 running
`distance_sensor_module`. It inherits the shared properties and raw command
helpers from [BaseModule](base-module.md).

```python
sensor = runtime.distance_sensor("98:3D:AE:41:97:C4")
# or:
sensor = runtime.module("98:3D:AE:41:97:C4")
```

## Wiring

| HC-SR04 | ESP32-C3 |
|---------|----------|
| VCC | 5 V |
| GND | GND |
| TRIG | GPIO3 |
| ECHO | GPIO10 through a 5 V to 3.3 V divider |

Do not connect ECHO directly to the ESP32-C3. Use `10 kΩ` between ECHO and
GPIO10, plus `20 kΩ` between GPIO10 and GND. The ESP32-C3 and HC-SR04 must
share ground.

GPIO3 and GPIO10 avoid the ESP32-C3 boot-strapping pins (GPIO2, GPIO8, and
GPIO9), the native USB pins (GPIO18 and GPIO19), and the pins normally used
by flash (GPIO12 through GPIO17).

## One-shot readings

```python
data = sensor.get_data()

if data["valid"]:
    print(data["distance_mm"])
    print(data["distance_cm"])
else:
    print(data["status"])
```

Convenience calls return a single value:

```python
millimetres = sensor.get_distance_mm()
centimetres = sensor.get_distance_cm()
metres = sensor.get_distance("m")
```

Invalid measurements are represented in the returned data instead of causing
a transport error. This is important when the sensor is disconnected: the
firmware continues to answer commands and reports `valid=False`.

A complete runnable example is available at
[`test/distance_sensor.py`](../test/distance_sensor.py).

## Configuration

```python
config = sensor.get_config()

sensor.set_sample_period(100)   # 60..2000 ms
sensor.set_max_distance(3000)   # 20..4000 mm
sensor.set_filter_window(5)     # 1, 3, 5, or 7 samples
sensor.set_name("front_range")
sensor.save_config()
```

The measurement period cannot be shorter than `60 ms`. Filtering uses the
median of the configured number of valid samples; a window of `1` disables
filtering.

Use `reset_config()` to restore the firmware defaults:

- sample period: `100 ms`
- maximum distance: `4000 mm`
- median window: `3`

The reset is immediate but volatile. Call `save_config()` afterward if the
defaults must survive a reboot.

## Self-test

```python
result = sensor.run_selftest()
print(result["ok"], result["status"])
```

The self-test confirms that the GPIO driver is running and attempts a
measurement. With no HC-SR04 connected, it reports a controlled no-echo
result rather than hanging.

## Streaming

```python
import time

sensor.start_streaming()
try:
    while True:
        data = sensor.get_latest_data()
        if data is not None:
            print(data["distance_cm"] if data["valid"] else data["status"])
        time.sleep(0.05)
finally:
    sensor.stop_streaming()
```

`get_latest_data()` is non-blocking and reads the latest cached `DS` telemetry
frame. The synchronous `get_data()` call remains available when streaming is
not needed.

## Browser UI

Open:

```text
http://rdk.local:8765/distance-sensor
```

The page shows the filtered and raw distance, echo duration, health state,
live history, streaming controls, configuration, self-test, and the required
wiring.
