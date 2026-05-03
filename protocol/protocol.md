# RDK Host <-> Firmware Protocol

This document is the source of truth for the serial protocol currently implemented between:
- Host relay/webview (`host/main/src/openrdk`)
- ESP firmware modules under `firmware/esp/modules/*`

It also lists all communication calls currently implemented by firmware modules.

## 1. Transport and Framing

- Transport: USB serial/JTAG or UART
- Default baud: `115200`
- Framed sync bytes: `AA 55 AA 55`
- Host module id in control frames: `0x00`
- Max framed message payload (`len` field): `200` bytes
- Sequence width: 24-bit unsigned (`0..16777215`, wraps)

### 1.1 Control Frame Format (handshake/discovery)

Control payload format after sync:

```
[MODULE_ID][CONTROL_BYTE]
```

Full frame:

```
[AA 55 AA 55][MODULE_ID][CONTROL_BYTE]
```

#### Control Calls

1. Host hello
- Host sends: `AA55AA55 00 01`
- Firmware replies: `AA55AA55 <FW_MODULE_ID> 06`

2. Host module query
- Host sends: `AA55AA55 00 04`
- Firmware replies:
  - `AA55AA55 <FW_MODULE_ID> 05 <name_len> <module_name_ascii_bytes>`

### 1.2 Stream Frame Format (commands/test/telemetry/output)

Full stream frame:

```
[AA55AA55][LEN][MESSAGE_BYTES...][MESSAGE_TYPE][SEQ_H][SEQ_M][SEQ_L]
```

Where:
- `LEN`: 1 byte, `1..200`
- `MESSAGE_TYPE`: 1 byte
- `SEQ_*`: 24-bit big-endian sequence value

### 1.3 Message Type Codes

- `0x01` -> `CMD`
- `0x02` -> `TEST`
- `0x03` -> `TELEMETRY`
- `0x04` -> `TRACTION_OUT`

Host default payloads (when no explicit payload is provided):
- `CMD`: `COMMAND`
- `TEST`: `TESTING`
- `TELEMETRY`: `TELEMETRY`
- `TRACTION_OUT`: `SET OUT 30`

## 2. Host Runtime Behavior

Host source files:
- `host/main/src/openrdk/constants.py`
- `host/main/src/openrdk/functions.py`
- `host/main/src/openrdk/webview.py`

### 2.1 Module IDs used in host

- `0x11` -> `traction_module`
- `0x12` -> `line_sensor_module`
- `0x13` -> `color_module`

Note: module-name query (`0x04`) is the primary identity signal; module-id mapping is fallback.

### 2.2 Telemetry Control Payloads

Host sends under message type `TELEMETRY (0x03)`:
- `TELEMETRY_START:<host_epoch_ms>`
- `TELEMETRY_SYNC:<host_epoch_ms>`
- `TELEMETRY_STOP`

Expected firmware ACK strings:
- `TELEMETRY STARTED`
- `TELEMETRY SYNCED`
- `TELEMETRY STOPPED`

### 2.3 Host Webview/API Calls That Trigger Protocol Traffic

- `POST /api/devices/{serial}/config/message-type`
- `POST /api/devices/{serial}/cmd/send`
- `POST /api/devices/{serial}/config/traction-out`
- `POST /api/devices/{serial}/traction-out/send`
- `POST /api/devices/{serial}/telemetry/start`
- `POST /api/devices/{serial}/telemetry/stop`

## 3. Firmware Command Catalog (All Current Calls)

## 3.1 `traction_module` (`firmware/esp/modules/traction_module`)

### Common framed responses
- `TEST` message type: responds `I RECIEVED TEST`
- Unknown `CMD`: responds `I RECIEVED CMD`
- `TRACTION_OUT` accepted and valid: `OK`
- `TRACTION_OUT` invalid/unsupported: `ERR`

### `TRACTION_OUT` calls
- `SET OUT <value>`
- `SET OUT RAW <value>`
- `CLR OUT`

### `CMD` calls: RPM PID and telemetry
- `GET PID RPM`
- `GET` (legacy alias for `GET PID RPM`)
- `GET TELEM`
- `SET PID RPM KP <value>`
- `SET KP <value>` (legacy)
- `SET PID RPM KI <value>`
- `SET KI <value>` (legacy)
- `SET PID RPM KD <value>`
- `SET KD <value>` (legacy)
- `SET PID RPM SP <value>`
- `SET SP <value>` (legacy)
- `SAVE PID RPM`
- `SAVE` (legacy alias)

### `CMD` calls: Position PID
- `GET PID POS`
- `GET TELEM POS`
- `SET PID POS KP <value>`
- `SET PID POS KI <value>`
- `SET PID POS KD <value>`
- `SET PID POS IWIN <value>`
- `SET PID POS ANGLE <value>`
- `SET PID POS TARGET <value>`
- `START PID POS`
- `STOP PID POS`
- `SAVE PID POS`

