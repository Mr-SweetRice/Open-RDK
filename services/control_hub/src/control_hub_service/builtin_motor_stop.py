from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import time


HOST_URL = os.environ.get("OPENRDK_HOST_URL", "http://127.0.0.1:8765").rstrip("/")


def request_json(path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        HOST_URL + path,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def stop_all_motors() -> dict:
    devices = request_json("/api/devices").get("devices", [])
    targets = [
        item for item in devices
        if isinstance(item, dict)
        and str(item.get("module_type") or "").lower() == "traction_module"
        and str(item.get("status") or "").lower() == "online connected"
        and str(item.get("serial_number") or "").strip()
    ]
    stopped, failed = [], []
    for device in targets:
        serial = str(device["serial_number"])
        encoded = urllib.parse.quote(serial, safe="")
        try:
            request_json(
                f"/api/devices/{encoded}/config/message-type",
                {"message_type": "CONTROL"},
            )
            result = request_json(
                f"/api/devices/{encoded}/traction-out/send",
                {"value": 0},
            )
            if not bool(result.get("ok")):
                raise RuntimeError(str(result.get("error_kind") or "motor rejected output zero"))
            stopped.append(serial)
        except (OSError, RuntimeError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            failed.append({"serial_number": serial, "error": str(exc)})
    return {"stopped": stopped, "failed": failed, "online_targets": len(targets)}


def stop_with_local_runtime() -> dict:
    """Fallback used when the script owned and already closed the host API."""
    from openrdk import CommsRuntime

    runtime = CommsRuntime(
        auto_start=True,
        enable_webview=False,
        enable_webview_updates=False,
        enable_mdns=False,
        enable_http_redirect=False,
    )
    try:
        # Give serial discovery enough time to restore connections after the
        # script-owned runtime has released them.
        deadline = time.monotonic() + 5.0
        targets = []
        while time.monotonic() < deadline:
            targets = [
                item for item in runtime.list_devices()
                if isinstance(item, dict)
                and str(item.get("module_type") or "").lower() == "traction_module"
                and str(item.get("status") or "").lower() == "online connected"
                and str(item.get("serial_number") or "").strip()
            ]
            if targets:
                break
            time.sleep(0.2)
        stopped, failed = [], []
        for device in targets:
            serial = str(device["serial_number"])
            try:
                runtime.traction(serial).stop()
                stopped.append(serial)
            except Exception as exc:
                failed.append({"serial_number": serial, "error": str(exc)})
        return {
            "stopped": stopped, "failed": failed,
            "online_targets": len(targets), "method": "local_runtime",
        }
    finally:
        runtime.stop()


def main() -> int:
    try:
        result = stop_all_motors()
    except Exception as exc:
        api_error = str(exc)
        try:
            result = stop_with_local_runtime()
            result["api_error"] = api_error
        except Exception as fallback_exc:
            print(json.dumps({"stopped": [], "failed": [{
                "error": api_error, "fallback_error": str(fallback_exc),
            }]}), flush=True)
            return 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
