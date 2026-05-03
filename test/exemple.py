#!/usr/bin/env python3
"""
    Para rodar:
    Da raiz do repositório:
    PYTHONPATH=host/main/src python3 test/exemple.py  # package: openrdk
"""

from __future__ import annotations
from openrdk import CommsRuntime
import time

def main():
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,          # set False to disable webview server
        enable_webview_updates=False,  # set False to disable realtime UI stream
    )

    try:
        openrdk.post("run")

        motor_esquerdo = openrdk.traction(openrdk.get_serial_by_name("motor_01"))
        motor_esquerdo.move_angle_forward(180)
        
        time.sleep(2)
        print("SETUP...OVER")

        while True:
            motor_esquerdo.move_angle_forward(90)
            motor_esquerdo.move_angle_backward(90)
           

    except KeyboardInterrupt:
        print("shutdown requested")
    finally:
        pass
       
if __name__ == "__main__":
    main()
