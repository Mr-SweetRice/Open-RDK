import copy
import json
import os
import tempfile
import time
from datetime import datetime

from .constants import HOST_TIMEZONE

COLOR_MODULE_TYPE = "color_module"
COLOR_PROFILE_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "color_profiles.json",
)

PALETTE_DEFINITIONS: dict[str, list[dict]] = {
    "5": [
        {"slot": 0, "name": "black", "enabled": True, "hex": "#050505"},
        {"slot": 1, "name": "white", "enabled": True, "hex": "#ffffff"},
        {"slot": 2, "name": "blue", "enabled": True, "hex": "#006dff"},
        {"slot": 3, "name": "green", "enabled": True, "hex": "#30ff00"},
        {"slot": 4, "name": "red", "enabled": True, "hex": "#ff0000"},
    ],
    "8": [
        {"slot": 0, "name": "black", "enabled": True, "hex": "#050505"},
        {"slot": 1, "name": "white", "enabled": True, "hex": "#ffffff"},
        {"slot": 2, "name": "violet", "enabled": True, "hex": "#8300ff"},
        {"slot": 3, "name": "blue", "enabled": True, "hex": "#006dff"},
        {"slot": 4, "name": "cyan", "enabled": True, "hex": "#00ffd5"},
        {"slot": 5, "name": "green", "enabled": True, "hex": "#a5ff00"},
        {"slot": 6, "name": "orange", "enabled": True, "hex": "#ff9b00"},
        {"slot": 7, "name": "red", "enabled": True, "hex": "#ff0000"},
    ],
    "16": [
        {"slot": 0, "name": "black", "enabled": True, "hex": "#050505"},
        {"slot": 1, "name": "white", "enabled": True, "hex": "#ffffff"},
        {"slot": 2, "name": "380nm", "enabled": True, "hex": "#6100ff"},
        {"slot": 3, "name": "405nm", "enabled": True, "hex": "#8300ff"},
        {"slot": 4, "name": "429nm", "enabled": True, "hex": "#004dff"},
        {"slot": 5, "name": "454nm", "enabled": True, "hex": "#006dff"},
        {"slot": 6, "name": "478nm", "enabled": True, "hex": "#00b7ff"},
        {"slot": 7, "name": "503nm", "enabled": True, "hex": "#00ffd5"},
        {"slot": 8, "name": "528nm", "enabled": True, "hex": "#30ff00"},
        {"slot": 9, "name": "552nm", "enabled": True, "hex": "#a5ff00"},
        {"slot": 10, "name": "577nm", "enabled": True, "hex": "#ffff00"},
        {"slot": 11, "name": "602nm", "enabled": True, "hex": "#ff9b00"},
        {"slot": 12, "name": "626nm", "enabled": True, "hex": "#ff3f00"},
        {"slot": 13, "name": "651nm", "enabled": True, "hex": "#ff0000"},
        {"slot": 14, "name": "675nm", "enabled": True, "hex": "#d00000"},
        {"slot": 15, "name": "700nm", "enabled": True, "hex": "#7a0000"},
    ],
}

HEALTH_FLAG_LABELS = {
    0: "sensor_ok",
    1: "saturated",
    2: "dark_valid",
    3: "white_valid",
    4: "calibrating",
    5: "selftest_ok",
    6: "auto_exposure",
    7: "sensor_present",
}


def now_iso() -> str:
    return datetime.now(HOST_TIMEZONE).isoformat()


def decode_health_flags(value: int | str | None) -> dict[str, bool]:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    return {name: bool(parsed & (1 << bit)) for bit, name in HEALTH_FLAG_LABELS.items()}


def _default_mode_profile(mode: str) -> dict:
    entries = copy.deepcopy(PALETTE_DEFINITIONS.get(str(mode), []))
    return {
        "mode": str(mode),
        "labels": entries,
        "summary": None,
        "patches": [],
        "last_calibrated_at": None,
        "updated_at": None,
    }


def default_device_profile(serial_number: str) -> dict:
    return {
        "serial_number": serial_number,
        "module_type": COLOR_MODULE_TYPE,
        "updated_at": None,
        "modes": {
            "5": _default_mode_profile("5"),
            "8": _default_mode_profile("8"),
            "16": _default_mode_profile("16"),
        },
    }


def _ensure_profiles_file(path: str):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fp:
            json.dump({"devices": {}}, fp, indent=2, sort_keys=True)
            fp.write("\n")


def load_profiles(path: str = COLOR_PROFILE_DB_PATH) -> dict:
    _ensure_profiles_file(path)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return {"devices": {}}

    devices = data.get("devices")
    if not isinstance(devices, dict):
        devices = {}
    return {"devices": devices}


