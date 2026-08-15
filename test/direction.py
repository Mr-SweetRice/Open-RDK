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
MOTOR_E_NAME = "esquerda"
MOTOR_D_NAME = "direita"

def ts() -> str:
    return f"{time.monotonic():.3f}s"


def main():
    openrdk = CommsRuntime(auto_start=True, enable_webview=True, enable_webview_updates=True)
    #time.sleep(2)
    #openrdk.list_devices(verbose=True)  # for debugging — verify your devices are detected and named as expected
    time.sleep(3)

    motor_e_serial = openrdk.get_serial_by_name(MOTOR_E_NAME)
    motor_d_serial = openrdk.get_serial_by_name(MOTOR_D_NAME)
    motors = openrdk.motors(
            {"E": motor_e_serial, "D": motor_d_serial},
            inverted="D",
        )
    


    try:
        
       # result = motors.move(40, return_encoder=True, duration=HOLD)
        #print(result)
        #motors.move_angle(90)
        result = motors.move_angle(90, return_encoder=True)
        print(result)
        # while True:
        #     encoders = motors.get_encoders()  # returns immediately
        #     print(encoders["left"]["position_deg"])

    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        motors.stop()
        openrdk.stop()


if __name__ == "__main__":
    main()
