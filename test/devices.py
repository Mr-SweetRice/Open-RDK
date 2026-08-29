#!/usr/bin/env python3
"""
Start the runtime, list detected devices, and keep the webview alive.

PYTHONPATH=host/main/src python3 test/devices.py
$env:PYTHONPATH="host/main/src"; python test/devices.py - WINDOWS
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime


def stop_online_motors(openrdk: CommsRuntime) -> None:
    """Stop every online traction module before shutting down the runtime."""
    stopped = []
    failures = []
    for device in openrdk.list_devices():
        if (
            str(device.get("module_type") or "").lower() != "traction_module"
            or str(device.get("status") or "").lower() != "online connected"
        ):
            continue
        serial = str(device.get("serial_number") or "").strip()
        if not serial:
            continue
        try:
            openrdk.traction(serial).stop()
            stopped.append(serial)
        except Exception as exc:
            failures.append(f"{serial}: {exc}")
    print(f"motor stop before shutdown: stopped={stopped} failures={failures}")
    if failures:
        raise RuntimeError("failed to stop traction modules: " + "; ".join(failures))


def webview():
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,
        enable_webview_updates=True,
    )

    try:
        openrdk.post("webview_complete")
        time.sleep(3)
        openrdk.list_devices(verbose="full")
        

        try:
            input("\npress Enter to quit\n")
        except EOFError:
            # Services started by systemd or a hidden Windows process do not
            # have an interactive stdin. Listing devices is still a complete
            # and valid execution in that environment.
            print("stdin is not interactive; shutting down")

    except KeyboardInterrupt:
        print("\nshutdown requested")

    finally:
        try:
            stop_online_motors(openrdk)
        finally:
            openrdk.stop()

if __name__ == "__main__":
    webview()
