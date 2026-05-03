from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from .constants import (
    MESSAGE_TYPE_CMD,
    MESSAGE_TYPE_TELEMETRY,
    MESSAGE_TYPE_TRACTION_OUT,
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

    def send_raw_traction(self, command: str, timeout_sec: float = 1.5) -> dict:
        self._set_message_type(MESSAGE_TYPE_TRACTION_OUT)
        self._ensure_online()
        result = send_device_traction_command_once(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            command=str(command or "").strip(),
            timeout_sec=timeout_sec,
        )
        return self._expect_ok(result, "send_raw_traction")


class TractionModule(BaseModule):
    EXPECTED_MODULE_TYPE = "traction_module"
    _DIRECTION_RPM_SETPOINT = 50.0

    def __init__(self, runtime: "CommsRuntime", serial_number: str, snapshot: dict | None = None):
        super().__init__(
            runtime=runtime,
            serial_number=serial_number,
            snapshot=snapshot,
            default_message_type=MESSAGE_TYPE_TRACTION_OUT,
        )

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

    def _set_output_direction(self, sign: int, timeout_sec: float = 1.5) -> dict:
        direction_sp = self._DIRECTION_RPM_SETPOINT if sign >= 0 else -self._DIRECTION_RPM_SETPOINT
        return self.send_raw_cmd(f"SET PID RPM SP {direction_sp:.2f}", timeout_sec=timeout_sec)

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

    def forward(self, value: float | int, timeout_sec: float = 1.5) -> dict:
        """
        Simplified sanitized movement method.
        Maps to TRACTION_OUT command: `SET OUT <value>`.
        """
        normalized = self._sanitize_output(value)
        self._set_output_direction(sign=1, timeout_sec=timeout_sec)
        self._set_message_type(MESSAGE_TYPE_TRACTION_OUT)
        self._ensure_online()
        set_device_traction_out_value(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            traction_out_value=normalized,
        )
        result = send_device_traction_out_once(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            value=normalized,
            timeout_sec=timeout_sec,
        )
        return self._expect_ok(result, "forward")

    def backward(self, value: float | int, timeout_sec: float = 1.5) -> dict:
        """
        Simplified sanitized backward movement method.
        Uses RPM setpoint sign for reverse direction and then sends `SET OUT <value>`.
        """
        normalized = self._sanitize_output(value)
        self._set_output_direction(sign=-1, timeout_sec=timeout_sec)
        self._set_message_type(MESSAGE_TYPE_TRACTION_OUT)
        self._ensure_online()
        set_device_traction_out_value(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            traction_out_value=normalized,
        )
        result = send_device_traction_out_once(
            db_path=self.runtime.db_path,
            serial_number=self.serial_number,
            value=normalized,
            timeout_sec=timeout_sec,
        )
        return self._expect_ok(result, "backward")

    def forward_raw(self, value: float | int, timeout_sec: float = 1.5) -> dict:
        """
        Direct raw-output helper.
        Maps to TRACTION_OUT command: `SET OUT RAW <value>`.
        """
        normalized = self._sanitize_output(value)
        return self.send_raw_traction(f"SET OUT RAW {normalized}", timeout_sec=timeout_sec)

    def stop(self, timeout_sec: float = 1.5) -> dict:
        """
        Safe stop helper.
        Maps to TRACTION_OUT command: `CLR OUT`.
        """
        return self.send_raw_traction("CLR OUT", timeout_sec=timeout_sec)

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

    def move_angle(self, direction: str, angle_deg: float | int, timeout_sec: float = 1.5) -> dict:
        """
        Relative position move helper.
        Reads current position, computes a target angle delta, enables position mode,
        and sends `SET PID POS ANGLE <target>`.
        """
        sign, normalized_direction = self._normalize_direction(direction)
        delta_deg = self._sanitize_angle_delta(angle_deg)
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
        return self.move_angle("forward", angle_deg=angle_deg, timeout_sec=timeout_sec)

    def move_angle_backward(self, angle_deg: float | int, timeout_sec: float = 1.5) -> dict:
        return self.move_angle("backward", angle_deg=angle_deg, timeout_sec=timeout_sec)

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


class LineSensorModule(BaseModule):
    EXPECTED_MODULE_TYPE = "line_sensor_module"

    def __init__(self, runtime: "CommsRuntime", serial_number: str, snapshot: dict | None = None):
        super().__init__(
            runtime=runtime,
            serial_number=serial_number,
            snapshot=snapshot,
            default_message_type=MESSAGE_TYPE_CMD,
        )

    def get_info(self, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd("GET INFO", timeout_sec=timeout_sec)

    def get_data(self, timeout_sec: float = 1.5) -> dict:
        return self.send_raw_cmd("GET DATA", timeout_sec=timeout_sec)

    def start_telemetry(self) -> dict:
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
