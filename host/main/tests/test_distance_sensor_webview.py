import asyncio
import json
import re
import tempfile
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from openrdk.webview import DistanceSensorConfigPayload, create_webview_app


class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, _tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)


class DistanceSensorWebviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "devices.json"
        self.comms_path = root / "comms.log"
        self.comms_path.write_text("", encoding="utf-8")
        self._write_device(status="online connected")
        self.app = create_webview_app(
            db_path=str(self.db_path),
            comms_log_path=str(self.comms_path),
            enable_realtime_stream=False,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_device(
        self,
        status: str,
        *,
        message_type: str = "CMD",
        telemetry_requested: bool = False,
        expected_page: str = "distance-sensor",
        expected_page_version: str = "1.0",
    ):
        self.db_path.write_text(
            json.dumps(
                {
                    "devices": [
                        {
                            "serial_number": "DS-TEST-01",
                            "name": "Front Distance",
                            "status": status,
                            "module_type": "distance_sensor_module",
                            "message_type": message_type,
                            "telemetry_requested": telemetry_requested,
                            "link_status": "live" if status == "online connected" else "not live",
                            "device_node": "/dev/ttyUSB9",
                            "firmware_version": "1.0",
                            "expected_page": expected_page,
                            "expected_page_version": expected_page_version,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def _endpoint(self, path: str, method: str):
        for route in self.app.routes:
            if getattr(route, "path", None) != path:
                continue
            if method.upper() in (getattr(route, "methods", set()) or set()):
                return route.endpoint
        self.fail(f"route not found: {method} {path}")

    def test_snapshot_parses_cached_distance_and_health_flags(self):
        endpoint = self._endpoint(
            "/api/devices/{serial_number}/distance-sensor/snapshot",
            "GET",
        )
        cached = (
            time.monotonic() - 0.02,
            "DS,-1,4100,23907,0,80,123456",
        )
        with patch("openrdk.webview.get_latest_ds_frame", return_value=cached):
            payload = asyncio.run(endpoint("DS-TEST-01"))

        self.assertTrue(payload["online"])
        self.assertFalse(payload["data"]["valid"])
        self.assertIsNone(payload["data"]["distance_mm"])
        self.assertEqual(payload["data"]["raw_mm"], 4100)
        self.assertEqual(payload["data"]["status"], "ABOVE_MAX")
        self.assertTrue(payload["data"]["health"]["above_max"])
        self.assertTrue(payload["data"]["health"]["config_loaded"])
        self.assertGreaterEqual(payload["data"]["age_ms"], 0)

    def test_distance_page_uses_firmware_requested_version(self):
        endpoint = self._endpoint("/distance-sensor", "GET")
        response = asyncio.run(endpoint(serial="DS-TEST-01", page_version=""))
        self.assertTrue(str(response.path).endswith("distance-sensor.html"))

    def test_unknown_distance_page_version_is_blocked(self):
        self._write_device(status="online connected", expected_page_version="9.9")
        endpoint = self._endpoint("/distance-sensor", "GET")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(endpoint(serial="DS-TEST-01", page_version=""))
        self.assertEqual(caught.exception.status_code, 409)

    def test_color_page_routes_legacy_and_blocks_unknown_versions(self):
        endpoint = self._endpoint("/color", "GET")
        response = asyncio.run(endpoint(serial="", page_version="legacy_1.0"))
        self.assertTrue(str(response.path).endswith("color.html"))
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(endpoint(serial="", page_version="9.9"))
        self.assertEqual(caught.exception.status_code, 409)

    def test_snapshot_rejects_valid_frame_with_negative_distance(self):
        endpoint = self._endpoint(
            "/api/devices/{serial_number}/distance-sensor/snapshot",
            "GET",
        )
        cached = (time.monotonic(), "DS,-1,-1,0,1,1,123456")
        with patch("openrdk.webview.get_latest_ds_frame", return_value=cached):
            payload = asyncio.run(endpoint("DS-TEST-01"))

        self.assertIsNone(payload["data"])

    def test_config_route_uses_canonical_firmware_commands(self):
        endpoint = self._endpoint(
            "/api/devices/{serial_number}/distance-sensor/config",
            "POST",
        )
        commands = []

        def send_command(**kwargs):
            command = kwargs["command"]
            commands.append(command)
            responses = {
                "GET CFG": "CFG,Front Sensor,120,3000,5",
                "GET DATA": "DS,825,831,4845,1,97,234567",
            }
            return {
                "ok": True,
                "command": command,
                "response": responses.get(command, "OK"),
            }

        config = DistanceSensorConfigPayload(
            name="Front Sensor",
            sample_ms=120,
            max_mm=3000,
            filter_window=5,
            save=True,
        )
        with (
            patch("openrdk.webview.get_device_message_type", return_value="CMD"),
            patch("openrdk.webview.send_device_cmd_once", side_effect=send_command),
            patch("openrdk.webview.get_latest_ds_frame", return_value=None),
        ):
            payload = asyncio.run(endpoint("DS-TEST-01", config))

        self.assertEqual(
            commands,
            [
                "SET CFG NAME Front Sensor",
                "SET CFG SAMPLE_MS 120",
                "SET CFG MAX_MM 3000",
                "SET CFG FILTER 5",
                "SAVE CFG",
                "GET CFG",
                "GET DATA",
            ],
        )
        self.assertEqual(payload["cfg"]["max_mm"], 3000)
        self.assertEqual(payload["data"]["distance_mm"], 825)

    def test_invalid_filter_window_is_rejected_before_transport(self):
        endpoint = self._endpoint(
            "/api/devices/{serial_number}/distance-sensor/config",
            "POST",
        )
        config = DistanceSensorConfigPayload(filter_window=9)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(endpoint("DS-TEST-01", config))
        self.assertEqual(raised.exception.status_code, 400)

    def test_refresh_preserves_a_manually_stopped_telemetry_stream(self):
        self._write_device(
            status="online connected",
            message_type="TELEMETRY",
            telemetry_requested=False,
        )
        endpoint = self._endpoint(
            "/api/devices/{serial_number}/distance-sensor/refresh",
            "POST",
        )
        mode_updates = []
        telemetry_updates = []

        def set_mode(**kwargs):
            mode_updates.append(kwargs["message_type"])
            return {"serial_number": "DS-TEST-01"}

        def set_telemetry(**kwargs):
            telemetry_updates.append(kwargs["enabled"])
            return {"serial_number": "DS-TEST-01"}

        responses = {
            "GET INFO": "INFO,Front Distance,distance_sensor_module,distance_sensor_module,20,HC-SR04,3,10,64",
            "GET CFG": "CFG,Front Distance,100,4000,3",
            "GET DATA": "DS,-1,-1,0,0,66,345678",
        }

        def send_command(**kwargs):
            command = kwargs["command"]
            return {"ok": True, "command": command, "response": responses[command]}

        with (
            patch("openrdk.webview.get_device_message_type", return_value="TELEMETRY"),
            patch("openrdk.webview.set_device_message_type", side_effect=set_mode),
            patch("openrdk.webview.set_device_telemetry_requested", side_effect=set_telemetry),
            patch("openrdk.webview.send_device_cmd_once", side_effect=send_command),
            patch("openrdk.webview.get_latest_ds_frame", return_value=None),
        ):
            payload = asyncio.run(endpoint("DS-TEST-01"))

        self.assertEqual(mode_updates, ["CMD", "TELEMETRY"])
        self.assertEqual(telemetry_updates, [])
        self.assertFalse(payload["data"]["valid"])
        self.assertEqual(payload["data"]["status"], "NO_ECHO")

    def test_offline_snapshot_is_readable_but_refresh_is_rejected(self):
        self._write_device(status="offline disconnected")
        snapshot_endpoint = self._endpoint(
            "/api/devices/{serial_number}/distance-sensor/snapshot",
            "GET",
        )
        refresh_endpoint = self._endpoint(
            "/api/devices/{serial_number}/distance-sensor/refresh",
            "POST",
        )
        with patch("openrdk.webview.get_latest_ds_frame", return_value=None):
            snapshot = asyncio.run(snapshot_endpoint("DS-TEST-01"))
        self.assertFalse(snapshot["online"])
        self.assertIsNone(snapshot["data"])

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(refresh_endpoint("DS-TEST-01"))
        self.assertEqual(raised.exception.status_code, 409)

    def test_distance_page_route_points_to_packaged_html(self):
        endpoint = self._endpoint("/distance-sensor", "GET")
        response = asyncio.run(endpoint())
        normalized = str(response.path).replace("\\", "/")
        self.assertTrue(normalized.endswith("web_new/distance-sensor.html"))
        self.assertTrue(Path(response.path).is_file())

    def test_distance_ui_script_only_references_existing_element_ids(self):
        web_root = Path(__file__).parents[1] / "src" / "openrdk" / "web_new"
        html = (web_root / "distance-sensor.html").read_text(encoding="utf-8")
        script = (web_root / "static" / "distance-sensor.js").read_text(
            encoding="utf-8"
        )
        parser = _IdCollector()
        parser.feed(html)
        referenced_ids = set(re.findall(r'\$\("([^"]+)"\)', script))
        self.assertEqual(referenced_ids - parser.ids, set())
        self.assertIn("static/distance-sensor.css", html)
        self.assertIn("static/distance-sensor.js", html)


if __name__ == "__main__":
    unittest.main()
