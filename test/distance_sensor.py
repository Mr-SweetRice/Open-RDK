#!/usr/bin/env python3
"""Read an Open-RDK HC-SR04 module from Python.

Run from the repository root:

    PYTHONPATH=host/main/src python test/distance_sensor.py
"""

from __future__ import annotations

import time

from openrdk import CommsRuntime


def main() -> None:
    runtime = CommsRuntime(
        auto_start=True,
        enable_webview=False,
        enable_mdns=False,
        enable_http_redirect=False,
    )
    try:
        deadline = time.monotonic() + 10.0
        devices = []
        while time.monotonic() < deadline:
            devices = [
                device
                for device in runtime.list_devices()
                if device.get("module_type") == "distance_sensor_module"
            ]
            if devices:
                break
            time.sleep(0.2)
        if not devices:
            raise RuntimeError("no distance_sensor_module was detected")

        serial_number = devices[0]["serial_number"]
        runtime.wait_online(serial_number)
        sensor = runtime.distance_sensor(serial_number)

        data = sensor.read()
        if data["valid"]:
            print(f"distance: {data['distance_cm']:.1f} cm")
        else:
            print(f"measurement unavailable: {data['status']}")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
