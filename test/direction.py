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

SPEED = 10   # test speed (0-100)
HOLD  = 2.0   # seconds to hold each direction


def ts() -> str:
    return f"{time.monotonic():.3f}s"


def main():
    openrdk = CommsRuntime(auto_start=True, enable_webview=False, enable_webview_updates=False)
    time.sleep(2)
    openrdk.list_devices(verbose=True)  # for debugging — verify your devices are detected and named as expected
    time.sleep(3)
    

    motorE= openrdk.traction(openrdk.get_serial_by_name("esquerda"))
    motorD = openrdk.traction(openrdk.get_serial_by_name("direita"))
    print("--- direction change test ---")
    print(f"speed={SPEED}, hold={HOLD}s per direction\n")

    try:
        # --- direction change test -------------------------------------------
        # move() is non-blocking: both commands are queued immediately.
        # The worker runs them sequentially: forward HOLD s → auto-stop →
        # direction change → backward HOLD s → auto-stop.
        print(f"[{ts()}] queuing: forward {HOLD}s then backward {HOLD}s")
        motorE.move( SPEED, duration=HOLD)   # forward, auto-stops after HOLD s
        motorD.move( SPEED, duration=HOLD)   # backward, runs after previous finishes
        motorE.join()                         # wait for both to complete
        print(f"[{ts()}] done — note any pause between directions above\n")

        # --- repeat direction change to confirm consistent timing -------------
        print(f"[{ts()}] direction change again")
        motorE.move(-SPEED, duration=HOLD)
        motorD.move(SPEED, duration=HOLD)
        motorE.join()
        print(f"[{ts()}] done")

    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        motorE.stop()
        motorD.stop()
        openrdk.stop()


if __name__ == "__main__":
    main()
