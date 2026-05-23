#!/usr/bin/env python3
"""
Line sensor monitor — shows live readings, auto-fixes track type, guides calibration.

PYTHONPATH=host/main/src python3 test/linesensor.py
"""

from __future__ import annotations
import time
from openrdk import CommsRuntime

SENSOR_NAME     = "linha"
PRINT_INTERVAL  = 0.1       # terminal refresh rate (s)
TARGET_TRACK    = "dark"    # black line on white background → LS_TRACK_DARK


def main():
    openrdk = CommsRuntime(
        auto_start=True,
        enable_webview=True,
        enable_webview_updates=True,
    )
    openrdk.post("webview_complete")

    sensor_serial = openrdk.get_serial_by_name(SENSOR_NAME)
    if not sensor_serial:
        print(f"ERROR: device '{SENSOR_NAME}' not found. Known devices:")
        openrdk.list_devices(verbose="names")
        openrdk.stop()
        return

    print(f"waiting for {SENSOR_NAME} ({sensor_serial})...")
    openrdk.wait_online(sensor_serial)

    sensor = openrdk.line_sensor(sensor_serial)

    # ── read and display current config ─────────────────────────────────────
    print("\nreading sensor config...")
    cfg = sensor.get_config()
    track_name = "DARK" if int(cfg.get("track_type", 0)) == 1 else "LIGHT"
    print(f"  track_type    : {track_name}  ({'dark line / white bg' if track_name == 'DARK' else 'white line / dark bg'})")
    print(f"  digital_th    : {cfg.get('digital_threshold', '?')}")
    print(f"  detect_th     : {cfg.get('detect_threshold', '?')}")
    print(f"  cal_time_ms   : {cfg.get('calibration_time_ms', '?')}")
    print(f"  name          : {cfg.get('sensor_name', '?')}")

    cal = sensor.get_calibration()
    cal_min = cal.get("min_raw", [])
    cal_max = cal.get("max_raw", [])
    if cal_min and cal_max:
        print(f"\ncalibration (min / max per sensor):")
        for i in range(len(cal_min)):
            rng = cal_max[i] - cal_min[i]
            note = "DEFAULT (uncalibrated)" if cal_min[i] == 0 and cal_max[i] == 4095 else \
                   f"range={rng}" + (" ← very narrow, recalibrate!" if rng < 200 else "")
            print(f"  S{i}: {cal_min[i]:4d} / {cal_max[i]:4d}   {note}")

    # ── fix track type if needed ─────────────────────────────────────────────
    if track_name != TARGET_TRACK.upper():
        print(f"\ntrack_type is {track_name} but should be {TARGET_TRACK.upper()}.")
        print("switching to DARK (black line on white background) and saving...")
        sensor.stop_streaming()          # ensure CMD mode for config change
        sensor.set_track_type(1)         # 0=LIGHT, 1=DARK
        sensor.save_config()
        print("track_type updated to DARK and saved to flash.")
    else:
        print(f"\ntrack_type is already {TARGET_TRACK.upper()} — OK")

    # ── calibration reminder ─────────────────────────────────────────────────
    any_uncalibrated = any(
        (cal_min[i] == 0 and cal_max[i] == 4095) or (cal_max[i] - cal_min[i] < 200)
        for i in range(len(cal_min))
    ) if cal_min else True

    if any_uncalibrated:
        print()
        print("─" * 60)
        print("CALIBRATION NEEDED")
        print("  1. Place sensor ~2mm above the track surface")
        print("  2. Run:  sensor.calibrate(duration_ms=5000, wait=True)")
        print("  3. During calibration: slowly sweep sensor over")
        print("     BOTH the white background AND the black line")
        print("  4. After calibration, normalized values should:")
        print("     • ~0.0 on white (no line)")
        print("     • ~1.0 on black (line detected)")
        print("─" * 60)
        print()

    # ── start streaming ──────────────────────────────────────────────────────
    print("starting streaming...")
    sensor.start_streaming()

    deadline = time.monotonic() + 5.0
    while sensor.get_latest_data() is None:
        if time.monotonic() > deadline:
            print("ERROR: no data after 5 s — check cable/port")
            sensor.stop_streaming()
            openrdk.stop()
            return
        time.sleep(0.01)

    print(f"streaming active — webview: {openrdk.webview_url}/line-sensor?serial={sensor_serial}")
    print()
    print("RAW = ADC counts (0-4095) | VAL = normalized 0.0-1.0 | pos = line position")
    print("In DARK mode: VAL≈1.0 on black line, VAL≈0.0 on white background")
    print()
    print(f"{'─'*14} RAW {'─'*14}  {'─'*20} VAL {'─'*20}  pos      str  line  [Hz]")

    last_print = 0.0
    frame_count = 0
    rate_check_at = time.monotonic()
    rate_hz = 0.0

    try:
        while True:
            data = sensor.get_latest_data()
            now  = time.monotonic()

            if data is not None:
                frame_count += 1

            elapsed = now - rate_check_at
            if elapsed >= 1.0:
                rate_hz = frame_count / elapsed
                frame_count = 0
                rate_check_at = now

            if (now - last_print) >= PRINT_INTERVAL:
                last_print = now
                if data is None:
                    print("  [no data]", flush=True)
                else:
                    raw  = data["raw"]
                    vals = data["values"]
                    pos  = data["position"]
                    str_ = data["strength"]
                    det  = "YES" if data["line_detected"] else "no"

                    raw_s = "  ".join(f"{r:4d}" for r in raw)
                    val_s = "  ".join(f"{v:.3f}" for v in vals)
                    print(
                        f"{raw_s}    {val_s}    {pos:+6.3f}  {str_:.3f}  {det}  [{rate_hz:.0f}]",
                        flush=True,
                    )

            time.sleep(0.005)

    except KeyboardInterrupt:
        print("\nshutdown")
    finally:
        sensor.stop_streaming()
        openrdk.stop()


if __name__ == "__main__":
    main()
