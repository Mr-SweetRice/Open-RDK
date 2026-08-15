#!/usr/bin/env python3
"""Exercise two traction motors while continuously displaying encoder state.

Run from the repository root:
    .venv/Scripts/python.exe test/encoder_live_two_motors.py

After the automatic movement sequence, both motors are stopped and encoder
watching continues. Turn either motor by hand to observe its position changing.
Press Ctrl+C to finish.
"""

from __future__ import annotations

import json
import threading
import time

from openrdk import CommsRuntime


LEFT_MOTOR_NAME = "esquerda"
RIGHT_MOTOR_NAME = "direita"
ENCODER_INTERVAL_SEC = 0.10
ANGLE_SETTLE_SEC = 1.0
ANGLE_TURNS_PER_DIRECTION = 3
SPEED_PERCENT = 0
SPEED_MOVE_DURATION_SEC = 2.0


def watch_encoders(motors, stop_event: threading.Event) -> None:
    """Print cached encoder values without blocking movement commands."""
    while not stop_event.is_set():
        states = motors.get_encoders(interval_sec=ENCODER_INTERVAL_SEC)
        print(json.dumps(states, ensure_ascii=False), flush=True)
        stop_event.wait(ENCODER_INTERVAL_SEC)


def run_automatic_sequence(motors) -> None:
    for direction in (1, -1):
        for turn_number in range(1, ANGLE_TURNS_PER_DIRECTION + 1):
            angle = 90 * direction
            print(f"\nMoving {angle:+d} degrees ({turn_number}/{ANGLE_TURNS_PER_DIRECTION})")
            motors.move_angle(angle)
            time.sleep(ANGLE_SETTLE_SEC)

    for direction in (1, -1):
        speed = SPEED_PERCENT * direction
        print(f"\nMoving at {speed:+d}% for {SPEED_MOVE_DURATION_SEC:.1f} seconds")
        motors.move(speed, duration=SPEED_MOVE_DURATION_SEC)
        time.sleep(ANGLE_SETTLE_SEC)


def main() -> None:
    runtime = CommsRuntime(
        auto_start=True,
        enable_webview=False,
        enable_webview_updates=False,
    )
    watcher_stop = threading.Event()
    watcher: threading.Thread | None = None
    motors = None

    try:
        time.sleep(3.0)
        left_serial = runtime.get_serial_by_name(LEFT_MOTOR_NAME)
        right_serial = runtime.get_serial_by_name(RIGHT_MOTOR_NAME)
        if not isinstance(left_serial, str) or not isinstance(right_serial, str):
            raise RuntimeError("Could not find both 'esquerda' and 'direita' traction modules")

        motors = runtime.motors(
            {"esquerda": left_serial, "direita": right_serial},
            inverted="direita",
        )

        watcher = threading.Thread(
            target=watch_encoders,
            args=(motors, watcher_stop),
            name="encoder-live-printer",
            daemon=True,
        )
        watcher.start()

        run_automatic_sequence(motors)
        motors.stop()

        print(
            "\nAutomatic sequence complete. Motors are stopped.\n"
            "Turn them by hand to watch the encoder values. Press Ctrl+C to exit."
        )
        while True:
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\nStopping test...")
    finally:
        if motors is not None:
            motors.stop()
            motors.stop_encoder_updates()
        watcher_stop.set()
        if watcher is not None:
            watcher.join(timeout=1.0)
        runtime.stop()


if __name__ == "__main__":
    main()
