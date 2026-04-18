# Line Sensor Migration Documentation

This document is the required root-level documentation for all additions in `line_sensor_module` related to the line-sensor migration.
From this point onward, every firmware addition/update must be recorded here.

## Mandatory Standards (obligatory instructions)

1. Do not alter existing function of communication unless solicited, if needed to do something solicited then ask.
2. Every add to the firmware must be documented in a markdown in the root of the firmware which include obligatory this very instruction.
3. Every firmware must keep the same general structure of directory, with a main dir with the code inside and a CMAKE list in the root, and any dependency in a componente directory.
4. Every new communication add to either the protocol or new communication function or call must be explicitly added to the markdown doc explaining what it does what the host expect to send and receive and what the module firmware expect to send and receive with that communication, function or call.
5. Every module firmware including test must be able to respond to the host test webview.

## Current Baseline

- Module path: `firmware/esp/modules/line_sensor_module`
- Firmware source migrated from: `firmware/legacy_firmware/line_sensor_module`
- Runtime identity reported by firmware:
  - module name: `line_sensor_module`
  - module type: `line_sensor_module`
  - module id: `0x12`
- Host protocol compatibility:
  - framed protocol enabled (default path)
  - legacy line parser available only by compile-time option (`LS_COMM_ENABLE_LINE_FALLBACK`), default `OFF`

## Protocol/Command Coverage Checklist

- [x] Framed handshake: `HELLO (0x01)` -> `ACK (0x06)`
- [x] Framed module query: `QUERY (0x04)` -> `0x05 <len> <module_name>`
- [x] Framed message type support:
  - `CMD (0x01)`
  - `TEST (0x02)`
  - `TELEMETRY (0x03)`
  - `TRACTION_OUT (0x04)`
- [x] Line-sensor command handling under `CMD`:
  - `GET DATA` / `GET TELEM`
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
- [x] Telemetry stream control under `TELEMETRY`:
  - `TELEMETRY_START[:host_epoch_ms]`
  - `TELEMETRY_SYNC[:host_epoch_ms]`
  - `TELEMETRY_STOP`
- [x] `TRACTION_OUT` compatibility commands accepted:
  - `SET OUT <value>`
  - `SET OUT RAW <value>`
  - `CLR OUT`

## Change Log

### 2026-04-18
- Copied line-sensor firmware base into the migration module workspace.
- Updated module communication to framed host protocol compatibility.
- Added framed protocol transport and host-compatible message type handling in `components/ls_comm/ls_comm.c`.
- Kept legacy line command path behind compile-time fallback switch.
- Set line fallback default to `OFF` to match framed-first behavior used by host and traction module.
- Added this `line_sensor.md` documentation and checklist.
- Updated `tools/line_sensor_tuner/index.html` to use framed serial comms (`AA55AA55 + len + payload + type + seq24`) with `CMD` and `TELEMETRY` message types, including telemetry start/sync/stop flow.
- Promoted the framed implementation to `firmware/esp/modules/line_sensor_module`.
- Moved the previous line-sensor tree to `firmware/legacy_firmware/line_sensor_module`.
- Updated firmware identity strings and module-info response to `line_sensor_module`.