def save_profiles(data: dict, path: str = COLOR_PROFILE_DB_PATH):
    _ensure_profiles_file(path)
    folder = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".color_profiles_", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2, sort_keys=True)
            fp.write("\n")
        for attempt in range(8):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def merge_device_profile(serial_number: str, payload: dict | None) -> dict:
    merged = default_device_profile(serial_number)
    if not isinstance(payload, dict):
        return merged

    merged["updated_at"] = payload.get("updated_at")
    modes = payload.get("modes")
    if isinstance(modes, dict):
        for mode_key in ("5", "8", "16"):
            incoming = modes.get(mode_key)
            if not isinstance(incoming, dict):
                continue
            target = merged["modes"][mode_key]
            target["summary"] = incoming.get("summary")
            target["patches"] = incoming.get("patches") if isinstance(incoming.get("patches"), list) else []
            target["last_calibrated_at"] = incoming.get("last_calibrated_at")
            target["updated_at"] = incoming.get("updated_at")
            labels = incoming.get("labels")
            if isinstance(labels, list):
                merged_labels = []
                defaults = {int(item["slot"]): item for item in PALETTE_DEFINITIONS.get(mode_key, [])}
                seen_slots: set[int] = set()
                for item in labels:
                    if not isinstance(item, dict):
                        continue
                    try:
                        slot = int(item.get("slot"))
                    except (TypeError, ValueError):
                        continue
                    if slot in seen_slots:
                        continue
                    base = copy.deepcopy(defaults.get(slot, {"slot": slot, "name": f"slot-{slot}", "enabled": True, "hex": "#888888"}))
                    if isinstance(item.get("name"), str) and item.get("name").strip():
                        base["name"] = item.get("name").strip()
                    if isinstance(item.get("enabled"), bool):
                        base["enabled"] = bool(item.get("enabled"))
                    if isinstance(item.get("hex"), str) and item.get("hex").strip():
                        base["hex"] = item.get("hex").strip()
                    merged_labels.append(base)
                    seen_slots.add(slot)
                for slot, base in defaults.items():
                    if slot not in seen_slots:
                        merged_labels.append(copy.deepcopy(base))
                merged_labels.sort(key=lambda item: int(item.get("slot", 0)))
                target["labels"] = merged_labels
    return merged


def get_device_profile(serial_number: str, path: str = COLOR_PROFILE_DB_PATH) -> dict:
    data = load_profiles(path)
    devices = data["devices"]
    payload = devices.get(serial_number)
    return merge_device_profile(serial_number, payload)


def set_device_profile(serial_number: str, profile: dict, path: str = COLOR_PROFILE_DB_PATH) -> dict:
    data = load_profiles(path)
    merged = merge_device_profile(serial_number, profile)
    merged["updated_at"] = now_iso()
    data["devices"][serial_number] = merged
    save_profiles(data, path)
    return merged


def update_device_mode_profile(
    serial_number: str,
    mode: int | str,
    *,
    summary: dict | None = None,
    patches: list[dict] | None = None,
    labels: list[dict] | None = None,
    last_calibrated_at: str | None = None,
    path: str = COLOR_PROFILE_DB_PATH,
) -> dict:
    mode_key = str(mode)
    profile = get_device_profile(serial_number, path=path)
    mode_profile = profile["modes"].setdefault(mode_key, _default_mode_profile(mode_key))
    if summary is not None:
        mode_profile["summary"] = summary
    if patches is not None:
        mode_profile["patches"] = list(patches)
    if labels is not None:
        mode_profile["labels"] = list(labels)
    if last_calibrated_at is not None:
        mode_profile["last_calibrated_at"] = last_calibrated_at
    mode_profile["updated_at"] = now_iso()
    return set_device_profile(serial_number, profile, path=path)


