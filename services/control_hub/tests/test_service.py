from __future__ import annotations

import tempfile
import time
import unittest
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from control_hub_service.app import ServiceState
from control_hub_service.executor import ExecutionManager
from control_hub_service.protocol import (
    MODULE_ID, SYNC, TYPE_CMD, StreamParser, build_stream, control_response,
)
from control_hub_service.storage import ScriptStore


class ProtocolTests(unittest.TestCase):
    def test_stream_parser_accepts_fragmented_frames(self):
        packet = build_stream("GET CFG", TYPE_CMD, 0x123456)
        parser = StreamParser()
        self.assertEqual(parser.feed(packet[:5]), [])
        frames = parser.feed(packet[5:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].message, "GET CFG")
        self.assertEqual(frames[0].sequence, 0x123456)

    def test_identity_control_response(self):
        name = b"control_hub_module"
        raw = b"noise" + SYNC + bytes((MODULE_ID, 0x05, len(name))) + name
        self.assertEqual(control_response(raw, 0x05), (True, "control_hub_module"))


class ScriptStoreTests(unittest.TestCase):
    def test_multiple_directories_are_scanned_together(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "one", root / "two"
            first.mkdir()
            second.mkdir()
            (first / "same.py").write_text("print(1)", encoding="utf-8")
            (second / "same.py").write_text("print(2)", encoding="utf-8")
            store = ScriptStore(root / "state")
            store.add_directory(str(first))
            store.add_directory(str(second))
            scripts = store.list()
            self.assertEqual([item["name"] for item in scripts], ["same.py", "same.py"])
            self.assertEqual(len({item["reference"] for item in scripts}), 2)


class ExecutionTests(unittest.TestCase):
    @staticmethod
    def wait(manager: ExecutionManager, timeout: float = 8) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with manager.lock:
                active = manager.active
            if active is None:
                return manager.status()
            time.sleep(0.03)
        raise AssertionError("execution did not finish")

    def test_builtin_motor_stop_runs_after_failed_main_action(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            scripts = ScriptStore(state)
            main = scripts.save("fail.py", "raise SystemExit(7)\n")
            manager = ExecutionManager(state, scripts)
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = json.dumps({"devices": []}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with patch.dict("os.environ", {"OPENRDK_HOST_URL": f"http://127.0.0.1:{server.server_port}"}):
                    manager.execute_direct({
                        "kind": "python", "script": main["reference"], "command": "",
                        "shell": "auto", "timeout_sec": 5,
                    })
                    status = self.wait(manager)
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["motor_stop"]["state"], "completed")
            self.assertIn('"online_targets": 0', status["motor_stop"]["stdout"])

    def test_motor_stop_configuration_is_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            scripts = ScriptStore(state)
            manager = ExecutionManager(state, scripts)
            config = manager.config()
            config["stop_action"] = {"enabled": False, "kind": "command", "command": "echo unsafe"}
            saved = manager.save_config(config)
            self.assertEqual(saved["stop_action"]["kind"], "builtin_openrdk")
            self.assertTrue(saved["stop_action"]["enabled"])
            self.assertTrue(saved["stop_action"]["readonly"])


class MigrationTests(unittest.TestCase):
    def test_legacy_webview_profile_and_scripts_are_imported_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy, state = root / "legacy", root / "state"
            scripts = legacy / "control_hub_scripts"
            scripts.mkdir(parents=True)
            (scripts / "old.py").write_text("print('legacy')\n", encoding="utf-8")
            (legacy / "control_hub_commands.json").write_text(
                '{"hub":{"device_name":"Antigo","menu":[{"enabled":true,"name":"Rodar","kind":"python","script":"old.py","command":"","shell":"auto"}]}}',
                encoding="utf-8",
            )
            (legacy / "espressif_devices.json").write_text(
                '{"devices":[{"serial_number":"hub","device_node":"COM24","module_type":"control_hub_module"}]}',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"OPENRDK_LEGACY_STATE_DIR": str(legacy)}):
                service = ServiceState(state)
            config = service.executor.config()
            self.assertEqual(config["device_name"], "Antigo")
            self.assertEqual(config["connection"]["port"], "COM24")
            self.assertTrue(config["menu"][0]["enabled"])
            self.assertTrue(service.scripts.resolve(config["menu"][0]["script"]).is_file())


if __name__ == "__main__":
    unittest.main()
