# AI Agent Quick Reference (`host/pi/comms`)

## Mission
Maintain a reliable host-side relay for ESP devices:
- detect attach/detach
- keep canonical device state
- record raw bytes
- expose state + stream to web clients

## Hard Constraints
- Run on host environment (`python3 -m msg_relay.run`), not dependent on devcontainer runtime.
- Keep `espressif_devices.json` as **device registry only**.
- Keep `comms-raw.log` as **raw communication only** (`sender`, `raw_hex`).
- Device `name` is host-managed display label: persists by serial while firmware is unchanged.
- Do not reintroduce legacy `communications` arrays inside device JSON.
- Avoid destructive git commands on user workspace.

## Critical Files
- `src/msg_relay/run.py`: process bootstrap, log trimming, web thread start, relay start.
- `src/msg_relay/functions.py`: udev + serial + handshake + keepalive + DB writes.
- `src/msg_relay/webview.py`: FastAPI app, REST, websocket, log-thread and stream-thread.
- `src/msg_relay/web/app.js`: client rendering and websocket consumer.
- `src/msg_relay/web/styles.css`: UI style.
- `src/msg_relay/espressif_devices.json`: device state DB.
- `comms-raw.log`: raw comms NDJSON DB.

## Startup Sequence
1. `run.py` resolves paths and starts log trimmer.
2. `run.py` triggers one-shot run access tracker.
3. `run.py` configures comms log path.
4. `run.py` starts webview in thread.
5. `run.py` calls `conex()` (udev monitor + serial logic).

## Protocol Summary
- Host sends handshake bytes.
- ESP replies ACK/PONG/module-info.
- Keepalive uses ping/pong.
- Module type is discovered on handshake and stored in device registry.

## Web Stream Model
- Thread A: tails `comms-raw.log`.
- Thread B: pops queue and fans events to websocket subscribers.
- Browser receives:
  - `snapshot`: current devices + last events
  - `comms`: incremental events

## API Contract
- `GET /api/devices` -> `{ "devices": [...] }`
- `POST /api/devices/{serial_number}/name` -> updates host-side display name
- `GET /api/comms` -> `{ "events": [...] }`
- `WS /ws/comms` -> `snapshot` then continuous `comms` messages

## Safe Change Guidelines
- Prefer minimal schema changes.
- If adding device fields, keep backward compatibility with existing keys.
- If touching stream path, verify:
  - `/api/health`
  - `/api/devices`
  - `/api/comms`
  - websocket snapshot receipt
- Keep frontend filtering behavior: selected serial shows host TX + selected device RX/TX.
