#!/usr/bin/env python3
"""
Bang-bang line follower with 90° corner turns — black line on white background (DARK mode).

Normal steering uses firmware position field (range -1..+1):
  pos > 0  → line is left  → turn left
  pos < 0  → line is right → turn right
  |pos| ≤ CENTER_BAND → centered → straight

When the line is lost for longer than LINE_LOST_MS the robot executes a 90°
pivot in the last known steering direction to recover the corner.

PYTHONPATH=host/main/src python3 test/exemple_on_off.py
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime

SENSOR_NAME    = "linha"
MOTOR_E_NAME   = "motor_esq1"   # E = esquerdo (left wheel)
MOTOR_D_NAME   = "motor_dir1"   # D = direito  (right wheel, inverted)

# ── bang-bang steering ────────────────────────────────────────────────────
STRAIGHT_SPEED = 40    # both motors when centred
TURN_INNER     = 5     # slower wheel during a turn
TURN_OUTER     = 70    # faster wheel during a turn
CENTER_BAND    = 0.05  # |pos| below this → go straight

# ── 90° corner recovery ────────────────────────────────────────────────────
PIVOT_SPEED            = 40    # motor % during a pivot
PIVOT_STOP_BAND        = 0.5   # |position| to declare pivot done (line re-centred)
CORNER_WINDOW_LEFT_MS  = 125   # time window for left-side sensors (tune if left misses)
CORNER_WINDOW_RIGHT_MS = 100   # time window for right-side sensors (tune if right misses)
CORNER_FRAMES          = 3     # consecutive frames the window condition must hold before pivot fires
LINE_LOST_MS           = 300   # ms without line before triggering a lost-line recovery pivot

# ── calibration ───────────────────────────────────────────────────────────
CAL_DURATION_MS   = 5000
CAL_MIN_RANGE     = 200
TARGET_TRACK_TYPE = 1   # 1 = DARK (black line / white background)


# ── helpers ───────────────────────────────────────────────────────────────

def _check_calibration(sensor) -> bool:
    cal = sensor.get_calibration()
    cal_min = cal.get("min_raw", [])
    cal_max = cal.get("max_raw", [])
    if not cal_min or not cal_max:
        return False
    for i in range(len(cal_min)):
        if cal_min[i] == 0 and cal_max[i] == 4095:
            return False
        if cal_max[i] - cal_min[i] < CAL_MIN_RANGE:
            return False
    return True


def _setup_sensor(sensor) -> None:
    cfg = sensor.get_config()
    track_type = int(cfg.get("track_type", 0))
    track_name = "DARK" if track_type == 1 else "LIGHT"

    print(f"  track_type : {track_name}")
    print(f"  detect_th  : {cfg.get('detect_threshold', '?')}")

    if track_type != TARGET_TRACK_TYPE:
        print(f"\ntrack_type is {track_name} — switching to DARK...")
        sensor.set_track_type(TARGET_TRACK_TYPE)
        sensor.save_config()
        print("track_type saved.")

    if _check_calibration(sensor):
        print("\ncalibration OK — skipping")
    else:
        print(f"\ncalibration needed — sweep sensor over BOTH surfaces for {CAL_DURATION_MS // 1000} s")
        print("  • white background  →  raw HIGH  →  VAL ≈ 0.0 in DARK mode")
        print("  • black line        →  raw LOW   →  VAL ≈ 1.0 in DARK mode")
        input("  press Enter when ready, then move sensor continuously...")
        sensor.calibrate(duration_ms=CAL_DURATION_MS, wait=True)
        sensor.save_config()
        print("calibration done and saved.")


def _pivot(motors, sensor, direction: int) -> None:
    """Spin in place until the line is re-centred (|pos| <= PIVOT_STOP_BAND)."""
    speed = direction * PIVOT_SPEED
    while True:
        data = sensor.get_latest_data()
        if data is None:
            time.sleep(0.002)
            continue
        if data["line_detected"] and abs(data["position"]) <= PIVOT_STOP_BAND:
            break
        motors.E.move(speed)
        motors.D.move(speed)
        motors.join()


# ── main ──────────────────────────────────────────────────────────────────

def main():
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,
        enable_webview_updates=True,
    )
    openrdk.post("webview_complete")

    sensor_serial  = openrdk.get_serial_by_name(SENSOR_NAME)
    motor_e_serial = openrdk.get_serial_by_name(MOTOR_E_NAME)
    motor_d_serial = openrdk.get_serial_by_name(MOTOR_D_NAME)

    print("waiting for devices...")
    openrdk.wait_online(sensor_serial)
    openrdk.wait_online(motor_e_serial)
    openrdk.wait_online(motor_d_serial)
    print("all devices online\n")

    sensor = openrdk.line_sensor(sensor_serial)
    motors = openrdk.motors(
        {"E": motor_e_serial, "D": motor_d_serial},
        inverted="D",
    )

    print("── sensor setup ──────────────────────────────────")
    _setup_sensor(sensor)
    print("──────────────────────────────────────────────────\n")

    print("starting streaming...")
    sensor.start_streaming()

    deadline = time.monotonic() + 5.0
    while sensor.get_latest_data() is None:
        if time.monotonic() > deadline:
            print("ERROR: no sensor data after 5 s — check connection")
            sensor.stop_streaming()
            openrdk.stop()
            return
        time.sleep(0.005)

    print("sensor streaming active")
    print(f"webview: {openrdk.webview_url}/line-sensor?serial={sensor_serial}\n")
    print("starting control loop — Ctrl+C to stop")

    last_dir      = 1          # +1 right, -1 left — last steering direction
    lost_since    = 0.0        # monotonic time when line was last seen lost
    corner_streak = 0          # consecutive frames the windowed corner condition holds
    last_active   = [0.0] * 5  # per-sensor last timestamp with digital == 1

    try:
        while True:
            data = sensor.get_latest_data()
            if data is None:
                time.sleep(0.002)
                continue

            now = time.monotonic()

            if not data["line_detected"]:
                if lost_since == 0.0:
                    lost_since = now
                elif (now - lost_since) >= (LINE_LOST_MS / 1000.0):
                    lost_since = 0.0
                    corner_streak = 0
                    last_active[:] = [0.0] * 5
                    _pivot(motors, sensor, last_dir)
                continue

            # line visible — reset lost timer
            lost_since = 0.0

            # ── corner detection ───────────────────────────────────────────────
            # Sensors on the same physical side can activate in rapid succession
            # rather than simultaneously (50 ms apart is common on short corners).
            # Strategy: keep a per-sensor last-active timestamp; a corner is
            # declared when BOTH side sensors have fired within CORNER_WINDOW_MS.
            # Sensor array is physically mirrored: S4(idx 4) is left, S0(idx 0) right.
            for i, d in enumerate(data["digital"]):
                if d:
                    last_active[i] = now

            wl = CORNER_WINDOW_LEFT_MS  / 1000.0
            wr = CORNER_WINDOW_RIGHT_MS / 1000.0
            left_recent  = (now - last_active[3]) <= wl and (now - last_active[4]) <= wl
            right_recent = (now - last_active[0]) <= wr and (now - last_active[1]) <= wr

            if (left_recent and not right_recent) or (right_recent and not left_recent):
                corner_streak += 1
                if corner_streak >= CORNER_FRAMES:
                    corner_streak = 0
                    last_active[:] = [0.0] * 5
                    last_dir = -1 if left_recent else 1
                    _pivot(motors, sensor, last_dir)
                continue
            else:
                corner_streak = 0

            pos = data["position"]

            # ── bang-bang steering ─────────────────────────────────────────────
            if abs(pos) <= CENTER_BAND:
                motors.E.move(STRAIGHT_SPEED)
                motors.D.move(STRAIGHT_SPEED)
            elif pos > 0:               # line is left → turn left
                motors.E.move(TURN_OUTER)
                motors.D.move(TURN_INNER)
            else:                       # line is right → turn right
                motors.E.move(TURN_INNER)
                motors.D.move(TURN_OUTER)

            if pos > 0.1:
                last_dir = -1
            elif pos < -0.1:
                last_dir = 1

            motors.join()

    except KeyboardInterrupt:
        print("\nshutdown")
    finally:
        sensor.stop_streaming()
        motors.stop()
        openrdk.stop()


if __name__ == "__main__":
    main()
