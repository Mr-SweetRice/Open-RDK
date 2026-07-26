#!/usr/bin/env python3
"""
Start the runtime, list detected devices, and keep the webview alive.

PYTHONPATH=host/main/src python3 test/devices.py
$env:PYTHONPATH="host/main/src"; python test/devices.py - WINDOWS
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime


def webview():
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,
        enable_webview_updates=True,
    )

    try:
        openrdk.post("webview")
        time.sleep(3)
        openrdk.list_devices(verbose="full")


        VELOCIDADE_BASE = 50

        sensor_cor = openrdk.color_sensor(openrdk.get_serial_by_name("cor"))

        
        motor_E = openrdk.traction(openrdk.get_serial_by_name("esquerda"))
        motor_D = openrdk.traction(openrdk.get_serial_by_name("direita"))
        # sensor_linha = openrdk.line_sensor(openrdk.get_serial_by_name("linha"))
        # sensor_linha.set_lost_position_mode("zero")

        last_pos = 0.0
        curve = True
        while curve:
            time.sleep(0.3)
            cor = sensor_cor.get_color()
            print(cor)

            if cor == "green":
                motor_E.move(VELOCIDADE_BASE)
                motor_D.move(-VELOCIDADE_BASE)
            if cor == "red":
                motor_E.move(-VELOCIDADE_BASE)
                motor_D.move(VELOCIDADE_BASE)
            #vals = sensor_linha.get_values()
            # pos = sensor_linha.get_position()
            # strg_num = float(pos["strength"])
            # pos_num = float(pos["position"])
            # print(pos_num)

           
            # if pos_num == 0.0 and strg_num >= 0.5:
            #     motor_E.stop()
            #     motor_D.stop()
            #     curve = False
            #     break
            # if pos_num == 0.0 and strg_num < 0.5:
            #     pos_num = last_pos
            
            
            # motor_E.move(VELOCIDADE_BASE*pos_num)
            # motor_D.move(VELOCIDADE_BASE*pos_num)
            # last_pos = pos_num
        

        input("\npress Enter to quit\n")

    except KeyboardInterrupt:
        print("\nshutdown requested")

    finally:
        openrdk.stop()

if __name__ == "__main__":
    webview()
