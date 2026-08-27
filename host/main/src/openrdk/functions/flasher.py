from __future__ import annotations

import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Callable

from . import _state
from .registry import _find_device_by_serial, _find_device_by_node, _load_db
from ..errors import DeviceNotFoundError, FlashError
from ..constants import DEFAULT_MODULE_TYPE


_FIRMWARE_DIR = Path(__file__).parent.parent / "firmware"

_FIRMWARE_MANIFEST: dict[str, dict] = {
    "traction_module": {
        "chip": "esp32c3",
        "flash-mode": "dio",
        "flash-size": "2MB",
        "flash-freq": "80m",
        "files": {
            "0x0":     "traction_module/bootloader.bin",
            "0x8000":  "traction_module/partition-table.bin",
            "0x10000": "traction_module/traction_module.bin",
        },
    },
    "line_sensor_module": {
        "chip": "esp32c3",
        "flash-mode": "dio",
        "flash-size": "2MB",
        "flash-freq": "80m",
        "files": {
            "0x0":     "line_sensor_module/bootloader.bin",
            "0x8000":  "line_sensor_module/partition-table.bin",
            "0x10000": "line_sensor_module/line_sensor_module.bin",
        },
    },
    "distance_sensor_module": {
        "chip": "esp32c3",
        "flash-mode": "dio",
        "flash-size": "2MB",
        "flash-freq": "80m",
        "files": {
            "0x0":     "distance_sensor_module/bootloader.bin",
            "0x8000":  "distance_sensor_module/partition-table.bin",
            "0x10000": "distance_sensor_module/distance_sensor_module.bin",
        },
    },
    "control_hub_module": {
        "chip": "esp32",
        "flash-mode": "dio",
        "flash-size": "2MB",
        "flash-freq": "40m",
        "files": {
            "0x1000":  "control_hub_module/bootloader.bin",
            "0x8000":  "control_hub_module/partition-table.bin",
            "0x10000": "control_hub_module/control_hub_module.bin",
        },
    },
}

SUPPORTED_FIRMWARE_TYPES: list[str] = list(_FIRMWARE_MANIFEST.keys())


def _stop_keepalive_and_wait(serial_number: str, timeout_sec: float = 8.0) -> None:
    thread: threading.Thread | None = None
    with _state._MONITOR_LOCK:
        thread = _state._KEEPALIVE_THREADS.get(serial_number)
    from .keepalive import _stop_keepalive_monitor
    _stop_keepalive_monitor(serial_number)
    if thread and thread.is_alive():
        thread.join(timeout=timeout_sec)


def _stop_keepalive_by_node(device_node: str, db_path: str, timeout_sec: float = 8.0) -> str | None:
    """Stop the keepalive thread for whatever device is on device_node. Returns its serial."""
    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_node(data["devices"], device_node)
        serial = str(item.get("serial_number") or "").strip() if item else None
    if serial:
        _stop_keepalive_and_wait(serial, timeout_sec=timeout_sec)
    return serial or None


def _post_flash_detect(
    device_node: str,
    serial_number: str | None,
    db_path: str,
    boot_wait_sec: float = 1.5,
) -> None:
    """
    After lock is released, the device may have reconnected while the lock blocked on_attach.
    Wait for the device to boot, then probe if on_attach didn't already detect the new type.
    """
    time.sleep(boot_wait_sec)

    from .keepalive import _start_keepalive_monitor
    from .registry import _normalize_module_type

    # If on_attach already ran and detected a non-default module type, just ensure keepalive is running.
    if serial_number:
        with _state._DB_LOCK:
            data = _load_db(db_path)
            item = _find_device_by_serial(data["devices"], serial_number)
        if item:
            existing = _normalize_module_type(item.get("module_type") or item.get("firmware_module"))
            if existing and existing != DEFAULT_MODULE_TYPE:
                _start_keepalive_monitor(
                    serial_number=serial_number, device_node=device_node, db_path=db_path
                )
                return

    # on_attach was blocked by the flash lock (device reconnected while lock was held),
    # or this is a by-port flash with no tracked serial. Probe manually.
    from .transport import _probe_link_via_handshake
    from .registry import get_active_serial_baud, _update_registry_by_serial
    from .registry import _normalize_module_type

    import os
    port_exists = os.path.exists(device_node)
    print(f"[flash] post-flash probe on {device_node} (exists={port_exists})", flush=True)

    link_live, module_type, module_id = _probe_link_via_handshake(
        port=device_node,
        db_path=db_path,
        serial_number=serial_number,
        baud=get_active_serial_baud(),
    )

    print(f"[flash] probe result: link_live={link_live} module_type={module_type!r} module_id={module_id}", flush=True)

    resolved = _normalize_module_type(module_type)
    if serial_number and resolved and resolved != DEFAULT_MODULE_TYPE:
        _update_registry_by_serial(
            serial_number=serial_number,
            db_path=db_path,
            module_type=resolved,
            module_id=module_id,
            device_node=device_node,
        )

    _start_keepalive_monitor(
        serial_number=serial_number, device_node=device_node, db_path=db_path
    )