### `CMD` calls: Position sine generator
- `GET PID POS SINE`
- `SET PID POS SINE AMP <value>`
- `SET PID POS SINE OFFSET <value>`
- `SET PID POS SINE PERIOD <value>`
- `START PID POS SINE`
- `STOP PID POS SINE`

### `CMD` calls: Invert/bridge
- `GET INVERT`
- `SET MOTOR INV <0|1>`
- `SET ENCODER INV <0|1>`
- `GET BRIDGE`
- `SET BRIDGE <0|1>`

### `CMD` calls: Curve and config
- `GET CURVE`
- `SET CURVE <10|20|...|100> <rpm>`
- `SAVE CURVE`
- `GET CFG`
- `SET CFG BRIDGE <value>`
- `SET CFG ENCODER_MODE <value>`
- `SET CFG MOTOR_INV <value>`
- `SET CFG ENCODER_INV <value>`
- `SET CFG PULLUP <value>`
- `SET CFG PWM_FREQ <value>`
- `SET CFG COUNTS_PER_REV <value>`
- `SET CFG GEAR_RATIO <value>`
- `SET CFG RPM_MAX <value>`
- `SET CFG NOTES <text>`
- `SAVE CFG`

### `TELEMETRY` behavior
- Accepts:
  - `TELEMETRY_START[:host_epoch_ms]`
  - `TELEMETRY_SYNC[:host_epoch_ms]`
  - `TELEMETRY_STOP`
- Streams periodic telemetry payloads as text in framed messages.

## 3.2 `line_sensor_module` (`firmware/esp/modules/line_sensor_module`)

### Framed common responses
- `TEST` message type: `I RECIEVED TEST`
- Unknown `CMD`: `I RECIEVED CMD`
- `TRACTION_OUT` accepted/valid: `OK`
- `TRACTION_OUT` invalid: `ERR`

### `CMD` calls
- `GET DATA`
- `GET TELEM`
- `GET CFG`
- `GET CAL`
- `GET INFO`
- `START CAL`
- `STOP CAL`
- `SAVE CFG`
- `SAVE CAL`
- `SET CFG TRACK <0|1>`
- `SET CFG NAME <text>`
- `SET CFG DIGITAL_TH <float>`
- `SET CFG DETECT_TH <float>`
- `SET CFG CAL_TIME_MS <ms>`

### `TELEMETRY` calls
- `TELEMETRY_START[:host_epoch_ms]`
- `TELEMETRY_SYNC[:host_epoch_ms]`
- `TELEMETRY_STOP`

### `TRACTION_OUT` compatibility calls (accepted for host mode compatibility)
- `SET OUT <value>`
- `SET OUT RAW <value>`
- `CLR OUT`

## 3.3 `color_module` (`firmware/esp/modules/color_module`)

### Identity and handshake
- Hello ACK module id: `0x13`
- Module query name: `color_module`

### Hardware profile
- MCU target: `ESP32-C3`
- Sensor: `TCS3472` / `TCS34725`
- I2C address: `0x29`
- SDA pin: `GPIO8`
- SCL pin: `GPIO9`
- LED pin: `GPIO10`

### Framed common responses
- `TEST` message type: `I RECIEVED TEST`
- Unknown `CMD`: `I RECIEVED CMD`
- `TRACTION_OUT` accepted/valid: `OK`
- `TRACTION_OUT` invalid: `ERR`

### Palette modes and built-in slot names
- Mode `4`: `red`, `green`, `blue`, `yellow`
- Mode `8`: `black`, `white`, `red`, `yellow`, `green`, `cyan`, `blue`, `magenta`
- Mode `16`: `black`, `white`, `gray`, `red`, `orange`, `yellow`, `lime`, `green`, `cyan`, `sky`, `blue`, `violet`, `magenta`, `pink`, `brown`, `olive`

### `CMD` calls
- `GET DATA`
- `GET TELEM`
- `GET CFG`
- `GET CAL`
- `GET CAL <4|8|16>`
- `GET CAL PATCH <mode> <slot>`
- `GET CAL PATCH <slot-or-name> [mode]`
- `GET INFO`
- `RUN SELFTEST`
- `START CAL`
- `STOP CAL`
- `SAVE CFG`
- `SAVE CAL`
- `RESET CFG`
- `RESET CAL`
- `RESET CAL ALL`
- `RESET CAL <4|8|16>`
- `SET CFG NAME <text>`
- `SET CFG SAMPLE_MS <ms>`
- `SET CFG LED <OFF|ON|AUTO|0|1|2>`
- `SET CFG GAIN_MODE <MANUAL|AUTO|0|1>`
- `SET CFG GAIN <value>`
- `SET CFG INTEGRATION_MS <ms>`
- `SET CFG CLASSIFIER <NORM_RGB|LAB|0|1>`
- `SET CFG CONF_TH <float_0_to_1>`
- `SET CFG TARGET_CLEAR <value>`
- `SET CFG PALETTE_MODE <4|8|16>`
- `SET CFG PATCH_SAMPLES <count>`
- `SET CAL PATCH <DARK|WHITE|slot|slot-name>`
- `COMMIT CAL PATCH <DARK|WHITE|slot|slot-name>`
- `SET CAL DARK <mode> <r> <g> <b> <c>`
- `SET CAL WHITE <mode> <r> <g> <b> <c>`
- `SET CAL PROTO <mode> <slot> <norm_r_milli> <norm_g_milli> <norm_b_milli> <luma_milli> <lab_l_centi> <lab_a_centi> <lab_b_centi> <sample_count>`
- `LED ON`
- `LED OFF`
- `LED AUTO`

