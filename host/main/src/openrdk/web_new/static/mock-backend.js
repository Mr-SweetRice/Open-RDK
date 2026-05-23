/* Mock backend for the RDK Web Monitor.
 * Intercepts fetch() + WebSocket so the pages render with realistic data
 * without a running Python host.
 */
(function () {
  "use strict";

  // ---------- shared state ----------
  const NOW = () => new Date().toISOString();
  const supportedMessageTypes = [
    { name: "CMD",          default_content: "GET PID RPM" },
    { name: "TELEMETRY",    default_content: "" },
    { name: "TRACTION_OUT", default_content: "OUT,0" },
    { name: "TEST",         default_content: "PING" },
    { name: "KEEPALIVE",    default_content: "KA" },
  ];
  const supportedBaudRates = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600];

  const devices = [
    {
      serial_number: "TR-001-B2C8",
      name: "Front Traction",
      module_type: "traction_module",
      device_node: "/dev/ttyUSB0",
      status: "online connected",
      link_status: "live",
      message_type: "CMD",
      traction_out_value: 35,
      error_count: 0,
      last_error_kind: "-",
      last_error_at: "-",
      telemetry_active: false,
    },
    {
      serial_number: "LS-001-A3F7",
      name: "Front Line Array",
      module_type: "line_sensor_module",
      device_node: "/dev/ttyUSB1",
      status: "online connected",
      link_status: "live",
      message_type: "CMD",
      traction_out_value: 0,
      error_count: 0,
      last_error_kind: "-",
      last_error_at: "-",
      telemetry_active: false,
    },
    {
      serial_number: "CL-001-D4E9",
      name: "Color Studio Head",
      module_type: "color_module",
      device_node: "/dev/ttyUSB2",
      status: "online connected",
      link_status: "live",
      message_type: "TELEMETRY",
      traction_out_value: 0,
      error_count: 0,
      last_error_kind: "-",
      last_error_at: "-",
      telemetry_active: true,
    },
    {
      serial_number: "UN-X07-1F02",
      name: "",
      module_type: "NOT-RDK-MODULE",
      device_node: "/dev/ttyUSB3",
      status: "offline disconnected",
      link_status: "not live",
      message_type: "CMD",
      traction_out_value: 0,
      error_count: 3,
      last_error_kind: "timeout",
      last_error_at: "2026-05-18T09:14:22Z",
      telemetry_active: false,
    },
  ];

  let activeBaudRate = 115200;

  // ---------- comms event log ----------
  const COLOR_SERIAL = "CL-001-D4E9";
  const TR_SERIAL    = "TR-001-B2C8";
  const LS_SERIAL    = "LS-001-A3F7";
  let lineCounter = 0;
  let seqAbs = 0;
  const events = [];

  function pushEvent(ev) {
    lineCounter += 1;
    seqAbs += 1;
    const full = Object.assign(
      { line: lineCounter, seq_abs: seqAbs, seq: seqAbs % 65536, ts: NOW(), latency_ms: 1.2 + Math.random() * 6 },
      ev,
    );
    events.push(full);
    if (events.length > 3000) events.splice(0, events.length - 3000);
    return full;
  }

  // seed a handshake + a few comms cycles
  function seedHistory() {
    pushEvent({ sender: "host", direction: "tx", phase: "hello", message_type: "TEST", device_serial: TR_SERIAL, message: "HELLO" });
    pushEvent({ sender: TR_SERIAL, direction: "rx", phase: "hello", message_type: "TEST", device_serial: TR_SERIAL, message: "HELLO ACK traction_module" });
    pushEvent({ sender: "host", direction: "tx", phase: "module", message_type: "CMD", device_serial: TR_SERIAL, message: "GET MODULE" });
    pushEvent({ sender: TR_SERIAL, direction: "rx", phase: "module", message_type: "CMD", device_serial: TR_SERIAL, message: "MODULE,traction_module,1.4.2" });

    pushEvent({ sender: "host", direction: "tx", phase: "hello", message_type: "TEST", device_serial: LS_SERIAL, message: "HELLO" });
    pushEvent({ sender: LS_SERIAL, direction: "rx", phase: "hello", message_type: "TEST", device_serial: LS_SERIAL, message: "HELLO ACK line_sensor_module" });
    pushEvent({ sender: "host", direction: "tx", phase: "module", message_type: "CMD", device_serial: LS_SERIAL, message: "GET MODULE" });
    pushEvent({ sender: LS_SERIAL, direction: "rx", phase: "module", message_type: "CMD", device_serial: LS_SERIAL, message: "MODULE,line_sensor_module,0.9.1" });

    pushEvent({ sender: "host", direction: "tx", phase: "hello", message_type: "TEST", device_serial: COLOR_SERIAL, message: "HELLO" });
    pushEvent({ sender: COLOR_SERIAL, direction: "rx", phase: "hello", message_type: "TEST", device_serial: COLOR_SERIAL, message: "HELLO ACK color_module" });
    pushEvent({ sender: "host", direction: "tx", phase: "module", message_type: "CMD", device_serial: COLOR_SERIAL, message: "GET MODULE" });
    pushEvent({ sender: COLOR_SERIAL, direction: "rx", phase: "module", message_type: "CMD", device_serial: COLOR_SERIAL, message: "MODULE,color_module,2.1.0" });

    // a few CMD round-trips
    for (let i = 0; i < 6; i += 1) {
      pushEvent({ sender: "host", direction: "tx", phase: "stream", message_type: "CMD", device_serial: TR_SERIAL, message: "GET PID RPM" });
      pushEvent({ sender: TR_SERIAL, direction: "rx", phase: "stream", message_type: "CMD", device_serial: TR_SERIAL,
        message: `PID,${(1.20 + Math.random()*0.05).toFixed(2)},${(0.32 + Math.random()*0.04).toFixed(2)},${(0.06 + Math.random()*0.01).toFixed(2)},RPM,${(380 + (Math.random()*40|0))}` });
    }
    // some telemetry from color
    for (let i = 0; i < 4; i += 1) {
      pushEvent({ sender: COLOR_SERIAL, direction: "rx", phase: "stream", message_type: "TELEMETRY", device_serial: COLOR_SERIAL,
        message: buildTelemetryLine() });
    }
  }

  // ---------- color module simulation ----------
  const colorState = {
    sensor_name: "Color Studio Head",
    palette_mode: 8,
    sample_period_ms: 80,
    led_mode: 1,
    gain_mode: 1,
    gain: 16,
    integration_ms: 50,
    classifier: 1, // Lab
    confidence_milli: 720,
    target_clear: 18000,
    patch_sample_count: 12,
    info: { module_type: "color_module", sensor_id: 0x44, firmware: "2.1.0" },
    last_calibrated_at: {
      "4":  "2026-04-12T11:08:14Z",
      "8":  "2026-05-12T16:24:09Z",
      "16": null,
    },
    detected_slot: 2,
    confidence: 880,
    raw: { r: 1240, g: 980, b: 460, c: 5600 },
    selftest_ok: true,
    calibrating: false,
    calibration_target_slot: -1,
    calibration_samples: 0,
  };

  const palettes = {
    "4":  [
      { slot: 0, name: "red",    hex: "#e34a4a", enabled: true },
      { slot: 1, name: "green",  hex: "#3cb371", enabled: true },
      { slot: 2, name: "blue",   hex: "#3a78d6", enabled: true },
      { slot: 3, name: "yellow", hex: "#e9c947", enabled: true },
    ],
    "8":  [
      { slot: 0, name: "red",     hex: "#e34a4a", enabled: true },
      { slot: 1, name: "orange",  hex: "#ff9f3f", enabled: true },
      { slot: 2, name: "yellow",  hex: "#e9c947", enabled: true },
      { slot: 3, name: "green",   hex: "#3cb371", enabled: true },
      { slot: 4, name: "cyan",    hex: "#4bc0ff", enabled: true },
      { slot: 5, name: "blue",    hex: "#3a78d6", enabled: true },
      { slot: 6, name: "magenta", hex: "#c84cc1", enabled: true },
      { slot: 7, name: "white",   hex: "#f1f2f4", enabled: true },
    ],
    "16": Array.from({ length: 16 }, (_, i) => ({
      slot: i,
      name: `slot-${i}`,
      hex: `hsl(${(i * 22) % 360}, 65%, 55%)`,
      enabled: true,
    })),
  };

  function profileForKey(modeKey) {
    const labels = palettes[String(modeKey)] || [];
    return {
      modes: {
        [String(modeKey)]: {
          labels: labels.map((p) => ({ ...p })),
          last_calibrated_at: colorState.last_calibrated_at[String(modeKey)] || null,
          patches: [],
          summary: null,
        },
      },
    };
  }

  function fullProfile() {
    const out = { modes: {} };
    for (const k of Object.keys(palettes)) {
      out.modes[k] = {
        labels: palettes[k].map((p) => ({ ...p })),
        last_calibrated_at: colorState.last_calibrated_at[k] || null,
        patches: [],
        summary: null,
      };
    }
    return out;
  }

  function colorSnapshot() {
    const raw = colorState.raw;
    const sum = Math.max(1, raw.r + raw.g + raw.b);
    return {
      info: colorState.info,
      cfg: {
        sensor_name: colorState.sensor_name,
        palette_mode: colorState.palette_mode,
        sample_period_ms: colorState.sample_period_ms,
        led_mode: colorState.led_mode,
        gain_mode: colorState.gain_mode,
        gain: colorState.gain,
        integration_ms: colorState.integration_ms,
        classifier: colorState.classifier,
        confidence_milli: colorState.confidence_milli,
        target_clear: colorState.target_clear,
        patch_sample_count: colorState.patch_sample_count,
      },
      data: {
        palette_mode: colorState.palette_mode,
        detected_slot: colorState.detected_slot,
        confidence_milli: colorState.confidence,
        top: [
          { slot: colorState.detected_slot,                       confidence_milli: colorState.confidence },
          { slot: (colorState.detected_slot + 1) % colorState.palette_mode, confidence_milli: 110 },
          { slot: (colorState.detected_slot + 3) % colorState.palette_mode, confidence_milli: 32  },
        ],
        raw,
        norm_rgb_milli: {
          r: Math.round((raw.r / sum) * 1000),
          g: Math.round((raw.g / sum) * 1000),
          b: Math.round((raw.b / sum) * 1000),
        },
        lab_l_centi: 6420,
        lab_a_centi: 1820,
        lab_b_centi: -1430,
        luma_milli: 612,
        gain: colorState.gain,
        integration_ms: colorState.integration_ms,
        led_mode: colorState.led_mode,
        led_active: 1,
        health_flags: 0b11001101,
        classifier: colorState.classifier,
        calibration_target_slot: colorState.calibration_target_slot,
        calibration_samples: colorState.calibration_samples,
        sample_timestamp_ms: Date.now() % 1_000_000,
        health: {
          sensor_ok: true,
          sensor_present: true,
          dark_valid: true,
          white_valid: true,
          auto_exposure: true,
          saturated: false,
          calibrating: colorState.calibrating,
          selftest_ok: colorState.selftest_ok,
        },
      },
      profile: fullProfile(),
    };
  }

  function buildTelemetryLine() {
    const snap = colorSnapshot();
    const d = snap.data;
    return [
      "TEL", d.palette_mode, d.detected_slot, d.confidence_milli,
      d.top[0].slot, d.top[0].confidence_milli,
      d.top[1].slot, d.top[1].confidence_milli,
      d.top[2].slot, d.top[2].confidence_milli,
      d.raw.r, d.raw.g, d.raw.b, d.raw.c,
      d.norm_rgb_milli.r, d.norm_rgb_milli.g, d.norm_rgb_milli.b,
      d.lab_l_centi, d.lab_a_centi, d.lab_b_centi, d.luma_milli,
      d.gain, d.integration_ms, d.led_mode, d.led_active,
      d.health_flags, d.classifier,
      d.calibration_target_slot, d.calibration_samples, d.sample_timestamp_ms,
    ].join(",");
  }

  // animate the color snapshot a bit so the page feels alive
  setInterval(() => {
    // wander rgb a bit
    const jitter = () => (Math.random() - 0.5) * 80;
    colorState.raw.r = Math.max(20, Math.min(8000, colorState.raw.r + jitter()));
    colorState.raw.g = Math.max(20, Math.min(8000, colorState.raw.g + jitter()));
    colorState.raw.b = Math.max(20, Math.min(8000, colorState.raw.b + jitter()));
    colorState.raw.c = Math.round(colorState.raw.r + colorState.raw.g + colorState.raw.b + 800);
    if (Math.random() < 0.06) {
      colorState.detected_slot = Math.floor(Math.random() * colorState.palette_mode);
      colorState.confidence = 600 + Math.floor(Math.random() * 380);
    }
  }, 600);

  // ---------- line sensor simulation ----------
  const lineState = {
    raw:   [220, 410, 880, 410, 230],
    value: [0.18, 0.42, 0.92, 0.41, 0.20],
    dig:   [0, 0, 1, 0, 0],
    pos: 0.05,
    str: 0.74,
    det: 1,
    cal: 0,
    remMs: 0,
    lag: 1.4,
    cfg: { track: 1, digTh: 0.5500, detTh: 0.2500, calMs: 4000 },
    minRaw: [120, 130, 110, 125, 118],
    maxRaw: [940, 950, 945, 948, 938],
    info:  { name: "Front Line Array", status: "online connected", link: "live" },
  };
  let lineT = 0;
  setInterval(() => {
    lineT += 0.08;
    // simulate the line sweeping across the array
    const pos = Math.sin(lineT * 0.7) * 0.7;
    lineState.pos = pos;
    lineState.str = 0.55 + 0.25 * Math.cos(lineT * 0.5);
    const centerSensor = (pos + 1) * 2; // 0..4
    for (let i = 0; i < 5; i += 1) {
      const dist = Math.abs(i - centerSensor);
      const lit = Math.exp(-dist * dist * 1.4);
      const noise = (Math.random() - 0.5) * 0.06;
      const v = Math.max(0, Math.min(1, lit + noise));
      lineState.value[i] = v;
      lineState.raw[i]   = Math.round(120 + v * 820);
      lineState.dig[i]   = v > 0.55 ? 1 : 0;
    }
    lineState.det = lineState.dig.some((d) => d === 1) ? 1 : 0;
    lineState.lag = 1.1 + Math.random() * 1.4;
  }, 90);

  function lineSnapshot() {
    const p = lineState;
    const ls = [
      "LS",
      ...p.raw,
      ...p.value.map((v) => v.toFixed(4)),
      ...p.dig,
      p.pos.toFixed(4),
      p.str.toFixed(4),
      p.det,
      p.cal,
      p.remMs,
      p.lag.toFixed(3),
    ].join(",");
    const cfg = `CFG,${p.cfg.track},${p.cfg.digTh.toFixed(4)},${p.cfg.detTh.toFixed(4)},${p.cfg.calMs},${p.info.name}`;
    const cal = `CAL,${p.minRaw.join(",")},${p.maxRaw.join(",")}`;
    const info = `INFO,line_sensor_module,${p.info.name},${p.info.status},-,-,-,-,-,-,${p.info.link},-,-`;
    return { ls, cfg, cal, info };
  }

  // ---------- helpers ----------
  function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }
  function notFound() { return jsonResponse({ detail: "not found" }, 404); }
  function ok()       { return jsonResponse({ ok: true }); }
  function findDevice(serial) { return devices.find((d) => d.serial_number === serial) || null; }
  function snapshotEnvelope() {
    return {
      type: "snapshot",
      devices,
      events: events.slice(-500),
      supported_message_types: supportedMessageTypes,
      supported_baud_rates: supportedBaudRates,
      active_baud_rate: activeBaudRate,
    };
  }

  // ---------- fetch override ----------
  const realFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    try {
      const path = url.split("?")[0];
      const method = (init && init.method || "GET").toUpperCase();
      let body = null;
      if (init && init.body) {
        try { body = JSON.parse(init.body); } catch (_e) { body = null; }
      }
      const handler = await routeFetch(path, method, body, url);
      if (handler) return handler;
    } catch (err) {
      console.warn("[mock-backend] route error", err);
    }
    // fall through for things like static files
    return realFetch(input, init);
  };

  async function routeFetch(path, method, body, url) {
    // device list
    if (path === "/api/devices" && method === "GET") return jsonResponse({ devices });
    if (path === "/api/devices/clear" && method === "POST") {
      // keep them — pretend we cleared, then re-detected
      return jsonResponse({ devices });
    }
    // /api/devices/{serial}/...
    let m = path.match(/^\/api\/devices\/([^/]+)\/name$/);
    if (m && method === "POST") {
      const dev = findDevice(decodeURIComponent(m[1]));
      if (!dev) return notFound();
      dev.name = String(body?.name || "").trim();
      return jsonResponse({ device: dev });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/config\/message-type$/);
    if (m) {
      const dev = findDevice(decodeURIComponent(m[1]));
      if (!dev) return notFound();
      if (method === "POST" && body?.message_type) {
        dev.message_type = String(body.message_type).toUpperCase();
      }
      return jsonResponse({
        device: dev,
        active_message_type: dev.message_type,
        supported_message_types: supportedMessageTypes,
      });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/config\/traction-out$/);
    if (m && method === "POST") {
      const dev = findDevice(decodeURIComponent(m[1]));
      if (!dev) return notFound();
      const v = Math.max(0, Math.min(100, parseInt(body?.value ?? 0, 10) || 0));
      dev.traction_out_value = v;
      return jsonResponse({ device: dev, traction_out_value: v });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/traction-out\/send$/);
    if (m && method === "POST") {
      const dev = findDevice(decodeURIComponent(m[1]));
      if (!dev) return notFound();
      const v = Math.max(0, Math.min(100, parseInt(body?.value ?? 0, 10) || 0));
      dev.traction_out_value = v;
      const tx = pushEvent({ sender: "host", direction: "tx", phase: "stream", message_type: "TRACTION_OUT", device_serial: dev.serial_number, message: `OUT,${v}` });
      broadcast({ type: "comms", event: tx });
      setTimeout(() => {
        const rx = pushEvent({ sender: dev.serial_number, direction: "rx", phase: "stream", message_type: "TRACTION_OUT", device_serial: dev.serial_number, message: `ACK,OUT,${v}` });
        broadcast({ type: "comms", event: rx });
      }, 120);
      return jsonResponse({ traction_out_value: v });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/cmd\/send$/);
    if (m && method === "POST") {
      const dev = findDevice(decodeURIComponent(m[1]));
      if (!dev) return notFound();
      const command = String(body?.command || "").trim() || "GET PID RPM";
      const tx = pushEvent({ sender: "host", direction: "tx", phase: "stream", message_type: "CMD", device_serial: dev.serial_number, message: command });
      broadcast({ type: "comms", event: tx });
      const response = mockCmdResponse(command);
      setTimeout(() => {
        const rx = pushEvent({ sender: dev.serial_number, direction: "rx", phase: "stream", message_type: "CMD", device_serial: dev.serial_number, message: response });
        broadcast({ type: "comms", event: rx });
      }, 140);
      return jsonResponse({ command, response });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/telemetry\/(start|stop)$/);
    if (m && method === "POST") {
      const dev = findDevice(decodeURIComponent(m[1]));
      if (!dev) return notFound();
      dev.telemetry_active = m[2] === "start";
      return jsonResponse({ device: dev });
    }

    // serial config
    if (path === "/api/config/serial" && method === "GET") {
      return jsonResponse({ active_baud_rate: activeBaudRate, supported_baud_rates: supportedBaudRates });
    }
    if (path === "/api/config/serial" && method === "POST") {
      activeBaudRate = parseInt(body?.baud_rate, 10) || activeBaudRate;
      return jsonResponse({ active_baud_rate: activeBaudRate, supported_baud_rates: supportedBaudRates });
    }

    // ---------- color api ----------
    if (path === "/api/color/devices" && method === "GET") {
      return jsonResponse({ devices: devices.filter((d) => d.module_type === "color_module") });
    }
    if (path === "/api/color/palettes" && method === "GET") {
      return jsonResponse({ palettes });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/snapshot$/);
    if (m && method === "GET") return jsonResponse(colorSnapshot());

    m = path.match(/^\/api\/devices\/([^/]+)\/color\/calibration$/);
    if (m && method === "GET") return jsonResponse({ profile: fullProfile() });

    m = path.match(/^\/api\/devices\/([^/]+)\/color\/profile$/);
    if (m) {
      if (method === "GET") return jsonResponse({ profile: fullProfile() });
      if (method === "POST") {
        if (body?.profile?.modes) {
          for (const [k, mode] of Object.entries(body.profile.modes)) {
            if (palettes[k] && Array.isArray(mode.labels)) {
              palettes[k] = mode.labels.map((l) => ({ ...l }));
            }
          }
        }
        return jsonResponse({ profile: fullProfile() });
      }
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/config$/);
    if (m && method === "POST") {
      Object.assign(colorState, {
        sensor_name: body?.sensor_name ?? colorState.sensor_name,
        palette_mode: Number(body?.palette_mode ?? colorState.palette_mode),
        sample_period_ms: Number(body?.sample_period_ms ?? colorState.sample_period_ms),
        led_mode: Number(body?.led_mode ?? colorState.led_mode),
        gain_mode: Number(body?.gain_mode ?? colorState.gain_mode),
        gain: Number(body?.gain ?? colorState.gain),
        integration_ms: Number(body?.integration_ms ?? colorState.integration_ms),
        classifier: Number(body?.classifier ?? colorState.classifier),
        confidence_milli: Number(body?.confidence_milli ?? colorState.confidence_milli),
        target_clear: Number(body?.target_clear ?? colorState.target_clear),
        patch_sample_count: Number(body?.patch_sample_count ?? colorState.patch_sample_count),
      });
      return jsonResponse(colorSnapshot());
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/save$/);
    if (m && method === "POST") {
      if (body?.persist_cal) {
        colorState.last_calibrated_at[String(colorState.palette_mode)] = NOW();
      }
      return jsonResponse({ saved: true });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/selftest$/);
    if (m && method === "POST") {
      return jsonResponse({
        result: { ok: true, message: "all 4 channels nominal" },
        snapshot: colorSnapshot(),
      });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/restore-defaults$/);
    if (m && method === "POST") {
      colorState.palette_mode = 8;
      colorState.gain = 16;
      colorState.integration_ms = 50;
      colorState.classifier = 1;
      return jsonResponse({ snapshot: colorSnapshot(), profile: fullProfile() });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/calibration\/start$/);
    if (m && method === "POST") {
      colorState.calibrating = true;
      colorState.calibration_samples = 0;
      return jsonResponse({ ok: true });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/calibration\/stop$/);
    if (m && method === "POST") {
      colorState.calibrating = false;
      colorState.calibration_target_slot = -1;
      colorState.calibration_samples = 0;
      return jsonResponse({ ok: true });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/calibration\/select$/);
    if (m && method === "POST") {
      const t = body?.target;
      colorState.calibration_target_slot = (t === "DARK") ? -2 : (t === "WHITE") ? -1 : Number(t);
      // start accumulating samples
      colorState.calibration_samples = 0;
      const target = Number(colorState.patch_sample_count || 12);
      const iv = setInterval(() => {
        colorState.calibration_samples = Math.min(target, colorState.calibration_samples + 2);
        if (colorState.calibration_samples >= target) clearInterval(iv);
      }, 180);
      return jsonResponse({ ok: true });
    }
    m = path.match(/^\/api\/devices\/([^/]+)\/color\/calibration\/commit$/);
    if (m && method === "POST") {
      colorState.calibration_samples = 0;
      colorState.calibration_target_slot = -1;
      return jsonResponse({ snapshot: colorSnapshot(), profile: fullProfile() });
    }

    // ---------- line sensor api ----------
    m = path.match(/^\/api\/devices\/([^/]+)\/line-sensor\/snapshot$/);
    if (m && method === "GET") return jsonResponse(lineSnapshot());
    m = path.match(/^\/api\/devices\/([^/]+)\/line-sensor\/stream\/start$/);
    if (m && method === "POST") return ok();
    m = path.match(/^\/api\/devices\/([^/]+)\/line-sensor\/refresh$/);
    if (m && method === "POST") return ok();

    return null;
  }

  function mockCmdResponse(command) {
    const c = command.toUpperCase();
    if (c.startsWith("GET PID")) {
      const kp = (1.20 + Math.random() * 0.06).toFixed(2);
      const ki = (0.32 + Math.random() * 0.04).toFixed(2);
      const kd = (0.06 + Math.random() * 0.01).toFixed(2);
      const rpm = 380 + (Math.random() * 40 | 0);
      return `PID,${kp},${ki},${kd},RPM,${rpm}`;
    }
    if (c.startsWith("GET DATA")) return "DATA,ok,42";
    if (c === "PING") return "PONG";
    return `ACK,${command}`;
  }

  // ---------- WebSocket override (mock /ws/comms) ----------
  const RealWebSocket = window.WebSocket;
  const mockSockets = [];
  function broadcast(payload) {
    const str = JSON.stringify(payload);
    for (const s of mockSockets) {
      if (s.readyState === 1 && s.onmessage) {
        try { s.onmessage({ data: str }); } catch (_e) {}
      }
    }
  }

  class MockWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.onopen = null;
      this.onmessage = null;
      this.onclose = null;
      this.onerror = null;
      // only mock /ws/comms; fall back to real for anything else
      if (!/\/ws\/comms$/.test(url)) {
        return new RealWebSocket(url);
      }
      mockSockets.push(this);
      setTimeout(() => {
        this.readyState = 1;
        if (this.onopen) this.onopen({});
        if (this.onmessage) this.onmessage({ data: JSON.stringify(snapshotEnvelope()) });
      }, 20);
    }
    send() {}
    close() {
      this.readyState = 3;
      if (this.onclose) this.onclose({});
      const idx = mockSockets.indexOf(this);
      if (idx >= 0) mockSockets.splice(idx, 1);
    }
  }
  MockWebSocket.CONNECTING = 0;
  MockWebSocket.OPEN = 1;
  MockWebSocket.CLOSING = 2;
  MockWebSocket.CLOSED = 3;
  window.WebSocket = MockWebSocket;

  // ---------- live tick: emit periodic comms ----------
  function tick() {
    // CMD heartbeat to traction
    const tx = pushEvent({ sender: "host", direction: "tx", phase: "stream", message_type: "CMD", device_serial: TR_SERIAL, message: "GET PID RPM" });
    broadcast({ type: "comms", event: tx });
    setTimeout(() => {
      const rx = pushEvent({ sender: TR_SERIAL, direction: "rx", phase: "stream", message_type: "CMD", device_serial: TR_SERIAL, message: mockCmdResponse("GET PID RPM") });
      broadcast({ type: "comms", event: rx });
    }, 90);

    // color telemetry
    const colorDev = findDevice(COLOR_SERIAL);
    if (colorDev?.telemetry_active) {
      const tev = pushEvent({ sender: COLOR_SERIAL, direction: "rx", phase: "stream", message_type: "TELEMETRY", device_serial: COLOR_SERIAL, message: buildTelemetryLine() });
      broadcast({ type: "comms", event: tev });
    }

    // occasional keepalive on TEST type
    if (Math.random() < 0.35) {
      const k = pushEvent({ sender: "host", direction: "tx", phase: "stream", message_type: "TEST", device_serial: LS_SERIAL, message: "PING" });
      broadcast({ type: "comms", event: k });
      setTimeout(() => {
        const r = pushEvent({ sender: LS_SERIAL, direction: "rx", phase: "stream", message_type: "TEST", device_serial: LS_SERIAL, message: "PONG" });
        broadcast({ type: "comms", event: r });
      }, 60);
    }
  }

  seedHistory();
  setInterval(tick, 900);
})();


