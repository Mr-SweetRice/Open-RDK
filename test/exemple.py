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
        enable_webview_updates=True,  # set False to disable realtime UI stream
    )

    try:
        openrdk.post("webview")
        motor_serial = openrdk.get_serial_by_name("motor_01")
        motor_esquerdo = openrdk.traction(motor_serial)

        motor_esquerdo.move_angle_forward(90)
        motor_esquerdo.stop()
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("shutdown requested")
    finally:
        pass
       


if __name__ == "__main__":
    main()
