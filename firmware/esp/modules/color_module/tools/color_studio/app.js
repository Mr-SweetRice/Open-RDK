(function () {
  const FRAME_SYNC_BYTES = [0xaa, 0x55, 0xaa, 0x55];
  const FRAME_MAX_LEN = 200;
  const FRAME_RX_MAX_BYTES = 4096;
  const TELEMETRY_SYNC_INTERVAL_MS = 1000;
  const RX_TIMEOUT_MS = 5000;
  const CAPTURE_SETTLE_MS = 500;
  const CMD_SPACING_MS = 70;
  const MAX_LOG_LINES = 320;
  const HEALTH_FLAGS = [
    { bit: 0, label: "sensor_ok", good: true },
    { bit: 1, label: "saturated", good: false },
    { bit: 2, label: "dark_valid", good: true },
    { bit: 3, label: "white_valid", good: true },
    { bit: 4, label: "cal_active", good: true },
    { bit: 5, label: "selftest_ok", good: true },
    { bit: 6, label: "auto_exposure", good: true },
    { bit: 7, label: "sensor_present", good: true },
  ];
  const PALETTES = {
    4: [
      { slot: 0, name: "red", rgb: "#e11d48" },
      { slot: 1, name: "green", rgb: "#16a34a" },
      { slot: 2, name: "blue", rgb: "#2563eb" },
      { slot: 3, name: "yellow", rgb: "#facc15" },
    ],
    8: [
      { slot: 0, name: "black", rgb: "#050505" },
      { slot: 1, name: "white", rgb: "#ffffff" },
      { slot: 2, name: "red", rgb: "#e11d48" },
      { slot: 3, name: "yellow", rgb: "#facc15" },
      { slot: 4, name: "green", rgb: "#16a34a" },
      { slot: 5, name: "cyan", rgb: "#06b6d4" },
      { slot: 6, name: "blue", rgb: "#2563eb" },
      { slot: 7, name: "magenta", rgb: "#db2777" },
    ],
    16: [
      { slot: 0, name: "black", rgb: "#050505" },
      { slot: 1, name: "white", rgb: "#ffffff" },
      { slot: 2, name: "gray", rgb: "#9ca3af" },
      { slot: 3, name: "red", rgb: "#e11d48" },
      { slot: 4, name: "orange", rgb: "#f97316" },
      { slot: 5, name: "yellow", rgb: "#facc15" },
      { slot: 6, name: "lime", rgb: "#84cc16" },
      { slot: 7, name: "green", rgb: "#16a34a" },
      { slot: 8, name: "cyan", rgb: "#06b6d4" },
      { slot: 9, name: "sky", rgb: "#38bdf8" },
      { slot: 10, name: "blue", rgb: "#2563eb" },
      { slot: 11, name: "violet", rgb: "#8b5cf6" },
      { slot: 12, name: "magenta", rgb: "#db2777" },
      { slot: 13, name: "pink", rgb: "#ec4899" },
      { slot: 14, name: "brown", rgb: "#92400e" },
      { slot: 15, name: "olive", rgb: "#6b8e23" },
    ],
  };

  const MSG_TYPE = {
    CMD: 0x01,
    TEST: 0x02,
    TELEMETRY: 0x03,
  };

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  const el = {
    connectBtn: document.getElementById("connectBtn"),
    refreshBtn: document.getElementById("refreshBtn"),
    selftestBtn: document.getElementById("selftestBtn"),
    saveCfgBtn: document.getElementById("saveCfgBtn"),
    saveCalBtn: document.getElementById("saveCalBtn"),
    stopCalBtn: document.getElementById("stopCalBtn"),
    stateText: document.getElementById("stateText"),
    cmdStatus: document.getElementById("cmdStatus"),
    detectedColor: document.getElementById("detectedColor"),
    confidenceValue: document.getElementById("confidenceValue"),
    paletteValue: document.getElementById("paletteValue"),
    ledValue: document.getElementById("ledValue"),
    colorSwatch: document.getElementById("colorSwatch"),
    top1Name: document.getElementById("top1Name"),
    top2Name: document.getElementById("top2Name"),
    top3Name: document.getElementById("top3Name"),
    sampleTs: document.getElementById("sampleTs"),
    rawValue: document.getElementById("rawValue"),
    normValue: document.getElementById("normValue"),
    labValue: document.getElementById("labValue"),
    lumaValue: document.getElementById("lumaValue"),
    exposureValue: document.getElementById("exposureValue"),
    captureValue: document.getElementById("captureValue"),
    infoName: document.getElementById("infoName"),
    infoModuleType: document.getElementById("infoModuleType"),
    infoFirmware: document.getElementById("infoFirmware"),
    infoModuleId: document.getElementById("infoModuleId"),
    infoSensorId: document.getElementById("infoSensorId"),
    infoI2c: document.getElementById("infoI2c"),
    healthFlags: document.getElementById("healthFlags"),
    calPaletteMode: document.getElementById("calPaletteMode"),
    applyPaletteBtn: document.getElementById("applyPaletteBtn"),
    refreshCalBtn: document.getElementById("refreshCalBtn"),
    resetCalBtn: document.getElementById("resetCalBtn"),
    paletteGrid: document.getElementById("paletteGrid"),
    sensorNameInput: document.getElementById("sensorNameInput"),
    sampleMsInput: document.getElementById("sampleMsInput"),
    targetClearInput: document.getElementById("targetClearInput"),
    paletteModeInput: document.getElementById("paletteModeInput"),
    ledModeInput: document.getElementById("ledModeInput"),
    gainModeInput: document.getElementById("gainModeInput"),
    gainInput: document.getElementById("gainInput"),
    integrationInput: document.getElementById("integrationInput"),
    classifierInput: document.getElementById("classifierInput"),
    confidenceInput: document.getElementById("confidenceInput"),
    patchSamplesInput: document.getElementById("patchSamplesInput"),
    applyCfgBtn: document.getElementById("applyCfgBtn"),
    resetCfgBtn: document.getElementById("resetCfgBtn"),
    exportBtn: document.getElementById("exportBtn"),
    importFile: document.getElementById("importFile"),
    log: document.getElementById("log"),
    manualInput: document.getElementById("manualInput"),
    manualSendBtn: document.getElementById("manualSendBtn"),
    patchOverlay: document.getElementById("patchOverlay"),
    overlayPatch: document.getElementById("overlayPatch"),
    overlayTitle: document.getElementById("overlayTitle"),
    overlayText: document.getElementById("overlayText"),
    overlayCaptureBtn: document.getElementById("overlayCaptureBtn"),
    overlayCloseBtn: document.getElementById("overlayCloseBtn"),
  };

  const state = {
    port: null,
    reader: null,
    writer: null,
    frameSeq: 0,
    frameRxBytes: [],
    telemTimer: null,
    closing: false,
    lastRxAt: 0,
    info: null,
    cfg: null,
    data: null,
    selftest: null,
    cal: {
      4: { summary: null, patches: {} },
      8: { summary: null, patches: {} },
      16: { summary: null, patches: {} },
    },
    overlayTarget: null,
    logLines: [],
  };

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function appendLog(text) {
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    const ms = String(d.getMilliseconds()).padStart(3, "0");
    state.logLines.push(`[${hh}:${mm}:${ss}.${ms}] ${text}`);
    if (state.logLines.length > MAX_LOG_LINES) {
      state.logLines.splice(0, state.logLines.length - MAX_LOG_LINES);
    }
    el.log.textContent = state.logLines.join("\n");
    el.log.scrollTop = el.log.scrollHeight;
  }

  function setCmdStatus(text, ok = true) {
    el.cmdStatus.textContent = text;
    el.cmdStatus.classList.toggle("ok", !!ok);
    el.cmdStatus.classList.toggle("err", !ok);
  }

  function setConnectionState(connected) {
    el.stateText.textContent = connected ? "Connected" : "Disconnected";
    el.stateText.classList.toggle("ok", connected);
    el.stateText.classList.toggle("err", !connected);
    el.connectBtn.textContent = connected ? "Disconnect" : "Connect";
  }

  function nextFrameSeq() {
    state.frameSeq = (state.frameSeq + 1) & 0x00ffffff;
    return state.frameSeq;
  }

  function appendFrameRxChunk(value) {
    if (!value || !value.length) {
      return;
    }
    for (const byte of value) {
      state.frameRxBytes.push(byte & 0xff);
    }
    if (state.frameRxBytes.length > FRAME_RX_MAX_BYTES) {
      state.frameRxBytes.splice(0, state.frameRxBytes.length - FRAME_RX_MAX_BYTES);
    }
  }

  function trimFrameRxToSyncPrefix() {
    if (!state.frameRxBytes.length) {
      return;
    }
    let keep = 0;
    const maxPrefix = Math.min(state.frameRxBytes.length, FRAME_SYNC_BYTES.length - 1);
    for (let prefixLen = maxPrefix; prefixLen > 0; prefixLen--) {
      let matches = true;
      for (let i = 0; i < prefixLen; i++) {
        if (state.frameRxBytes[state.frameRxBytes.length - prefixLen + i] !== FRAME_SYNC_BYTES[i]) {
          matches = false;
          break;
        }
      }
      if (matches) {
        keep = prefixLen;
        break;
      }
    }
    if (keep > 0) {
      state.frameRxBytes.splice(0, state.frameRxBytes.length - keep);
    } else {
      state.frameRxBytes = [];
    }
  }

  function findFrameSyncStart() {
    const maxStart = state.frameRxBytes.length - FRAME_SYNC_BYTES.length;
    for (let i = 0; i <= maxStart; i++) {
      let ok = true;
      for (let j = 0; j < FRAME_SYNC_BYTES.length; j++) {
        if (state.frameRxBytes[i + j] !== FRAME_SYNC_BYTES[j]) {
          ok = false;
          break;
        }
      }
      if (ok) {
        return i;
      }
    }
    return -1;
  }

  function consumeFramedMessages(onMessageText) {
    while (true) {
      const syncStart = findFrameSyncStart();
      if (syncStart < 0) {
        trimFrameRxToSyncPrefix();
        return;
      }
      if (syncStart > 0) {
        state.frameRxBytes.splice(0, syncStart);
      }

      const base = FRAME_SYNC_BYTES.length;
      if (state.frameRxBytes.length < base + 1) {
        return;
      }

      const msgLen = state.frameRxBytes[base];
      if (msgLen <= 0 || msgLen > FRAME_MAX_LEN) {
        state.frameRxBytes.splice(0, 1);
        continue;
      }

      const frameLen = base + 1 + msgLen + 1 + 3;
      if (state.frameRxBytes.length < frameLen) {
        return;
      }

      const frame = state.frameRxBytes.splice(0, frameLen);
      const payload = frame.slice(base + 1, base + 1 + msgLen);
      const text = decoder.decode(Uint8Array.from(payload)).trim();
      if (text) {
        onMessageText(text);
      }
    }
  }

  function sendLine(text, messageType = "CMD", logTx = true) {
    if (!state.writer) {
      return;
    }

    const payloadText = String(text || "").trim();
    if (!payloadText) {
      return;
    }

    const rawPayload = encoder.encode(payloadText);
    const payload = rawPayload.length > FRAME_MAX_LEN ? rawPayload.slice(0, FRAME_MAX_LEN) : rawPayload;
    const typeCode = MSG_TYPE[messageType] || MSG_TYPE.CMD;
    const seq = nextFrameSeq();
    const frameLen = FRAME_SYNC_BYTES.length + 1 + payload.length + 1 + 3;
    const frame = new Uint8Array(frameLen);
    let i = 0;

    for (const byte of FRAME_SYNC_BYTES) {
      frame[i++] = byte;
    }
    frame[i++] = payload.length & 0xff;
    frame.set(payload, i);
    i += payload.length;
    frame[i++] = typeCode & 0xff;
    frame[i++] = (seq >> 16) & 0xff;
    frame[i++] = (seq >> 8) & 0xff;
    frame[i++] = seq & 0xff;
    state.writer.write(frame);

    if (logTx) {
      appendLog(`>> [${messageType}] ${payloadText}`);
    }
  }

  async function sendConfigCommand(line) {
    sendLine(line, "CMD");
    await sleep(CMD_SPACING_MS);
  }

  function getPalette(mode) {
    return PALETTES[Number(mode)] || PALETTES[8];
  }

  function paletteName(mode, slot) {
    if (slot === -2) return "dark";
    if (slot === -1) return "white";
    const patch = getPalette(mode).find((item) => item.slot === Number(slot));
    return patch ? patch.name : "unknown";
  }

  function paletteRgb(mode, slot) {
    if (slot === -2) return "#050505";
    if (slot === -1) return "#ffffff";
    const patch = getPalette(mode).find((item) => item.slot === Number(slot));
    return patch ? patch.rgb : "#1f2937";
  }

  function renderHealthFlags(flags) {
    el.healthFlags.innerHTML = "";
    HEALTH_FLAGS.forEach((entry) => {
      const active = (flags & (1 << entry.bit)) !== 0;
      const span = document.createElement("span");
      span.className = `pill ${active ? (entry.good ? "ok" : "err") : ""}`.trim();
      span.textContent = active ? entry.label : `${entry.label}:0`;
      el.healthFlags.appendChild(span);
    });
  }

  function renderInfo() {
    const info = state.info || {};
    el.infoName.textContent = info.name || "-";
    el.infoModuleType.textContent = info.moduleType || "-";
    el.infoFirmware.textContent = info.firmwareModule || "-";
    el.infoModuleId.textContent = info.moduleId != null ? String(info.moduleId) : "-";
    el.infoSensorId.textContent = info.sensorId != null ? String(info.sensorId) : "-";
    if (info.i2cAddress != null) {
      el.infoI2c.textContent = `addr 0x${Number(info.i2cAddress).toString(16)}, SDA ${info.sdaPin}, SCL ${info.sclPin}, LED ${info.ledPin}`;
    } else {
      el.infoI2c.textContent = "-";
    }
  }

  function renderConfig() {
    const cfg = state.cfg;
    if (!cfg) {
      return;
    }
    el.sensorNameInput.value = cfg.sensorName;
    el.sampleMsInput.value = cfg.samplePeriodMs;
    el.targetClearInput.value = cfg.targetClear;
    el.paletteModeInput.value = String(cfg.paletteMode);
    el.calPaletteMode.value = String(cfg.paletteMode);
    el.ledModeInput.value = String(cfg.ledMode);
    el.gainModeInput.value = String(cfg.gainMode);
    el.gainInput.value = cfg.gain;
    el.integrationInput.value = cfg.integrationMs;
    el.classifierInput.value = String(cfg.classifier);
    el.confidenceInput.value = cfg.confidenceThreshold.toFixed(2);
    el.patchSamplesInput.value = cfg.patchSampleCount;
    el.paletteValue.textContent = `${cfg.paletteMode} colors`;
  }

  function toCssRgb(norm) {
    const r = Math.max(0, Math.min(255, Math.round(norm[0] * 255)));
    const g = Math.max(0, Math.min(255, Math.round(norm[1] * 255)));
    const b = Math.max(0, Math.min(255, Math.round(norm[2] * 255)));
    return `rgb(${r}, ${g}, ${b})`;
  }

  function renderData() {
    const data = state.data;
    if (!data) {
      return;
    }

    const detectedName = data.detectedSlot >= 0 ? paletteName(data.paletteMode, data.detectedSlot) : "unclassified";
    const swatchColor = data.detectedSlot >= 0 ? paletteRgb(data.paletteMode, data.detectedSlot) : toCssRgb(data.normRgb);
    el.detectedColor.textContent = detectedName;
    el.confidenceValue.textContent = data.confidence.toFixed(3);
    el.ledValue.textContent = data.ledActive ? "ON" : "OFF";
    el.colorSwatch.style.background = swatchColor;
    el.sampleTs.textContent = `${data.sampleTimestampMs} ms`;
    el.rawValue.textContent = `${data.raw.r}, ${data.raw.g}, ${data.raw.b}, ${data.raw.c}`;
    el.normValue.textContent = `${data.normRgb[0].toFixed(3)}, ${data.normRgb[1].toFixed(3)}, ${data.normRgb[2].toFixed(3)}`;
    el.labValue.textContent = `${data.lab[0].toFixed(2)}, ${data.lab[1].toFixed(2)}, ${data.lab[2].toFixed(2)}`;
    el.lumaValue.textContent = data.luma.toFixed(3);
    el.exposureValue.textContent = `gain ${data.gain} / ${data.integrationMs} ms / clear ${data.raw.c}`;

    const captureLabel = data.calibrating
      ? `${paletteName(data.paletteMode, data.calibrationTargetSlot)} (${data.calibrationSamples} samples)`
      : "idle";
    el.captureValue.textContent = captureLabel;

    const top = data.top.map((item) => {
      if (!item || item.slot < 0) {
        return "-";
      }
      return `${paletteName(data.paletteMode, item.slot)} (${item.confidence.toFixed(3)})`;
    });
    el.top1Name.textContent = top[0] || "-";
    el.top2Name.textContent = top[1] || "-";
    el.top3Name.textContent = top[2] || "-";
    renderHealthFlags(data.healthFlags);
  }

  function renderPaletteGrid() {
    const mode = Number(el.calPaletteMode.value || 8);
    const calState = state.cal[mode] || { summary: null, patches: {} };
    const summary = calState.summary;
    const cards = [
      { slot: -2, name: "dark", rgb: "#050505" },
      { slot: -1, name: "white", rgb: "#ffffff" },
      ...getPalette(mode),
    ];

    el.paletteGrid.innerHTML = "";
    cards.forEach((patch) => {
      const card = document.createElement("div");
      const patchState = patch.slot >= 0 ? (calState.patches[patch.slot] || null) : null;
      const valid = patch.slot === -2
        ? !!(summary && summary.darkValid)
        : patch.slot === -1
          ? !!(summary && summary.whiteValid)
          : !!(patchState && patchState.valid);
      const sampleCount = patch.slot >= 0 && patchState ? patchState.sampleCount : "-";
      const metaExtra = patch.slot === -2 && summary
        ? `raw ${summary.dark.r}, ${summary.dark.g}, ${summary.dark.b}, ${summary.dark.c}`
        : patch.slot === -1 && summary
          ? `raw ${summary.white.r}, ${summary.white.g}, ${summary.white.b}, ${summary.white.c}`
          : patchState
            ? `norm ${patchState.normRgb.map((value) => value.toFixed(3)).join(", ")}`
            : "no data";

      card.className = "patch-card";
      card.innerHTML = `
        <div class="patch-title">
          <span>${patch.name}</span>
          <span class="pill ${valid ? "ok" : ""}">${valid ? "valid" : "empty"}</span>
        </div>
        <div class="patch-color" style="background:${patch.rgb};"></div>
        <div class="patch-meta">
          <div>Samples: ${sampleCount}</div>
          <div>${metaExtra}</div>
        </div>
        <div class="patch-actions">
          <button data-action="show" data-slot="${patch.slot}">Show Patch</button>
          <button data-action="capture" data-slot="${patch.slot}" class="primary">Capture</button>
        </div>
      `;
      el.paletteGrid.appendChild(card);
    });
  }

  function parseDataParts(parts) {
    if (parts.length < 30) {
      return null;
    }
    return {
      paletteMode: Number(parts[1]) || 8,
      detectedSlot: Number(parts[2]),
      confidence: (Number(parts[3]) || 0) / 1000,
      top: [
        { slot: Number(parts[4]), confidence: (Number(parts[5]) || 0) / 1000 },
        { slot: Number(parts[6]), confidence: (Number(parts[7]) || 0) / 1000 },
        { slot: Number(parts[8]), confidence: (Number(parts[9]) || 0) / 1000 },
      ],
      raw: {
        r: Number(parts[10]) || 0,
        g: Number(parts[11]) || 0,
        b: Number(parts[12]) || 0,
        c: Number(parts[13]) || 0,
      },
      normRgb: [
        (Number(parts[14]) || 0) / 1000,
        (Number(parts[15]) || 0) / 1000,
        (Number(parts[16]) || 0) / 1000,
      ],
      lab: [
        (Number(parts[17]) || 0) / 100,
        (Number(parts[18]) || 0) / 100,
        (Number(parts[19]) || 0) / 100,
      ],
      luma: (Number(parts[20]) || 0) / 1000,
      gain: Number(parts[21]) || 0,
      integrationMs: Number(parts[22]) || 0,
      ledMode: Number(parts[23]) || 0,
      ledActive: Number(parts[24]) === 1,
      healthFlags: Number(parts[25]) || 0,
      classifier: Number(parts[26]) || 0,
      calibrationTargetSlot: Number(parts[27]),
      calibrationSamples: Number(parts[28]) || 0,
      sampleTimestampMs: Number(parts[29]) || 0,
      calibrating: (Number(parts[25]) & (1 << 4)) !== 0,
    };
  }

  function parseCfgParts(parts) {
    if (parts.length < 12) {
      return null;
    }
    return {
      sensorName: parts[1] || "color-sensor-esp",
      samplePeriodMs: Number(parts[2]) || 80,
      ledMode: Number(parts[3]) || 0,
      gainMode: Number(parts[4]) || 0,
      gain: Number(parts[5]) || 4,
      integrationMs: Number(parts[6]) || 50,
      classifier: Number(parts[7]) || 1,
      confidenceThreshold: (Number(parts[8]) || 0) / 1000,
      targetClear: Number(parts[9]) || 18000,
      paletteMode: Number(parts[10]) || 8,
      patchSampleCount: Number(parts[11]) || 12,
    };
  }

  function parseCalParts(parts) {
    if (parts.length < 15) {
      return null;
    }
    return {
      paletteMode: Number(parts[1]) || 8,
      classCount: Number(parts[2]) || 0,
      validMask: Number(parts[3]) || 0,
      enabledMask: Number(parts[4]) || 0,
      darkValid: Number(parts[5]) === 1,
      whiteValid: Number(parts[6]) === 1,
      dark: {
        r: Number(parts[7]) || 0,
        g: Number(parts[8]) || 0,
        b: Number(parts[9]) || 0,
        c: Number(parts[10]) || 0,
      },
      white: {
        r: Number(parts[11]) || 0,
        g: Number(parts[12]) || 0,
        b: Number(parts[13]) || 0,
        c: Number(parts[14]) || 0,
      },
    };
  }

  function parsePatchParts(parts) {
    if (parts.length < 14) {
      return null;
    }
    return {
      paletteMode: Number(parts[1]) || 8,
      slot: Number(parts[2]),
      enabled: Number(parts[3]) === 1,
      valid: Number(parts[4]) === 1,
      sampleCount: Number(parts[5]) || 0,
      name: parts[6] || "",
      normRgb: [
        (Number(parts[7]) || 0) / 1000,
        (Number(parts[8]) || 0) / 1000,
        (Number(parts[9]) || 0) / 1000,
      ],
      lab: [
        (Number(parts[10]) || 0) / 100,
        (Number(parts[11]) || 0) / 100,
        (Number(parts[12]) || 0) / 100,
      ],
      luma: (Number(parts[13]) || 0) / 1000,
    };
  }

  function parseInfoParts(parts) {
    if (parts.length < 11) {
      return null;
    }
    return {
      name: parts[1] || "-",
      moduleType: parts[2] || "-",
      firmwareModule: parts[3] || "-",
      moduleId: Number(parts[4]) || 0,
      sensorId: Number(parts[5]) || 0,
      healthFlags: Number(parts[6]) || 0,
      i2cAddress: Number(parts[7]) || 0,
      sdaPin: Number(parts[8]) || 0,
      sclPin: Number(parts[9]) || 0,
      ledPin: Number(parts[10]) || 0,
    };
  }

  function parseSelftestParts(parts) {
    if (parts.length < 4) {
      return null;
    }
    return {
      ok: Number(parts[1]) === 1,
      sensorId: Number(parts[2]) || 0,
      message: parts.slice(3).join(","),
    };
  }

  function handleLine(line) {
    appendLog(`<< ${line}`);

    if (line === "OK") {
      setCmdStatus("OK", true);
      return;
    }
    if (line === "ERR") {
      setCmdStatus("ERR", false);
      return;
    }
    if (line === "TELEMETRY STARTED") {
      setCmdStatus("Telemetry started", true);
      return;
    }
    if (line === "TELEMETRY STOPPED") {
      setCmdStatus("Telemetry stopped", true);
      return;
    }
    if (line === "TELEMETRY SYNCED") {
      return;
    }

    const parts = line.split(",");
    if (parts[0] === "DATA" || parts[0] === "TEL") {
      state.data = parseDataParts(parts);
      renderData();
      return;
    }
    if (parts[0] === "CFG") {
      state.cfg = parseCfgParts(parts);
      renderConfig();
      renderPaletteGrid();
      return;
    }
    if (parts[0] === "CAL") {
      const cal = parseCalParts(parts);
      if (cal) {
        state.cal[cal.paletteMode].summary = cal;
        renderPaletteGrid();
      }
      return;
    }
    if (parts[0] === "PATCH") {
      const patch = parsePatchParts(parts);
      if (patch) {
        state.cal[patch.paletteMode].patches[patch.slot] = patch;
        renderPaletteGrid();
      }
      return;
    }
    if (parts[0] === "INFO") {
      state.info = parseInfoParts(parts);
      renderInfo();
      if (state.info) {
        renderHealthFlags(state.info.healthFlags);
      }
      return;
    }
    if (parts[0] === "SELFTEST") {
      state.selftest = parseSelftestParts(parts);
      if (state.selftest) {
        setCmdStatus(`Selftest: ${state.selftest.message}`, state.selftest.ok);
      }
    }
  }

  async function readLoop() {
    try {
      while (state.reader) {
        const { value, done } = await state.reader.read();
        if (done) {
          break;
        }
        if (value && value.length) {
          state.lastRxAt = Date.now();
        }
        appendFrameRxChunk(value);
        consumeFramedMessages(handleLine);
      }
    } catch (err) {
      if (!state.closing) {
        appendLog(`RX ERROR: ${err && err.message ? err.message : err}`);
      }
    } finally {
      if (!state.closing && (state.port || state.reader || state.writer)) {
        await closeSerial(false);
      }
    }
  }

  function startTelemetry() {
    if (!state.writer || state.telemTimer) {
      return;
    }
    sendLine(`TELEMETRY_START:${Date.now()}`, "TELEMETRY");
    state.telemTimer = setInterval(() => {
      if (state.writer) {
        sendLine(`TELEMETRY_SYNC:${Date.now()}`, "TELEMETRY", false);
      }
    }, TELEMETRY_SYNC_INTERVAL_MS);
  }

  function stopTelemetry(sendStop = true) {
    if (state.telemTimer) {
      clearInterval(state.telemTimer);
      state.telemTimer = null;
    }
    if (sendStop && state.writer) {
      sendLine("TELEMETRY_STOP", "TELEMETRY");
    }
  }

  async function closeSerial(logDisconnect = true) {
    if (state.closing) {
      return;
    }
    state.closing = true;
    try {
      stopTelemetry(true);
      state.frameRxBytes = [];
      if (state.reader) {
        try { await state.reader.cancel(); } catch (_err) {}
        try { state.reader.releaseLock(); } catch (_err) {}
        state.reader = null;
      }
      if (state.writer) {
        try { state.writer.releaseLock(); } catch (_err) {}
        state.writer = null;
      }
      if (state.port) {
        const openPort = state.port;
        state.port = null;
        try { await openPort.close(); } catch (_err) {}
      }
      setConnectionState(false);
      if (logDisconnect) {
        appendLog("PORT: disconnected");
      }
    } finally {
      state.closing = false;
    }
  }

  async function connect() {
    if (!("serial" in navigator)) {
      setCmdStatus("Web Serial unavailable", false);
      return;
    }
    try {
      state.port = await navigator.serial.requestPort();
      await state.port.open({ baudRate: 115200 });
      state.reader = state.port.readable.getReader();
      state.writer = state.port.writable.getWriter();
      state.lastRxAt = Date.now();
      setConnectionState(true);
      appendLog("PORT: connected");
      void readLoop();
      startTelemetry();
      await refreshAll();
    } catch (err) {
      appendLog(`CONNECT ERROR: ${err && err.message ? err.message : err}`);
      await closeSerial(false);
    }
  }

  async function refreshCalibration(mode) {
    const palette = getPalette(mode);
    state.cal[mode].patches = {};
    sendLine(`GET CAL ${mode}`, "CMD");
    await sleep(CMD_SPACING_MS);
    for (const patch of palette) {
      sendLine(`GET CAL PATCH ${mode} ${patch.slot}`, "CMD");
      await sleep(CMD_SPACING_MS);
    }
  }

  async function refreshAll() {
    sendLine("GET INFO", "CMD");
    await sleep(CMD_SPACING_MS);
    sendLine("GET CFG", "CMD");
    await sleep(CMD_SPACING_MS);
    sendLine("GET DATA", "CMD");
    await sleep(CMD_SPACING_MS);
    await refreshCalibration(Number(el.calPaletteMode.value || 8));
  }

  async function applyConfig() {
    const paletteMode = Number(el.paletteModeInput.value || 8);
    await sendConfigCommand(`SET CFG NAME ${el.sensorNameInput.value.trim() || "color-sensor-esp"}`);
    await sendConfigCommand(`SET CFG SAMPLE_MS ${Math.max(20, Number(el.sampleMsInput.value) || 80)}`);
    await sendConfigCommand(`SET CFG TARGET_CLEAR ${Math.max(1000, Number(el.targetClearInput.value) || 18000)}`);
    await sendConfigCommand(`SET CFG PALETTE_MODE ${paletteMode}`);
    await sendConfigCommand(`SET CFG LED ${Number(el.ledModeInput.value) === 0 ? "OFF" : "ON"}`);
    await sendConfigCommand(`SET CFG GAIN_MODE ${Number(el.gainModeInput.value) === 0 ? "MANUAL" : "AUTO"}`);
    await sendConfigCommand(`SET CFG GAIN ${Math.max(1, Number(el.gainInput.value) || 4)}`);
    await sendConfigCommand(`SET CFG INTEGRATION_MS ${Math.max(3, Number(el.integrationInput.value) || 50)}`);
    await sendConfigCommand(`SET CFG CLASSIFIER ${Number(el.classifierInput.value) === 0 ? "NORM_RGB" : "LAB"}`);
    await sendConfigCommand(`SET CFG CONF_TH ${Math.max(0.05, Number(el.confidenceInput.value) || 0.3).toFixed(2)}`);
    await sendConfigCommand(`SET CFG PATCH_SAMPLES ${Math.max(3, Number(el.patchSamplesInput.value) || 12)}`);
    sendLine("GET CFG", "CMD");
    await sleep(CMD_SPACING_MS);
    el.calPaletteMode.value = String(paletteMode);
    await refreshCalibration(paletteMode);
    setCmdStatus("Config applied", true);
  }

  async function runCapture(slot) {
    const mode = Number(el.calPaletteMode.value || 8);
    const samplePeriodMs = state.cfg ? state.cfg.samplePeriodMs : Number(el.sampleMsInput.value || 80);
    const patchSamples = state.cfg ? state.cfg.patchSampleCount : Number(el.patchSamplesInput.value || 12);
    const targetName = paletteName(mode, slot);
    const waitMs = Math.max(800, (samplePeriodMs * patchSamples) + CAPTURE_SETTLE_MS);

    setCmdStatus(`Capturing ${targetName}`, true);
    sendLine("START CAL", "CMD");
    await sleep(CMD_SPACING_MS);
    sendLine(`SET CAL PATCH ${targetName}`, "CMD");
    await sleep(waitMs);
    sendLine(`COMMIT CAL PATCH ${targetName}`, "CMD");
    await sleep(CMD_SPACING_MS);
    sendLine("STOP CAL", "CMD");
    await sleep(CMD_SPACING_MS);
    sendLine("GET DATA", "CMD");
    await sleep(CMD_SPACING_MS);
    await refreshCalibration(mode);
    setCmdStatus(`Captured ${targetName}`, true);
  }

  function showOverlay(slot) {
    const mode = Number(el.calPaletteMode.value || 8);
    state.overlayTarget = { mode, slot };
    el.overlayTitle.textContent = paletteName(mode, slot);
    el.overlayPatch.style.background = paletteRgb(mode, slot);
    el.patchOverlay.classList.add("visible");
  }

  function closeOverlay() {
    state.overlayTarget = null;
    el.patchOverlay.classList.remove("visible");
  }

  function exportProfile() {
    const payload = {
      version: 1,
      exportedAt: new Date().toISOString(),
      info: state.info,
      cfg: state.cfg,
      cal: state.cal,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const name = state.info && state.info.name ? state.info.name : "color-module";
    a.href = url;
    a.download = `${name}-profile.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function importProfile(file) {
    if (!file) {
      return;
    }
    const text = await file.text();
    const payload = JSON.parse(text);
    const cal = payload && payload.cal ? payload.cal : null;
    if (!cal) {
      throw new Error("Missing calibration data");
    }

    for (const modeKey of ["4", "8", "16"]) {
      const mode = Number(modeKey);
      const summary = cal[modeKey] && cal[modeKey].summary;
      const patches = cal[modeKey] && cal[modeKey].patches;
      if (!summary) {
        continue;
      }

      if (summary.darkValid) {
        await sendConfigCommand(`SET CAL DARK ${mode} ${summary.dark.r} ${summary.dark.g} ${summary.dark.b} ${summary.dark.c}`);
      }
      if (summary.whiteValid) {
        await sendConfigCommand(`SET CAL WHITE ${mode} ${summary.white.r} ${summary.white.g} ${summary.white.b} ${summary.white.c}`);
      }
      if (patches) {
        for (const key of Object.keys(patches)) {
          const patch = patches[key];
          if (!patch || !patch.valid) {
            continue;
          }
          await sendConfigCommand(
            `SET CAL PROTO ${mode} ${patch.slot} ${Math.round(patch.normRgb[0] * 1000)} ${Math.round(patch.normRgb[1] * 1000)} ${Math.round(patch.normRgb[2] * 1000)} ${Math.round(patch.luma * 1000)} ${Math.round(patch.lab[0] * 100)} ${Math.round(patch.lab[1] * 100)} ${Math.round(patch.lab[2] * 100)} ${patch.sampleCount}`
          );
        }
      }
    }

    await refreshCalibration(Number(el.calPaletteMode.value || 8));
    setCmdStatus("Profile imported", true);
  }

  function handlePaletteGridClick(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const slot = Number(button.dataset.slot);
    if (button.dataset.action === "show") {
      showOverlay(slot);
      return;
    }
    if (button.dataset.action === "capture") {
      void runCapture(slot);
    }
  }

  el.connectBtn.addEventListener("click", () => {
    if (state.port) {
      void closeSerial(true);
    } else {
      void connect();
    }
  });

  el.refreshBtn.addEventListener("click", () => {
    void refreshAll();
  });

  el.selftestBtn.addEventListener("click", () => {
    sendLine("RUN SELFTEST", "CMD");
  });

  el.saveCfgBtn.addEventListener("click", () => {
    sendLine("SAVE CFG", "CMD");
  });

  el.saveCalBtn.addEventListener("click", () => {
    sendLine("SAVE CAL", "CMD");
  });

  el.stopCalBtn.addEventListener("click", () => {
    sendLine("STOP CAL", "CMD");
  });

  el.applyCfgBtn.addEventListener("click", () => {
    void applyConfig();
  });

  el.resetCfgBtn.addEventListener("click", () => {
    sendLine("RESET CFG", "CMD");
  });

  el.applyPaletteBtn.addEventListener("click", () => {
    el.paletteModeInput.value = el.calPaletteMode.value;
    void applyConfig();
  });

  el.refreshCalBtn.addEventListener("click", () => {
    void refreshCalibration(Number(el.calPaletteMode.value || 8));
  });

  el.resetCalBtn.addEventListener("click", () => {
    sendLine(`RESET CAL ${Number(el.calPaletteMode.value || 8)}`, "CMD");
    void refreshCalibration(Number(el.calPaletteMode.value || 8));
  });

  el.calPaletteMode.addEventListener("change", () => {
    renderPaletteGrid();
    void refreshCalibration(Number(el.calPaletteMode.value || 8));
  });

  el.paletteGrid.addEventListener("click", handlePaletteGridClick);

  el.overlayCloseBtn.addEventListener("click", closeOverlay);
  el.overlayCaptureBtn.addEventListener("click", () => {
    if (!state.overlayTarget) {
      return;
    }
    const slot = state.overlayTarget.slot;
    closeOverlay();
    void runCapture(slot);
  });

  el.exportBtn.addEventListener("click", exportProfile);
  el.importFile.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) {
      return;
    }
    void importProfile(file).catch((err) => {
      setCmdStatus(`Import failed: ${err.message}`, false);
      appendLog(`IMPORT ERROR: ${err.message}`);
    });
    event.target.value = "";
  });

  el.manualSendBtn.addEventListener("click", () => {
    sendLine(el.manualInput.value, "CMD");
    el.manualInput.select();
  });

  el.manualInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendLine(el.manualInput.value, "CMD");
      el.manualInput.select();
    }
  });

  setInterval(() => {
    if (state.port && Date.now() - state.lastRxAt > RX_TIMEOUT_MS) {
      setCmdStatus("No RX from module", false);
    }
  }, 1000);

  if ("serial" in navigator) {
    navigator.serial.addEventListener("disconnect", () => {
      void closeSerial(true);
    });
  }

  renderPaletteGrid();
  renderInfo();
  renderHealthFlags(0);
})();
