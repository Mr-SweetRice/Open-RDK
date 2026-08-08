#!/usr/bin/env python3
"""Simple example for measuring realistic sensor-loop latency.

Run with:
    PYTHONPATH=host/main/src python3 test/latency_probe.py

Only one runtime should be running while using this standalone example.
"""

from openrdk import CommsRuntime, StreamLatencyWatch


BASE_SPEED = 55
TURN_SPEED = 20
POSITION_DEADBAND = 0.10


runtime = CommsRuntime(auto_start=True, enable_webview=False)
motors = None

try:
    # LINE SENSOR — full data:
    # sensor = runtime.line_sensor(runtime.get_serial_by_name("linha"))
    # watch = StreamLatencyWatch(sensor, getter="get_data", expected_period_ms=20)

    # LINE SENSOR — position:
    sensor = runtime.line_sensor(runtime.get_serial_by_name("linha"))
    watch = StreamLatencyWatch(
        sensor,
        name="linha position",
        getter="get_position",
        expected_period_ms=20,
    )
    left_serial = runtime.get_serial_by_name("esquerda")
    right_serial = runtime.get_serial_by_name("direita")
    runtime.wait_online(left_serial)
    runtime.wait_online(right_serial)
    motors = runtime.motors(
        {"left": left_serial, "right": right_serial},
        inverted="right",
    )

    # LINE SENSOR — normalized values:
    # sensor = runtime.line_sensor(runtime.get_serial_by_name("linha"))
    # watch = StreamLatencyWatch(sensor, getter="get_values", expected_period_ms=20)

    # COLOR SENSOR — detected color:
    # sensor = runtime.color_sensor(runtime.get_serial_by_name("cor"))
    # watch = StreamLatencyWatch(sensor, getter="get_color")

    count = 0
    while True:
        value = watch.read(timeout_sec=1.5)

        # Simple bang-bang line following for latency testing.
        position = value["position"]
        if not value["line_detected"]:
            left_speed = 0
            right_speed = 0
        elif position < -POSITION_DEADBAND:
            # Line is left: slow the left wheel.
            left_speed = TURN_SPEED
            right_speed = BASE_SPEED
        elif position > POSITION_DEADBAND:
            # Line is right: slow the right wheel.
            left_speed = BASE_SPEED
            right_speed = TURN_SPEED
        else:
            left_speed = BASE_SPEED
            right_speed = BASE_SPEED

        motors.left.move(left_speed)
        motors.right.move(right_speed)
        motors.join()

        count += 1
        if count % 100 == 0:
            print(watch.summary())

except KeyboardInterrupt:
    print("\nStopped.")
    if "watch" in locals():
        print(watch.summary())
finally:
    if motors is not None:
        motors.stop()
    runtime.stop()
