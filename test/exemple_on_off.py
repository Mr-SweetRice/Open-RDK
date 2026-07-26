#!/usr/bin/env python3
"""
Simple proportional line follower using all five line-sensor values.

Sensor order on the current PCB:
  index:       0      1      2      3      4
  position:  right                  left

PYTHONPATH=host/main/src python3 test/exemple_on_off.py
"""

from __future__ import annotations

import time

from openrdk import CommsRuntime


SENSOR_NAME = "linha"
MOTOR_E_NAME = "esquerda"
MOTOR_D_NAME = "direita"

BASE_SPEED = 70.0
KP = 45.0
MAX_SPEED = 150.0
MIN_SIGNAL = 0.05

# Positive error means that the line is toward sensor 0 (robot right).
SENSOR_WEIGHTS = (1.0, 0.5, 0.0, -0.5, -1.0)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def calculate_error(values: list[float], previous_error: float) -> float:
    """Calculate line position from all five normalized sensor values."""
    if len(values) != len(SENSOR_WEIGHTS):
        raise ValueError(f"expected 5 sensor values, received {len(values)}")

    signal = sum(values)
    if signal < MIN_SIGNAL:
        # No useful sensor signal: retain the last steering direction.
        return previous_error

    weighted_sum = sum(
        value * weight
        for value, weight in zip(values, SENSOR_WEIGHTS)
    )
    return weighted_sum / signal


def main() -> None:
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,
        enable_webview_updates=True,
    )
    openrdk.post("webview_complete")

    sensor_serial = openrdk.get_serial_by_name(SENSOR_NAME)
    motor_e_serial = openrdk.get_serial_by_name(MOTOR_E_NAME)
    motor_d_serial = openrdk.get_serial_by_name(MOTOR_D_NAME)

    print("waiting for devices...")
    openrdk.wait_online(sensor_serial)
    openrdk.wait_online(motor_e_serial)
    openrdk.wait_online(motor_d_serial)

    sensor = openrdk.line_sensor(sensor_serial)
    motors = openrdk.motors(
        {"E": motor_e_serial, "D": motor_d_serial},
        inverted="D",
    )

    previous_error = 0.0
    last_print = 0.0

    print("proportional control active — Ctrl+C to stop")

    try:
        while True:
            # get_values() is paced by the runtime-owned telemetry stream.
            values = sensor.get_values()
            error = calculate_error(values, previous_error)
            correction = KP * error

            left_speed = clamp(
                BASE_SPEED + correction,
                -MAX_SPEED,
                MAX_SPEED,
            )
            right_speed = clamp(
                BASE_SPEED - correction,
                -MAX_SPEED,
                MAX_SPEED,
            )

            motors.E.move(right_speed)
            motors.D.move(left_speed)
            motors.join()

            previous_error = error

            now = time.monotonic()
            if now - last_print >= 0.25:
                print(
                    f"values={[round(v, 3) for v in values]} "
                    f"error={error:+.3f} "
                    f"motors=({left_speed:.1f}, {right_speed:.1f})"
                )
                last_print = now

    except KeyboardInterrupt:
        print("\nshutdown requested")
    finally:
        motors.stop()
        openrdk.stop()


if __name__ == "__main__":
    main()
