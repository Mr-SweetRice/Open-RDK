#!/usr/bin/env python3
"""
SDK usage example.

Run from repo root:
    PYTHONPATH=host/pi/comms/src python3 test/exemple.py
"""

from __future__ import annotations

import socket
import time

from msg_relay import RelayRuntime


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except Exception:
        return "127.0.0.1"


def main():
    runtime = RelayRuntime(
        auto_start=True,
        enable_webview=True,          # set False to disable webview server
        enable_webview_updates=True,  # set False to disable realtime UI stream
    )

    try:
        print(f"runtime running: {runtime.is_running}")
        print(f"webview running: {runtime.is_webview_running}")
        hostname = socket.gethostname()
        lan_ip = _lan_ip()
        print(f"webview url (host): {runtime.webview_url}")
        print(f"webview url (lan): http://{lan_ip}:8765")
        print(f"webview url (hostname): http://{hostname}:8765")

        devices = runtime.list_devices()
        print(f"devices found: {len(devices)}")
        for dev in devices:
            serial = str(dev.get("serial_number") or "")
            module_type = str(dev.get("module_type") or dev.get("firmware_module") or "")
            status = str(dev.get("status") or "")
            print(f"- {serial} | {module_type} | {status}")

        if not devices:
            print("no devices detected")
            return

        serial = str(devices[0].get("serial_number") or "").strip()
        if not serial:
            print("first device has no serial_number")
            return




        # Sanitized traction example (commented on purpose):
        motor_1 = runtime.traction("98:3D:AE:41:9A:40")
        motor_1.move_angle_forward(90)
        #motor_1.forward(30)
        # motor_1.forward_raw(15)
        motor_1.stop()
        print(motor_1.get_pid_rpm())

        # Runtime now starts comms + webview together by default.
        # If you need to reduce memory usage:
        #   enable_webview=False
        # or
        #   enable_webview_updates=False
        print("webview running; press Ctrl+C to stop")
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("shutdown requested")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
