from __future__ import annotations

import math
import queue as _queue
import threading
import time
from typing import TYPE_CHECKING

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

        sensor.calibrate(duration_ms=3000)   # move sensor over line while running
        sensor.save_calibration()
    """

    EXPECTED_MODULE_TYPE = "line_sensor_module"
    SENSOR_COUNT = 5

    def __init__(self, runtime: "CommsRuntime", serial_number: str, snapshot: dict | None = None):
        super().__init__(
            runtime=runtime,
            serial_number=serial_number,
            snapshot=snapshot,
            default_message_type=MESSAGE_TYPE_CMD,
        )

    # ---- Parsing helpers ----

    @staticmethod
    def _parse_data_response(response: str) -> dict:
        # LS,raw[0..4],value[0..4],digital[0..4],position,strength,
        #    line_detected,calibrating,calibration_remaining_ms,input_lag_ms
        parts = [p.strip() for p in str(response or "").split(",")]
        if len(parts) < 22 or parts[0] != "LS":
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
            input_lag_ms           = float(parts[21])
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
            "input_lag_ms": input_lag_ms,
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
        Full sensor snapshot. Keys:
          raw      — list[int]   raw ADC values (0–4095) per sensor
          values   — list[float] normalized reflectance (0.0–1.0) per sensor
          digital  — list[bool]  per-sensor threshold output
          position — float       line position (-1.0 left … 0.0 center … 1.0 right)
          strength — float       peak line signal (0.0–1.0)
          line_detected — bool   True when strength ≥ detect_threshold
          calibrating            — bool
          calibration_remaining_ms — int
          input_lag_ms           — float
        """
        result = self.send_raw_cmd("GET DATA", timeout_sec=timeout_sec)
        response = str(result.get("response") or "").strip()
        return self._parse_data_response(response)

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
            "position": data["position"],
            "strength": data["strength"],
            "line_detected": data["line_detected"],
        }

    def get_info(self, timeout_sec: float = 1.5) -> dict:
        """Device identity: serial number, name, status, module type."""
        return self.send_raw_cmd("GET INFO", timeout_sec=timeout_sec)

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
            "position": data["position"],
            "strength": data["strength"],
            "line_detected": data["line_detected"],
        }


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