def flash_firmware_on_port(
    device_node: str,
    firmware_type: str,
    baud: int = 460800,
    on_output: Callable[[str], None] | None = None,
) -> dict:
    firmware_type = str(firmware_type or "").strip().lower()
    if firmware_type not in _FIRMWARE_MANIFEST:
        raise FlashError(
            f"unsupported firmware_type '{firmware_type}', "
            f"supported: {SUPPORTED_FIRMWARE_TYPES}"
        )

    manifest = _FIRMWARE_MANIFEST[firmware_type]
    # ESP32-C3 boards using the native USB Serial/JTAG port can remain in the
    # ROM downloader after an RTS hard reset. A watchdog reset releases the
    # download strap and reliably starts the newly flashed application.
    after_reset = (
        "watchdog-reset"
        if str(manifest.get("chip", "")).strip().lower() == "esp32c3"
        else "hard-reset"
    )

    for relative_path in manifest["files"].values():
        full_path = _FIRMWARE_DIR / relative_path
        if not full_path.is_file():
            raise FlashError(f"firmware binary missing: {full_path}")

    cmd = [
        sys.executable, "-m", "esptool",
        "--chip", manifest["chip"],
        "--port", device_node,
        "--baud", str(baud),
        "--before", "default-reset",
        "--after", after_reset,
        "--connect-attempts", "5",
        "write-flash",
        "--flash-mode", manifest["flash-mode"],
        "--flash-size", manifest["flash-size"],
        "--flash-freq", manifest["flash-freq"],
    ]
    for offset, relative_path in sorted(manifest["files"].items()):
        cmd += [offset, str(_FIRMWARE_DIR / relative_path)]

    output_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            output_lines.append(line)
            if on_output is not None:
                on_output(line)
            else:
                print(f"[flash] {line}", flush=True)
        proc.wait()
    except FileNotFoundError:
        raise FlashError("esptool not found — install it with: pip install esptool")
    except Exception as exc:
        raise FlashError(f"flash failed on {device_node}: {exc}") from exc

    if proc.returncode != 0:
        raise FlashError(
            f"esptool exited with code {proc.returncode} on {device_node}"
        )

    return {
        "ok": True,
        "device_node": device_node,
        "firmware_type": firmware_type,
        "returncode": proc.returncode,
        "output": "\n".join(output_lines),
    }


def flash_firmware(
    serial_number: str,
    firmware_type: str,
    db_path: str,
    baud: int = 460800,
    on_output: Callable[[str], None] | None = None,
) -> dict:
    serial_number = str(serial_number or "").strip()
    if not serial_number:
        raise DeviceNotFoundError("serial_number is required")

    with _state._DB_LOCK:
        data = _load_db(db_path)
        item = _find_device_by_serial(data["devices"], serial_number)

    if not item:
        raise DeviceNotFoundError(f"device not found: {serial_number}")
    device_node = str(item.get("device_node") or "").strip()
    if not device_node:
        raise FlashError(
            f"device_node unknown for {serial_number} — is the device connected?"
        )

    with _state._FLASH_LOCK:
        _state._FLASH_LOCKED_SERIALS.add(serial_number)
        _state._FLASH_LOCKED_NODES.add(device_node)
    try:
        _stop_keepalive_and_wait(serial_number, timeout_sec=8.0)
        time.sleep(0.15)
        result = flash_firmware_on_port(
            device_node=device_node,
            firmware_type=firmware_type,
            baud=baud,
            on_output=on_output,
        )
    finally:
        with _state._FLASH_LOCK:
            _state._FLASH_LOCKED_SERIALS.discard(serial_number)
            _state._FLASH_LOCKED_NODES.discard(device_node)

    result["serial_number"] = serial_number
    # Device rebooted. If on_attach was blocked by the lock, probe it now.
    _post_flash_detect(
        device_node=device_node,
        serial_number=serial_number,
        db_path=db_path,
    )
    return result


def flash_firmware_by_node(
    device_node: str,
    firmware_type: str,
    db_path: str,
    baud: int = 460800,
    on_output: Callable[[str], None] | None = None,
) -> dict:
    """Flash by port path. Looks up and stops any keepalive on the port before flashing."""
    serial_number = _stop_keepalive_by_node(device_node, db_path, timeout_sec=8.0)

    with _state._FLASH_LOCK:
        if serial_number:
            _state._FLASH_LOCKED_SERIALS.add(serial_number)
        _state._FLASH_LOCKED_NODES.add(device_node)
    try:
        time.sleep(0.15)
        result = flash_firmware_on_port(
            device_node=device_node,
            firmware_type=firmware_type,
            baud=baud,
            on_output=on_output,
        )
    finally:
        with _state._FLASH_LOCK:
            if serial_number:
                _state._FLASH_LOCKED_SERIALS.discard(serial_number)
            _state._FLASH_LOCKED_NODES.discard(device_node)

    if serial_number:
        result["serial_number"] = serial_number
    _post_flash_detect(
        device_node=device_node,
        serial_number=serial_number,
        db_path=db_path,
    )
    return result
