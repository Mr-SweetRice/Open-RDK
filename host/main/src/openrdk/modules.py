from __future__ import annotations

import math
import queue as _queue
import threading
import time
from typing import TYPE_CHECKING

from .color_support import (
    get_device_profile,
    parse_color_cal_line,
    parse_color_cfg_line,
    parse_color_data_line,
    parse_color_info_line,
    parse_color_patch_line,
    parse_color_selftest_line,
)
from .constants import (
    MESSAGE_TYPE_CMD,
    MESSAGE_TYPE_CONTROL,
    MESSAGE_TYPE_TELEMETRY,
    STATUS_ONLINE_CONNECTED,
    TRACTION_OUT_MAX_VALUE,
    TRACTION_OUT_MIN_VALUE,
)
from .errors import (
    CommandFailedError,
    DeviceNotFoundError,
    DeviceOfflineError,
    ModuleTypeMismatchError,
)
from .functions import (
    get_latest_ds_frame,
    get_latest_ls_frame,
    send_device_cmd_once,
    send_device_traction_command_once,
    send_device_traction_out_once,
    set_device_message_type,
    set_device_telemetry_requested,
    set_device_traction_out_value,
)

if TYPE_CHECKING:
    from .ordk_runtime import CommsRuntime


def _normalize_module_type(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


class BaseModule:
    """
    Shared SDK behavior for sanitized module classes.

    Keeps the public surface safe while still exposing raw methods for advanced users.
    """

    EXPECTED_MODULE_TYPE: str | None = None

    def __init__(
        self,
        runtime: "CommsRuntime",
        serial_number: str,
        snapshot: dict | None = None,
        default_message_type: str | None = None,
    ):
        self.runtime = runtime
        self.serial_number = str(serial_number or "").strip()
        if not self.serial_number:
            raise DeviceNotFoundError("serial_number is required")
        self.runtime.ensure_running()
        self._snapshot: dict = {}
        self.refresh(snapshot=snapshot)
        if default_message_type:
            self._set_message_type(default_message_type)

    @property
    def module_type(self) -> str:
        return _normalize_module_type(
            str(
                self._snapshot.get("module_type")
                or self._snapshot.get("firmware_module")
                or ""
            )
        )

    @property
    def status(self) -> str:
        return str(self._snapshot.get("status") or "")

    @property
    def is_online(self) -> bool:
        return self.status == STATUS_ONLINE_CONNECTED

    def refresh(self, snapshot: dict | None = None) -> dict:
        current = dict(snapshot) if isinstance(snapshot, dict) else self.runtime.get_device(self.serial_number)
        if not isinstance(current, dict):
            raise DeviceNotFoundError(f"device not found: {self.serial_number}")
        expected = _normalize_module_type(self.EXPECTED_MODULE_TYPE or "")
        actual = _normalize_module_type(
            str(current.get("module_type") or current.get("firmware_module") or "")
        )
        if expected and actual and expected != actual:
            raise ModuleTypeMismatchError(
                f"{self.serial_number} reports '{actual}', expected '{expected}'"
            )
        self._snapshot = current
        return dict(self._snapshot)

    def _ensure_online(self):
        self.refresh()
        if not self.is_online:
            raise DeviceOfflineError(
                f"{self.serial_number} is not online connected (status='{self.status}')"
            )

    def _set_message_type(self, message_type: str) -> dict:
        updated = set_device_message_type(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            message_type=message_type,
        )
        if not isinstance(updated, dict):
            raise DeviceNotFoundError(f"device not found: {self.serial_number}")
        self._snapshot.update(updated)
        return dict(updated)

    def _expect_ok(self, result: dict | None, op_name: str) -> dict:
        if not isinstance(result, dict):
            raise DeviceNotFoundError(f"device not found during {op_name}: {self.serial_number}")
        if not bool(result.get("ok")):
            error_kind = str(result.get("error_kind") or "unknown_error")
            ack = str(result.get("ack") or result.get("response") or "")
            raise CommandFailedError(
                f"{op_name} failed for {self.serial_number}: {error_kind} {ack}".strip()
            )
        return dict(result)

    # Expert/raw helpers for advanced users.
    def send_raw_cmd(
        self,
        command: str,
        timeout_sec: float = 1.5,
        retries: int = 2,
        retry_delay_sec: float = 0.08,
    ) -> dict:
        self._set_message_type(MESSAGE_TYPE_CMD)
        self._ensure_online()
        command_text = str(command or "").strip()
        attempts = max(1, int(retries))
        last_result = None
        retriable_error_kinds = {
            "cmd_send_timeout",
            "cmd_timeout",
            "cmd_superseded",
            "cmd_cancelled",
            "cmd_mode_inactive",
            "cmd_mode_required",
            "keepalive_not_running",
            "keepalive_exception",
            "serial_open_failed",
            "serial_open_timeout",
            "stream_reader_unavailable",
            "comm_error",
        }
        for attempt_idx in range(attempts):
            result = send_device_cmd_once(
                db_path=self.runtime.db_path,
                serial_number=self.serial_number,
                command=command_text,
                timeout_sec=timeout_sec,
            )
            last_result = result
            if isinstance(result, dict) and bool(result.get("ok")):
                return dict(result)

            error_kind = ""
            if isinstance(result, dict):
                error_kind = str(result.get("error_kind") or "").strip().lower()
            should_retry = (
                attempt_idx < attempts - 1
                and error_kind in retriable_error_kinds
            )
            if should_retry and retry_delay_sec > 0:
                time.sleep(float(retry_delay_sec))

        return self._expect_ok(last_result, "send_raw_cmd")

    def send_raw_control(self, command: str, timeout_sec: float = 1.5) -> dict:
        self._set_message_type(MESSAGE_TYPE_CONTROL)
        self._ensure_online()
        result = send_device_traction_command_once(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            command=str(command or "").strip(),
            timeout_sec=timeout_sec,
        )
        return self._expect_ok(result, "send_raw_control")

    def send_raw_traction(self, command: str, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_control(command, timeout_sec=timeout_sec)


class TractionModule(BaseModule):
    EXPECTED_MODULE_TYPE = "traction_module"

    def __init__(self, runtime: "CommsRuntime", serial_number: str, snapshot: dict | None = None):
        super().__init__(
            runtime=runtime,
            serial_number=serial_number,
            snapshot=snapshot,
            default_message_type=MESSAGE_TYPE_CONTROL,
        )
        self._task_queue: _queue.Queue = _queue.Queue()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        self._inverted: bool = False  # set by Motors group for physically reversed motors

    def _worker(self) -> None:
        while True:
            item = self._task_queue.get()
            if item is None:
                self._task_queue.task_done()
                break
            fn, done_event = item
            try:
                fn()
            except Exception:
                pass
            finally:
                self._task_queue.task_done()
                if done_event is not None:
                    done_event.set()

    def _submit(self, fn, blocking: bool = False) -> None:
        if blocking:
            done = threading.Event()
            self._task_queue.put((fn, done))
            done.wait()
        else:
            self._task_queue.put((fn, None))

    def join(self) -> None:
        """Wait for all pending motor tasks to complete."""
        self._task_queue.join()

    @staticmethod
    def _sanitize_output(value: float | int) -> int:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError("traction output must be numeric")
        if parsed < TRACTION_OUT_MIN_VALUE:
            parsed = float(TRACTION_OUT_MIN_VALUE)
        if parsed > TRACTION_OUT_MAX_VALUE:
            parsed = float(TRACTION_OUT_MAX_VALUE)
        return int(round(parsed))

    @staticmethod
    def _sanitize_angle_delta(value: float | int) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError("angle must be numeric")
        if not math.isfinite(parsed):
            raise ValueError("angle must be finite")
        if parsed < 0.0:
            raise ValueError("angle must be >= 0")
        return parsed

    @staticmethod
    def _normalize_direction(direction: str) -> tuple[int, str]:
        text = str(direction or "").strip().lower()
        if text in {"forward", "fwd", "f", "cw", "clockwise", "+", "positive"}:
            return 1, "forward"
        if text in {"backward", "reverse", "rev", "b", "ccw", "counterclockwise", "-", "negative"}:
            return -1, "backward"
        raise ValueError(
            "direction must be one of: "
            "forward/fwd/f/cw/clockwise or backward/reverse/b/ccw/counterclockwise"
        )

    @staticmethod
    def _parse_position_telem_response(response: str) -> dict:
        parts = [part.strip() for part in str(response or "").split(",")]
        if len(parts) < 6 or parts[0] != "TP":
            raise CommandFailedError(f"unexpected GET TELEM POS response: {response}")
        try:
            target_deg = float(parts[1])
            position_deg = float(parts[2])
            cmd_pwm_signed = float(parts[3])
            cmd_raw = float(parts[4])
            i_term = float(parts[5])
        except (TypeError, ValueError):
            raise CommandFailedError(f"invalid GET TELEM POS numeric response: {response}")
        return {
            "target_deg": target_deg,
            "position_deg": position_deg,
            "cmd_pwm_signed": cmd_pwm_signed,
            "cmd_raw": cmd_raw,
            "i_term": i_term,
        }

    @staticmethod
    def _parse_position_pid_response(response: str) -> dict:
        parts = [part.strip() for part in str(response or "").split(",")]
        if len(parts) < 7 or parts[0] != "PP":
            raise CommandFailedError(f"unexpected GET PID POS response: {response}")
        try:
            kp = float(parts[1])
            ki = float(parts[2])
            kd = float(parts[3])
            target_deg = float(parts[4])
            enabled = int(parts[5]) == 1
            iwin = float(parts[6])
        except (TypeError, ValueError):
            raise CommandFailedError(f"invalid GET PID POS numeric response: {response}")
        return {
            "kp": kp,
            "ki": ki,
            "kd": kd,
            "target_deg": target_deg,
            "enabled": enabled,
            "integral_window_deg": iwin,
        }

    def move(
        self,
        value: float | int,
        timeout_sec: float = 1.5,
        duration: float | None = None,
    ) -> None:
        """
        Signed speed control. Non-blocking — submits to this motor's worker thread.
        Positive = forward, negative = backward, zero = stop.

            motor.move(200)                # forward, runs until stop() is called
            motor.move(-200)               # backward
            motor.move(200, duration=2.0)  # forward for 2 s, then auto-stops
            motor.move(0)                  # stop
            motor.join()                   # wait for pending tasks
        """
        v = -float(value) if self._inverted else float(value)
        if v == 0.0:
            return self.stop(timeout_sec)
        self._submit(lambda: self._move_impl(v, timeout_sec, duration))

    def _move_impl(
        self,
        value: float | int,
        timeout_sec: float = 1.5,
        duration: float | None = None,
    ) -> dict:
        fval = float(value)
        if fval == 0.0:
            return self._stop_impl(timeout_sec)
        sign = 1 if fval > 0 else -1
        signed_value = sign * self._sanitize_output(abs(fval))  # -100 to +100
        set_device_traction_out_value(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            traction_out_value=signed_value,
        )
        self._set_message_type(MESSAGE_TYPE_CONTROL)
        self._ensure_online()
        result = send_device_traction_out_once(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            value=signed_value,
            timeout_sec=timeout_sec,
        )
        self._expect_ok(result, "move")
        if duration is not None:
            time.sleep(max(0.0, float(duration)))
            return self._stop_impl(timeout_sec)
        return dict(result)

    def forward(self, value: float | int, timeout_sec: float = 1.5) -> dict:
        # Deprecated: use move(positive_value) instead.
        return self.move(abs(float(value)), timeout_sec)

    def backward(self, value: float | int, timeout_sec: float = 1.5) -> dict:
        # Deprecated: use move(negative_value) instead.
        return self.move(-abs(float(value)), timeout_sec)

    def forward_raw(self, value: float | int, timeout_sec: float = 1.5) -> dict:
        """
        Direct raw-output helper.
        Maps to CONTROL command: `SET OUT RAW <value>`.
        """
        normalized = self._sanitize_output(value)
        return self.send_raw_traction(f"SET OUT RAW {normalized}", timeout_sec=timeout_sec)

    def stop(self, timeout_sec: float = 1.5) -> None:
        """
        Stop the motor. Priority: drains pending tasks, then blocks until sent.
        """
        while True:
            try:
                self._task_queue.get_nowait()
                self._task_queue.task_done()
            except _queue.Empty:
                break
        self._submit(lambda: self._stop_impl(timeout_sec), blocking=True)

    def _stop_impl(self, timeout_sec: float = 1.5) -> dict:
        # Intentionally does NOT send CLR OUT: CLR OUT returns control to the RPM
        # PID which resumes at its last setpoint (±50 RPM) and restarts the motor.
        # SET OUT 0 keeps firmware in forced-output mode with 0% PWM.
        set_device_traction_out_value(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            traction_out_value=0,
        )
        result = send_device_traction_out_once(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            value=0,
            timeout_sec=timeout_sec,
        )
        return self._expect_ok(result, "stop")

    def get_position_telemetry(self, timeout_sec: float = 1.5) -> dict:
        """
        Read current position telemetry snapshot.
        Expects response format: `TP,<target_deg>,<position_deg>,<cmd_pwm_signed>,<cmd_raw>,<i_term>`.
        """
        result = self.send_raw_cmd("GET TELEM POS", timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        parsed = self._parse_position_telem_response(response)
        parsed["raw"] = result
        return parsed

    def get_position_pid(self, timeout_sec: float = 1.5) -> dict:
        """
        Read current position PID config snapshot.
        Expects response format: `PP,<kp>,<ki>,<kd>,<target_deg>,<enabled>,<iwin>`.
        """
        result = self.send_raw_cmd("GET PID POS", timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        parsed = self._parse_position_pid_response(response)
        parsed["raw"] = result
        return parsed

    def move_angle(
        self,
        angle_deg: float | int,
        timeout_sec: float = 1.5,
    ) -> None:
        """
        Signed relative position move. Non-blocking.
        Positive = forward, negative = backward.

            motor.move_angle(90)   # forward 90°
            motor.move_angle(-90)  # backward 90°
            motor.join()           # wait for completion
        """
        a = -float(angle_deg) if self._inverted else float(angle_deg)
        self._submit(lambda: self._move_angle_impl(a, timeout_sec))

    def _move_angle_impl(
        self,
        angle_deg: float | int,
        timeout_sec: float = 1.5,
    ) -> dict:
        try:
            parsed = float(angle_deg)
        except (TypeError, ValueError):
            raise ValueError("angle_deg must be numeric")
        if not math.isfinite(parsed):
            raise ValueError("angle_deg must be finite")
        sign = 1 if parsed >= 0 else -1
        delta_deg = abs(parsed)
        normalized_direction = "forward" if sign > 0 else "backward"
        timeout_val = max(2.0, float(timeout_sec))
        telem = self.get_position_telemetry(timeout_sec=timeout_val)
        pid = self.get_position_pid(timeout_sec=timeout_val)
        current_position_deg = float(telem["position_deg"])
        current_target_deg = float(pid["target_deg"])
        pid_enabled = bool(pid["enabled"])
        base_deg = current_target_deg if pid_enabled else current_position_deg
        base_source = "target" if pid_enabled else "position"
        target_position_deg = base_deg + (float(sign) * delta_deg)

        start_result = self.send_raw_cmd("START PID POS", timeout_sec=timeout_val)
        set_target_result = self.send_raw_cmd(
            f"SET PID POS ANGLE {target_position_deg:.4f}",
            timeout_sec=timeout_val,
        )
        return {
            "direction": normalized_direction,
            "angle_deg": parsed,
            "angle_delta_deg": delta_deg,
            "current_position_deg": current_position_deg,
            "current_target_deg": current_target_deg,
            "pid_enabled_before_move": pid_enabled,
            "base_source": base_source,
            "base_deg": base_deg,
            "target_position_deg": target_position_deg,
            "start_result": start_result,
            "set_target_result": set_target_result,
            "telem": telem,
            "pid": pid,
        }

    def move_angle_forward(self, angle_deg: float | int, timeout_sec: float = 1.5) -> dict:
        # Deprecated: use move_angle(positive_angle) instead.
        return self.move_angle(abs(float(angle_deg)), timeout_sec)

    def move_angle_backward(self, angle_deg: float | int, timeout_sec: float = 1.5) -> dict:
        # Deprecated: use move_angle(negative_angle) instead.
        return self.move_angle(-abs(float(angle_deg)), timeout_sec)

    def set_pid_rpm(
        self,
        kp: float | None = None,
        ki: float | None = None,
        kd: float | None = None,
        timeout_sec: float = 1.5,
    ) -> dict:
        if kp is None and ki is None and kd is None:
            raise ValueError("at least one of kp, ki, kd must be provided")
        results = {}
        if kp is not None:
            results["kp"] = self.send_raw_cmd(f"SET PID RPM KP {float(kp)}", timeout_sec=timeout_sec)
        if ki is not None:
            results["ki"] = self.send_raw_cmd(f"SET PID RPM KI {float(ki)}", timeout_sec=timeout_sec)
        if kd is not None:
            results["kd"] = self.send_raw_cmd(f"SET PID RPM KD {float(kd)}", timeout_sec=timeout_sec)
        return results

    def get_pid_rpm(self, timeout_sec: float = 1.5) -> dict:
        """
        Sanitized PID read helper.
        Expects response format: `P,<kp>,<ki>,<kd>,<sp>`.
        """
        result = self.send_raw_cmd("GET PID RPM", timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        parts = [part.strip() for part in response.split(",")]
        if len(parts) < 5 or parts[0] != "P":
            raise CommandFailedError(f"unexpected GET PID RPM response: {response}")
        try:
            kp = float(parts[1])
            ki = float(parts[2])
            kd = float(parts[3])
            sp = float(parts[4])
        except (TypeError, ValueError):
            raise CommandFailedError(f"invalid PID numeric response: {response}")
        return {
            "kp": kp,
            "ki": ki,
            "kd": kd,
            "sp": sp,
            "raw": result,
        }


class ColorSensorModule(BaseModule):
    """SDK wrapper for the firmware ``color_module``."""

    EXPECTED_MODULE_TYPE = "color_module"

    @staticmethod
    def _response(result: dict) -> str:
        return str(result.get("response") or "").strip()

    @staticmethod
    def _parsed(response: str, parser, operation: str) -> dict:
        parsed = parser(response)
        if parsed is None:
            raise CommandFailedError(f"unexpected {operation} response: {response}")
        return parsed

    def _query(self, command: str, parser, operation: str, timeout_sec: float) -> dict:
        result = self.send_raw_cmd(command, timeout_sec=timeout_sec)
        parsed = self._parsed(self._response(result), parser, operation)
        parsed["raw_result"] = result
        return parsed

    def _label_for_slot(self, palette_mode: int, slot: int) -> dict | None:
        if slot < 0:
            return None
        profile = get_device_profile(self.serial_number)
        mode = profile.get("modes", {}).get(str(palette_mode), {})
        for label in mode.get("labels", []):
            try:
                if int(label.get("slot")) == slot:
                    return dict(label)
            except (TypeError, ValueError):
                continue
        return None

    def _add_color_names(self, data: dict) -> dict:
        palette_mode = int(data["palette_mode"])
        detected_slot = int(data["detected_slot"])
        detected_label = self._label_for_slot(palette_mode, detected_slot)
        data["color_name"] = (
            str(detected_label.get("name"))
            if detected_label is not None
            else "unclassified"
        )
        data["detected_color"] = (
            {
                "slot": detected_slot,
                "name": data["color_name"],
                "hex": detected_label.get("hex"),
                "enabled": bool(detected_label.get("enabled", True)),
            }
            if detected_label is not None
            else None
        )
        for candidate in data.get("top", []):
            label = self._label_for_slot(palette_mode, int(candidate["slot"]))
            candidate["name"] = (
                str(label.get("name")) if label is not None else "unclassified"
            )
            candidate["hex"] = label.get("hex") if label is not None else None
        return data

    def get_data(self, timeout_sec: float = 1.5) -> dict:
        data = self._query("GET DATA", parse_color_data_line, "GET DATA", timeout_sec)
        return self._add_color_names(data)

    def get_color(self, timeout_sec: float = 1.5) -> str:
        """Return the current host-configured color name, or ``unclassified``."""
        return self.get_data(timeout_sec=timeout_sec)["color_name"]

    def get_config(self, timeout_sec: float = 1.5) -> dict:
        return self._query("GET CFG", parse_color_cfg_line, "GET CFG", timeout_sec)

    def get_info(self, timeout_sec: float = 1.5) -> dict:
        return self._query("GET INFO", parse_color_info_line, "GET INFO", timeout_sec)

    def get_calibration(self, palette_mode: int | None = None, timeout_sec: float = 1.5) -> dict:
        command = "GET CAL" if palette_mode is None else f"GET CAL {int(palette_mode)}"
        return self._query(command, parse_color_cal_line, "GET CAL", timeout_sec)

    def get_calibration_patch(
        self,
        slot: int,
        palette_mode: int | None = None,
        timeout_sec: float = 1.5,
    ) -> dict:
        command = (
            f"GET CAL PATCH {int(slot)}"
            if palette_mode is None
            else f"GET CAL PATCH {int(palette_mode)} {int(slot)}"
        )
        return self._query(command, parse_color_patch_line, "GET CAL PATCH", timeout_sec)

    def run_selftest(self, timeout_sec: float = 2.0) -> dict:
        return self._query("RUN SELFTEST", parse_color_selftest_line, "RUN SELFTEST", timeout_sec)

    def set_config(self, field: str, value, timeout_sec: float = 1.5) -> dict:
        """Set a firmware CFG field and return the updated parsed configuration."""
        normalized = str(field or "").strip().upper()
        allowed = {
            "NAME", "SAMPLE_MS", "GAIN", "INTEGRATION_MS", "CONF_TH",
            "TARGET_CLEAR", "PALETTE_MODE", "PATCH_SAMPLES", "LED",
            "GAIN_MODE", "CLASSIFIER",
        }
        if normalized not in allowed:
            raise ValueError(f"unsupported color configuration field: {field}")
        clean_value = str(value).replace(",", "-").replace("\r", " ").replace("\n", " ")
        result = self.send_raw_cmd(
            f"SET CFG {normalized} {clean_value}",
            timeout_sec=timeout_sec,
        )
        parsed = parse_color_cfg_line(self._response(result))
        if parsed is None:
            parsed = self.get_config(timeout_sec=timeout_sec)
            parsed["set_result"] = result
        else:
            parsed["raw_result"] = result
        return parsed

    def save_config(self, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd("SAVE CFG", timeout_sec=timeout_sec)

    def reset_config(self, timeout_sec: float = 1.5) -> dict:
        self.send_raw_cmd("RESET CFG", timeout_sec=timeout_sec)
        return self.get_config(timeout_sec=timeout_sec)

    def start_calibration(self, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd("START CAL", timeout_sec=timeout_sec)

    def stop_calibration(self, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd("STOP CAL", timeout_sec=timeout_sec)

    def select_calibration_patch(self, slot: int, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd(f"SET CAL PATCH {int(slot)}", timeout_sec=timeout_sec)

    def commit_calibration_patch(self, slot: int, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd(f"COMMIT CAL PATCH {int(slot)}", timeout_sec=timeout_sec)

    def save_calibration(self, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd("SAVE CAL", timeout_sec=timeout_sec)

    def reset_calibration(
        self,
        palette_mode: int | str | None = None,
        timeout_sec: float = 1.5,
    ) -> dict:
        target = "" if palette_mode is None else f" {str(palette_mode).upper()}"
        return self.send_raw_cmd(f"RESET CAL{target}", timeout_sec=timeout_sec)


class LineSensorModule(BaseModule):
    """
    SDK wrapper for the line sensor module.

    5 sensors, each producing a raw ADC value (0–4095) and a normalized
    reflectance value (0.0–1.0) after calibration. The firmware also computes
    a weighted line position (-1.0 = far left, 0.0 = center, 1.0 = far right).

    Typical usage:
        sensor = openrdk.line_sensor(serial)
        data = sensor.get_data()        # full snapshot
        vals = sensor.get_values()      # [0.12, 0.45, 0.98, 0.40, 0.10]
        pos  = sensor.get_position()    # {"position": 0.05, "line_detected": True, ...}

        # Optional: keep the last detected position through temporary dead zones.
        sensor.set_lost_position_mode("hold")

        sensor.calibrate(duration_ms=3000)   # move sensor over line while running
        sensor.save_calibration()
    """

    EXPECTED_MODULE_TYPE = "line_sensor_module"
    SENSOR_COUNT = 5

    def __init__(
        self,
        runtime: "CommsRuntime",
        serial_number: str,
        snapshot: dict | None = None,
        lost_position_mode: str = "zero",
    ):
        super().__init__(
            runtime=runtime,
            serial_number=serial_number,
            snapshot=snapshot,
            default_message_type=MESSAGE_TYPE_CMD,
        )
        self._last_data_timestamp = 0.0
        self._lost_position_mode = self._normalize_lost_position_mode(
            lost_position_mode
        )
        self._last_valid_position: float | None = None

    # ---- Parsing helpers ----

    @staticmethod
    def _normalize_lost_position_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in {"zero", "reset"}:
            return "zero"
        if normalized in {"hold", "last", "hold_last"}:
            return "hold"
        raise ValueError("lost_position_mode must be 'zero' or 'hold'")

    @property
    def lost_position_mode(self) -> str:
        return self._lost_position_mode

    def set_lost_position_mode(self, mode: str) -> None:
        """Choose whether loss reports zero or holds the last detected position."""
        self._lost_position_mode = self._normalize_lost_position_mode(mode)
        if self._lost_position_mode == "zero":
            self._last_valid_position = None

    def _position_with_loss_policy(self, data: dict) -> float:
        position = float(data["position"])
        if bool(data.get("line_detected")):
            self._last_valid_position = position
            return position
        if (
            self._lost_position_mode == "hold"
            and self._last_valid_position is not None
        ):
            return self._last_valid_position
        return position

    @staticmethod
    def _parse_data_response(response: str) -> dict:
        # LS,raw[0..4],value[0..4],digital[0..4],position,strength,
        #    line_detected,calibrating,calibration_remaining_ms
        parts = [p.strip() for p in str(response or "").split(",")]
        if len(parts) < 21 or parts[0] != "LS":
            raise CommandFailedError(f"unexpected GET DATA response: {response}")
        try:
            raw     = [int(parts[i + 1]) for i in range(5)]
            values  = [float(parts[i + 6]) for i in range(5)]
            digital = [bool(int(parts[i + 11])) for i in range(5)]
            position               = float(parts[16])
            strength               = float(parts[17])
            line_detected          = bool(int(parts[18]))
            calibrating            = bool(int(parts[19]))
            calibration_remaining_ms = int(parts[20])
        except (TypeError, ValueError, IndexError) as exc:
            raise CommandFailedError(f"invalid GET DATA response: {response}") from exc
        return {
            "raw": raw,
            "values": values,
            "digital": digital,
            "position": position,
            "strength": strength,
            "line_detected": line_detected,
            "calibrating": calibrating,
            "calibration_remaining_ms": calibration_remaining_ms,
        }

    @staticmethod
    def _parse_cfg_response(response: str) -> dict:
        # CFG,track_type,digital_threshold,detect_threshold,calibration_time_ms,sensor_name
        parts = [p.strip() for p in str(response or "").split(",")]
        if len(parts) < 5 or parts[0] != "CFG":
            raise CommandFailedError(f"unexpected CFG response: {response}")
        try:
            track_type        = int(parts[1])
            digital_threshold = float(parts[2])
            detect_threshold  = float(parts[3])
            calibration_time_ms = int(parts[4])
        except (TypeError, ValueError, IndexError) as exc:
            raise CommandFailedError(f"invalid CFG response: {response}") from exc
        sensor_name = parts[5] if len(parts) > 5 else ""
        return {
            "track_type": track_type,
            "track_type_name": "light" if track_type == 1 else "dark",
            "digital_threshold": digital_threshold,
            "detect_threshold": detect_threshold,
            "calibration_time_ms": calibration_time_ms,
            "sensor_name": sensor_name,
        }

    @staticmethod
    def _parse_cal_response(response: str) -> dict:
        # CAL,min_raw[0..4],max_raw[0..4]
        parts = [p.strip() for p in str(response or "").split(",")]
        if len(parts) < 11 or parts[0] != "CAL":
            raise CommandFailedError(f"unexpected CAL response: {response}")
        try:
            min_raw = [int(parts[i + 1]) for i in range(5)]
            max_raw = [int(parts[i + 6]) for i in range(5)]
        except (TypeError, ValueError, IndexError) as exc:
            raise CommandFailedError(f"invalid CAL response: {response}") from exc
        return {"min_raw": min_raw, "max_raw": max_raw}

    # ---- Data reading ----

    def get_data(self, timeout_sec: float = 1.5) -> dict:
        """
        Return the next sensor snapshot from the runtime-owned telemetry stream.

        The first call enables telemetry for this sensor. Subsequent calls wait
        for a frame newer than the one returned previously, so control loops are
        paced by real sensor updates instead of repeatedly consuming stale data.

        Keys:
          raw      — list[int]   raw ADC values (0–4095) per sensor
          values   — list[float] normalized reflectance (0.0–1.0) per sensor
          digital  — list[bool]  per-sensor threshold output
          position — float       line position (-1.0 left … 0.0 center … 1.0 right)
          strength — float       peak line signal (0.0–1.0)
          line_detected — bool   True when strength ≥ detect_threshold
          calibrating            — bool
          calibration_remaining_ms — int
        """
        timeout = max(0.0, float(timeout_sec))
        deadline = time.monotonic() + timeout
        self._ensure_online()
        snapshot = self.runtime.require_device(
            self.serial_number,
            wait_timeout_sec=min(timeout, 1.0),
        )
        stream_active = (
            str(snapshot.get("message_type") or "").upper() == MESSAGE_TYPE_TELEMETRY
            and bool(snapshot.get("telemetry_requested"))
        )
        stream_requested_at = time.monotonic()
        if not stream_active:
            self.start_telemetry()
            deadline = max(deadline, stream_requested_at + 5.0)

        while True:
            result = get_latest_ls_frame(self.serial_number)
            if result is not None:
                timestamp, response = result
                is_new = timestamp > self._last_data_timestamp
                belongs_to_stream = stream_active or timestamp >= stream_requested_at
                if is_new and belongs_to_stream:
                    data = self._parse_data_response(response)
                    self._last_data_timestamp = timestamp
                    return data
            if time.monotonic() >= deadline:
                raise CommandFailedError(
                    f"timed out waiting for line sensor telemetry: {self.serial_number}"
                )
            time.sleep(0.002)

    @property
    def last_data_received_monotonic(self) -> float | None:
        """Host monotonic timestamp of the last telemetry frame returned by get_data()."""
        return self._last_data_timestamp if self._last_data_timestamp > 0 else None

    def get_values(self, timeout_sec: float = 1.5) -> list:
        """
        Normalized reflectance for all 5 sensors (0.0–1.0 each).
        0.0 = background, 1.0 = full line signal.
        """
        return self.get_data(timeout_sec)["values"]

    def get_raw(self, timeout_sec: float = 1.5) -> list:
        """Raw ADC readings (0–4095) for all 5 sensors."""
        return self.get_data(timeout_sec)["raw"]

    def get_position(self, timeout_sec: float = 1.5) -> dict:
        """
        Line position snapshot.
          position      — float (-1.0 = far left, 0.0 = center, 1.0 = far right)
          strength      — float (0.0–1.0)
          line_detected — bool
        """
        data = self.get_data(timeout_sec)
        return {
            "position": self._position_with_loss_policy(data),
            "strength": data["strength"],
            "line_detected": data["line_detected"],
        }

    def get_info(self, timeout_sec: float = 1.5) -> dict:
        """Device identity: serial number, name, status, module type."""
        return self.send_raw_cmd("GET INFO", timeout_sec=timeout_sec)

    def get_version(self, timeout_sec: float = 1.5) -> str:
        """Read the semantic firmware version reported by the module."""
        result = self.send_raw_cmd("GET VERSION", timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        parts = [part.strip() for part in response.split(",", 1)]
        if len(parts) != 2 or parts[0] != "VERSION" or not parts[1]:
            raise CommandFailedError(f"unexpected GET VERSION response: {response}")
        return parts[1]

    # ---- Configuration ----

    def get_config(self, timeout_sec: float = 1.5) -> dict:
        """Read current configuration from device."""
        result = self.send_raw_cmd("GET CFG", timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        return self._parse_cfg_response(response)

    def get_calibration(self, timeout_sec: float = 1.5) -> dict:
        """Read stored min/max calibration values for each sensor."""
        result = self.send_raw_cmd("GET CAL", timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        return self._parse_cal_response(response)

    def set_track_type(self, track_type: str | int, timeout_sec: float = 1.5) -> dict:
        """
        Set line colour relative to background.
          "dark" / 0 — dark line on light background
          "light" / 1 — light line on dark background (default)
        Returns updated config dict.
        """
        if isinstance(track_type, str):
            t = 1 if track_type.strip().lower() == "light" else 0
        else:
            t = int(bool(track_type))
        result = self.send_raw_cmd(f"SET CFG TRACK {t}", timeout_sec=timeout_sec)
        return self._parse_cfg_response(str(result.get("response") or "").strip())

    def set_digital_threshold(self, threshold: float, timeout_sec: float = 1.5) -> dict:
        """
        Per-sensor digital output threshold (0.05–0.95, default 0.45).
        Sensor digital output = 1 when its normalized value exceeds this.
        Returns updated config dict.
        """
        t = max(0.05, min(0.95, float(threshold)))
        result = self.send_raw_cmd(f"SET CFG DIGITAL_TH {t:.4f}", timeout_sec=timeout_sec)
        return self._parse_cfg_response(str(result.get("response") or "").strip())

    def set_detect_threshold(self, threshold: float, timeout_sec: float = 1.5) -> dict:
        """
        Minimum line strength to report line_detected=True (0.05–0.95, default 0.20).
        Returns updated config dict.
        """
        t = max(0.05, min(0.95, float(threshold)))
        result = self.send_raw_cmd(f"SET CFG DETECT_TH {t:.4f}", timeout_sec=timeout_sec)
        return self._parse_cfg_response(str(result.get("response") or "").strip())

    def set_calibration_time(self, duration_ms: int, timeout_sec: float = 1.5) -> dict:
        """Set calibration duration in milliseconds (min 100, default 3000)."""
        ms = max(100, int(duration_ms))
        result = self.send_raw_cmd(f"SET CFG CAL_TIME_MS {ms}", timeout_sec=timeout_sec)
        return self._parse_cfg_response(str(result.get("response") or "").strip())

    def set_name(self, name: str, timeout_sec: float = 1.5) -> dict:
        """Set device name (max 32 chars). Returns updated config dict."""
        clean = str(name or "").replace(",", "-")[:32]
        result = self.send_raw_cmd(f"SET CFG NAME {clean}", timeout_sec=timeout_sec)
        return self._parse_cfg_response(str(result.get("response") or "").strip())

    def save_config(self, timeout_sec: float = 1.5) -> dict:
        """Persist current configuration to NVS flash."""
        return self.send_raw_cmd("SAVE CFG", timeout_sec=timeout_sec)

    # ---- Calibration control ----

    def calibrate(
        self,
        duration_ms: int | None = None,
        timeout_sec: float = 1.5,
        wait: bool = False,
    ) -> dict:
        """
        Start calibration. Move the sensor over the full line surface while running.
        If duration_ms is given, updates the calibration time first.
        Calibration data is saved automatically when it completes.

        wait=True blocks until calibration finishes (polls get_data every 250 ms).
        """
        if duration_ms is not None:
            self.set_calibration_time(duration_ms, timeout_sec=timeout_sec)
        result = self.send_raw_cmd("START CAL", timeout_sec=timeout_sec)
        if wait:
            deadline = time.monotonic() + ((duration_ms or 3000) / 1000.0) + 2.0
            while time.monotonic() < deadline:
                try:
                    if not self.get_data(timeout_sec=max(timeout_sec, 1.5))["calibrating"]:
                        break
                except Exception:
                    pass
                time.sleep(0.25)
        return result

    def stop_calibration(self, timeout_sec: float = 1.5) -> dict:
        """Stop calibration early, keeping the data captured so far."""
        return self.send_raw_cmd("STOP CAL", timeout_sec=timeout_sec)

    def save_calibration(self, timeout_sec: float = 1.5) -> dict:
        """Persist current calibration to NVS flash (auto-saved on completion too)."""
        return self.send_raw_cmd("SAVE CAL", timeout_sec=timeout_sec)

    # ---- Telemetry streaming ----

    def start_telemetry(self) -> dict:
        """Enable continuous sensor data streaming."""
        self._set_message_type(MESSAGE_TYPE_TELEMETRY)
        updated = set_device_telemetry_requested(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            enabled=True,
        )
        if not isinstance(updated, dict):
            raise DeviceNotFoundError(f"device not found: {self.serial_number}")
        self._snapshot.update(updated)
        return dict(updated)

    def stop_telemetry(self) -> dict:
        """Disable continuous sensor data streaming."""
        self._set_message_type(MESSAGE_TYPE_TELEMETRY)
        updated = set_device_telemetry_requested(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            enabled=False,
        )
        if not isinstance(updated, dict):
            raise DeviceNotFoundError(f"device not found: {self.serial_number}")
        self._snapshot.update(updated)
        return dict(updated)

    def start_streaming(self) -> dict:
        """
        Start continuous sensor data streaming at ~50 Hz.
        Firmware pushes LS frames every 20 ms; SDK caches the latest one.
        Read with get_latest_data() — non-blocking, near-zero latency.
        """
        return self.start_telemetry()

    def stop_streaming(self) -> dict:
        """Stop continuous sensor data streaming."""
        return self.stop_telemetry()

    def get_latest_data(self) -> dict | None:
        """
        Non-blocking read of the most recent cached telemetry frame.
        Returns None if no frame has been received yet.
        Call start_streaming() once before entering the control loop.
        Same dict shape as get_data().
        """
        result = get_latest_ls_frame(self.serial_number)
        if result is None:
            return None
        _ts, text = result
        try:
            return self._parse_data_response(text)
        except Exception:
            return None

    def get_latest_values(self) -> list | None:
        """Non-blocking. Returns normalized values list[float] (0.0–1.0), or None."""
        data = self.get_latest_data()
        return data["values"] if data else None

    def get_latest_position(self) -> dict | None:
        """Non-blocking. Returns {position, strength, line_detected}, or None."""
        data = self.get_latest_data()
        if data is None:
            return None
        return {
            "position": self._position_with_loss_policy(data),
            "strength": data["strength"],
            "line_detected": data["line_detected"],
        }


class DistanceSensorModule(BaseModule):
    """SDK wrapper for an HC-SR04 distance sensor module."""

    EXPECTED_MODULE_TYPE = "distance_sensor_module"
    MIN_DISTANCE_MM = 20
    MAX_DISTANCE_MM = 4000
    MIN_SAMPLE_PERIOD_MS = 60
    MAX_SAMPLE_PERIOD_MS = 2000
    FILTER_WINDOWS = (1, 3, 5, 7)

    HEALTH_FLAG_NAMES = {
        0: "valid",
        1: "no_echo",
        2: "echo_stuck",
        3: "below_min",
        4: "above_max",
        5: "filter_active",
        6: "config_loaded",
    }

    def __init__(
        self,
        runtime: "CommsRuntime",
        serial_number: str,
        snapshot: dict | None = None,
    ):
        super().__init__(
            runtime=runtime,
            serial_number=serial_number,
            snapshot=snapshot,
            default_message_type=MESSAGE_TYPE_CMD,
        )

    @classmethod
    def decode_health_flags(cls, value: int | str | None) -> dict[str, bool]:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        return {
            name: bool(parsed & (1 << bit))
            for bit, name in cls.HEALTH_FLAG_NAMES.items()
        }

    @staticmethod
    def _measurement_status(valid: bool, health: dict[str, bool]) -> str:
        if valid:
            return "OK"
        if health.get("echo_stuck"):
            return "ECHO_STUCK"
        if health.get("no_echo"):
            return "NO_ECHO"
        if health.get("below_min"):
            return "BELOW_MIN"
        if health.get("above_max"):
            return "ABOVE_MAX"
        return "NOT_READY"

    @classmethod
    def _parse_data_response(cls, response: str) -> dict:
        """
        Parse the canonical telemetry payload:
        DS,<distance_mm>,<raw_distance_mm>,<echo_us>,<valid>,
           <health_flags>,<sample_timestamp_ms>

        A transitional firmware variant may include a textual status between
        ``valid`` and ``health_flags``. Accepting both keeps host and firmware
        upgrades backward compatible.
        """
        parts = [part.strip() for part in str(response or "").split(",")]
        if len(parts) < 7 or parts[0] != "DS":
            raise CommandFailedError(f"unexpected GET DATA response: {response}")

        has_status_field = len(parts) >= 8
        status_index = 5 if has_status_field else None
        health_index = 6 if has_status_field else 5
        timestamp_index = 7 if has_status_field else 6

        try:
            distance_mm = int(parts[1])
            raw_distance_mm = int(parts[2])
            echo_us = int(parts[3])
            valid_value = int(parts[4])
            if valid_value not in (0, 1):
                raise ValueError("valid must be 0 or 1")
            if valid_value == 1 and distance_mm < 0:
                raise ValueError("valid distance must be non-negative")
            health_flags = int(parts[health_index])
            sample_timestamp_ms = int(parts[timestamp_index])
        except (TypeError, ValueError, IndexError) as exc:
            raise CommandFailedError(f"invalid GET DATA response: {response}") from exc

        valid = bool(valid_value)
        health = cls.decode_health_flags(health_flags)
        status = (
            str(parts[status_index] or "").strip().upper()
            if status_index is not None
            else ""
        )
        if not status:
            status = cls._measurement_status(valid, health)

        distance_cm = (float(distance_mm) / 10.0) if valid and distance_mm >= 0 else None
        distance_m = (float(distance_mm) / 1000.0) if valid and distance_mm >= 0 else None
        return {
            "kind": "DS",
            "distance_mm": distance_mm,
            "filtered_distance_mm": distance_mm,
            "raw_distance_mm": raw_distance_mm,
            "echo_us": echo_us,
            "valid": valid,
            "status": status,
            "health_flags": health_flags,
            "health": health,
            "sample_timestamp_ms": sample_timestamp_ms,
            "distance_cm": distance_cm,
            "distance_m": distance_m,
        }

    @staticmethod
    def _parse_cfg_response(response: str) -> dict:
        # CFG,<sensor_name>,<sample_period_ms>,<max_distance_mm>,<filter_window>
        parts = [part.strip() for part in str(response or "").split(",")]
        if len(parts) < 5 or parts[0] != "CFG":
            raise CommandFailedError(f"unexpected GET CFG response: {response}")
        try:
            sample_period_ms = int(parts[2])
            max_distance_mm = int(parts[3])
            filter_window = int(parts[4])
        except (TypeError, ValueError, IndexError) as exc:
            raise CommandFailedError(f"invalid GET CFG response: {response}") from exc
        return {
            "sensor_name": parts[1],
            "sample_period_ms": sample_period_ms,
            "max_distance_mm": max_distance_mm,
            "filter_window": filter_window,
        }

    @classmethod
    def _parse_info_response(cls, response: str) -> dict:
        # INFO,<name>,<module_type>,<firmware_module>,<module_id>,
        #      <sensor_model>,<trigger_pin>,<echo_pin>,<health_flags>
        parts = [part.strip() for part in str(response or "").split(",")]
        if len(parts) < 9 or parts[0] != "INFO":
            raise CommandFailedError(f"unexpected GET INFO response: {response}")
        try:
            module_id = int(parts[4])
            trigger_pin = int(parts[6])
            echo_pin = int(parts[7])
            health_flags = int(parts[8])
        except (TypeError, ValueError, IndexError) as exc:
            raise CommandFailedError(f"invalid GET INFO response: {response}") from exc
        return {
            "sensor_name": parts[1],
            "module_type": parts[2],
            "firmware_module": parts[3],
            "module_id": module_id,
            "module_id_hex": f"0x{module_id:02X}",
            "sensor_model": parts[5],
            "trigger_pin": trigger_pin,
            "echo_pin": echo_pin,
            "health_flags": health_flags,
            "health": cls.decode_health_flags(health_flags),
        }

    @classmethod
    def _parse_selftest_response(cls, response: str) -> dict:
        # SELFTEST,<ok>,<health_flags>,<distance_mm>
        parts = [part.strip() for part in str(response or "").split(",")]
        if len(parts) < 4 or parts[0] != "SELFTEST":
            raise CommandFailedError(f"unexpected RUN SELFTEST response: {response}")
        try:
            ok_value = int(parts[1])
            if ok_value not in (0, 1):
                raise ValueError("ok must be 0 or 1")
            health_flags = int(parts[2])
            distance_mm = int(parts[3])
        except (TypeError, ValueError, IndexError) as exc:
            raise CommandFailedError(f"invalid RUN SELFTEST response: {response}") from exc
        ok = bool(ok_value)
        health = cls.decode_health_flags(health_flags)
        return {
            "ok": ok,
            "status": "OK" if ok else cls._measurement_status(False, health),
            "health_flags": health_flags,
            "health": health,
            "distance_mm": distance_mm,
        }

    @staticmethod
    def _normalize_unit(unit: str) -> str:
        value = str(unit or "mm").strip().lower()
        aliases = {
            "mm": "mm",
            "millimeter": "mm",
            "millimeters": "mm",
            "millimetre": "mm",
            "millimetres": "mm",
            "cm": "cm",
            "centimeter": "cm",
            "centimeters": "cm",
            "centimetre": "cm",
            "centimetres": "cm",
            "m": "m",
            "meter": "m",
            "meters": "m",
            "metre": "m",
            "metres": "m",
        }
        normalized = aliases.get(value)
        if normalized is None:
            raise ValueError("unit must be 'mm', 'cm', or 'm'")
        return normalized

    @classmethod
    def _distance_from_data(cls, data: dict, unit: str):
        if not bool(data.get("valid")):
            return None
        normalized = cls._normalize_unit(unit)
        distance_mm = int(data["distance_mm"])
        if distance_mm < 0:
            return None
        if normalized == "mm":
            return distance_mm
        if normalized == "cm":
            return float(distance_mm) / 10.0
        return float(distance_mm) / 1000.0

    def get_data(self, timeout_sec: float = 1.5) -> dict:
        """Read and parse one HC-SR04 measurement."""
        result = self.send_raw_cmd("GET DATA", timeout_sec=timeout_sec)
        return self._parse_data_response(str(result.get("response") or "").strip())

    def read(self, timeout_sec: float = 1.5) -> dict:
        """Alias for :meth:`get_data`."""
        return self.get_data(timeout_sec=timeout_sec)

    def get_distance_mm(self, timeout_sec: float = 1.5) -> int | None:
        """Return filtered distance in millimetres, or None for an invalid sample."""
        return self._distance_from_data(self.get_data(timeout_sec), "mm")

    def get_distance_cm(self, timeout_sec: float = 1.5) -> float | None:
        """Return filtered distance in centimetres, or None for an invalid sample."""
        return self._distance_from_data(self.get_data(timeout_sec), "cm")

    def get_distance(
        self,
        unit: str = "mm",
        timeout_sec: float = 1.5,
    ) -> int | float | None:
        """Return filtered distance in ``mm``, ``cm``, or ``m``."""
        return self._distance_from_data(self.get_data(timeout_sec), unit)

    def get_info(self, timeout_sec: float = 1.5) -> dict:
        """Read module identity, HC-SR04 pins, and health flags."""
        result = self.send_raw_cmd("GET INFO", timeout_sec=timeout_sec)
        return self._parse_info_response(str(result.get("response") or "").strip())

    def get_config(self, timeout_sec: float = 1.5) -> dict:
        """Read the current sampling and filter configuration."""
        result = self.send_raw_cmd("GET CFG", timeout_sec=timeout_sec)
        return self._parse_cfg_response(str(result.get("response") or "").strip())

    def _config_after_command(self, command: str, timeout_sec: float) -> dict:
        result = self.send_raw_cmd(command, timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        if response.startswith("CFG,"):
            return self._parse_cfg_response(response)
        return self.get_config(timeout_sec=timeout_sec)

    def set_name(self, name: str, timeout_sec: float = 1.5) -> dict:
        clean = str(name or "").strip().replace(",", "-")[:32]
        if not clean:
            raise ValueError("name is required")
        return self._config_after_command(f"SET CFG NAME {clean}", timeout_sec)

    def set_sample_period_ms(self, value: int, timeout_sec: float = 1.5) -> dict:
        sample_period_ms = int(value)
        if not self.MIN_SAMPLE_PERIOD_MS <= sample_period_ms <= self.MAX_SAMPLE_PERIOD_MS:
            raise ValueError(
                f"sample_period_ms must be between "
                f"{self.MIN_SAMPLE_PERIOD_MS} and {self.MAX_SAMPLE_PERIOD_MS}"
            )
        return self._config_after_command(
            f"SET CFG SAMPLE_MS {sample_period_ms}",
            timeout_sec,
        )

    def set_sample_period(self, value: int, timeout_sec: float = 1.5) -> dict:
        """Alias for :meth:`set_sample_period_ms`."""
        return self.set_sample_period_ms(value, timeout_sec=timeout_sec)

    def set_max_distance_mm(self, value: int, timeout_sec: float = 1.5) -> dict:
        max_distance_mm = int(value)
        if not self.MIN_DISTANCE_MM <= max_distance_mm <= self.MAX_DISTANCE_MM:
            raise ValueError(
                f"max_distance_mm must be between "
                f"{self.MIN_DISTANCE_MM} and {self.MAX_DISTANCE_MM}"
            )
        return self._config_after_command(
            f"SET CFG MAX_MM {max_distance_mm}",
            timeout_sec,
        )

    def set_max_distance(self, value: int, timeout_sec: float = 1.5) -> dict:
        """Alias for :meth:`set_max_distance_mm`."""
        return self.set_max_distance_mm(value, timeout_sec=timeout_sec)

    def set_filter_window(self, value: int, timeout_sec: float = 1.5) -> dict:
        filter_window = int(value)
        if filter_window not in self.FILTER_WINDOWS:
            allowed = ", ".join(str(item) for item in self.FILTER_WINDOWS)
            raise ValueError(f"filter_window must be one of: {allowed}")
        return self._config_after_command(
            f"SET CFG FILTER {filter_window}",
            timeout_sec,
        )

    def save_config(self, timeout_sec: float = 1.5) -> dict:
        """Persist the current configuration in NVS."""
        return self.send_raw_cmd("SAVE CFG", timeout_sec=timeout_sec)

    def reset_config(self, timeout_sec: float = 1.5) -> dict:
        """Restore firmware defaults and return the resulting configuration."""
        return self._config_after_command("RESET CFG", timeout_sec)

    def run_selftest(self, timeout_sec: float = 1.5) -> dict:
        """Run the firmware self-test and return its structured result."""
        result = self.send_raw_cmd("RUN SELFTEST", timeout_sec=timeout_sec)
        return self._parse_selftest_response(str(result.get("response") or "").strip())

    def selftest(self, timeout_sec: float = 1.5) -> dict:
        """Alias for :meth:`run_selftest`."""
        return self.run_selftest(timeout_sec=timeout_sec)

    def start_telemetry(self) -> dict:
        """Enable continuous DS telemetry."""
        self._set_message_type(MESSAGE_TYPE_TELEMETRY)
        updated = set_device_telemetry_requested(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            enabled=True,
        )
        if not isinstance(updated, dict):
            raise DeviceNotFoundError(f"device not found: {self.serial_number}")
        self._snapshot.update(updated)
        return dict(updated)

    def stop_telemetry(self) -> dict:
        """Disable continuous DS telemetry."""
        self._set_message_type(MESSAGE_TYPE_TELEMETRY)
        updated = set_device_telemetry_requested(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            enabled=False,
        )
        if not isinstance(updated, dict):
            raise DeviceNotFoundError(f"device not found: {self.serial_number}")
        self._snapshot.update(updated)
        return dict(updated)

    def start_streaming(self) -> dict:
        """Start continuous telemetry for non-blocking cached reads."""
        return self.start_telemetry()

    def stop_streaming(self) -> dict:
        """Stop continuous telemetry."""
        return self.stop_telemetry()

    def get_latest_data(self) -> dict | None:
        """Return the latest cached DS frame without blocking, or None."""
        result = get_latest_ds_frame(self.serial_number)
        if result is None:
            return None
        _received_at, text = result
        try:
            return self._parse_data_response(text)
        except Exception:
            return None

    def get_latest_distance(self, unit: str = "mm") -> int | float | None:
        """Return the latest valid cached distance in the selected unit."""
        data = self.get_latest_data()
        if data is None:
            return None
        return self._distance_from_data(data, unit)



def run_together(*callables) -> list:
    """
    Run callables concurrently in threads, wait for all to finish.
    Returns results in the same order the callables were given.
    Raises the first exception if any callable fails.

        run_together(
            lambda: motor_e.move(200),
            lambda: motor_d.move(-200),
        )
        run_together(
            lambda: motor_e.move_angle(90),
            lambda: motor_d.move_angle(-45),
            lambda: motor_c.stop(),
        )
    """
    if not callables:
        return []
    results = [None] * len(callables)
    errors: list = [None] * len(callables)

    def _run(i: int, fn) -> None:
        try:
            results[i] = fn()
        except Exception as exc:
            errors[i] = exc

    threads = [
        threading.Thread(target=_run, args=(i, fn), daemon=True)
        for i, fn in enumerate(callables)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for err in errors:
        if err is not None:
            raise err
    return results


class Motors:
    """
    Groups TractionModule instances with optional inversion for reversed motors.
    All move/move_angle calls are non-blocking per motor (each has its own thread).

        motors = Motors(E=motor_e, D=motor_d, inverted="D")
        motors.move(200)            # both forward, D auto-inverted
        motors.move_angle(90)       # both forward 90°
        motors.join()               # wait for all to finish
        motors.stop()               # drain + stop all motors

        motors.E.move_angle(90)     # individual motor — full TractionModule API
        motors["D"].move(-45)       # bracket access also works

        motors.run_together(        # explicit concurrent callables
            lambda: motors.E.move_angle(90),
            lambda: motors.D.move_angle(-45),
        )
    """

    def __init__(self, inverted=None, **motors: TractionModule):
        if not motors:
            raise ValueError("at least one motor is required")
        if isinstance(inverted, str):
            inv = {inverted}
        elif inverted:
            inv = set(inverted)
        else:
            inv = set()
        unknown = inv - set(motors)
        if unknown:
            raise ValueError(f"inverted names not found in motors: {unknown}")
        self._motors: dict[str, TractionModule] = dict(motors)
        for name, motor in self._motors.items():
            motor._inverted = name in inv

    def __getattr__(self, name: str) -> TractionModule:
        try:
            motors = object.__getattribute__(self, "_motors")
        except AttributeError:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
        if name in motors:
            return motors[name]
        raise AttributeError(f"'{type(self).__name__}' has no motor '{name}'")

    def __getitem__(self, name: str) -> TractionModule:
        try:
            return self._motors[name]
        except KeyError:
            raise KeyError(f"motor '{name}' not found; available: {list(self._motors)}")

    @property
    def names(self) -> list[str]:
        return list(self._motors)

    def move(
        self,
        value: float | int,
        timeout_sec: float = 1.5,
        duration: float | None = None,
        join: bool = True,
    ) -> None:
        """
        Submit signed speed to all motors concurrently.
        Blocks until done by default; pass join=False to return immediately.
        Inversion is applied automatically per-motor.
        """
        for motor in self._motors.values():
            motor.move(float(value), timeout_sec, duration=duration)
        if join:
            self.join()

    def move_angle(
        self,
        angle_deg: float | int,
        timeout_sec: float = 1.5,
        join: bool = True,
    ) -> None:
        """
        Submit signed position move to all motors concurrently.
        Blocks until done by default; pass join=False to return immediately.
        Inversion is applied automatically per-motor.
        """
        for motor in self._motors.values():
            motor.move_angle(float(angle_deg), timeout_sec)
        if join:
            self.join()

    def stop(self, timeout_sec: float = 1.5) -> None:
        """Drain and stop all motors concurrently. Blocking."""
        threads = [
            threading.Thread(target=m.stop, args=(timeout_sec,), daemon=True)
            for m in self._motors.values()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def join(self) -> None:
        """Wait for all pending tasks on all motors to complete."""
        for m in self._motors.values():
            m.join()

    def run_together(self, *callables) -> list:
        """Run arbitrary callables concurrently and wait for all to finish."""
        return run_together(*callables)
