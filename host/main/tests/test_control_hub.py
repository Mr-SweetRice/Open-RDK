import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from openrdk.constants import CONTROL_HUB_MODULE_ID, MODULE_ID_TO_TYPE
from openrdk.functions.flasher import SUPPORTED_FIRMWARE_TYPES
from openrdk.webview import (
    ControlHubExecutor,
    ControlHubScriptStore,
    _parse_control_hub_imu_response,
)
from openrdk.functions.udev import _WindowsSerialDevice, matches


class ControlHubTests(unittest.TestCase):
    def test_control_hub_imu_response_returns_euler_and_calibration_state(self):
        parsed = _parse_control_hub_imu_response(
            "IMU,12.50,-3.25,47.75,0.010,-0.020,0.030,1,0,100"
        )
        self.assertEqual(parsed["roll_deg"], 12.5)
        self.assertEqual(parsed["pitch_deg"], -3.25)
        self.assertEqual(parsed["yaw_deg"], 47.75)
        self.assertEqual(parsed["gyro_z_dps"], 0.03)
        self.assertTrue(parsed["calibrated"])
        self.assertFalse(parsed["calibrating"])
        self.assertEqual(parsed["calibration_progress"], 100)

    def test_control_hub_imu_response_rejects_invalid_payload(self):
        with self.assertRaisesRegex(ValueError, "IMU response"):
            _parse_control_hub_imu_response("IMU,invalid")

    def test_windows_discovery_accepts_ch9102_and_rejects_bluetooth_serial(self):
        ch9102 = _WindowsSerialDevice(SimpleNamespace(
            device="COM24", name="COM24", serial_number="5945007747",
            manufacturer="WCH.CN", product="USB-Enhanced-SERIAL CH9102",
            description="USB-Enhanced-SERIAL CH9102", vid=0x1A86, pid=0x55D4,
        ))
        bluetooth = _WindowsSerialDevice(SimpleNamespace(
            device="COM4", name="COM4", serial_number=None,
            manufacturer=None, product=None,
            description="Standard Serial over Bluetooth", vid=None, pid=None,
        ))
        self.assertTrue(matches(ch9102))
        self.assertFalse(matches(bluetooth))

    def test_module_identity_and_flasher_registration(self):
        self.assertEqual(CONTROL_HUB_MODULE_ID, 0x15)
        self.assertEqual(MODULE_ID_TO_TYPE[0x15], "control_hub_module")
        self.assertIn("control_hub_module", SUPPORTED_FIRMWARE_TYPES)

    def test_executor_accepts_only_matching_host_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "hub-1",
                "module_type": "control_hub_module",
                "status": "online connected",
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(str(db), str(root / "profiles.json"), enable_module_sync=False)
            executor.set_profile("hub-1", {"menu": [{
                "enabled": True, "name": "Demo", "kind": "command",
                "command": "echo safe", "shell": "auto",
            }]})
            encoded = base64.urlsafe_b64encode(b"echo safe").decode().rstrip("=")
            event = {
                "direction": "rx", "message_type": "CONTROL",
                "message": f"EXEC,0,0,{encoded}", "device_serial": "hub-1", "seq": 9,
            }
            with patch.object(executor, "_notify_firmware"), patch("openrdk.webview.subprocess.Popen") as popen:
                process = popen.return_value
                process.communicate.return_value = ("safe\n", "")
                process.returncode = 0
                executor.handle_event(event)
                for _ in range(100):
                    if popen.called and (executor.status("hub-1") or {}).get("state") == "completed":
                        break
                    time.sleep(0.01)
                popen.assert_called_once()
                argv = popen.call_args.args[0]
                self.assertEqual(argv[-1], "echo safe")
                self.assertFalse(popen.call_args.kwargs["shell"])

    @unittest.skipUnless(os.name == "nt", "Windows command execution test")
    def test_executor_runs_cmd_commands_on_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executor = ControlHubExecutor(str(root / "devices.json"), str(root / "profiles.json"), enable_module_sync=False)
            executor._active["hub-1"] = {"slot": 0, "process": None, "stop_requested": False}
            with patch.object(
                executor, "_stop_all_traction_motors",
                return_value={"stopped": ["traction-1"], "failed": []},
            ) as stop_motors:
                executor._run("hub-1", 0, "Windows test", "command", "echo WINDOWS_OK", "cmd")
            status = executor.status("hub-1")
            self.assertEqual(status["state"], "completed")
            self.assertEqual(status["shell"], "cmd")
            self.assertIn("WINDOWS_OK", status["stdout"])
            self.assertEqual(status["motor_stop"]["stopped"], ["traction-1"])
            stop_motors.assert_called_once_with()

    def test_executor_rejects_command_not_in_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "hub-1", "module_type": "control_hub_module",
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(str(db), str(root / "profiles.json"), enable_module_sync=False)
            executor.set_profile("hub-1", {"menu": [{
                "enabled": True, "name": "Demo", "kind": "command",
                "command": "echo allowed",
            }]})
            encoded = base64.urlsafe_b64encode(b"echo different").decode().rstrip("=")
            with patch.object(executor, "_notify_firmware"), patch("openrdk.webview.subprocess.Popen") as run:
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 10,
                    "message": f"EXEC,0,0,{encoded}", "device_serial": "hub-1",
                })
                run.assert_not_called()
            self.assertEqual(executor.status("hub-1")["state"], "rejected")

    def test_python_store_and_executor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "hub-1", "module_type": "control_hub_module",
                "status": "online connected",
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), str(root / "scripts"),
                enable_module_sync=False,
            )
            executor.script_store.save("demo.py", "print('PYTHON_OK')\n")
            executor.set_profile("hub-1", {"menu": [{
                "enabled": True, "name": "Python demo", "kind": "python",
                "script": "demo.py", "command": "", "shell": "auto",
            }]})
            encoded = base64.urlsafe_b64encode(b"demo.py").decode().rstrip("=")
            with patch.object(executor, "_notify_firmware"), \
                    patch.object(
                        executor, "_stop_all_traction_motors",
                        return_value={"stopped": ["traction-1"], "failed": []},
                    ) as stop_motors:
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 11,
                    "message": f"EXEC,0,1,{encoded}", "device_serial": "hub-1",
                })
                for _ in range(100):
                    status = executor.status("hub-1") or {}
                    if status.get("state") != "running":
                        break
                    time.sleep(0.01)
            status = executor.status("hub-1")
            self.assertEqual(status["state"], "completed")
            self.assertEqual(status["kind"], "python")
            self.assertIn("PYTHON_OK", status["stdout"])
            self.assertEqual(status["motor_stop"]["stopped"], ["traction-1"])
            stop_motors.assert_called_once_with()

    def test_executor_allows_same_sequence_for_different_execution_payloads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "hub-1", "module_type": "control_hub_module",
                "status": "online connected",
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), enable_module_sync=False,
            )
            executor.set_profile("hub-1", {"menu": [
                {"enabled": True, "name": "First", "kind": "command", "command": "echo first"},
                {"enabled": True, "name": "Second", "kind": "command", "command": "echo second"},
            ]})
            first = base64.urlsafe_b64encode(b"echo first").decode().rstrip("=")
            second = base64.urlsafe_b64encode(b"echo second").decode().rstrip("=")
            with patch.object(executor, "_notify_firmware"), \
                    patch("openrdk.webview.subprocess.Popen") as popen:
                process = popen.return_value
                process.communicate.return_value = ("ok\n", "")
                process.returncode = 0
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 0x800000,
                    "message": f"EXEC,0,0,{first}", "device_serial": "hub-1",
                })
                for _ in range(100):
                    if (executor.status("hub-1") or {}).get("state") == "completed":
                        break
                    time.sleep(0.01)
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 0x800000,
                    "message": f"EXEC,1,0,{second}", "device_serial": "hub-1",
                })
                for _ in range(100):
                    status = executor.status("hub-1") or {}
                    if popen.call_count == 2 and status.get("slot") == 1 and status.get("state") == "completed":
                        break
                    time.sleep(0.01)
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(executor.status("hub-1")["slot"], 1)

    def test_event_dedup_keeps_distinct_live_traction_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executor = ControlHubExecutor(
                str(root / "devices.json"), str(root / "profiles.json"),
                enable_module_sync=False,
            )
            self.assertFalse(executor._is_duplicate_event("hub-1", 0x800000, "TRACT,0,RPM,5"))
            self.assertFalse(executor._is_duplicate_event("hub-1", 0x800000, "TRACT,0,RPM,10"))
            self.assertTrue(executor._is_duplicate_event("hub-1", 0x800000, "TRACT,0,RPM,10"))

    def test_control_events_do_not_start_module_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "hub-1", "module_type": "control_hub_module",
                "status": "online connected",
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(str(db), str(root / "profiles.json"))
            with patch.object(executor, "_schedule_module_sync") as schedule:
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 20,
                    "message": "IGNORED", "device_serial": "hub-1",
                })
                schedule.assert_not_called()
                executor.handle_event({
                    "direction": "rx", "message_type": "CMD", "seq": 21,
                    "message": "OK", "device_serial": "hub-1",
                })
                schedule.assert_called_once_with()

    def test_stop_without_active_process_is_acknowledged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executor = ControlHubExecutor(
                str(root / "devices.json"), str(root / "profiles.json"),
                enable_module_sync=False,
            )
            with patch.object(executor, "_notify_firmware") as notify:
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 7,
                    "message": "STOP,1", "device_serial": "hub-1",
                })
                for _ in range(100):
                    if notify.called:
                        break
                    time.sleep(0.01)
            notify.assert_called_once_with("hub-1", 1, "STOPPED")
            self.assertEqual(executor.status("hub-1")["state"], "stopped")

    def test_script_store_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ControlHubScriptStore(folder)
            with self.assertRaises(ValueError):
                store.save("../outside.py", "print('bad')")

    def test_script_store_uses_50_mb_limit(self):
        self.assertEqual(ControlHubScriptStore.MAX_BYTES, 50 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as folder:
            store = ControlHubScriptStore(folder)
            store.MAX_BYTES = 8
            store.save("small.py", "12345678")
            with self.assertRaisesRegex(ValueError, "50 MB"):
                store.save("large.py", "123456789")

    def test_connected_modules_are_encoded_for_oled_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [
                {"serial_number": "hub-1", "name": "Módulo de Controle",
                 "module_type": "control_hub_module", "status": "online connected",
                 "message_type": "CMD"},
                {"serial_number": "sensor-1", "name": "Sensor Linha",
                 "module_type": "line_sensor_module", "status": "online connected"},
                {"serial_number": "traction-1", "name": "Tracao Principal",
                 "module_type": "traction_module", "status": "online connected"},
                {"serial_number": "offline-1", "name": "Ignorar",
                 "module_type": "distance_sensor_module", "status": "offline disconnected"},
            ]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), enable_module_sync=False,
            )
            with patch("openrdk.webview.get_device_message_type", return_value="CMD"), \
                    patch("openrdk.webview.send_device_cmd_once", return_value={"ok": True}) as send:
                executor._sync_connected_modules()
            commands = [call.kwargs["command"] for call in send.call_args_list]
            self.assertEqual(len(commands), 3)
            self.assertEqual(commands[-1], "SET MODULE COUNT 2")
            fields = [command.split(" ", 4) for command in commands[:-1]]
            self.assertEqual([field[3] for field in fields], ["0", "1"])
            decoded = [ControlHubExecutor._decode(field[4]) for field in fields]
            self.assertEqual(decoded, ["Sensor Linha", "Tracao Principal"])

    def test_oled_traction_event_routes_to_connected_traction_module(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [
                {"serial_number": "hub-1", "module_type": "control_hub_module",
                 "status": "online connected", "message_type": "CMD"},
                {"serial_number": "traction-1", "module_type": "traction_module",
                 "status": "online connected", "message_type": "CMD"},
            ]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), enable_module_sync=False,
            )
            executor._module_sync_targets["hub-1"] = (
                ("traction-1", "traction_module"),
            )
            with patch.object(executor, "_notify_traction_firmware") as notify, \
                    patch("openrdk.webview.get_device_message_type", return_value="CMD"), \
                    patch("openrdk.webview.send_device_cmd_once", return_value={"ok": True}) as send:
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 21,
                    "message": "TRACT,0,POS,90", "device_serial": "hub-1",
                })
                for _ in range(100):
                    if (executor.status("hub-1") or {}).get("state") == "traction_completed":
                        break
                    time.sleep(0.01)
            self.assertEqual(
                [call.kwargs["command"] for call in send.call_args_list],
                ["SET PID POS ANGLE 90", "START PID POS"],
            )
            notify.assert_called_once_with("hub-1", 0, "POS", "DONE")

    def test_oled_force_output_uses_control_protocol_and_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [
                {"serial_number": "hub-1", "module_type": "control_hub_module",
                 "status": "online connected", "message_type": "CMD"},
                {"serial_number": "traction-1", "module_type": "traction_module",
                 "status": "online connected", "message_type": "CMD"},
            ]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), enable_module_sync=False,
            )
            executor._module_sync_targets["hub-1"] = (
                ("traction-1", "traction_module"),
            )
            with patch.object(executor, "_notify_traction_firmware"), \
                    patch("openrdk.webview.get_device_message_type", return_value="CMD"), \
                    patch("openrdk.webview.set_device_message_type") as set_mode, \
                    patch("openrdk.webview.set_device_traction_out_value"), \
                    patch("openrdk.webview.send_device_traction_out_once", return_value={"ok": True}) as send_out, \
                    patch("openrdk.webview.send_device_traction_command_once", return_value={"ok": True}) as send_cmd:
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 22,
                    "message": "TRACT,0,OUT,-35", "device_serial": "hub-1",
                })
                for _ in range(100):
                    if (executor.status("hub-1") or {}).get("state") == "traction_completed":
                        break
                    time.sleep(0.01)
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 23,
                    "message": "TRACT,0,CLEAR,0", "device_serial": "hub-1",
                })
                for _ in range(100):
                    status = executor.status("hub-1") or {}
                    if status.get("state") == "traction_completed" and status.get("action") == "CLEAR":
                        break
                    time.sleep(0.01)
            self.assertEqual(
                [call.args[2] for call in set_mode.call_args_list],
                ["CONTROL", "CONTROL", "CMD"],
            )
            send_out.assert_called_once()
            self.assertEqual(send_out.call_args.kwargs["value"], -35)
            send_cmd.assert_called_once()
            self.assertEqual(send_cmd.call_args.kwargs["command"], "CLR OUT")

    def test_oled_speed_event_sends_signed_rpm_setpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [
                {"serial_number": "hub-1", "module_type": "control_hub_module",
                 "status": "online connected", "message_type": "CMD"},
                {"serial_number": "traction-1", "module_type": "traction_module",
                 "status": "online connected", "message_type": "CMD"},
            ]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), enable_module_sync=False,
            )
            executor._module_sync_targets["hub-1"] = (
                ("traction-1", "traction_module"),
            )
            with patch.object(executor, "_notify_traction_firmware"), \
                    patch("openrdk.webview.get_device_message_type", return_value="CMD"), \
                    patch("openrdk.webview.send_device_cmd_once", return_value={"ok": True}) as send:
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 24,
                    "message": "TRACT,0,RPM,-75", "device_serial": "hub-1",
                })
                for _ in range(100):
                    if (executor.status("hub-1") or {}).get("state") == "traction_completed":
                        break
                    time.sleep(0.01)
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs["command"], "SET PID RPM SP -75")

    def test_menu_execution_motor_stop_neutralizes_every_traction_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "traction-1", "module_type": "traction_module",
                "status": "online connected", "message_type": "CONTROL",
                "traction_out_value": 42,
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), enable_module_sync=False,
            )
            with patch("openrdk.webview.set_device_message_type") as set_mode, \
                    patch("openrdk.webview.set_device_traction_out_value") as set_out_value, \
                    patch(
                        "openrdk.webview.send_device_traction_out_once",
                        return_value={"ok": True},
                    ) as immediate_stop, \
                    patch(
                        "openrdk.webview.send_device_traction_command_once",
                        return_value={"ok": True},
                    ) as clear_force, \
                    patch(
                        "openrdk.webview.send_device_cmd_once",
                        return_value={"ok": True},
                    ) as send_cmd:
                result = executor._stop_all_traction_motors()
            self.assertEqual(result, {"stopped": ["traction-1"], "failed": []})
            immediate_stop.assert_called_once()
            self.assertEqual(immediate_stop.call_args.kwargs["value"], 0)
            self.assertEqual(
                [call.kwargs["command"] for call in send_cmd.call_args_list],
                ["STOP PID POS", "STOP PID POS SINE", "SET PID RPM SP 0"],
            )
            clear_force.assert_called_once()
            self.assertEqual(clear_force.call_args.kwargs["command"], "CLR OUT")
            self.assertEqual(
                [call.args[2] for call in set_mode.call_args_list],
                ["CONTROL", "CMD", "CONTROL", "CMD"],
            )
            set_out_value.assert_called_once_with(str(db), "traction-1", 0)

    def test_menu_execution_motor_stop_also_neutralizes_cmd_motor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "traction-1", "module_type": "traction_module",
                "status": "online connected", "message_type": "CMD",
                "traction_out_value": 0,
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), enable_module_sync=False,
            )
            with patch("openrdk.webview.set_device_message_type"), \
                    patch("openrdk.webview.set_device_traction_out_value"), \
                    patch(
                        "openrdk.webview.send_device_traction_out_once",
                        return_value={"ok": True},
                    ) as send_out, \
                    patch(
                        "openrdk.webview.send_device_traction_command_once",
                        return_value={"ok": True},
                    ), \
                    patch(
                        "openrdk.webview.send_device_cmd_once",
                        return_value={"ok": True},
                    ) as send_cmd:
                result = executor._stop_all_traction_motors()
            self.assertEqual(result, {"stopped": ["traction-1"], "failed": []})
            send_out.assert_called_once()
            self.assertEqual(
                [call.kwargs["command"] for call in send_cmd.call_args_list],
                ["STOP PID POS", "STOP PID POS SINE", "SET PID RPM SP 0"],
            )

    def test_encoder_stop_event_terminates_python_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "devices.json"
            db.write_text(json.dumps({"devices": [{
                "serial_number": "hub-1", "module_type": "control_hub_module",
                "status": "online connected",
            }]}), encoding="utf-8")
            executor = ControlHubExecutor(
                str(db), str(root / "profiles.json"), str(root / "scripts"),
                enable_module_sync=False,
            )
            executor.script_store.save("wait.py", "import time\nprint('STARTED', flush=True)\ntime.sleep(20)\n")
            executor.set_profile("hub-1", {"menu": [{
                "enabled": True, "name": "Wait", "kind": "python", "script": "wait.py",
            }]})
            encoded = base64.urlsafe_b64encode(b"wait.py").decode().rstrip("=")
            with patch.object(executor, "_notify_firmware"):
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 12,
                    "message": f"EXEC,0,1,{encoded}", "device_serial": "hub-1",
                })
                for _ in range(100):
                    active = executor._active.get("hub-1") or {}
                    if active.get("process") is not None:
                        break
                    time.sleep(0.01)
                executor.handle_event({
                    "direction": "rx", "message_type": "CONTROL", "seq": 13,
                    "message": "STOP,0", "device_serial": "hub-1",
                })
                for _ in range(200):
                    status = executor.status("hub-1") or {}
                    if status.get("state") == "stopped":
                        break
                    time.sleep(0.01)
            self.assertEqual(executor.status("hub-1")["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
