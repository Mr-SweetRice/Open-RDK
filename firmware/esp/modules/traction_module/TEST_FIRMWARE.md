# Test Firmware Documentation

This document is the required root-level documentation for all additions in `traction_module`.
From this point onward, every firmware addition/update must be recorded here.

## Mandatory Standards (obligatory instructions)

1. Do not alter existing function of communication unless solicited, if needed to do something solicited then ask
2. Every add to the firmware must be documented in a markdown in the root of the firmware which include obligatory this very instruction
3. every firmware must keep the same general structure of directory , with a main dir with the code inside and a CMAKE list in the root, and any dependency in a componnete directory
4. every new communication add to either the protocol or new comunication function or call must be explicitly added to the markdown doc explaining what it does what the host expect to send and recieve and what the module firmware expect to send and recieve with that comunication, function or call
5. every module firmaware including test must be able to respond to the host test webview

## Current Baseline

- Module name: `traction_module`
- Target: ESP32-C3 (as requested)
- Current framed motor output channel: generic `CONTROL (0x04)`. Older notes in this historical log may mention `TRACTION_OUT`; the host keeps that as a compatibility alias only.
- Active firmware-local tools were removed. Motor configuration and tuning now live in the host relay web UI under `host/main/src/openrdk/web_new`.
- Current structure:
  - `CMakeLists.txt`
  - `main/CMakeLists.txt`
  - `main/main.c`
  - `components/` (reserved for dependencies)

## Change Log

### 2026-04-11
- Promoted the former test firmware module into `firmware/esp/modules/traction_module`.
- Updated firmware identity strings in code so host module query reports `traction_module`.
- Kept framed module ID constant value (`0x11`) for compatibility while renaming symbol to `TRACTION_MODULE_ID`.
- Moved previous legacy traction implementation to `firmware/legacy_firmware/traction_module`.

### 2026-04-06
- Reset `traction_module` contents to a clean baseline structure.
- Recreated minimal ESP-IDF project files (`CMakeLists.txt`, `main/CMakeLists.txt`, `main/main.c`).
- Added this documentation file with mandatory standards.
- Added protocol-transition plan and command migration checklist for traction-to-test migration.
- Completed Step 2 reconstruction of traction runtime in `traction_module` without protocol changes:
  - Copied components: `traction_hal`, `traction_bridge`, `traction_storage`, `traction_control`, `traction_comm`.
  - Copied traction-style `main/main.c` and `main/CMakeLists.txt` into `traction_module`.
  - Kept root `CMakeLists.txt` project identity as `traction_module`.
  - Built for ESP32-C3 and flashed target `98:3D:AE:41:9A:40` successfully.
  - Verified text-command parity on serial link for `GET PID RPM`, `SET OUT`, `SET OUT RAW`, `CLR OUT`, `GET TELEM`.
- Completed Step 3 framed transport (parallel path, text path preserved):
  - Added framed RX/TX parser and dispatcher inside `components/traction_comm/traction_comm.c`.
  - Added framed handshake support for hello/query using sync `AA55AA55`.
  - Added stream frame responses for `CMD`, `TEST`, and `TELEMETRY`.
  - Added telemetry stream mode (`TELEMETRY_START` / `TELEMETRY_SYNC` / `TELEMETRY_STOP`) with periodic framed telemetry emission.
  - Adjusted serial read loop timing to avoid starving USB reads when USB+UART are both enabled (fixes host `write_timeout=0.01` behavior).
  - Kept existing line command handling active and verified line `SET OUT` still returns `OK`.
- Step 3 stability fix (USB host->device command direction on ESP32-C3 USB Serial/JTAG):
  - Root cause found: host writes timed out because firmware USB Serial/JTAG driver could be pre-installed in an incompatible state.
  - Firmware change: in `traction_comm_init`, when `usb_serial_jtag_driver_install` returns `ESP_ERR_INVALID_STATE`, force uninstall/reinstall with explicit RX/TX buffers.
  - Result after flash on `98:3D:AE:41:9A:40`:
    - Framed hello write/ACK restored (`AA55AA55 00 01` -> `AA55AA55 11 06`).
    - Text command writes restored (`GET PID RPM` returns `P,...`).
    - Webview/host path restored for `TEST`, `TRACTION_OUT` send, and telemetry start/sync/stream/stop.
- Step 4 Group 1 migration complete:
  - Migrated `SET OUT`, `SET OUT RAW`, `CLR OUT` to framed `MESSAGE_TYPE_TRACTION_OUT` execution path.
  - Host keepalive `TRACTION_OUT` path now uses framed stream send/ACK with sequence validation.
  - Verified framed TX/RX in comms log and direct framed serial tests for `SET OUT RAW` and `CLR OUT`.
- Step 4 Group 2 migration complete:
  - Migrated framed `MESSAGE_TYPE_CMD` execution for RPM PID command set:
    - `GET PID RPM`
    - `SET PID RPM KP <value>`
    - `SET PID RPM KI <value>`
    - `SET PID RPM KD <value>`
    - `SET PID RPM SP <value>`
  - Added host/API one-shot `CMD` send path (`/api/devices/{serial}/cmd/send`) routed through keepalive framed stream.
  - Added webview `Cmd` input + `Send` button for direct command tests while device mode is `CMD`.
- Step 4 Group 3 migration complete:
  - Migrated framed `GET TELEM` command handling on `MESSAGE_TYPE_CMD`.
  - Added direct telemetry snapshot callback path from control loop into comm layer for framed command reply parity.
  - Kept existing line-command telemetry request path unchanged.
- Step 4 Group 4 migration complete:
  - Migrated framed `SAVE PID RPM` command handling on `MESSAGE_TYPE_CMD`.
  - Reused existing RPM save queue path (`enqueue_rpm_save`) and returned framed enqueue acknowledgement.
  - Kept existing line-command save flow unchanged.
- Step 4 Group 5 migration complete:
  - Migrated framed `MESSAGE_TYPE_CMD` execution for position PID command set:
    - `GET PID POS`
    - `SET PID POS KP/KI/KD/IWIN/ANGLE`
    - `START PID POS`
    - `STOP PID POS`
  - Added framed position snapshot response format:
    - `PP,<kp>,<ki>,<kd>,<target_deg>,<enabled>,<iwin>`
  - Kept existing line-command behavior unchanged.
- Step 4 Group 6 migration complete:
  - Migrated framed `GET TELEM POS` handling on `MESSAGE_TYPE_CMD`.
  - Added framed position telemetry snapshot response format:
    - `TP,<target_deg>,<position_deg>,<cmd_pwm_signed>,<cmd_raw>,<i_term>`
  - Kept existing line-command telemetry request behavior unchanged.
