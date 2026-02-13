# Web UI Notes (`src/msg_relay/web`)

## Files
- `index.html`: shell layout (device panel + comms panel)
- `app.js`: state, websocket consumer, device selection, render logic
- `styles.css`: dark blue/orange UI theme, responsive layout

## Behavior
- Device list shows:
  - editable display name
  - serial number
  - module type
  - link/status
  - tty port (`device_node`)
- Rename action updates host-side JSON (`name` field), no firmware change required.
- Clicking a device filters comms to:
  - selected device events
  - host TX events
- Comms panel shows line number, direction (TX/RX), sender, raw hex.
- UI auto-scrolls when viewer is already at bottom.

## Data Sources
- Devices: `GET /api/devices`
- Comms snapshot + live updates: `WS /ws/comms`

## Constraints
- Keep frontend dependency-free (vanilla JS/CSS/HTML).
- Keep rendering incremental and lightweight (no full-page refresh loops).
