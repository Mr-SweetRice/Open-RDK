# Open-RDK HC-SR04 Distance Sensor Module

ESP-IDF firmware for a dedicated ESP32-C3 Open-RDK distance sensor module.

## Identity and pins

- Module query name: `distance_sensor_module`
- Module ID: `0x14`
- HC-SR04 `TRIG`: `GPIO3`
- HC-SR04 `ECHO`: `GPIO10`
- ESP-IDF target: `esp32c3`

`GPIO3` and `GPIO10` avoid the ESP32-C3 boot strapping pins (`GPIO2`,
`GPIO8`, `GPIO9`), native USB Serial/JTAG pins (`GPIO18`, `GPIO19`), UART0
pins (`GPIO20`, `GPIO21`), and the pins reserved for flash.

## Required electrical connection

The HC-SR04 must be powered from 5 V and share ground with the ESP32-C3.
Its `ECHO` output is a 5 V signal and must **never** be connected directly
to an ESP32-C3 GPIO.

Use this divider:

```text
HC-SR04 ECHO --- 10 kOhm ---+--- GPIO10
                            |
                          20 kOhm
                            |
                           GND
```

This reduces a 5 V echo to approximately 3.33 V. Connect `TRIG` directly to
`GPIO3`; the sensor's TTL trigger input accepts the ESP32-C3 3.3 V high level.
Place a 100 nF ceramic capacitor and a 10 uF bulk capacitor close to the
sensor's 5 V/GND pins.

## Measurement behavior

- Trigger pulse: 10 us
- Minimum interval between trigger pulses: 60 ms
- Default sample period: 100 ms
- Physical supported range: 20 to 4000 mm
- Echo acquisition has a 30 ms deadline (plus at most one scheduler tick
  before the waiting task resumes)
- A missing sensor or target produces a normal `DS` response with
  `valid=0`, `filtered_distance_mm=-1`, and the `NO_ECHO` flag. It never
  blocks the communication task indefinitely.
- Valid measurements can be median-filtered with a window of 1, 3, 5, or 7.

## Open-RDK protocol

The transport and frame layout are unchanged from `protocol/protocol.md`:
sync `AA 55 AA 55`, 200-byte maximum payload, message types `CMD`, `TEST`,
`TELEMETRY`, and `CONTROL`, and a 24-bit sequence number.

Handshake:

```text
Host hello          AA55AA55 00 01
Firmware hello ACK  AA55AA55 14 06

Host module query   AA55AA55 00 04
Firmware reply      AA55AA55 14 05 <len> distance_sensor_module
```

Commands:

```text
GET DATA
GET TELEM
GET CFG
GET INFO
RUN SELFTEST
SET CFG NAME <text>
SET CFG SAMPLE_MS <60..2000>
SET CFG MAX_MM <20..4000>
SET CFG FILTER <1|3|5|7>
SAVE CFG
RESET CFG
```

`MAX_DISTANCE_MM` and `FILTER_WINDOW` are accepted as compatibility aliases,
but new host code should emit `MAX_MM` and `FILTER`.

`RESET CFG` restores defaults in RAM; use `SAVE CFG` afterward to persist the
reset. Invalid or incompatible NVS data is ignored and the runtime defaults are
used.

Data response and telemetry:

```text
DS,<filtered_mm>,<raw_mm>,<echo_us>,<valid>,<health_flags>,<sample_timestamp_ms>
```

Invalid measurements always set `filtered_mm` to `-1`. `raw_mm` remains
available for below/above-range pulses and is `-1` when no usable pulse was
captured.

Health flag bits:

| Bit | Mask | Meaning |
| --- | ---: | --- |
| 0 | `0x01` | Latest measurement is valid |
| 1 | `0x02` | No rising edge / no echo before timeout |
| 2 | `0x04` | Echo was already high or did not fall before timeout |
| 3 | `0x08` | Measured distance is below 20 mm |
| 4 | `0x10` | Measured distance is above configured maximum |
| 5 | `0x20` | Median filter window is greater than one |
| 6 | `0x40` | Configuration was loaded from or saved to NVS |
| 7 | `0x80` | Reserved |

Configuration response:

```text
CFG,<sensor_name>,<sample_period_ms>,<max_distance_mm>,<filter_window>
```

Information response:

```text
INFO,<sensor_name>,distance_sensor_module,distance_sensor_module,20,HC-SR04,3,10,<health_flags>
```

Self-test response:

```text
SELFTEST,<ok>,<health_flags>,<distance_mm>
```

The HC-SR04 has no readable identity register. Therefore a failed self-test
means that no valid echo was obtained; it cannot distinguish an absent sensor
from a correctly connected sensor with no target in range.

Telemetry control uses the standard payloads and acknowledgements:

```text
TELEMETRY_START[:host_epoch_ms] -> TELEMETRY STARTED
TELEMETRY_SYNC[:host_epoch_ms]  -> TELEMETRY SYNCED
TELEMETRY_STOP                  -> TELEMETRY STOPPED
```

## Build

From the repository root in an ESP-IDF 5.x environment:

```bash
tools/scripts/build_firmware.sh firmware/esp/modules/distance_sensor_module
```

On Windows with the ESP-IDF environment active:

```powershell
tools/scripts/build.ps1 -ModulePath firmware/esp/modules/distance_sensor_module
```

## Validation checklist

The module was compiled for ESP32-C3 with ESP-IDF v5.3. The resulting
application image was `0x3f8e0` bytes, leaving 75% of the default app
partition free.

Hardware validation should cover:

1. Module hello and query return ID `0x14` and `distance_sensor_module`.
2. A target at known 100, 500, 1000, and 3000 mm distances stays within the
   accuracy expected from the HC-SR04 and installation geometry.
3. Disconnecting the HC-SR04 returns a bounded `DS,-1,-1,0,0,...` response
   with `NO_ECHO`, while hello and command traffic remain responsive.
4. Holding `ECHO` high returns `ECHO_STUCK` without blocking communication.
5. Targets below 20 mm and beyond the configured maximum set `BELOW_MIN` and
   `ABOVE_MAX`, respectively.
6. Filter windows 1, 3, 5, and 7 reject invalid values and produce the median
   of valid samples.
7. `SAVE CFG`, reboot, `GET CFG`, and the `CONFIG_LOADED` flag confirm NVS
   persistence.
8. Telemetry start, sync, and stop acknowledgements match the Open-RDK
   contract, and each new measurement produces one `DS` telemetry frame.
