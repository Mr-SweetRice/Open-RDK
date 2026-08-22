from __future__ import annotations

import unittest
from unittest.mock import Mock

from openrdk.errors import CommandFailedError
from openrdk.modules import LineSensorModule


class LineSensorVersionTests(unittest.TestCase):
    def test_get_version_returns_firmware_semver(self):
        sensor = object.__new__(LineSensorModule)
        sensor.send_raw_cmd = Mock(
            return_value={"ok": True, "response": "VERSION,1.0"}
        )

        self.assertEqual(sensor.get_version(), "1.0")
        sensor.send_raw_cmd.assert_called_once_with(
            "GET VERSION", timeout_sec=1.5
        )

    def test_get_version_rejects_invalid_response(self):
        sensor = object.__new__(LineSensorModule)
        sensor.send_raw_cmd = Mock(
            return_value={"ok": True, "response": "OK"}
        )

        with self.assertRaises(CommandFailedError):
            sensor.get_version()


if __name__ == "__main__":
    unittest.main()
