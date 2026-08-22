from __future__ import annotations

import time
import unittest
from unittest.mock import Mock, call, patch

import openrdk
from openrdk.constants import DISTANCE_SENSOR_MODULE_ID, MODULE_ID_TO_TYPE
from openrdk.errors import CommandFailedError
from openrdk.functions import _state
from openrdk.functions.flasher import (
    _FIRMWARE_DIR,
    _FIRMWARE_MANIFEST,
    SUPPORTED_FIRMWARE_TYPES,
    flash_firmware_on_port,
)
from openrdk.functions.keepalive import (
    _SENSOR_TELEMETRY_RX_TIMEOUT_SEC,
    _latest_sensor_telemetry_timestamp,
)
from openrdk.functions.transport import (
    _log_stream_rx_frame,
    get_latest_ds_frame,
)
from openrdk.modules import DistanceSensorModule
from openrdk.ordk_runtime import CommsRuntime


class DistanceSensorParsingTests(unittest.TestCase):
    def test_parses_valid_canonical_ds_payload(self):
        data = DistanceSensorModule._parse_data_response(
            "DS,1234,1240,7195,1,97,4242"
        )

        self.assertEqual(data["distance_mm"], 1234)
        self.assertEqual(data["filtered_distance_mm"], 1234)
        self.assertEqual(data["raw_distance_mm"], 1240)
        self.assertEqual(data["echo_us"], 7195)
        self.assertTrue(data["valid"])
        self.assertEqual(data["status"], "OK")
        self.assertEqual(data["distance_cm"], 123.4)
        self.assertEqual(data["distance_m"], 1.234)
        self.assertTrue(data["health"]["valid"])
        self.assertTrue(data["health"]["filter_active"])
        self.assertTrue(data["health"]["config_loaded"])

    def test_invalid_measurement_is_data_not_transport_error(self):
        data = DistanceSensorModule._parse_data_response(
            "DS,-1,-1,30000,0,66,5000"
        )

        self.assertFalse(data["valid"])
        self.assertEqual(data["status"], "NO_ECHO")
        self.assertIsNone(data["distance_cm"])
        self.assertIsNone(data["distance_m"])
        self.assertTrue(data["health"]["no_echo"])

    def test_accepts_transitional_payload_with_explicit_status(self):
        data = DistanceSensorModule._parse_data_response(
            "DS,-1,-1,30000,0,ABOVE_MAX,80,5001"
        )

        self.assertFalse(data["valid"])
        self.assertEqual(data["status"], "ABOVE_MAX")
        self.assertTrue(data["health"]["above_max"])

    def test_rejects_malformed_ds_payload(self):
        invalid_payloads = (
            "",
            "LS,100",
            "DS,100,101,582,2,1,42",
            "DS,not-a-number,101,582,1,1,42",
            "DS,-1,-1,0,1,1,42",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(CommandFailedError):
                    DistanceSensorModule._parse_data_response(payload)

    def test_parses_cfg_info_and_selftest(self):
        cfg = DistanceSensorModule._parse_cfg_response(
            "CFG,front,100,4000,5"
        )
        info = DistanceSensorModule._parse_info_response(
            "INFO,front,distance_sensor_module,distance_sensor_module,"
            "20,HC-SR04,3,10,65,1.0,distance-sensor,1.0"
        )
        selftest = DistanceSensorModule._parse_selftest_response(
            "SELFTEST,0,66,-1"
        )

        self.assertEqual(
            cfg,
            {
                "sensor_name": "front",
                "sample_period_ms": 100,
                "max_distance_mm": 4000,
                "filter_window": 5,
            },
        )
        self.assertEqual(info["module_id"], DISTANCE_SENSOR_MODULE_ID)
        self.assertEqual(info["module_id_hex"], "0x14")
        self.assertEqual(info["sensor_model"], "HC-SR04")
        self.assertEqual(info["trigger_pin"], 3)
        self.assertEqual(info["echo_pin"], 10)
        self.assertEqual(info["firmware_version"], "1.0")
        self.assertEqual(info["expected_page"], "distance-sensor")
        self.assertEqual(info["expected_page_version"], "1.0")
        self.assertFalse(selftest["ok"])
        self.assertEqual(selftest["status"], "NO_ECHO")
        self.assertEqual(selftest["distance_mm"], -1)


class DistanceSensorApiTests(unittest.TestCase):
    @staticmethod
    def _sensor_with_response(response: str) -> DistanceSensorModule:
        sensor = object.__new__(DistanceSensorModule)
        sensor.send_raw_cmd = Mock(
            return_value={"ok": True, "response": response}
        )
        return sensor

    def test_one_shot_distance_helpers_and_units(self):
        sensor = self._sensor_with_response("DS,1250,1260,7289,1,65,10")

        self.assertEqual(sensor.get_distance_mm(timeout_sec=2.0), 1250)
        self.assertEqual(sensor.get_distance_cm(), 125.0)
        self.assertEqual(sensor.get_distance("m"), 1.25)
        with self.assertRaises(ValueError):
            sensor.get_distance("feet")

        sensor.send_raw_cmd.assert_any_call("GET DATA", timeout_sec=2.0)

    def test_convenience_distance_is_none_for_invalid_measurement(self):
        sensor = self._sensor_with_response("DS,-1,-1,30000,0,66,11")

        self.assertIsNone(sensor.get_distance_mm())
        self.assertIsNone(sensor.get_distance_cm())
        self.assertIsNone(sensor.get_distance("m"))

    def test_config_setters_emit_canonical_firmware_commands(self):
        sensor = self._sensor_with_response("CFG,front,100,3000,5")

        sensor.set_name("front")
        sensor.set_sample_period(100)
        sensor.set_max_distance(3000)
        sensor.set_filter_window(5)

        self.assertEqual(
            sensor.send_raw_cmd.call_args_list,
            [
                call("SET CFG NAME front", timeout_sec=1.5),
                call("SET CFG SAMPLE_MS 100", timeout_sec=1.5),
                call("SET CFG MAX_MM 3000", timeout_sec=1.5),
                call("SET CFG FILTER 5", timeout_sec=1.5),
            ],
        )

    def test_config_validation_matches_firmware_ranges(self):
        sensor = self._sensor_with_response("CFG,front,100,3000,5")

        invalid_calls = (
            lambda: sensor.set_sample_period_ms(59),
            lambda: sensor.set_sample_period_ms(2001),
            lambda: sensor.set_max_distance_mm(19),
            lambda: sensor.set_max_distance_mm(4001),
            lambda: sensor.set_filter_window(2),
        )
        for invoke in invalid_calls:
            with self.subTest(invoke=invoke):
                with self.assertRaises(ValueError):
                    invoke()


class DistanceSensorDiscoveryAndCacheTests(unittest.TestCase):
    def tearDown(self):
        with _state._LATEST_DS_LOCK:
            _state._LATEST_DS_FRAMES.clear()
        with _state._LATEST_LS_LOCK:
            _state._LATEST_LS_FRAMES.clear()

    def test_module_id_mapping_exports_and_flash_manifest(self):
        self.assertEqual(
            MODULE_ID_TO_TYPE[DISTANCE_SENSOR_MODULE_ID],
            "distance_sensor_module",
        )
        self.assertIs(openrdk.DistanceSensorModule, DistanceSensorModule)
        self.assertEqual(openrdk.__version__, "0.2.0")
        self.assertIn("distance_sensor_module", SUPPORTED_FIRMWARE_TYPES)
        self.assertGreaterEqual(_SENSOR_TELEMETRY_RX_TIMEOUT_SEC, 2.5)
        for manifest in _FIRMWARE_MANIFEST.values():
            for relative_path in manifest["files"].values():
                self.assertTrue((_FIRMWARE_DIR / relative_path).is_file())

    def test_esp32c3_flash_uses_watchdog_reset(self):
        process = Mock()
        process.stdout = iter(())
        process.returncode = 0

        with patch(
            "openrdk.functions.flasher.subprocess.Popen",
            return_value=process,
        ) as popen:
            result = flash_firmware_on_port(
                "COM23",
                "distance_sensor_module",
                on_output=lambda _line: None,
            )

        command = popen.call_args.args[0]
        after_index = command.index("--after")
        self.assertEqual(command[after_index + 1], "watchdog-reset")
        self.assertTrue(result["ok"])
        process.wait.assert_called_once_with()

    def test_ds_frame_is_cached_and_exposed(self):
        serial_number = "AA:BB:CC:DD:EE:FF"
        payload = "DS,500,510,2915,1,65,1234"

        with patch(
            "openrdk.functions.transport._append_communication_event"
        ):
            _log_stream_rx_frame(
                db_path="unused.json",
                port="COM1",
                serial_number=serial_number,
                parsed={
                    "message_text": payload,
                    "message_type": "TELEMETRY",
                    "frame_bytes": b"frame",
                },
            )

        cached = get_latest_ds_frame(serial_number)
        self.assertIsNotNone(cached)
        self.assertEqual(cached[1], payload)

    def test_latest_data_reads_ds_cache_without_blocking(self):
        serial_number = "11:22:33:44:55:66"
        with _state._LATEST_DS_LOCK:
            _state._LATEST_DS_FRAMES[serial_number] = (
                time.monotonic(),
                "DS,750,755,4373,1,65,2000",
            )
        sensor = object.__new__(DistanceSensorModule)
        sensor.serial_number = serial_number

        data = sensor.get_latest_data()

        self.assertEqual(data["distance_mm"], 750)
        self.assertEqual(sensor.get_latest_distance("cm"), 75.0)

    def test_keepalive_uses_newest_ls_or_ds_cache_timestamp(self):
        serial_number = "22:33:44:55:66:77"
        with _state._LATEST_LS_LOCK:
            _state._LATEST_LS_FRAMES[serial_number] = (10.0, "LS,...")
        with _state._LATEST_DS_LOCK:
            _state._LATEST_DS_FRAMES[serial_number] = (20.0, "DS,...")

        self.assertEqual(
            _latest_sensor_telemetry_timestamp(serial_number),
            20.0,
        )

    def test_runtime_factories_select_distance_sensor_module(self):
        snapshot = {
            "serial_number": "sensor-1",
            "module_type": "distance_sensor_module",
        }

        class FakeRuntime:
            def ensure_running(self):
                return self

            def require_device(self, serial_number):
                self.required_serial = serial_number
                return snapshot

        runtime = FakeRuntime()
        sentinel_module = object()
        factory = Mock(return_value=sentinel_module)
        with patch("openrdk.modules.DistanceSensorModule", factory):
            detected = CommsRuntime.module(runtime, "sensor-1")
            explicit = CommsRuntime.distance_sensor(runtime, "sensor-1")

        self.assertIs(detected, sentinel_module)
        self.assertIs(explicit, sentinel_module)
        self.assertEqual(
            factory.call_args_list,
            [
                call(runtime, serial_number="sensor-1", snapshot=snapshot),
                call(runtime, serial_number="sensor-1", snapshot=snapshot),
            ],
        )


if __name__ == "__main__":
    unittest.main()