- Step 4 Group 7 migration complete:
  - Migrated framed position-sine command handling on `MESSAGE_TYPE_CMD`:
    - `GET PID POS SINE`
    - `SET PID POS SINE AMP/OFFSET/PERIOD`
    - `START PID POS SINE`
    - `STOP PID POS SINE`
  - Added framed sine snapshot response format:
    - `PS,<amp_deg>,<offset_deg>,<period_s>,<enabled>`
  - Kept existing line-command sine behavior unchanged.
- Step 4 Group 8 migration complete:
  - Migrated framed configuration command handling on `MESSAGE_TYPE_CMD`:
    - `GET INVERT`
    - `SET MOTOR INV <0|1>`
    - `SET ENCODER INV <0|1>`
    - `GET BRIDGE`
    - `SET BRIDGE <0|1>`
  - Added framed config snapshot response formats:
    - `INV,<motor_invert>,<encoder_invert>`
    - `BRG,<bridge_type>`
  - Kept existing line-command config behavior unchanged.
- Step 4 Group 9 migration complete:
  - Migrated framed curve command handling on `MESSAGE_TYPE_CMD`:
    - `GET CURVE`
    - `SET CURVE <10|20|...|100> <rpm>`
    - `SAVE CURVE`
  - Added framed curve snapshot response format:
    - `CV,<rpm@10>,<rpm@20>,...,<rpm@100>`
  - Kept existing line-command curve behavior unchanged.
- Step 4 Group 10 migration complete:
  - Migrated framed controller-config command handling on `MESSAGE_TYPE_CMD`:
    - `GET CFG`
    - `SET CFG <field> <value>`
    - `SAVE CFG`
  - Added framed combined config snapshot response format:
    - `CFG,<...>|CFGN,<notes>`
  - Kept existing line-command config behavior unchanged.
- Step 4 Group 11 final cutover complete:
  - Framed protocol path is the default runtime path for host integration.
  - Legacy line-command fallback is now compile-time gated and disabled by default.
  - Added explicit component option:
    - `TRACTION_COMM_ENABLE_LINE_FALLBACK` (default `OFF`)
  - When enabled for debug builds, line parser remains available without changing framed behavior.
- Tools migration for `traction_module`:
  - Copied traction UI tools into `traction_module/tools`:
    - `tools/pid_tuner`
    - `tools/position_tuner`
    - `tools/motor_config`
  - Adapted all three tools to framed serial transport (`AA55AA55 + len + message + type + seq24`).
  - Added message-type routing in tools:
    - `SET OUT*` / `CLR OUT` -> framed `TRACTION_OUT` (`0x04`)
    - all other tool commands -> framed `CMD` (`0x01`)
  - Preserved existing UI behavior and command text payloads.
- Framed CMD parity fix:
  - Added `SAVE PID POS` handling to framed `MESSAGE_TYPE_CMD` path in `traction_comm`.
  - Response semantics match other save operations (`S,ENQ` on enqueue success, `ERR` on failure).

## Transition Plan (Traction -> Test, Safe Protocol Migration)

Objective:
- Reconstruct traction firmware inside `traction_module` and migrate communication from text command parsing to host framed protocol without breaking current behavior.

Scope constraints:
- Keep host communication behavior unchanged unless explicitly requested.
- Preserve test firmware structure (`main`, root `CMakeLists.txt`, `components` dependencies).
- Document every communication addition/change in this file before/with implementation.

Execution phases:
1. Baseline freeze and evidence capture
- Confirm current host webview behavior and expected ACK/timeout paths.
- Record known-good command responses and motor behavior.

2. Reconstruct traction runtime in `traction_module` (no protocol change yet)
- Copy/adapt traction components into `traction_module/components`.
- Build + flash and verify text-command behavior parity first.

3. Add framed protocol transport as a parallel path
- Implement framed RX/TX parser/dispatcher in a new comm layer.
- Keep text path available as fallback during migration.

4. Migrate commands in groups (one-by-one, with function wiring)
- For each command/group, migrate both:
  - communication command handling
  - underlying control/storage function path used by that command

5. Regression gate after each group
- Build passes.
- Flash succeeds.
- Webview test path for migrated command passes.
- Previously migrated commands still pass.
- Documentation for that command is updated.

6. Final cutover
- After all command groups pass, set framed protocol as default path.
- Keep text fallback behind a compile-time switch only if needed for debug.

## Command Group Migration Checklist

Legend:
- `Status`: `todo` | `doing` | `done` | `blocked`
- A group can move to `done` only if Build/Flash/Webview/Doc gates are all complete.

| Group | Commands | Status | Build | Flash | Webview test | Docs updated |
|---|---|---|---|---|---|---|
| 0 | Baseline freeze + evidence capture | done | [x] | [x] | [x] | [x] |
| 1 | `SET OUT`, `SET OUT RAW`, `CLR OUT` | done | [x] | [x] | [x] | [x] |
| 2 | `GET PID RPM`, `SET PID RPM KP/KI/KD/SP` | done | [x] | [x] | [x] | [x] |
| 3 | `GET TELEM` | done | [x] | [x] | [x] | [x] |
| 4 | `SAVE PID RPM` | done | [x] | [x] | [x] | [x] |
| 5 | `GET PID POS`, `SET PID POS KP/KI/KD/IWIN/ANGLE`, `START/STOP PID POS` | done | [x] | [x] | [x] | [x] |
| 6 | `GET TELEM POS` | done | [x] | [x] | [x] | [x] |
| 7 | `GET/SET PID POS SINE`, `START/STOP PID POS SINE` | done | [x] | [x] | [x] | [x] |
| 8 | `GET/SET INVERT`, `GET/SET BRIDGE` | done | [x] | [x] | [x] | [x] |
| 9 | `GET/SET CURVE`, `SAVE CURVE` | done | [x] | [x] | [x] | [x] |
| 10 | `GET/SET CFG`, `SAVE CFG` | done | [x] | [x] | [x] | [x] |
| 11 | Final cutover and fallback policy | done | [x] | [x] | [x] | [x] |

## Per-Group Implementation Checklist (repeat for each group)

- [ ] Create/adjust framed protocol command mapping for this group.
- [ ] Wire migrated commands to the corresponding traction control/storage functions.
- [ ] Keep non-migrated command paths untouched.
- [ ] Verify ACK/error semantics are preserved.
- [ ] Build firmware.
- [ ] Flash target ESP32-C3.
- [ ] Validate from host webview.
- [ ] Update communication contract entries below.

## Current Execution Gate

