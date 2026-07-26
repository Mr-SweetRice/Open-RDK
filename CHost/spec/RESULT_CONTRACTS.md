# Python Result Shape Contracts

Values below are representative. Types and keys are the compatibility contract.

## Runtime device snapshot

```python
{
    "serial_number": str,
    "name": str,
    "module_type": str,
    "firmware_module": str,
    "module_id": int,
    "device_node": str,
    "status": str,
    "link_status": str,
    "message_type": str,
    "telemetry_requested": bool,
    "telemetry_active": bool,
}
```

Additional diagnostic fields are allowed. Required identity and state fields
must not change type.

## Raw command result

```python
{
    "ok": bool,
    "serial_number": str,
    "command": str,
    "response": str,
    "error_kind": str | None,
    "latency_ms": float | None,
}
```

CONTROL results may expose the acknowledgment as `ack`.

## Line sensor data

```python
{
    "raw": [int, int, int, int, int],
    "values": [float, float, float, float, float],
    "digital": [bool, bool, bool, bool, bool],
    "position": float,
    "strength": float,
    "line_detected": bool,
    "calibrating": bool,
    "calibration_remaining_ms": int,
}
```

Line configuration:

```python
{
    "track_type": int,
    "track_type_name": str,
    "digital_threshold": float,
    "detect_threshold": float,
    "calibration_time_ms": int,
    "sensor_name": str,
}
```

Line calibration:

```python
{"min_raw": [int, int, int, int, int],
 "max_raw": [int, int, int, int, int]}
```

## Color sensor data

```python
{
    "kind": str,
    "palette_mode": int,
    "detected_slot": int,
    "confidence_milli": int,
    "color_name": str,
    "detected_color": {
        "slot": int, "name": str, "hex": str | None, "enabled": bool
    } | None,
    "top": [
        {"slot": int, "confidence_milli": int, "name": str, "hex": str | None}
    ],
    "raw": {"r": int, "g": int, "b": int, "c": int},
    "norm_rgb_milli": {"r": int, "g": int, "b": int},
    "lab_l_centi": int,
    "lab_a_centi": int,
    "lab_b_centi": int,
    "luma_milli": int,
    "gain": int,
    "integration_ms": int,
    "led_mode": int,
    "led_active": int,
    "health_flags": int,
    "health": dict[str, bool],
    "classifier": int,
    "calibration_target_slot": int,
    "calibration_samples": int,
    "sample_timestamp_ms": int,
}
```

Color configuration uses `sensor_name`, `sample_period_ms`, `led_mode`,
`gain_mode`, `gain`, `integration_ms`, `classifier`, `confidence_milli`,
`target_clear`, `palette_mode`, and `patch_sample_count`.

## Distance sensor data

The exact current Pi parser is the source of truth. Native parity must preserve
the keys produced by `DistanceSensorModule.get_data`, including distance,
validity, timeout, sample timestamp/age, filtering, health flags, and raw result
metadata. Step 1 capture tooling records the live shape without sending a
command unless explicitly requested.

Convenience returns:

- `get_distance_mm()` -> `int | None`
- `get_distance_cm()` -> `float | None`
- `get_distance(unit)` -> `int | float | None`

## Traction PID results

RPM PID:

```python
{"kp": float, "ki": float, "kd": float, "sp": float, "raw": dict}
```

Position telemetry:

```python
{
    "target_deg": float,
    "position_deg": float,
    "cmd_pwm_signed": float,
    "cmd_raw": float,
    "i_term": float,
    "raw": dict,
}
```

Position PID:

```python
{
    "kp": float,
    "ki": float,
    "kd": float,
    "target_deg": float,
    "enabled": bool,
    "integral_window_deg": float,
    "raw": dict,
}
```

## Failure contract

- Missing serial -> `DeviceNotFoundErrorC` or argument error before I/O.
- Offline device -> `DeviceOfflineErrorC`.
- Wrong module type -> `ModuleTypeMismatchErrorC`.
- Timeout/protocol rejection -> `CommandFailedErrorC` with stable
  `error_kind`.
- Invalid numeric user input -> `ValueError`.
- No valid latest telemetry -> `None` only for methods whose standard return
  annotation permits it.

