#!/usr/bin/env python3
"""
    Para rodar:
    Da raiz do repositório:
    PYTHONPATH=host/main/src python3 test/exemple.py  # package: openrdk
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime, FlashError


def main():
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,
        enable_webview_updates=True,
    )
    openrdk.post("webview_complete")
    time.sleep(3) # wait for bootstrap_connected_devices to finish scanning

    try:
        devices = openrdk.list_devices(verbose="full")

        # motor_esq = openrdk.module(openrdk.get_serial_by_name("motor_esq"))
        # motor_esq.move_angle("F", 90)

        # --- flash a device (hold BOOT + press RESET first) ---
        #openrdk.flash_firmware("98:3D:AE:41:5A:24", "traction_module")

        input("\npress Enter to quit\n")

    except KeyboardInterrupt:
        print("\nshutdown requested")
    except FlashError as exc:
        print(f"flash failed: {exc}")
    finally:
        openrdk.stop()


if __name__ == "__main__":
    main()