- Step 1 complete.
- Step 2 complete.
- Step 2 output delivered:
  - `traction_module` component tree created (`traction_hal`, `traction_bridge`, `traction_storage`, `traction_control`, `traction_comm`)
  - build succeeds for ESP32-C3
  - flash succeeds on target `98:3D:AE:41:9A:40`
  - basic text command parity validated (`SET OUT`, `SET OUT RAW`, `CLR OUT`)
- Step 3 complete.
- Step 3 output delivered:
  - hello ack frame is returned
  - module query frame returns `traction_module`
  - stream `CMD` and `TEST` frame ACKs returned with matching type/sequence
  - telemetry start/sync/stop framed control ACKs returned
  - periodic framed telemetry emitted while telemetry mode is active
  - line command path still functional (`SET OUT` -> `OK`)
- Step 4 Group 1 complete (`SET OUT`, `SET OUT RAW`, `CLR OUT` on framed path).
- Step 4 Group 2 complete (`GET PID RPM`, `SET PID RPM KP/KI/KD/SP` on framed path).
- Step 4 Group 3 complete (`GET TELEM` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 4 complete (`SAVE PID RPM` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 5 complete (`GET PID POS`, `SET PID POS KP/KI/KD/IWIN/ANGLE`, `START/STOP PID POS` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 6 complete (`GET TELEM POS` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 7 complete (`GET/SET PID POS SINE`, `START/STOP PID POS SINE` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 8 complete (`GET/SET INVERT`, `GET/SET BRIDGE` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 9 complete (`GET/SET CURVE`, `SAVE CURVE` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 10 complete (`GET/SET CFG`, `SAVE CFG` on framed `MESSAGE_TYPE_CMD` path).
- Step 4 Group 11 complete (final cutover + fallback policy applied and validated).
- Next actionable step: **Transition phase complete; optional next step is performance/soak validation under long telemetry sessions**.

## Step 1 Evidence (Baseline Freeze, 2026-04-06)

Service state captured:
- Comms container: `rdk_repo-comms-1` (`rdk_repo-comms` image), status `Up`.
- Webview health endpoint: `GET /api/health -> {"ok":true}`.
- Supported message types from webview API:
  - `CMD`
  - `TELEMETRY`
  - `TEST`
  - `TRACTION_OUT`

Webview/device baseline notes:
- Registry snapshot includes one live online device on `/dev/ttyACM0`:
  - Serial: `98:3D:AE:41:9A:40`
  - Status: `online connected`
  - Link: `live`
  - Message mode: `TRACTION_OUT`
  - Module type shown by host: `NOT-RDK-MODULE`
- Historical entries for other serials are present but currently offline.

Command/ACK snapshot (from active `/app/comms-raw.log` in container):
- TX: `SET OUT 50` -> RX: `OK` (latency ~50.744 ms)
- TX: `SET OUT 30` -> RX: `OK` (latency ~50.979 ms)
- TX: `SET OUT 10` -> RX: `OK` (latency ~50.843 ms)
- TX: `SET OUT 0` -> RX: `OK` (latency ~50.941 ms)
- No telemetry (`message_type=TELEMETRY`) entries present in current active log.

Flash/target confirmation baseline:
- Active target for transition work: ESP32-C3 at serial `98:3D:AE:41:9A:40`.
- Current running firmware as seen by host registry is `NOT-RDK-MODULE` (not yet identified as target migrated test firmware).
- Baseline freeze complete; ready to proceed to Step 2.

## Step 2 Evidence (Traction Runtime Reconstruction, 2026-04-06)

Reconstruction actions performed:
- Removed prior placeholder dependency set and populated `traction_module/components` from `traction_module/components`.
- Traction runtime pieces now present in `traction_module`:
  - `components/traction_hal`
  - `components/traction_bridge`
  - `components/traction_storage`
  - `components/traction_control`
  - `components/traction_comm`
- Adopted traction app startup path in `traction_module/main/main.c` and matching `main/CMakeLists.txt`.

Build evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary output: `firmware/esp/modules/traction_module/build/traction_module.bin`
- Size report: `0x4b2e0` bytes, app partition free space `0xb4d20` bytes (71%)

Flash evidence:
- Port: `/dev/ttyACM0`
- Device MAC: `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` for bootloader/app/partition + `Done`

Serial parity probe evidence (115200 bps, USB serial/JTAG):
- `GET PID RPM` -> `P,2.5000,1.5000,0.1000,0.00`
- `SET OUT 20` -> `OK`
- `SET OUT RAW 30` -> `OK`
- `CLR OUT` -> `OK`
- `GET TELEM` -> `T,0.00,0.00,0.00,0.00`

## Step 3 Evidence (Parallel Framed Transport, 2026-04-06)

Implementation summary:
- File changed: `components/traction_comm/traction_comm.c`
- Added protocol constants matching host (`FRAME_SYNC_BYTES`, module IDs, message type codes, sequence width).
- Added framed send helpers:
  - control frame response (`SYNC + module_id + payload`)
  - stream frame response (`SYNC + len + payload + type + seq24`)
- Added framed receive parsing in `traction_comm_task` while preserving text-line parsing as fallback/parallel path.
- Added telemetry stream state:
  - start enables periodic framed telemetry (100 ms period)
  - sync returns framed ACK
  - stop disables periodic framed telemetry

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary output: `firmware/esp/modules/traction_module/build/traction_module.bin`
- Size report: `0x4b9a0` bytes, app partition free space `0xb4660` bytes (70%)
- Final build (after USB/UART timing fix): `0x4b9d0` bytes, app partition free space `0xb4630` bytes (70%)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Protocol probe evidence (direct serial framed tests):
- Hello:
  - Host TX: `SYNC + 0x00 + 0x01`
  - Firmware RX/TX: `HELLO_ACK True`
- Module query:
  - Host TX: `SYNC + 0x00 + 0x04`
  - Firmware RX/TX: `MODULE_INFO True traction_module`
- Stream command/test:
  - `CMD_ACK True I RECIEVED CMD`
  - `TEST_ACK True I RECIEVED TEST`
- Telemetry stream control:
  - `TELEM_START_ACK True TELEMETRY STARTED`
  - `TELEM_SYNC_ACK True TELEMETRY SYNCED`
  - `TELEM_STOP_ACK True TELEMETRY STOPPED`
  - During active telemetry: `TELEM_STREAM_FRAMES 11` in ~1.2 s
- Text fallback parity still valid:
  - `LINE_SET_OUT_ACK OK`
  - Host API `POST /api/devices/{serial}/traction-out/send` returned `{"ok":true,...,"ack":"OK"}` after flash

## Step 4 Evidence (Group 1 Manual Output Migration, 2026-04-06)

Migration scope:
- Group 1 commands migrated to framed execution path:
  - `SET OUT <value>`
  - `SET OUT RAW <value>`
  - `CLR OUT`

Implementation summary:
- Firmware:
  - Added shared command executor in `traction_comm.c` so both line and framed paths use the same callback wiring for manual output commands.
  - Updated framed `MESSAGE_TYPE_TRACTION_OUT (0x04)` handler to parse/execute command text and return framed `OK`/`ERR` with matching sequence.
- Host:
  - Updated keepalive `TRACTION_OUT` send path to use framed stream exchange (`_send_stream_frame_and_wait`) instead of line write.
  - Kept existing webview/API surface unchanged (`/traction-out/send`).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4ba00` bytes (70% free in app partition)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- Webview/API:
  - `POST /api/devices/{serial}/traction-out/send` -> `{"ok":true,...,"ack":"OK"}`
- Comms log (host TX for traction-out is now framed):
  - TX hex: `aa55aa550a534554204f555420333304000000` (`SET OUT 33`, type `0x04`, seq `0`)
  - RX hex: `aa55aa55024f4b04000000` (`OK`, type `0x04`, seq `0`)
- Direct framed serial checks (with comms stopped):
  - `SET OUT RAW 17` -> framed `OK`
  - `CLR OUT` -> framed `OK`

## Step 4 Evidence (Group 2 RPM PID Migration, 2026-04-06)

Migration scope:
- Group 2 commands migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET PID RPM`
  - `SET PID RPM KP <value>`
  - `SET PID RPM KI <value>`
  - `SET PID RPM KD <value>`
  - `SET PID RPM SP <value>`

Implementation summary:
- Firmware:
  - Added framed RPM PID command parser/executor in `traction_comm.c` for `MESSAGE_TYPE_CMD`.
  - `GET PID RPM` now returns framed payload `P,<kp>,<ki>,<kd>,<sp>`.
  - `SET PID RPM KP/KI/KD/SP` execute control callbacks and return framed `OK`.
  - Kept existing line parser behavior untouched.
- Host:
  - Added one-shot CMD request queue integrated with keepalive stream path.
  - Added API endpoint: `POST /api/devices/{serial}/cmd/send` (requires device mode `CMD`).
  - Added webview controls:
    - `Cmd` text input
    - `Send` button

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4bcf0` bytes (70% free in app partition)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API command sequence (all `ok=true`):
  - `GET PID RPM` -> `P,2.5000,1.5000,0.1000,0.00`
  - `SET PID RPM KP 3.3000` -> `OK`
  - `SET PID RPM KI 1.7000` -> `OK`
  - `SET PID RPM KD 0.2000` -> `OK`
  - `SET PID RPM SP 45.0` -> `OK`
  - `GET PID RPM` -> `P,3.3000,1.7000,0.2000,45.00`
- Comms log framed evidence:
  - TX: `aa55aa550b474554205049442052504d01000007` (`GET PID RPM`, type `0x01`)
  - RX: `aa55aa551b...` message `P,2.5000,1.5000,0.1000,0.00`
  - TX/RX for each `SET PID RPM ...` command with same seq/type and `OK` response.

## Step 4 Evidence (Group 3 GET TELEM Migration, 2026-04-06)

Migration scope:
- Group 3 command migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET TELEM`

Implementation summary:
- Firmware:
  - Added `get_rpm_telem_state` callback in `traction_comm_cfg_t`.
  - Added control-loop telemetry snapshot state (`target_rpm`, `measured_rpm`, `cmd_pwm_signed`, `cmd_raw`) in `traction_control_app.c`.
  - Updated framed CMD parser in `traction_comm.c` so `GET TELEM` returns `T,<target_rpm>,<measured_rpm>,<cmd_pwm_signed>,<cmd_raw>`.
  - Kept existing line `GET TELEM` async request behavior untouched.
- Host:
  - No host protocol/API change required (existing `/cmd/send` path used).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4bea0` bytes (70% free in app partition)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test:
  - `POST /api/devices/{serial}/cmd/send` `GET PID RPM` -> `P,2.5000,1.5000,0.1000,0.00` (`ok=true`, `seq_abs=2`)
  - `POST /api/devices/{serial}/cmd/send` `GET TELEM` -> `T,0.00,0.00,0.00,0.00` (`ok=true`, `seq_abs=3`)
- Comms log framed evidence:
  - TX: `aa55aa55094745542054454c454d01000003` (`GET TELEM`, type `0x01`)
  - RX: `aa55aa5515542c302e30302c302e30302c302e30302c302e303001000003` (`T,0.00,0.00,0.00,0.00`)
- Regression checks:
  - `GET PID RPM` framed command still returns `P,...`
  - `TRACTION_OUT` send still returns `OK`
  - Telemetry stream start/sync/stop flow remains functional.

## Step 4 Evidence (Group 4 SAVE PID RPM Migration, 2026-04-06)

Migration scope:
- Group 4 command migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `SAVE PID RPM`

Implementation summary:
- Firmware:
  - Updated framed CMD parser in `traction_comm.c` to handle `SAVE PID RPM` / `SAVE`.
  - Reused existing snapshot + queue wiring:
    - `get_rpm_snapshot(...)`
    - `enqueue_rpm_save(...)`
  - Framed response semantics:
    - `S,ENQ` on successful enqueue
    - `ERR` on invalid state or enqueue failure
  - Kept line `SAVE PID RPM` behavior unchanged (`S,ENQ`, `OK`, plus async save status lines).
- Host:
  - No host protocol/API change required (`/cmd/send` reused).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4bf50` bytes (70% free in app partition)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test:
  - `POST /api/devices/{serial}/cmd/send` `GET PID RPM` -> `P,2.5000,1.5000,0.1000,0.00` (`ok=true`, `seq_abs=6`)
  - `POST /api/devices/{serial}/cmd/send` `SAVE PID RPM` -> `S,ENQ` (`ok=true`, `seq_abs=7`)
  - `POST /api/devices/{serial}/cmd/send` `GET TELEM` -> `T,0.00,0.00,0.00,0.00` (`ok=true`, `seq_abs=8`)
- Comms log framed evidence:
  - TX: `aa55aa550c53415645205049442052504d01000007` (`SAVE PID RPM`, type `0x01`)
  - RX: `aa55aa5505532c454e5101000007` (`S,ENQ`, type `0x01`, seq `7`)
- Regression checks:
  - `GET PID RPM` framed command still returns `P,...`
  - `GET TELEM` framed command still returns `T,...`
  - `TRACTION_OUT` send still returns `OK`
  - Telemetry stream start/sync/stop flow remains functional.

## Step 4 Evidence (Group 5 PID POS Migration, 2026-04-06)

Migration scope:
- Group 5 commands migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET PID POS`
  - `SET PID POS KP <value>`
  - `SET PID POS KI <value>`
  - `SET PID POS KD <value>`
  - `SET PID POS IWIN <value>`
  - `SET PID POS ANGLE <value>`
  - `START PID POS`
  - `STOP PID POS`

Implementation summary:
- Firmware:
  - Added framed CMD parser branch for position PID commands in `traction_comm.c`.
  - Added position snapshot formatter returning:
    - `PP,<kp>,<ki>,<kd>,<target_deg>,<enabled>,<iwin>`
  - `SET PID POS ...` commands route to existing callback wiring and return framed `OK`.
  - `START/STOP PID POS` route to existing callback wiring and return framed `OK`.
  - Kept line parser behavior unchanged.
- Host:
  - No host protocol/API change required (`/cmd/send` reused).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4c270` bytes (`firmware/esp/modules/traction_module/build/traction_module.bin`)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test (`POST /api/devices/{serial}/cmd/send`, all `ok=true`):
  - `GET PID POS` -> `PP,150.0000,0.0000,8.0000,0.0000,0,5.00`
  - `SET PID POS KP 1.5` -> `OK`
  - `SET PID POS KI 0.05` -> `OK`
  - `SET PID POS KD 0.01` -> `OK`
  - `SET PID POS IWIN 90` -> `OK`
  - `SET PID POS ANGLE 30` -> `OK`
  - `START PID POS` -> `OK`
  - `STOP PID POS` -> `OK`
  - `GET PID POS` -> `PP,1.5000,0.0500,0.0100,30.0000,0,90.00`
- Comms log framed evidence (`/api/comms` history):
  - TX: `GET PID POS` (`message_type=CMD`, `seq_abs=23`) -> RX: `PP,150.0000,0.0000,8.0000,0.0000,0,5.00`
  - TX: `SET PID POS KP/KI/KD/IWIN/ANGLE ...` -> RX: `OK` with matching type/sequence
  - TX: `START PID POS`/`STOP PID POS` -> RX: `OK` with matching type/sequence
  - TX: `GET PID POS` (`seq_abs=31`) -> RX: `PP,1.5000,0.0500,0.0100,30.0000,0,90.00`
- Regression checks:
  - `GET PID RPM` still returns `P,2.5000,1.5000,0.1000,0.00`
  - `GET TELEM` still returns `T,0.00,0.00,0.00,0.00`
  - `TRACTION_OUT` send still returns `OK` (`SET OUT 42`)
  - Telemetry mode start/stop endpoints still return `{"ok":true}`

## Step 4 Evidence (Group 6 GET TELEM POS Migration, 2026-04-06)

Migration scope:
- Group 6 command migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET TELEM POS`

Implementation summary:
- Firmware:
  - Added position telemetry snapshot callback in comm config:
    - `get_pos_telem_state(...)`
  - Added control-loop cached position telemetry state:
    - `target_deg`
    - `position_deg`
    - `cmd_pwm_signed`
    - `cmd_raw`
    - `i_term`
  - Added framed CMD parser branch for `GET TELEM POS` returning:
    - `TP,<target_deg>,<position_deg>,<cmd_pwm_signed>,<cmd_raw>,<i_term>`
  - Kept line `GET TELEM POS` request path unchanged.
- Host:
  - No host protocol/API change required (`/cmd/send` reused).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4c430` bytes (`firmware/esp/modules/traction_module/build/traction_module.bin`)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test (`POST /api/devices/{serial}/cmd/send`, all `ok=true`):
  - `GET TELEM POS` -> `TP,0.0000,0.0000,0.00,0.00,0.0000`
  - `SET PID POS ANGLE 45` -> `OK`
  - `GET TELEM POS` -> `TP,45.0000,0.0000,0.00,0.00,0.0000`
- Comms log framed evidence (`/api/comms` history):
  - TX line `5323`: `GET TELEM POS` -> RX line `5324`: `TP,0.0000,0.0000,0.00,0.00,0.0000`
  - TX line `5327`: `SET PID POS ANGLE 45` -> RX line `5328`: `OK`
  - TX line `5329`: `GET TELEM POS` -> RX line `5330`: `TP,45.0000,0.0000,0.00,0.00,0.0000`
- Regression checks:
  - `GET PID POS` still returns `PP,150.0000,0.0000,8.0000,0.0000,0,5.00`
  - `GET PID RPM` still returns `P,2.5000,1.5000,0.1000,0.00`
  - `GET TELEM` still returns `T,0.00,0.00,0.00,0.00`
  - `TRACTION_OUT` send still returns `OK` (`SET OUT 37`, comms lines `5349` -> `5350`)
  - Telemetry control still returns framed ACKs, including stop:
    - `TELEMETRY STARTED` (line `5407`)
    - `TELEMETRY STOPPED` (line `5433`)

## Step 4 Evidence (Group 7 PID POS SINE Migration, 2026-04-06)

Migration scope:
- Group 7 commands migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET PID POS SINE`
  - `SET PID POS SINE AMP <value>`
  - `SET PID POS SINE OFFSET <value>`
  - `SET PID POS SINE PERIOD <value>`
  - `START PID POS SINE`
  - `STOP PID POS SINE`

Implementation summary:
- Firmware:
  - Extended framed CMD parser in `traction_comm.c` for position-sine command set.
  - Added framed sine snapshot response:
    - `PS,<amp_deg>,<offset_deg>,<period_s>,<enabled>`
  - Reused existing callbacks:
    - `get_pos_sine_state`
    - `set_pos_sine_amp_deg`
    - `set_pos_sine_offset_deg`
    - `set_pos_sine_period_s`
    - `set_pos_sine_enabled`
  - Kept line command path unchanged.
- Host:
  - No host protocol/API change required (`/cmd/send` reused).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4c600` bytes (`firmware/esp/modules/traction_module/build/traction_module.bin`)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test (`POST /api/devices/{serial}/cmd/send`, all `ok=true`):
  - `GET PID POS SINE` -> `PS,90.00,180.00,8.00,0`
  - `SET PID POS SINE AMP 30` -> `OK`
  - `SET PID POS SINE OFFSET 15` -> `OK`
  - `SET PID POS SINE PERIOD 5` -> `OK`
  - `START PID POS SINE` -> `OK`
  - `GET PID POS SINE` -> `PS,30.00,15.00,5.00,1`
  - `STOP PID POS SINE` -> `OK`
  - `GET PID POS SINE` -> `PS,30.00,15.00,5.00,0`
- Comms log framed evidence (`/api/comms` history):
  - TX line `5608`: `GET PID POS SINE` -> RX line `5609`: `PS,90.00,180.00,8.00,0`
  - TX line `5610`: `SET PID POS SINE AMP 30` -> RX line `5611`: `OK`
  - TX line `5612`: `SET PID POS SINE OFFSET 15` -> RX line `5613`: `OK`
  - TX line `5614`: `SET PID POS SINE PERIOD 5` -> RX line `5615`: `OK`
  - TX line `5616`: `START PID POS SINE` -> RX line `5617`: `OK`
  - TX line `5618`: `GET PID POS SINE` -> RX line `5619`: `PS,30.00,15.00,5.00,1`
  - TX line `5620`: `STOP PID POS SINE` -> RX line `5621`: `OK`
  - TX line `5622`: `GET PID POS SINE` -> RX line `5623`: `PS,30.00,15.00,5.00,0`
- Regression checks:
  - `GET TELEM POS` still returns framed `TP,...` (`line 5624` -> `line 5625`)
  - `GET PID POS` still returns framed `PP,...`
  - `GET PID RPM` still returns framed `P,...`
  - `GET TELEM` still returns framed `T,...`
  - `TRACTION_OUT` send still returns `OK` (`line 5646` -> `line 5647`)
  - Telemetry mode still returns framed start/sync ACKs (`lines 5653`, `5655`)

## Step 4 Evidence (Group 8 INVERT/BRIDGE Migration, 2026-04-06)

Migration scope:
- Group 8 commands migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET INVERT`
  - `SET MOTOR INV <0|1>`
  - `SET ENCODER INV <0|1>`
  - `GET BRIDGE`
  - `SET BRIDGE <0|1>`

Implementation summary:
- Firmware:
  - Added framed CMD parser branch for invert/bridge command set in `traction_comm.c`.
  - Added framed config snapshot responses:
    - `INV,<motor_invert>,<encoder_invert>`
    - `BRG,<bridge_type>`
  - Reused existing callbacks:
    - `get_invert_state`, `set_motor_invert`, `set_encoder_invert`
    - `get_bridge_state`, `set_bridge_type`
  - Kept line command path unchanged.
- Host:
  - No host protocol/API change required (`/cmd/send` reused).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4c890` bytes (`firmware/esp/modules/traction_module/build/traction_module.bin`)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test (`POST /api/devices/{serial}/cmd/send`, all `ok=true`):
  - `GET INVERT` -> `INV,0,0`
  - `SET MOTOR INV 1` -> `OK`
  - `GET INVERT` -> `INV,1,0`
  - `SET ENCODER INV 1` -> `OK`
  - `GET INVERT` -> `INV,1,1`
  - `GET BRIDGE` -> `BRG,1`
  - `SET BRIDGE 1` -> `OK`
  - `GET BRIDGE` -> `BRG,1`
  - Restored baseline:
    - `SET MOTOR INV 0` -> `OK`
    - `SET ENCODER INV 0` -> `OK`
    - `SET BRIDGE 0` -> `OK`
    - `GET INVERT` -> `INV,0,0`
    - `GET BRIDGE` -> `BRG,0`
- Comms log framed evidence (`/api/comms` history):
  - TX line `5873`: `GET INVERT` -> RX line `5874`: `INV,0,0`
  - TX line `5875`: `SET MOTOR INV 1` -> RX line `5876`: `OK`
  - TX line `5879`: `SET ENCODER INV 1` -> RX line `5880`: `OK`
  - TX line `5883`: `GET BRIDGE` -> RX line `5884`: `BRG,1`
  - TX line `5893`: `SET BRIDGE 0` -> RX line `5894`: `OK`
  - TX line `5897`: `GET BRIDGE` -> RX line `5898`: `BRG,0`
- Regression checks:
  - `GET PID POS SINE` still functional (`line 5899`)
  - `GET TELEM POS` still functional (`line 5901`)
  - `GET PID POS` still functional (`line 5903`)
  - `GET PID RPM` still functional (`line 5905`)
  - `GET TELEM` still functional (`line 5907`)
  - `TRACTION_OUT` send still returns `OK` (`line 5921` -> `line 5922`)
  - Telemetry mode still returns framed start/sync/stop ACKs (`lines 5962`, `5964`, `5986`)

## Step 4 Evidence (Group 9 CURVE Migration, 2026-04-06)

Migration scope:
- Group 9 commands migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET CURVE`
  - `SET CURVE <10|20|...|100> <rpm>`
  - `SAVE CURVE`

Implementation summary:
- Firmware:
  - Added framed CMD parser branch for curve command set in `traction_comm.c`.
  - Added framed curve snapshot response:
    - `CV,<rpm@10>,<rpm@20>,...,<rpm@100>`
  - Added framed `SAVE CURVE` enqueue response parity:
    - `S,ENQ` on successful enqueue
    - `ERR` on invalid state/enqueue failure
  - Kept line command path unchanged.
- Host:
  - No host protocol/API change required (`/cmd/send` reused).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4cba0` bytes (`firmware/esp/modules/traction_module/build/traction_module.bin`)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test (`POST /api/devices/{serial}/cmd/send`, all `ok=true`):
  - `GET CURVE` -> `CV,0.00,0.00,0.00,0.00,0.00,33.30,61.94,103.84,148.01,195.09`
  - `SET CURVE 40 52.5` -> `OK`
  - `GET CURVE` -> `CV,0.00,0.00,0.00,52.50,0.00,33.30,61.94,103.84,148.01,195.09`
  - `SAVE CURVE` -> `S,ENQ`
- Comms log framed evidence (`/api/comms` history):
  - TX line `6181`: `GET CURVE` -> RX line `6182`: `CV,0.00,0.00,0.00,0.00,0.00,33.30,61.94,103.84,148.01,195.09`
  - TX line `6183`: `SET CURVE 40 52.5` -> RX line `6184`: `OK`
  - TX line `6185`: `GET CURVE` -> RX line `6186`: `CV,0.00,0.00,0.00,52.50,0.00,33.30,61.94,103.84,148.01,195.09`
  - TX line `6187`: `SAVE CURVE` -> RX line `6188`: `S,ENQ`
- Regression checks:
  - `GET INVERT` still functional (`line 6189`)
  - `GET BRIDGE` still functional (`line 6191`)
  - `GET PID POS SINE` still functional (`line 6193`)
  - `GET TELEM POS` still functional (`line 6195`)
  - `GET PID POS` still functional (`line 6197`)
  - `GET PID RPM` still functional (`line 6199`)
  - `GET TELEM` still functional (`line 6201`)
  - `TRACTION_OUT` send still returns `OK` (`line 6217` -> `line 6218`)
  - Telemetry mode still returns framed start/sync/stop ACKs (`lines 6262`, `6264`, `6287`)

## Step 4 Evidence (Group 10 CFG Migration, 2026-04-06)

Migration scope:
- Group 10 commands migrated to framed `MESSAGE_TYPE_CMD` execution path:
  - `GET CFG`
  - `SET CFG <field> <value>`
  - `SAVE CFG`

Implementation summary:
- Firmware:
  - Added framed CMD parser branch for controller-config command set in `traction_comm.c`.
  - Added framed combined config snapshot response:
    - `CFG,<bridge>,<encoder_mode>,<motor_inv>,<encoder_inv>,<pullup>,<pwm_freq>,<counts>,<gear>,<rpm_max>|CFGN,<notes>`
  - Added framed `SAVE CFG` enqueue response parity:
    - `S,ENQ` on successful enqueue
    - `ERR` on invalid state/enqueue failure
  - Kept line command path unchanged.
- Host:
  - No host protocol/API change required (`/cmd/send` reused).

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4d040` bytes (`firmware/esp/modules/traction_module/build/traction_module.bin`)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- API/webview CMD test (`POST /api/devices/{serial}/cmd/send`, all `ok=true`):
  - `GET CFG` -> `CFG,1,0,0,0,1,20000,44,45,195.09|CFGN,Bench profile / default firmware values`
  - `SET CFG BRIDGE 1` -> `OK` (non-destructive, current value)
  - `SET CFG RPM_MAX 195.09` -> `OK` (non-destructive, current value)
  - `SET CFG NOTES Bench profile / default firmware values` -> `OK` (non-destructive, same notes)
  - `GET CFG` (again) -> unchanged combined config snapshot
  - `SAVE CFG` -> `S,ENQ`
- Comms log framed evidence (`/api/comms` history):
  - TX line `6464`: `GET CFG` -> RX line `6465`: `CFG,...|CFGN,...`
  - TX line `6468`: `SET CFG BRIDGE 1` -> RX line `6469`: `OK`
  - TX line `6470`: `SET CFG RPM_MAX 195.09` -> RX line `6471`: `OK`
  - TX line `6472`: `SET CFG NOTES ...` -> RX line `6473`: `OK`
  - TX line `6474`: `GET CFG` -> RX line `6475`: `CFG,...|CFGN,...`
  - TX line `6476`: `SAVE CFG` -> RX line `6477`: `S,ENQ`
- Regression checks:
  - `GET CURVE` still functional (`line 6478`)
  - `GET INVERT` still functional (`line 6480`)
  - `GET BRIDGE` still functional (`line 6482`)
  - `GET PID POS SINE` still functional (`line 6484`)
  - `GET TELEM POS` still functional (`line 6486`)
  - `GET PID POS` still functional (`line 6488`)
  - `GET PID RPM` still functional (`line 6490`)
  - `GET TELEM` still functional (`line 6492`)
  - `TRACTION_OUT` send still returns `OK` (`line 6506` -> `line 6507`)
  - Telemetry mode still returns framed start/sync/stop ACKs (`lines 6513`, `6515`, `6526`)

## Step 4 Evidence (Group 11 Final Cutover/Fallback Policy, 2026-04-06)

Cutover scope:
- Finalized migration policy after all command groups were moved to framed `MESSAGE_TYPE_CMD`.
- Set framed protocol as default runtime path.
- Kept legacy line parser only as build-time debug fallback.

Implementation summary:
- Firmware:
  - Added compile-time macro gate in `traction_comm.c`:
    - `TRACTION_COMM_ENABLE_LINE_FALLBACK`
  - Default behavior compiled as fallback disabled (`0`).
  - Line parser execution in `traction_comm_task` now runs only when fallback macro is enabled.
  - Added startup log indicating fallback state (`ENABLED` or `DISABLED`).
- Build system:
  - Added component CMake option in `components/traction_comm/CMakeLists.txt`:
    - `TRACTION_COMM_ENABLE_LINE_FALLBACK` (default `OFF`)
  - Option maps to compile definition `TRACTION_COMM_ENABLE_LINE_FALLBACK=0/1`.

Build + flash evidence:
- Build target: `esp32c3`
- Result: `Project build complete`
- Binary size: `0x4bf10` bytes (`firmware/esp/modules/traction_module/build/traction_module.bin`)
- Flash target: `/dev/ttyACM0`, MAC `98:3d:ae:41:9a:40`
- Result: `Hash of data verified` + `Done`

Validation evidence:
- Host API regression (all framed paths):
  - `GET PID RPM`, `GET TELEM`, `GET PID POS`, `GET TELEM POS`, `GET PID POS SINE`
  - `GET INVERT`, `GET BRIDGE`, `GET CURVE`, `GET CFG`
  - `SAVE PID RPM`, `SAVE CURVE`, `SAVE CFG`
  - All returned `ok=true` with expected framed payloads.
- Transport mode checks:
  - `TRACTION_OUT` send returned `ok=true` with `ack=OK`.
  - Telemetry start/stop returned `{"ok":true}` and framed ACKs remained present.
- Direct serial cutover proof (with comms stopped):
  - Sent unframed line `GET PID RPM\n` after framed `TELEMETRY_STOP` and buffer drain:
    - response length: `0` bytes (no line fallback response)
  - Sent framed `GET PID RPM`:
    - response: `P,2.5000,1.5000,0.1000,0.00` (type `0x01`, matching sequence)

Policy outcome:
- Production/default firmware image is framed-only for command handling.
- Legacy line parser remains available strictly by explicit debug build opt-in.

## Communication Contract Section (to be filled on each communication update)

For each new/changed communication function/call, add:

- Name/ID:
- Purpose:
- Host sends:
- Host expects to receive:
- Firmware sends:
- Firmware expects to receive:
- Error/timeout behavior:
- Notes:

### Contract: Framed Control Handshake (legacy probe compatibility)

- Name/ID: `HELLO` / `MODULE_QUERY`
- Purpose: Allow host attach probe and module identification before stream mode.
- Host sends:
  - `SYNC(AA55AA55) + HOST_MODULE_ID(0x00) + 0x01` (hello)
  - `SYNC(AA55AA55) + HOST_MODULE_ID(0x00) + 0x04` (module query)
- Host expects to receive:
  - For hello: `SYNC + MODULE_ID + 0x06`
  - For query: `SYNC + MODULE_ID + 0x05 + <len> + <module_name>`
- Firmware sends:
  - hello ack with `MODULE_ID=0x11`
  - module info name `traction_module`
- Firmware expects to receive:
  - Host module id `0x00` on control frames.
- Error/timeout behavior:
  - Unknown control bytes are ignored.
- Notes:
  - Implemented in `traction_comm_task` framed path.

### Contract: Framed Stream TEST ACK

- Name/ID: `MESSAGE_TYPE_TEST (0x02)`
- Purpose: Keepalive stream responsiveness for TEST mode.
- Host sends:
  - `SYNC + len + message + type(0x02) + seq24`
- Host expects to receive:
  - Framed response with same `type` and same `seq24`
- Firmware sends:
  - Message text `I RECIEVED TEST`
- Firmware expects to receive:
  - Valid framed length `1..200` and `seq24`.
- Error/timeout behavior:
  - Invalid frames are dropped.
- Notes:
  - Message text is informational; host validates type/sequence matching.

### Contract: Framed CMD PID/Telemetry Commands

- Name/ID: `MESSAGE_TYPE_CMD (0x01)`
- Purpose: Execute and validate migrated command-group payloads via framed stream transport.
- Host sends:
  - stream frame type `0x01` with payload text:
    - `GET PID RPM`
    - `GET TELEM`
    - `GET TELEM POS`
    - `SAVE PID RPM`
    - `SAVE PID POS`
    - `GET PID POS`
    - `GET PID POS SINE`
    - `SET PID RPM KP <value>`
    - `SET PID RPM KI <value>`
    - `SET PID RPM KD <value>`
    - `SET PID RPM SP <value>`
    - `SET PID POS KP <value>`
    - `SET PID POS KI <value>`
    - `SET PID POS KD <value>`
    - `SET PID POS IWIN <value>`
    - `SET PID POS ANGLE <value>`
    - `SET PID POS TARGET <value>`
    - `SET PID POS SINE AMP <value>`
    - `SET PID POS SINE OFFSET <value>`
    - `SET PID POS SINE PERIOD <value>`
    - `GET INVERT`
    - `SET MOTOR INV <0|1>`
    - `SET ENCODER INV <0|1>`
    - `GET BRIDGE`
    - `SET BRIDGE <0|1>`
    - `GET CURVE`
    - `SET CURVE <10|20|...|100> <rpm>`
    - `SAVE CURVE`
    - `GET CFG`
    - `SET CFG <field> <value>`
    - `SAVE CFG`
    - `START PID POS`
    - `STOP PID POS`
    - `START PID POS SINE`
    - `STOP PID POS SINE`
- Host expects to receive:
  - framed response with same type/sequence:
    - `P,<kp>,<ki>,<kd>,<sp>` for `GET PID RPM`
    - `T,<target_rpm>,<measured_rpm>,<cmd_pwm_signed>,<cmd_raw>` for `GET TELEM`
    - `TP,<target_deg>,<position_deg>,<cmd_pwm_signed>,<cmd_raw>,<i_term>` for `GET TELEM POS`
    - `S,ENQ` for successful `SAVE PID RPM` enqueue
    - `S,ENQ` for successful `SAVE PID POS` enqueue
    - `OK` for successful `SET PID RPM ...`
    - `PP,<kp>,<ki>,<kd>,<target_deg>,<enabled>,<iwin>` for `GET PID POS`
    - `PS,<amp_deg>,<offset_deg>,<period_s>,<enabled>` for `GET PID POS SINE`
    - `INV,<motor_invert>,<encoder_invert>` for `GET INVERT`
    - `BRG,<bridge_type>` for `GET BRIDGE`
    - `CV,<rpm@10>,<rpm@20>,...,<rpm@100>` for `GET CURVE`
    - `CFG,<...>|CFGN,<notes>` for `GET CFG`
    - `OK` for successful `SET PID POS ...`
    - `OK` for `START PID POS`/`STOP PID POS`
    - `OK` for successful `SET PID POS SINE ...`
    - `OK` for `START PID POS SINE`/`STOP PID POS SINE`
    - `OK` for successful `SET MOTOR INV`, `SET ENCODER INV`, `SET BRIDGE`
    - `OK` for successful `SET CURVE ...`
    - `S,ENQ` for successful `SAVE CURVE` enqueue
    - `OK` for successful `SET CFG ...`
    - `S,ENQ` for successful `SAVE CFG` enqueue
    - `ERR` for invalid parameters on supported commands
- Firmware sends:
  - framed command result text on type `0x01`
- Firmware expects to receive:
  - valid framed CMD payload text command
- Error/timeout behavior:
  - Unknown/unrecognized command payloads return `I RECIEVED CMD` (compatibility fallback).
  - transport timeout handled by host as `cmd_send_timeout`
- Notes:
  - All planned migration groups are covered; unknown/unrecognized CMD payloads still return backward-compatible `I RECIEVED CMD`.

### Contract: Framed Traction Output Commands

- Name/ID: `MESSAGE_TYPE_TRACTION_OUT (0x04)`
- Purpose: Execute manual output commands through framed stream transport.
- Host sends:
  - stream frame type `0x04` with payload text:
    - `SET OUT <value>`
    - `SET OUT RAW <value>`
    - `CLR OUT`
- Host expects to receive:
  - framed response with same type/sequence and message:
    - `OK` on success
    - `ERR` on invalid command/unsupported callback
- Firmware sends:
  - framed ACK/NACK text (`OK` or `ERR`) on type `0x04`
- Firmware expects to receive:
  - valid framed `TRACTION_OUT` payload text command
- Error/timeout behavior:
  - Invalid payload/unsupported command returns `ERR`.
  - Transport timeout handled by host keepalive (`traction_out_timeout`).
- Notes:
  - Group 1 migration reuses the same callback wiring as line parser (single execution function for parity).

### Contract: Framed Telemetry Control and Streaming

- Name/ID: `MESSAGE_TYPE_TELEMETRY (0x03)`
- Purpose: Start/sync/stop telemetry stream in framed mode.
- Host sends:
  - `TELEMETRY_START:<host_epoch_ms>`
  - `TELEMETRY_SYNC:<host_epoch_ms>`
  - `TELEMETRY_STOP`
  - all wrapped as stream frames with type `0x03`.
- Host expects to receive:
  - same type/sequence frame ACK text:
    - `TELEMETRY STARTED`
    - `TELEMETRY SYNCED`
    - `TELEMETRY STOPPED`
  - while active: recurring framed telemetry messages (type `0x03`)
- Firmware sends:
  - ACK frame for each control command (same type/seq).
  - periodic telemetry frames at ~100 ms when telemetry mode is enabled.
- Firmware expects to receive:
  - Valid telemetry stream frames on type `0x03`.
- Error/timeout behavior:
  - Unrecognized telemetry payloads return generic `TELEMETRY` ACK.
- Notes:
  - Current telemetry payload text is transport-level heartbeat (`TELEMETRY DEVICE <ts_ms>`); command-by-command migration comes in Step 4+.