def parse_color_data_line(line: str) -> dict | None:
    parts = [part.strip() for part in str(line or "").split(",")]
    if len(parts) < 26 or parts[0] not in {"DATA", "TEL"}:
        return None
    try:
        if len(parts) >= 30:
            lab_a_centi = int(parts[18])
            lab_b_centi = int(parts[19])
            luma_milli = int(parts[20])
            gain = int(parts[21])
            integration_ms = int(parts[22])
            led_mode = int(parts[23])
            led_active = int(parts[24])
            health_flags = int(parts[25])
            classifier = int(parts[26])
            calibration_target_slot = int(parts[27])
            calibration_samples = int(parts[28])
            sample_timestamp_ms = int(parts[29])
        else:
            lab_a_centi = 0
            lab_b_centi = 0
            luma_milli = 0
            gain = int(parts[18])
            integration_ms = int(parts[19])
            led_mode = int(parts[20])
            led_active = 0
            health_flags = int(parts[21])
            classifier = int(parts[22])
            calibration_target_slot = int(parts[23])
            calibration_samples = int(parts[24])
            sample_timestamp_ms = int(parts[25])
        return {
            "kind": parts[0],
            "palette_mode": int(parts[1]),
            "detected_slot": int(parts[2]),
            "confidence_milli": int(parts[3]),
            "top": [
                {"slot": int(parts[4]), "confidence_milli": int(parts[5])},
                {"slot": int(parts[6]), "confidence_milli": int(parts[7])},
                {"slot": int(parts[8]), "confidence_milli": int(parts[9])},
            ],
            "raw": {
                "r": int(parts[10]),
                "g": int(parts[11]),
                "b": int(parts[12]),
                "c": int(parts[13]),
            },
            "norm_rgb_milli": {
                "r": int(parts[14]),
                "g": int(parts[15]),
                "b": int(parts[16]),
            },
            "lab_l_centi": int(parts[17]),
            "lab_a_centi": lab_a_centi,
            "lab_b_centi": lab_b_centi,
            "luma_milli": luma_milli,
            "gain": gain,
            "integration_ms": integration_ms,
            "led_mode": led_mode,
            "led_active": led_active,
            "health_flags": health_flags,
            "health": decode_health_flags(health_flags),
            "classifier": classifier,
            "calibration_target_slot": calibration_target_slot,
            "calibration_samples": calibration_samples,
            "sample_timestamp_ms": sample_timestamp_ms,
        }
    except (TypeError, ValueError, IndexError):
        return None


def parse_color_cfg_line(line: str) -> dict | None:
    parts = [part.strip() for part in str(line or "").split(",")]
    if len(parts) < 11 or parts[0] != "CFG":
        return None
    try:
        return {
            "sensor_name": parts[1],
            "sample_period_ms": int(parts[2]),
            "led_mode": int(parts[3]),
            "gain_mode": int(parts[4]),
            "gain": int(parts[5]),
            "integration_ms": int(parts[6]),
            "classifier": int(parts[7]),
            "confidence_milli": int(parts[8]),
            "target_clear": int(parts[9]),
            "palette_mode": int(parts[10]),
            "patch_sample_count": int(parts[11]) if len(parts) > 11 else None,
        }
    except (TypeError, ValueError, IndexError):
        return None


def parse_color_info_line(line: str) -> dict | None:
    parts = [part.strip() for part in str(line or "").split(",")]
    if len(parts) < 11 or parts[0] != "INFO":
        return None
    try:
        health_flags = int(parts[6])
        return {
            "sensor_name": parts[1],
            "module_type": parts[2],
            "firmware_module": parts[3],
            "module_id": int(parts[4]),
            "sensor_id": int(parts[5]),
            "health_flags": health_flags,
            "health": decode_health_flags(health_flags),
            "i2c_address": int(parts[7]),
            "sda_pin": int(parts[8]),
            "scl_pin": int(parts[9]),
            "led_pin": int(parts[10]),
        }
    except (TypeError, ValueError, IndexError):
        return None


def parse_color_cal_line(line: str) -> dict | None:
    parts = [part.strip() for part in str(line or "").split(",")]
    if len(parts) < 15 or parts[0] != "CAL":
        return None
    try:
        return {
            "palette_mode": int(parts[1]),
            "class_count": int(parts[2]),
            "valid_mask": int(parts[3]),
            "enabled_mask": int(parts[4]),
            "dark_valid": bool(int(parts[5])),
            "white_valid": bool(int(parts[6])),
            "dark": {"r": int(parts[7]), "g": int(parts[8]), "b": int(parts[9]), "c": int(parts[10])},
            "white": {"r": int(parts[11]), "g": int(parts[12]), "b": int(parts[13]), "c": int(parts[14])},
        }
    except (TypeError, ValueError, IndexError):
        return None


def parse_color_patch_line(line: str) -> dict | None:
    parts = [part.strip() for part in str(line or "").split(",")]
    if len(parts) < 13 or parts[0] != "PATCH":
        return None
    try:
        return {
            "palette_mode": int(parts[1]),
            "slot": int(parts[2]),
            "enabled": bool(int(parts[3])),
            "valid": bool(int(parts[4])),
            "sample_count": int(parts[5]),
            "name": parts[6],
            "norm_rgb_milli": {"r": int(parts[7]), "g": int(parts[8]), "b": int(parts[9])},
            "lab": {"l_centi": int(parts[10]), "a_centi": int(parts[11]), "b_centi": int(parts[12])},
            "luma_milli": int(parts[13]) if len(parts) > 13 else 0,
        }
    except (TypeError, ValueError, IndexError):
        return None


def parse_color_selftest_line(line: str) -> dict | None:
    parts = [part.strip() for part in str(line or "").split(",")]
    if len(parts) < 4 or parts[0] != "SELFTEST":
        return None
    try:
        return {
            "ok": bool(int(parts[1])),
            "sensor_id": int(parts[2]),
            "message": parts[3],
        }
    except (TypeError, ValueError, IndexError):
        return None
