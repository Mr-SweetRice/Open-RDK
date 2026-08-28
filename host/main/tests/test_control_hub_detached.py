from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openrdk
from openrdk.functions import udev
from openrdk.functions.control_hub_reservation import is_reserved_control_hub
from openrdk.functions.flasher import SUPPORTED_FIRMWARE_TYPES


class DetachedControlHubTests(unittest.TestCase):
    def test_control_hub_is_not_part_of_public_sdk_or_flasher(self):
        self.assertFalse(hasattr(openrdk, "ControlHubModule"))
        self.assertNotIn("control_hub_module", SUPPORTED_FIRMWARE_TYPES)

    def test_old_registry_entry_is_reserved_and_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "hub-1", "device_node": "COM24",
                "module_type": "control_hub_module", "status": "online connected",
            }]}), encoding="utf-8")
            with patch.dict("os.environ", {"CONTROL_HUB_SERVICE_STATE_DIR": str(root / "state")}):
                udev._mark_all_devices_offline(str(db))
                self.assertTrue(is_reserved_control_hub("hub-1", "COM24"))
            self.assertEqual(json.loads(db.read_text(encoding="utf-8"))["devices"], [])

    def test_service_selected_port_is_ignored_before_serial_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / "config.json").write_text(json.dumps({
                "connection": {"port": "COM24", "auto_connect": False},
            }), encoding="utf-8")
            with patch.dict("os.environ", {"CONTROL_HUB_SERVICE_STATE_DIR": str(state)}):
                self.assertTrue(is_reserved_control_hub("unknown", "COM24"))
                self.assertFalse(is_reserved_control_hub("unknown", "COM25"))


if __name__ == "__main__":
    unittest.main()
