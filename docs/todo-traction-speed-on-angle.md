# TODO: Speed control for move_angle

## What's missing

`move_angle()` currently runs at whatever speed the position PID's gains allow
(full PWM until close to target). There is no way to limit the approach speed
from the Python side without firmware changes.

## Why it wasn't added

The firmware's position PID loop (`traction_control_app_speed_task`) has no
serial command for clamping the output. The only available per-move levers are
the PID gains (`SET PID POS KP/KI/KD`) and the target angle
(`SET PID POS ANGLE`). Scaling KP as a speed proxy was considered and rejected
because it changes the settling dynamics, not just the cruise speed, and the
effect isn't intuitive to users.

## What the firmware needs

Add a new command to the traction_comm layer, e.g.:

```
SET PID POS MAX_OUT <percent>   # clamp position PID output to 0–100 %
GET PID POS MAX_OUT             # read current clamp value
```

In `traction_control_app.c`, add a `s_pos_out_max_pct` field (default 100.0)
and replace the hard-coded `MOTOR_MAX_OUTPUT_PCT` cap in the position PID branch
with this configurable value. Expose set/get via `traction_comm_cfg_t` callbacks.

## Python API (once firmware supports it)

```python
motor.move_angle(180, speed=50)   # 50 % max PWM during the move
motors.move_angle(90, speed=75)   # group call, same speed for all
```

`speed` (1–100) would map directly to `SET PID POS MAX_OUT <speed>` before
`START PID POS`, and restore the original max-out value after the move
completes (or leave it set if non-blocking and user calls join).
