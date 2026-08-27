from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from openrdk.errors import CommandFailedError
from openrdk.modules import ControlHubModule


class ControlHubModuleTests(unittest.TestCase):
    def setUp(self):
        self.hub = object.__new__(ControlHubModule)
        self.hub.send_raw_cmd = Mock()

    def test_servo_api_uses_one_based_servo_numbers(self):
        self.hub.send_raw_cmd.return_value = {"ok": True, "response": "OK"}

        self.hub.set_servo_angle(3, 120)
        self.hub.set_servo_pulse_us(2, 1500)

        self.assertEqual(
            [call.args[0] for call in self.hub.send_raw_cmd.call_args_list],
            ["SET SERVO 2 120", "SET SERVO_US 1 1500"],
        )

    def test_servo_api_validates_number_angle_and_pulse(self):
        with self.assertRaises(ValueError):
            self.hub.set_servo_angle(0, 90)
        with self.assertRaises(ValueError):
            self.hub.set_servo_angle(1, 181)
        with self.assertRaises(ValueError):
            self.hub.set_servo_pulse_us(1, 499)

    def test_gpio_api_maps_physical_pins_and_parses_reading(self):
        def reply(command, **_kwargs):
            if command == "GET GPIO 10":
                return {"ok": True, "response": "GPIO,10,35,0,1"}
            return {"ok": True, "response": "OK"}

        self.hub.send_raw_cmd.side_effect = reply

        self.hub.write_pin(4, True)
        reading = self.hub.read_pin(35)

        self.assertEqual(self.hub.send_raw_cmd.call_args_list[0].args[0], "SET GPIO 6 1")
        self.assertEqual(self.hub.send_raw_cmd.call_args_list[1].args[0], "GET GPIO 10")
        self.assertEqual(reading["gpio"], 35)
        self.assertEqual(reading["mode"], "input")
        self.assertTrue(reading["high"])

    def test_gpio_api_rejects_input_only_and_unknown_output_pins(self):
        with self.assertRaisesRegex(ValueError, "input-only"):
            self.hub.write_pin(34, 1)
        with self.assertRaisesRegex(ValueError, "not exposed"):
            self.hub.write_pin(33, 1)

    def test_imu_api_returns_euler_gyro_and_calibration_state(self):
        self.hub.send_raw_cmd.return_value = {
            "ok": True,
            "response": "IMU,12.50,-3.25,47.75,0.010,-0.020,0.030,1,0,100",
        }

        state = self.hub.read_imu()

        self.assertEqual(state["euler"], {"roll": 12.5, "pitch": -3.25, "yaw": 47.75})
        self.assertEqual(state["gyro_dps"]["z"], 0.03)
        self.assertTrue(state["calibrated"])
        self.assertFalse(state["calibrating"])
        self.assertEqual(state["calibration_progress"], 100)

    def test_imu_api_rejects_invalid_response(self):
        self.hub.send_raw_cmd.return_value = {"ok": True, "response": "IMU,invalid"}

        with self.assertRaises(CommandFailedError):
            self.hub.read_imu()

    def test_raw_imu_api_parses_mpu6050_register_values(self):
        self.hub.send_raw_cmd.return_value = {
            "ok": True,
            "response": "IMU_RAW,1,-2,16384,4,-5,6",
        }

        state = self.hub.read_imu_raw()

        self.assertEqual(
            {name: state[name] for name in ("ax", "ay", "az", "gx", "gy", "gz")},
            {"ax": 1, "ay": -2, "az": 16384, "gx": 4, "gy": -5, "gz": 6},
        )

    def test_calibrate_imu_waits_until_finished(self):
        responses = iter(
            [
                {"ok": True, "response": "OK"},
                {"ok": True, "response": "IMU,0,0,0,0,0,0,0,1,25"},
                {"ok": True, "response": "IMU,0,0,0,0,0,0,1,0,100"},
            ]
        )
        self.hub.send_raw_cmd.side_effect = lambda *_args, **_kwargs: next(responses)

        with patch("openrdk.modules.time.sleep"):
            state = self.hub.calibrate_imu(poll_interval_sec=0.01)

        self.assertTrue(state["calibrated"])
        self.assertEqual(
            [call.args[0] for call in self.hub.send_raw_cmd.call_args_list],
            ["CALIBRATE IMU", "GET IMU", "GET IMU"],
        )


if __name__ == "__main__":
    unittest.main()
