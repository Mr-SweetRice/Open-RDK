# TractionModule

Controls a traction (motor) module. Inherits all properties and raw helpers from [BaseModule](base-module.md).

```python
motor = runtime.traction("98:3D:AE:41:97:C4")
# or
motor = runtime.module("98:3D:AE:41:97:C4")  # auto-detects type
```

→ [CommsRuntime](runtime.md) · [BaseModule](base-module.md) · [Errors](errors.md)

---

## Class Constant

### `EXPECTED_MODULE_TYPE = "traction_module"`

---

## Output Range

All motor output commands are sent through the generic `CONTROL (0x04)` frame type. The host still exposes `traction_out_value` in the registry because it is the motor output setpoint.

Signed movement values are clamped to `[-100, 100]`; raw magnitude helpers clamp to the same range before sending `SET OUT RAW <value>`.

---

## Basic Movement

### `forward(value, timeout_sec=1.5) → dict`
Set a positive RPM setpoint then send `SET OUT <value>`.

| Parameter | Description |
|-----------|-------------|
| `value` | Output magnitude `0–100` |
| `timeout_sec` | Per-command timeout |

```python
motor.forward(60)
```

---

### `backward(value, timeout_sec=1.5) → dict`
Set a negative RPM setpoint then send `SET OUT <value>`.

```python
motor.backward(40)
```

---

### `forward_raw(value, timeout_sec=1.5) → dict`
Send `SET OUT RAW <value>` directly. Bypasses RPM setpoint direction — raw PWM output.

```python
motor.forward_raw(75)
```

---

### `stop(timeout_sec=1.5) → dict`
Send `CLR OUT`. Stops motor output immediately.

```python
motor.stop()
```

---

## Angle Movement

Reads the current encoder position and moves a relative number of degrees using the position PID.

### `move_angle(direction, angle_deg, timeout_sec=1.5) → dict`

| Parameter | Description |
|-----------|-------------|
| `direction` | Direction string (see table below) |
| `angle_deg` | Degrees to move (must be ≥ 0) |
| `timeout_sec` | Per-command timeout (minimum 2.0 internally) |

**Direction aliases:**

| Forward | Backward |
|---------|----------|
| `"forward"`, `"fwd"`, `"f"` | `"backward"`, `"reverse"`, `"rev"`, `"b"` |
| `"cw"`, `"clockwise"`, `"+"`, `"positive"` | `"ccw"`, `"counterclockwise"`, `"-"`, `"negative"` |

Returns a dict with:

| Key | Description |
|-----|-------------|
| `direction` | Normalized direction string |
| `angle_delta_deg` | Requested delta |
| `current_position_deg` | Encoder position before move |
| `current_target_deg` | PID target before move |
| `pid_enabled_before_move` | Whether position PID was active |
| `base_source` | `"target"` or `"position"` — which value the delta was applied to |
| `target_position_deg` | Computed target sent to firmware |
| `start_result` | Result of `START PID POS` |
| `set_target_result` | Result of `SET PID POS ANGLE <target>` |
| `telem` | Full position telemetry snapshot |
| `pid` | Full position PID snapshot |

```python
motor.move_angle("forward", 90)
motor.move_angle("b", 45)
```

---

### `move_angle_forward(angle_deg, timeout_sec=1.5) → dict`
Shorthand for `move_angle("forward", angle_deg, timeout_sec)`.

```python
motor.move_angle_forward(180)
```

---

### `move_angle_backward(angle_deg, timeout_sec=1.5) → dict`
Shorthand for `move_angle("backward", angle_deg, timeout_sec)`.

```python
motor.move_angle_backward(90)
```

---

## Position Telemetry

### `get_position_telemetry(timeout_sec=1.5) → dict`
Send `GET TELEM POS`. Firmware response format: `TP,<target_deg>,<position_deg>,<cmd_pwm_signed>,<cmd_raw>,<i_term>`.

Returns:

| Key | Type | Description |
|-----|------|-------------|
| `target_deg` | `float` | PID target position |
| `position_deg` | `float` | Current encoder position |
| `cmd_pwm_signed` | `float` | Signed PWM command output |
| `cmd_raw` | `float` | Raw PWM value |
| `i_term` | `float` | Integrator term |
| `raw` | `dict` | Full `send_raw_cmd` result |

```python
telem = motor.get_position_telemetry()
print(telem["position_deg"])
```

---

## Position PID

### `get_position_pid(timeout_sec=1.5) → dict`
Send `GET PID POS`. Firmware response format: `PP,<kp>,<ki>,<kd>,<target_deg>,<enabled>,<iwin>`.

Returns:

| Key | Type | Description |
|-----|------|-------------|
| `kp` | `float` | Proportional gain |
| `ki` | `float` | Integral gain |
| `kd` | `float` | Derivative gain |
| `target_deg` | `float` | Current PID target |
| `enabled` | `bool` | Whether position PID is active |
| `integral_window_deg` | `float` | Anti-windup window |
| `raw` | `dict` | Full `send_raw_cmd` result |

```python
pid = motor.get_position_pid()
print(pid["enabled"], pid["target_deg"])
```

---

## RPM PID

### `get_pid_rpm(timeout_sec=1.5) → dict`
Send `GET PID RPM`. Firmware response format: `P,<kp>,<ki>,<kd>,<sp>`.

Returns:

| Key | Type | Description |
|-----|------|-------------|
| `kp` | `float` | Proportional gain |
| `ki` | `float` | Integral gain |
| `kd` | `float` | Derivative gain |
| `sp` | `float` | Current RPM setpoint |
| `raw` | `dict` | Full `send_raw_cmd` result |

```python
gains = motor.get_pid_rpm()
print(gains["kp"], gains["sp"])
```

---

### `set_pid_rpm(kp=None, ki=None, kd=None, timeout_sec=1.5) → dict`
Set one or more RPM PID gains. At least one of `kp`, `ki`, `kd` must be provided.

Sends `SET PID RPM KP <value>`, `SET PID RPM KI <value>`, `SET PID RPM KD <value>` for each provided gain.

Returns a dict with keys `"kp"`, `"ki"`, `"kd"` mapped to the individual command results (only for gains that were set).

```python
motor.set_pid_rpm(kp=1.2, ki=0.05)
motor.set_pid_rpm(kp=1.2, ki=0.05, kd=0.01)
```

---

## Example

```python
from openrdk import CommsRuntime

runtime = CommsRuntime(auto_start=True, enable_webview=False)
motor = runtime.traction("98:3D:AE:41:97:C4")

# Tune RPM PID
motor.set_pid_rpm(kp=1.5, ki=0.1, kd=0.0)

# Move forward 180 degrees, then back
motor.move_angle_forward(180)
motor.move_angle_backward(180)

# Read current state
telem = motor.get_position_telemetry()
print(f"position: {telem['position_deg']:.1f}°")

motor.stop()
runtime.stop()
```
