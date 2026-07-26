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
        openrdk.post("webview_complete")
        time.sleep(3)
        openrdk.list_devices(verbose="full")
        

        input("\npress Enter to quit\n")

    except KeyboardInterrupt:
        print("\nshutdown requested")

    finally:
        openrdk.stop()

if __name__ == "__main__":
    webview()