### `TELEMETRY` calls
- `TELEMETRY_START[:host_epoch_ms]`
- `TELEMETRY_SYNC[:host_epoch_ms]`
- `TELEMETRY_STOP`

### `TRACTION_OUT` compatibility calls
- `SET OUT <value>`
- `SET OUT RAW <value>`
- `CLR OUT`

### Response payload contracts

`GET DATA` and streamed telemetry use:

```text
DATA|TEL,
<palette_mode>,
<detected_slot>,
<confidence_milli>,
<top0_slot>,<top0_confidence_milli>,
<top1_slot>,<top1_confidence_milli>,
<top2_slot>,<top2_confidence_milli>,
<raw_r>,<raw_g>,<raw_b>,<raw_c>,
<norm_r_milli>,<norm_g_milli>,<norm_b_milli>,
<lab_l_centi>,<lab_a_centi>,<lab_b_centi>,
<luma_milli>,
<gain>,
<integration_ms>,
<led_mode>,
<led_active>,
<health_flags>,
<classifier>,
<calibration_target_slot>,
<calibration_samples>,
<sample_timestamp_ms>
```

Where:
- `detected_slot` is `-1` when confidence is below threshold or calibration is incomplete
- `calibration_target_slot` uses `-2` for `DARK`, `-1` for `WHITE`, `-128` for idle
- `health_flags` bits:
  - bit `0`: sensor ok
  - bit `1`: sample saturated
  - bit `2`: dark reference valid
  - bit `3`: white reference valid
  - bit `4`: calibration active
  - bit `5`: selftest ok
  - bit `6`: auto exposure enabled
  - bit `7`: sensor present

`GET CFG` returns:

```text
CFG,<sensor_name>,<sample_period_ms>,<led_mode>,<gain_mode>,<gain>,<integration_ms>,<classifier>,<confidence_threshold_milli>,<target_clear>,<palette_mode>,<patch_sample_count>
```

`GET CAL [mode]` returns:

```text
CAL,<palette_mode>,<class_count>,<valid_mask>,<enabled_mask>,<dark_valid>,<white_valid>,<dark_r>,<dark_g>,<dark_b>,<dark_c>,<white_r>,<white_g>,<white_b>,<white_c>
```

`GET CAL PATCH ...` returns:

```text
PATCH,<palette_mode>,<slot>,<enabled>,<valid>,<sample_count>,<name>,<norm_r_milli>,<norm_g_milli>,<norm_b_milli>,<lab_l_centi>,<lab_a_centi>,<lab_b_centi>,<luma_milli>
```

`GET INFO` returns:

```text
INFO,<sensor_name>,<module_type>,<firmware_module>,<module_id>,<sensor_id>,<health_flags>,<i2c_address>,<sda_pin>,<scl_pin>,<led_pin>
```

`RUN SELFTEST` returns a structured result even on failure:

```text
SELFTEST,<ok>,<sensor_id>,<message>
```

### Persistence notes
- Configuration is stored in NVS blob `cfg`
- Calibration is stored in NVS blob `cal`
- Calibration profiles are versioned and stored separately for palette modes `4`, `8`, and `16`

## 3.4 `test_firware` placeholder (`firmware/esp/modules/test_firware`)

Current placeholder implementation does not define protocol command handlers yet.

## 4. Legacy Line Fallback Policy

- `traction_module`:
  - line fallback parser is compile-time gated (`TRACTION_COMM_ENABLE_LINE_FALLBACK`)
  - default is `OFF`
- `line_sensor_module`:
  - line fallback parser is compile-time gated (`LS_COMM_ENABLE_LINE_FALLBACK`)
  - default is `OFF`

This keeps framed protocol as default while preserving optional debug compatibility.

## 5. Error/Ack Contract Summary

- Generic success: `OK`
- Generic error: `ERR`
- Test ack: `I RECIEVED TEST`
- Unknown command fallback under framed `CMD`: `I RECIEVED CMD`
- Telemetry control ack:
  - `TELEMETRY STARTED`
  - `TELEMETRY SYNCED`
  - `TELEMETRY STOPPED`

## 6. Compliance Notes

- Any new firmware communication call must be added here.
- Any frame format or message type changes must be reflected here and in host relay constants.
