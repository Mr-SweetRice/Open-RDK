#!/usr/bin/env python3
"""
Direction change test.
Uses duration= so the worker thread holds the motor on for the exact time,
then auto-stops — no heartbeat timing dependency.

PYTHONPATH=host/main/src python3 test/direction.py
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime

SPEED = 100   # test speed (0-100)
HOLD  = 2.0   # seconds to hold each direction


def ts() -> str:
    return f"{time.monotonic():.3f}s"


def main():
    openrdk = CommsRuntime(auto_start=True, enable_webview=False, enable_webview_updates=False)
    time.sleep(2)
    openrdk.list_devices(verbose=True)  # for debugging — verify your devices are detected and named as expected
    

    motor = openrdk.traction(openrdk.get_serial_by_name("motor_esq1"))

    print("--- direction change test ---")
    print(f"speed={SPEED}, hold={HOLD}s per direction\n")

    try:
        # --- direction change test -------------------------------------------
        # move() is non-blocking: both commands are queued immediately.
        # The worker runs them sequentially: forward HOLD s → auto-stop →
        # direction change → backward HOLD s → auto-stop.
        print(f"[{ts()}] queuing: forward {HOLD}s then backward {HOLD}s")
        motor.move( SPEED, duration=HOLD)   # forward, auto-stops after HOLD s
        motor.move(-SPEED, duration=HOLD)   # backward, runs after previous finishes
        motor.join()                         # wait for both to complete
        print(f"[{ts()}] done — note any pause between directions above\n")

        # --- same-direction test: should be instant, no CMD switch -----------
        print(f"[{ts()}] same direction x3 — no reconnect expected")
        motor.move(SPEED,      duration=0.4)
        motor.move(SPEED - 20, duration=0.4)
        motor.move(SPEED - 40, duration=0.4)
        motor.join()
        print(f"[{ts()}] done\n")

        # --- repeat direction change to confirm consistent timing -------------
        print(f"[{ts()}] direction change again")
        motor.move( SPEED, duration=HOLD)
        motor.move(-SPEED, duration=HOLD)
        motor.join()
        print(f"[{ts()}] done")

    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        motor.stop()
        openrdk.stop()


if __name__ == "__main__":
    main()
