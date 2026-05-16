#!/usr/bin/env python3
"""
Flash the new traction_module firmware to all connected traction modules.

PYTHONPATH=host/main/src python3 test/flash_traction.py
"""

from __future__ import annotations
import sys
import time

from openrdk.functions.flasher import flash_firmware_by_node
from openrdk.functions import stop_all_keepalive_monitors

PORTS   = ["/dev/ttyACM0", "/dev/ttyACM2"]
FW_TYPE = "traction_module"


def on_output(line: str) -> None:
    print(f"  {line}", flush=True)


def main() -> None:
    print("Stopping keepalive monitors...")
    stop_all_keepalive_monitors()
    time.sleep(1.0)

    failed = []
    for port in PORTS:
        print(f"\n{'='*52}")
        print(f"  Target : {port}")
        print(f"  Action : hold BOOT, press RESET, release BOOT")
        input(f"  Press Enter when {port} is in bootloader mode... ")

        print(f"Flashing {port} ...")
        try:
            result = flash_firmware_by_node(
                device_node=port,
                firmware_type=FW_TYPE,
                baud=460800,
                on_output=on_output,
            )
            print(f"  OK — {result}")
        except Exception as exc:
            print(f"  FAILED — {exc}")
            failed.append(port)

    print(f"\n{'='*52}")
    if failed:
        print(f"Failed ports: {failed}")
        sys.exit(1)
    else:
        print("Both modules flashed. Press RESET on each to boot into the new firmware.")


if __name__ == "__main__":
    main()
