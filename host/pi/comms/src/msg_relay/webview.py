import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .constants import WEBVIEW_HOST, WEBVIEW_PORT, WEBVIEW_REFRESH_SECONDS


def _safe_load_db(db_path: str) -> dict:
    try:
        with open(db_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"devices": []}

    if not isinstance(data, dict):
        return {"devices": []}
    devices = data.get("devices")
    if not isinstance(devices, list):
        return {"devices": []}
    return data


def _safe_load_device_comms(db_path: str, serial_number: str) -> dict:
    data = _safe_load_db(db_path)
    for item in data.get("devices", []):
        if item.get("serial_number") != serial_number:
            continue
        communications = item.get("communications")
        if not isinstance(communications, list):
            communications = []
        return {"serial_number": serial_number, "communications": communications}
    return {"serial_number": serial_number, "communications": []}


def _render_index_html(refresh_ms: int) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ESP Host Device Status</title>
  <style>
    :root {{
      --bg: #0a1220;
      --bg-grad: #111f35;
      --card: #0f1a2d;
      --text: #d8e4f5;
      --muted: #89a0be;
      --blue: #4c8dff;
      --orange: #ff9f43;
      --line: #233751;
      --head: #12233a;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Tahoma", sans-serif;
      background: radial-gradient(circle at top right, var(--bg-grad) 0%, var(--bg) 55%);
      color: var(--text);
    }}
    .wrap {{
      max-width: 1340px;
      margin: 24px auto;
      padding: 0 16px;
    }}
    h1 {{
      margin: 0 0 8px 0;
      font-size: 1.35rem;
    }}
    .meta {{
      color: var(--muted);
      margin-bottom: 16px;
      font-size: 0.92rem;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
    }}
    .layout {{
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 14px;
      align-items: start;
    }}
    .table-card {{
      min-width: 0;
    }}
    .detail-card {{
      min-height: 420px;
      display: flex;
      flex-direction: column;
    }}
    .detail-head {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: #10213a;
      color: #c1d8f7;
      font-size: 0.92rem;
      line-height: 1.4;
    }}
    .detail-head strong {{
      display: block;
      color: #e6f0ff;
      margin-bottom: 2px;
    }}
    .tabs {{
      display: flex;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #0f1f35;
    }}
    .tab-btn {{
      border: 1px solid #2d4463;
      background: #162a45;
      color: #a9c0dd;
      padding: 7px 10px;
      border-radius: 8px;
      font-size: 0.82rem;
      cursor: pointer;
      user-select: none;
    }}
    .tab-btn.active {{
      border-color: var(--blue);
      background: #214071;
      color: #e4eeff;
    }}
    .comm-body {{
      margin: 0;
      padding: 12px;
      min-height: 300px;
      overflow: hidden;
      background: #0c172a;
      color: #d4e6ff;
      font-size: 0.82rem;
      line-height: 1.45;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .comm-empty {{
      color: var(--muted);
    }}
    .comm-line {{
      display: grid;
      grid-template-columns: 2.2em 1fr;
      gap: 8px;
      align-items: start;
      white-space: pre-wrap;
      word-break: break-word;
      border-bottom: 1px dashed rgba(137, 160, 190, 0.22);
      padding-bottom: 3px;
    }}
    .comm-line:last-child {{
      border-bottom: none;
    }}
    .line-no {{
      color: var(--orange);
      font-weight: 700;
      text-align: right;
      user-select: none;
    }}
    .row-no {{
      color: var(--orange);
      font-weight: 700;
      width: 2.2em;
      text-align: right;
      user-select: none;
    }}
    .device-row-live {{
      cursor: pointer;
    }}
    .device-row-live:hover {{
      background: rgba(76, 141, 255, 0.12);
    }}
    .device-row-selected {{
      background: rgba(255, 159, 67, 0.18);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.93rem;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: var(--head);
      font-weight: 600;
      color: #aec6e7;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    .status-online {{
      color: var(--blue);
      font-weight: 600;
    }}
    .status-offline {{
      color: var(--orange);
      font-weight: 600;
    }}
    .link-live {{
      color: var(--blue);
      font-weight: 600;
    }}
    .link-notlive {{
      color: var(--orange);
      font-weight: 600;
    }}
    .module-type {{
      color: #9bc3ff;
      font-weight: 600;
    }}
    .empty {{
      padding: 14px 12px;
      color: var(--muted);
    }}
    code {{
      background: #1a2c46;
      padding: 0 4px;
      border-radius: 4px;
    }}
    @media (max-width: 1020px) {{
      .layout {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>ESP Host Device Status</h1>
    <div class="meta">
      Auto-refresh: every <strong>{refresh_ms} ms</strong>.
      APIs: <code>/api/devices</code>, <code>/api/device-comms?serial_number=...</code>.
    </div>
    <div class="layout">
      <div class="card table-card">
        <table id="devices-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Serial Number</th>
              <th>Module Type</th>
              <th>Device Node</th>
              <th>Status</th>
              <th>Link</th>
              <th>Last Event</th>
              <th>Last Link Check</th>
            </tr>
          </thead>
          <tbody id="devices-body">
            <tr><td class="empty" colspan="8">Loading...</td></tr>
          </tbody>
        </table>
      </div>
      <div class="card detail-card">
        <div class="detail-head" id="detail-head">
          <strong>No live connection selected</strong>
          Click a row with live link to inspect communication frames.
        </div>
        <div class="tabs">
          <button id="tab-raw" class="tab-btn active" type="button">Raw Bytes</button>
          <button id="tab-text" class="tab-btn" type="button">Converted Text</button>
        </div>
        <div id="comm-body" class="comm-body"><div class="comm-empty">No communication data yet.</div></div>
      </div>
    </div>
  </div>
  <script>
    const refreshMs = {refresh_ms};
    const baseUrl = `${{window.location.protocol}}//${{window.location.host}}`;
    const deviceApiUrl = `${{baseUrl}}/api/devices`;
    const commApiUrl = `${{baseUrl}}/api/device-comms`;

    let devicesCache = [];
    let selectedSerial = "";
    let selectedTab = "raw";
    let communicationsCache = [];
    const COMM_LINE_LIMIT = 20;

    function esc(v) {{
      if (v === null || v === undefined) return "";
      return String(v)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }}

    function timeOnly(value) {{
      const text = String(value || "");
      const match = text.match(/([0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}})/);
      return match ? match[1] : text;
    }}

    function renderCommPlaceholder(message) {{
      renderCommLines([message]);
    }}

    function renderCommLines(lines) {{
      const pane = document.getElementById("comm-body");
      const paddedLines = Array.from(
        {{ length: COMM_LINE_LIMIT }},
        (_, idx) => lines[idx] || ""
      );
      pane.innerHTML = paddedLines
        .map(
          (line, idx) =>
            `<div class="comm-line"><span class="line-no">${{idx + 1}}</span><span>${{line ? esc(line) : "&nbsp;"}}</span></div>`
        )
        .join("");
    }}

    function isLiveConnection(device) {{
      const status = String(device?.status || "").toLowerCase();
      const link = String(device?.link_status || "").toLowerCase();
      return status.includes("online") && link === "live";
    }}

    function statusClass(text) {{
      const t = String(text || "").toLowerCase();
      return t.includes("online") ? "status-online" : "status-offline";
    }}

    function linkClass(text) {{
      const t = String(text || "").toLowerCase();
      return t === "live" ? "link-live" : "link-notlive";
    }}

    function selectedDevice() {{
      return devicesCache.find((d) => d.serial_number === selectedSerial) || null;
    }}

    function renderDetailHeader(errorText = "") {{
      const head = document.getElementById("detail-head");
      if (!selectedSerial) {{
        head.innerHTML = "<strong>No live connection selected</strong>Click a row with live link to inspect communication frames.";
        return;
      }}

      const device = selectedDevice();
      const moduleType = device?.module_type || device?.firmware_module || "unknown";
      const state = device ? `${{device.status || "unknown"}} | ${{device.link_status || "unknown"}}` : "device not in table";
      const extra = errorText ? ` | ${{
        esc(errorText)
      }}` : "";
      head.innerHTML = `<strong>Serial: ${{esc(selectedSerial)}} | Module: ${{esc(moduleType)}}</strong>${{esc(state)}}${{extra}}`;
    }}

    function renderCommunications() {{
      if (!selectedSerial) {{
        renderCommPlaceholder("No communication data yet.");
        return;
      }}
      if (!communicationsCache.length) {{
        renderCommPlaceholder("No frames captured for this connection yet.");
        return;
      }}

      const lines = communicationsCache.map((item) => {{
        const ts = timeOnly(item.timestamp || "");
        const phase = String(item.phase || "-").toUpperCase();
        const direction = String(item.direction || "-").toUpperCase();
        if (selectedTab === "raw") {{
          return `[${{ts}}] ${{phase}} ${{direction}}  ${{item.raw_hex || ""}}`;
        }}
        return `[${{ts}}] ${{phase}} ${{direction}}  ${{item.text || ""}}`;
      }});
      renderCommLines(lines);
    }}

    function renderRows(devices) {{
      const body = document.getElementById("devices-body");
      if (!devices.length) {{
        body.innerHTML = '<tr><td class="empty" colspan="8">No devices in registry.</td></tr>';
        return;
      }}

      body.innerHTML = devices.map((d, idx) => {{
        const live = isLiveConnection(d);
        const selected = selectedSerial && d.serial_number === selectedSerial;
        const rowClasses = [
          live ? "device-row-live" : "",
          selected ? "device-row-selected" : "",
        ].join(" ").trim();
        return `
          <tr class="${{rowClasses}}" data-serial="${{esc(d.serial_number)}}" data-live="${{live ? "1" : "0"}}">
            <td class="row-no">${{idx + 1}}</td>
            <td>${{esc(d.serial_number)}}</td>
            <td class="module-type">${{esc(d.module_type || d.firmware_module || "unknown")}}</td>
            <td>${{esc(d.device_node)}}</td>
            <td class="${{statusClass(d.status)}}">${{esc(d.status)}}</td>
            <td class="${{linkClass(d.link_status)}}">${{esc(d.link_status)}}</td>
            <td>${{esc(timeOnly(d.last_event_at))}}</td>
            <td>${{esc(timeOnly(d.last_link_check_at))}}</td>
          </tr>
        `;
      }}).join("");

      body.querySelectorAll("tr[data-live='1']").forEach((row) => {{
        row.addEventListener("click", () => {{
          const serial = row.getAttribute("data-serial");
          if (!serial) {{
            return;
          }}
          selectedSerial = serial;
          communicationsCache = [];
          renderRows(devicesCache);
          renderDetailHeader();
          renderCommunications();
          refreshCommunications();
        }});
      }});
    }}

    function setTab(tabName) {{
      selectedTab = tabName;
      document.getElementById("tab-raw").classList.toggle("active", tabName === "raw");
      document.getElementById("tab-text").classList.toggle("active", tabName === "text");
      renderCommunications();
    }}

    async function refreshCommunications() {{
      if (!selectedSerial) {{
        renderDetailHeader();
        renderCommunications();
        return;
      }}

      try {{
        const res = await fetch(
          `${{commApiUrl}}?serial_number=${{encodeURIComponent(selectedSerial)}}`,
          {{ cache: "no-store" }}
        );
        if (!res.ok) {{
          throw new Error(`HTTP ${{res.status}}`);
        }}
        const payload = await res.json();
        const rows = Array.isArray(payload.communications) ? payload.communications : [];
        communicationsCache = rows;
        renderDetailHeader();
        renderCommunications();
      }} catch (err) {{
        renderDetailHeader(err.message || "failed to load comms");
        renderCommPlaceholder(`Failed to load communication data: ${{err.message || err}}`);
      }}
    }}

    async function refresh() {{
      try {{
        const res = await fetch(deviceApiUrl, {{ cache: "no-store" }});
        if (!res.ok) {{
          throw new Error(`HTTP ${{res.status}}`);
        }}
        const data = await res.json();
        devicesCache = Array.isArray(data.devices) ? data.devices : [];
        renderRows(devicesCache);
        renderDetailHeader();
        await refreshCommunications();
      }} catch (err) {{
        const body = document.getElementById("devices-body");
        body.innerHTML = `<tr><td class="empty" colspan="7">Failed to load data: ${{esc(err.message)}}</td></tr>`;
      }}
    }}

    document.getElementById("tab-raw").addEventListener("click", () => setTab("raw"));
    document.getElementById("tab-text").addEventListener("click", () => setTab("text"));
    refresh();
    setInterval(refresh, refreshMs);
  </script>
</body>
</html>
"""


def _build_handler(db_path: str, refresh_seconds: float):
    refresh_ms = max(int(max(refresh_seconds, 0.2) * 1000), 200)
    index_html = _render_index_html(refresh_ms).encode("utf-8")

    class DeviceStatusHandler(BaseHTTPRequestHandler):
        def _send_json(self, payload: dict):
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, body: bytes):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/devices":
                payload = _safe_load_db(db_path)
                self._send_json(payload)
                return
            if path == "/api/device-comms":
                serial_number = parse_qs(parsed.query).get("serial_number", [""])[0].strip()
                payload = _safe_load_device_comms(db_path, serial_number)
                self._send_json(payload)
                return
            if path == "/":
                self._send_html(index_html)
                return

            body = b"Not Found\n"
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            path = urlparse(self.path).path
            if path in ("/api/devices", "/api/device-comms"):
                body = b""
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return
            if path == "/":
                body = b""
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return

            body = b""
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def log_message(self, fmt: str, *args):
            print(f"[webview] {self.address_string()} - {fmt % args}", flush=True)

    return DeviceStatusHandler


def start_webview_server(
    db_path: str,
    host: str = WEBVIEW_HOST,
    port: int = WEBVIEW_PORT,
    refresh_seconds: float = WEBVIEW_REFRESH_SECONDS,
) -> ThreadingHTTPServer:
    handler = _build_handler(db_path, refresh_seconds)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="msg-relay-webview",
        daemon=True,
    )
    thread.start()
    return server