// ============================================================================
// Web Serial mock + simulated traction firmware
// ============================================================================
(function () {
  "use strict";
  if (typeof navigator === "undefined") return;

  // ---------- shared firmware state ----------
  const fw = {
    // RPM PID
    kp: 1.00, ki: 2.20, kd: 0.00,
    sp: 0,                 // setpoint rpm
    rpm: 0,                // actual rpm
    outPct: 0,             // applied PWM %
    outRawPct: 0,          // raw command %
    outOverride: null,     // when set, forces out (SET OUT)
    integ: 0,              // PID integrator
    prevErr: 0,

    // Position PID
    posKp: 4.00, posKi: 0.00, posKd: 0.10, posIwin: 5.0,
    posTarget: 0,
    posEnabled: false,
    pos: 0,                // current angle (deg)
    posOutPwm: 0,
    posOutRaw: 0,
    posIntegral: 0,
    posPrevErr: 0,

    // Sine sweep
    sineAmp: 90, sineOffset: 180, sinePeriod: 8.0,
    sineEnabled: false,
    sineT0: 0,

    // Motor config
    cfg: {
      bridge: 0,       // 0=TB6612, 1=DRV8833
      encMode: 0,      // 0=Quadx4, 1=Quadx2, 2=Single
      motorInv: 0,
      encInv: 0,
      pullup: 1,
      pwmFreq: 20000,
      countsPerRev: 44,
      gearRatio: 45,
      rpmMax: 199.89,
      notes: "Bench profile / default firmware values",
    },
    curve: [0, 0, 0, 0, 5.86, 23.6, 51.0, 87.6, 132.4, 199.89],
  };

  // ---------- physics tick ----------
  let lastTick = performance.now();
  setInterval(() => {
    const now = performance.now();
    const dt = Math.max(0.001, Math.min(0.1, (now - lastTick) / 1000));
    lastTick = now;

    // ---- RPM loop ----
    let out;
    if (fw.outOverride != null) {
      out = fw.outOverride;
      fw.integ = 0; fw.prevErr = 0;
    } else {
      const err = fw.sp - fw.rpm;
      fw.integ += err * dt;
      // clamp integ
      if (fw.integ > 60) fw.integ = 60;
      if (fw.integ < -60) fw.integ = -60;
      const deriv = (err - fw.prevErr) / dt;
      fw.prevErr = err;
      out = fw.kp * err + fw.ki * fw.integ + fw.kd * deriv;
      if (out > 100) out = 100;
      if (out < -100) out = -100;
    }
    fw.outRawPct = out;
    fw.outPct = out;
    // motor: first-order, max rpm ≈ rpmMax at 100% pwm
    const targetRpm = (out / 100) * fw.cfg.rpmMax;
    const tau = 0.18; // motor time constant
    fw.rpm += (targetRpm - fw.rpm) * (dt / tau);
    // small noise
    fw.rpm += (Math.random() - 0.5) * 0.6;

    // ---- Position loop ----
    if (fw.sineEnabled) {
      const t = (now - fw.sineT0) / 1000;
      const angle = fw.sineOffset + fw.sineAmp * Math.sin((2 * Math.PI * t) / fw.sinePeriod);
      fw.posTarget = angle;
    }
    if (fw.posEnabled) {
      let pErr = fw.posTarget - fw.pos;
      // wrap to [-180,180]
      while (pErr > 180) pErr -= 360;
      while (pErr < -180) pErr += 360;
      if (Math.abs(pErr) < fw.posIwin) {
        fw.posIntegral += pErr * dt;
        if (fw.posIntegral > 50) fw.posIntegral = 50;
        if (fw.posIntegral < -50) fw.posIntegral = -50;
      } else {
        fw.posIntegral = 0;
      }
      const pDeriv = (pErr - fw.posPrevErr) / dt;
      fw.posPrevErr = pErr;
      let pOut = fw.posKp * pErr + fw.posKi * fw.posIntegral + fw.posKd * pDeriv;
      if (pOut > 100) pOut = 100;
      if (pOut < -100) pOut = -100;
      fw.posOutPwm = pOut;
      fw.posOutRaw = pOut;
      // move position toward where the output would drive it
      const angularVelMax = 180; // deg/s at 100% pwm
      fw.pos += (pOut / 100) * angularVelMax * dt;
    } else {
      fw.posOutPwm = 0;
      fw.posOutRaw = 0;
    }
  }, 30);

  // ---------- frame protocol ----------
  const SYNC = [0xAA, 0x55, 0xAA, 0x55];
  const MAX_LEN = 200;
  const enc = new TextEncoder();
  const dec = new TextDecoder();

  function buildFrame(text) {
    const payload = enc.encode(String(text));
    const len = Math.min(payload.length, MAX_LEN);
    const frame = new Uint8Array(SYNC.length + 1 + len + 1 + 3);
    let i = 0;
    for (const b of SYNC) frame[i++] = b;
    frame[i++] = len;
    frame.set(payload.subarray(0, len), i); i += len;
    frame[i++] = 0x01; // CMD
    frame[i++] = 0; frame[i++] = 0; frame[i++] = 0; // seq
    return frame;
  }

  // ---------- response router ----------
  function handleCommand(text, queue) {
    const cmd = String(text || "").trim();
    const U = cmd.toUpperCase();
    function out(line) { queue.push(line); }

    // GET PID RPM → P,kp,ki,kd,sp
    if (U === "GET PID RPM") {
      out(`P,${fw.kp.toFixed(2)},${fw.ki.toFixed(2)},${fw.kd.toFixed(2)},${fw.sp.toFixed(2)}`);
      out("OK");
      return;
    }
    // GET TELEM → T,sp,rpm,out,outRaw
    if (U === "GET TELEM") {
      out(`T,${fw.sp.toFixed(2)},${fw.rpm.toFixed(2)},${fw.outPct.toFixed(2)},${fw.outRawPct.toFixed(2)}`);
      return;
    }
    // GET TELEM POS → TP,target,pos,outPwm,outRaw,iTerm
    if (U === "GET TELEM POS") {
      out(`TP,${fw.posTarget.toFixed(3)},${fw.pos.toFixed(3)},${fw.posOutPwm.toFixed(2)},${fw.posOutRaw.toFixed(2)},${fw.posIntegral.toFixed(3)}`);
      return;
    }
    // GET PID POS → PP,kp,ki,kd,target,enabled,iwin
    if (U === "GET PID POS") {
      out(`PP,${fw.posKp.toFixed(4)},${fw.posKi.toFixed(4)},${fw.posKd.toFixed(4)},${fw.posTarget.toFixed(2)},${fw.posEnabled ? 1 : 0},${fw.posIwin.toFixed(2)}`);
      out("OK");
      return;
    }
    // GET PID POS SINE → PS,amp,offset,period,enabled
    if (U === "GET PID POS SINE") {
      out(`PS,${fw.sineAmp.toFixed(2)},${fw.sineOffset.toFixed(2)},${fw.sinePeriod.toFixed(2)},${fw.sineEnabled ? 1 : 0}`);
      out("OK");
      return;
    }
    // GET CURVE → CV,r0,...,r9
    if (U === "GET CURVE") {
      out("CV," + fw.curve.map((v) => Number(v).toFixed(2)).join(","));
      return;
    }
    // GET CFG → CFG,... + CFGN,...
    if (U === "GET CFG") {
      const c = fw.cfg;
      out(`CFG,${c.bridge},${c.encMode},${c.motorInv},${c.encInv},${c.pullup},${c.pwmFreq},${c.countsPerRev},${c.gearRatio},${c.rpmMax.toFixed(2)}`);
      out("CFGN," + c.notes);
      return;
    }

    // SET commands
    let m = U.match(/^SET PID RPM (KP|KI|KD|SP) (-?[0-9.]+)$/);
    if (m) {
      const v = parseFloat(m[2]);
      if (m[1] === "KP") fw.kp = v;
      else if (m[1] === "KI") fw.ki = v;
      else if (m[1] === "KD") fw.kd = v;
      else if (m[1] === "SP") { fw.sp = v; fw.integ = 0; }
      out("OK"); return;
    }
    m = U.match(/^SET PID POS (KP|KI|KD|IWIN|ANGLE) (-?[0-9.]+)$/);
    if (m) {
      const v = parseFloat(m[2]);
      if (m[1] === "KP") fw.posKp = v;
      else if (m[1] === "KI") fw.posKi = v;
      else if (m[1] === "KD") fw.posKd = v;
      else if (m[1] === "IWIN") fw.posIwin = v;
      else if (m[1] === "ANGLE") fw.posTarget = v;
      out("OK"); return;
    }
    m = U.match(/^SET PID POS SINE (AMP|OFFSET|PERIOD) (-?[0-9.]+)$/);
    if (m) {
      const v = parseFloat(m[2]);
      if (m[1] === "AMP") fw.sineAmp = v;
      else if (m[1] === "OFFSET") fw.sineOffset = v;
      else if (m[1] === "PERIOD") fw.sinePeriod = v;
      out("OK"); return;
    }
    if (U === "START PID POS") { fw.posEnabled = true; out("OK"); return; }
    if (U === "STOP PID POS")  { fw.posEnabled = false; fw.sineEnabled = false; out("OK"); return; }
    if (U === "START PID POS SINE") { fw.sineEnabled = true; fw.sineT0 = performance.now(); out("OK"); return; }
    if (U === "STOP PID POS SINE")  { fw.sineEnabled = false; out("OK"); return; }
    if (U === "SAVE PID RPM" || U === "SAVE PID POS") { setTimeout(() => out("S,OK"), 80); out("OK"); return; }

    m = U.match(/^SET OUT(?:\s+RAW)?\s+(-?[0-9.]+)$/);
    if (m) { fw.outOverride = parseFloat(m[1]); out("OK"); return; }
    if (U === "CLR OUT") { fw.outOverride = null; out("OK"); return; }

    m = U.match(/^SET CURVE\s+([0-9.]+)\s+(-?[0-9.]+)$/);
    if (m) {
      const pwm = parseFloat(m[1]);
      const rpm = parseFloat(m[2]);
      const idx = [10,20,30,40,50,60,70,80,90,100].indexOf(Math.round(pwm));
      if (idx >= 0) fw.curve[idx] = rpm;
      out("OK"); return;
    }
    m = U.match(/^SET CFG\s+(\S+)\s+(.*)$/);
    if (m) {
      const key = m[1]; const raw = m[2].trim();
      const num = parseFloat(raw);
      switch (key) {
        case "BRIDGE": fw.cfg.bridge = parseInt(raw,10)||0; break;
        case "ENCODER_MODE": fw.cfg.encMode = parseInt(raw,10)||0; break;
        case "MOTOR_INV": fw.cfg.motorInv = parseInt(raw,10)||0; break;
        case "ENCODER_INV": fw.cfg.encInv = parseInt(raw,10)||0; break;
        case "PULLUP": fw.cfg.pullup = parseInt(raw,10)||0; break;
        case "PWM_FREQ": fw.cfg.pwmFreq = parseInt(raw,10)||fw.cfg.pwmFreq; break;
        case "COUNTS_PER_REV": fw.cfg.countsPerRev = parseInt(raw,10)||fw.cfg.countsPerRev; break;
        case "GEAR_RATIO": fw.cfg.gearRatio = parseInt(raw,10)||fw.cfg.gearRatio; break;
        case "RPM_MAX": fw.cfg.rpmMax = Number.isFinite(num) ? num : fw.cfg.rpmMax; break;
        case "NOTES":
          // preserve original-case from cmd, not U
          fw.cfg.notes = cmd.slice("SET CFG NOTES ".length);
          break;
      }
      out("OK"); return;
    }
    if (U === "SAVE CURVE" || U === "SAVE CFG") {
      setTimeout(() => out("S,OK"), 80);
      out("OK"); return;
    }

    // unknown → just OK so the page status stays clean
    out("OK");
  }

  // ---------- mock port ----------
  class MockReader {
    constructor(port) { this.port = port; this._closed = false; }
    async read() {
      if (this._closed) return { value: undefined, done: true };
      // wait for data
      while (this.port._rxQueue.length === 0 && !this._closed) {
        await new Promise((r) => {
          this.port._rxWaiters.push(r);
          setTimeout(r, 60); // also poll for new generated frames
        });
        if (this._closed) return { value: undefined, done: true };
      }
      const value = this.port._rxQueue.shift();
      return { value, done: false };
    }
    async cancel() { this._closed = true; this.port._wakeRxWaiters(); }
    releaseLock() {}
  }

  class MockWriter {
    constructor(port) { this.port = port; }
    async write(data) {
      this.port._ingest(data);
    }
    releaseLock() {}
  }

  class MockPort {
    constructor() {
      this._open = false;
      this._rxQueue = [];
      this._rxWaiters = [];
      this._frameBuf = [];
    }
    async open(_opts) { this._open = true; }
    async close() { this._open = false; this._wakeRxWaiters(); }
    get readable() {
      const self = this;
      return { getReader() { return new MockReader(self); } };
    }
    get writable() {
      const self = this;
      return { getWriter() { return new MockWriter(self); } };
    }
    _wakeRxWaiters() {
      const ws = this._rxWaiters; this._rxWaiters = [];
      for (const w of ws) try { w(); } catch (_) {}
    }
    _emit(text) {
      this._rxQueue.push(buildFrame(text));
      this._wakeRxWaiters();
    }
    _ingest(data) {
      for (const b of data) this._frameBuf.push(b & 0xff);
      while (this._frameBuf.length >= SYNC.length + 1) {
        // find sync
        let s = -1;
        for (let i = 0; i + 4 <= this._frameBuf.length; i++) {
          if (this._frameBuf[i] === SYNC[0] && this._frameBuf[i+1] === SYNC[1]
              && this._frameBuf[i+2] === SYNC[2] && this._frameBuf[i+3] === SYNC[3]) {
            s = i; break;
          }
        }
        if (s < 0) {
          if (this._frameBuf.length > 8) this._frameBuf = this._frameBuf.slice(-3);
          return;
        }
        if (s > 0) this._frameBuf.splice(0, s);
        if (this._frameBuf.length < SYNC.length + 1) return;
        const len = this._frameBuf[SYNC.length];
        const frameLen = SYNC.length + 1 + len + 1 + 3;
        if (this._frameBuf.length < frameLen) return;
        const payload = this._frameBuf.slice(SYNC.length + 1, SYNC.length + 1 + len);
        this._frameBuf.splice(0, frameLen);
        const text = dec.decode(Uint8Array.from(payload)).trim();
        if (!text) continue;
        const lines = [];
        handleCommand(text, lines);
        for (const l of lines) this._emit(l);
      }
    }
  }

  const ports = [new MockPort()];
  const listeners = { connect: [], disconnect: [] };
  const mockSerial = {
    async requestPort() { return ports[0]; },
    async getPorts()    { return ports; },
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener(t, fn) { const a = listeners[t] || []; const i = a.indexOf(fn); if (i>=0) a.splice(i,1); },
  };

  // override (or define) navigator.serial
  try {
    Object.defineProperty(navigator, "serial", { value: mockSerial, configurable: true, writable: true });
  } catch (_e) {
    try { navigator.serial = mockSerial; } catch (_ee) {}
  }
})();
