#!/usr/bin/env python3
"""
Line follower — PD control.

Sensor layout (verify against your PCB — lower index = left assumed):
  index:  0      1      2      3      4
          left                       right

PYTHONPATH=host/main/src python3 test/exemple.py
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime, FlashError

# ---- PD tuning ---------------------------------------------------------------
KP = 45
KD = 2
VEL_BASE = 40
TURN_SPEED = 45

# ---- Detection thresholds ----------------------------------------------------
DETECT_TH_90 = 90   # 0-100 scale

# ---- Position weights --------------------------------------------------------
WEIGHTS = [-2, -1, 0, 1, 2]


def compute_position(values: list[float], prev_error: float) -> tuple[float, bool]:
    normal = [v * 100.0 for v in values]
    total = sum(normal)
    if total < 1.0:
        return prev_error, True
    return sum(n * w for n, w in zip(normal, WEIGHTS)) / total, False


def detect_90(normal: list[float]) -> tuple[bool, bool]:
    left_90 = (
        normal[0] >= DETECT_TH_90 and normal[1] >= DETECT_TH_90 and normal[2] >= DETECT_TH_90
        and normal[3] < DETECT_TH_90 and normal[4] < DETECT_TH_90
    )
    right_90 = (
        normal[3] > DETECT_TH_90 and normal[4] > DETECT_TH_90 and normal[2] > DETECT_TH_90
        and normal[1] < DETECT_TH_90 and normal[0] < DETECT_TH_90
    )
    return left_90, right_90


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def main():
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=False,
        enable_webview_updates=False,
    )
    time.sleep(2)

    sensor = openrdk.line_sensor(openrdk.get_serial_by_name("linha"))
    motors = openrdk.motors(
        {
            "E": openrdk.get_serial_by_name("teste_rafael"),
            #"D": openrdk.get_serial_by_name("motor_dir1"),
        },
        
    )

    print("calibrating... move sensor over the full line surface (5 s)")
    #sensor.calibrate(duration_ms=5000, wait=True)
    print("calibration done — starting line follow")

    prev_error = 0.0
    prev_time = time.monotonic()

    try:
        while True:
            motors.E.move(40)


    except KeyboardInterrupt:
        print("\nshutdown requested")
    except FlashError as exc:
        print(f"flash failed: {exc}")
    finally:
        motors.stop()
        openrdk.stop()


if __name__ == "__main__":
    main()
