# Standard Host Public API Baseline

This document freezes the public API observed on the Raspberry Pi standard host
at the start of `CHost` Step 1. It is a compatibility target, not permission for
`openrdkC` production code to import or control the standard runtime.

Standard import:

```python
from openrdk import CommsRuntime
```

Native optional import:

```python
from openrdkC import CommsRuntimeC
```

## Runtime

`CommsRuntime` exposes:

- Properties: `db_path`, `comms_log_path`, `is_running`, `webview_enabled`,
  `webview_updates_enabled`, `is_webview_running`, `webview_url`, `mdns_url`,
  `last_error`, `last_webview_error`, `lan_ip`, `supported_firmware_types`.
- Lifecycle: `start`, `ensure_running`, `stop`.
- Discovery: `list_devices`, `get_device`, `require_device`, `wait_online`,
  `find_device_by_serial`, `find_device_by_name`, `get_serial_by_name`.
- Management: `rename_device`, `post`, `flash_firmware`,
  `flash_firmware_by_port`.
- Factories: `module`, `traction`, `motors`, `line_sensor`, `color_sensor`,
  `distance_sensor`.

`CommsRuntimeC` must provide equivalent behavior for discovery, lifecycle, and
module factories before it is called compatible. Flashing and webview support
may be delivered after core SDK parity, but missing features must raise an
explicit `NotImplementedError`, never silently invoke `openrdk`.

## Base module

Properties:

- `module_type`
- `status`
- `is_online`

Methods:

- `refresh`
- `send_raw_cmd`
- `send_raw_control`
- `send_raw_traction`

## Traction module

- `join`
- `move`
- `forward`
- `backward`
- `forward_raw`
- `stop`
- `get_position_telemetry`
- `get_position_pid`
- `move_angle`
- `move_angle_forward`
- `move_angle_backward`
- `set_pid_rpm`
- `get_pid_rpm`

## Color sensor module

- `get_data`
- `get_color`
- `get_config`
- `get_info`
- `get_calibration`
- `get_calibration_patch`
- `run_selftest`
- `set_config`
- `save_config`
- `reset_config`
- `start_calibration`
- `stop_calibration`
- `select_calibration_patch`
- `commit_calibration_patch`
- `save_calibration`
- `reset_calibration`

## Line sensor module

- Property: `lost_position_mode`
- `set_lost_position_mode`
- `get_data`
- `get_values`
- `get_raw`
- `get_position`
- `get_info`
- `get_config`
- `get_calibration`
- `set_track_type`
- `set_digital_threshold`
- `set_detect_threshold`
- `set_calibration_time`
- `set_name`
- `save_config`
- `calibrate`
- `stop_calibration`
- `save_calibration`
- `start_telemetry`
- `stop_telemetry`
- `start_streaming`
- `stop_streaming`
- `get_latest_data`
- `get_latest_values`
- `get_latest_position`

## Distance sensor module

- `decode_health_flags`
- `get_data`
- `read`
- `get_distance_mm`
- `get_distance_cm`
- `get_distance`
- `get_info`
- `get_config`
- `set_name`
- `set_sample_period_ms`
- `set_sample_period`
- `set_max_distance_mm`
- `set_max_distance`
- `set_filter_window`
- `save_config`
- `reset_config`
- `run_selftest`
- `selftest`
- `start_telemetry`
- `stop_telemetry`
- `start_streaming`
- `stop_streaming`
- `get_latest_data`
- `get_latest_distance`

## Motors group

- Property: `names`
- `move`
- `move_angle`
- `stop`
- `join`
- `run_together`

## Compatibility rules

1. Native methods use the same units, bounds, defaults, and signed-direction
   conventions.
2. Existing dictionary keys remain stable.
3. Native-only metadata may be added under a namespaced `native` key.
4. Exceptions map to the equivalent `openrdkC.errors` class.
5. Timeouts use seconds in Python and monotonic milliseconds internally.
6. A device serial is mandatory for every device operation.
7. Deprecated aliases remain available until a documented major release.
8. The native runtime must never acquire a port already owned by the standard
   runtime.

