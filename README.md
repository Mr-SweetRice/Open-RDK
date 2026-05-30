# Open-RDK

Host relay, web UI, SDK, and ESP32-C3 firmware modules for the RDK platform.

## Layout

- `host/main/`: Python host relay, SDK entrypoint, mDNS/HTTPS web UI, and module tools.
- `host/main/src/openrdk/web_new/`: current browser interface served by the host relay.
- `firmware/esp/modules/`: active ESP-IDF firmware projects, one directory per module.
- `firmware/legacy_firmware/`: preserved legacy firmware sources.
- `protocol/protocol.md`: current host/firmware serial protocol.
- `docs/`: SDK and runtime reference.
- `tools/scripts/`: helper scripts.

## Host Relay

Run from `host/main`:

```bash
pip install -e .
openrdk
```

The relay owns serial communication, device discovery, telemetry, and all browser tools. The UI is served on port `8765`; with mDNS enabled the local URL is:

```text
http://rdk.local:8765
```

If HTTP redirect is enabled and port 80 is available, `http://rdk.local` redirects to the relay UI.

## Firmware

Each active firmware is an ESP-IDF project:

```bash
cd firmware/esp/modules/traction_module
idf.py set-target esp32c3
idf.py build
idf.py -p <PORT> flash monitor
```

The active firmware module tools were removed from firmware directories; configuration and tuning now happen through the host relay UI.

## Protocol

The framed protocol uses `CMD`, `TEST`, `TELEMETRY`, and generic `CONTROL (0x04)` message types. `CONTROL` replaces the old public `TRACTION_OUT` message type name; the host still accepts `TRACTION_OUT` as a backward-compatible alias for old registries.

See `protocol/protocol.md` for the full frame format and command catalog.
