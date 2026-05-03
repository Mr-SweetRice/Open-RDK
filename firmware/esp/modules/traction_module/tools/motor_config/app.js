(function () {
  const CURVE_PWM_POINTS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
  const CURVE_PREP_MS = 400;
  const CURVE_SETTLE_MS = 1200;
  const CURVE_SAMPLE_MS = 1000;
  const CURVE_SAMPLE_PERIOD_MS = 100;
  const SNAPSHOT_TIMEOUT_MS = 2500;
  const SAVE_TIMEOUT_MS = 5000;
  const RX_TIMEOUT_MS = 3000;

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const FRAME_SYNC_BYTES = [0xAA, 0x55, 0xAA, 0x55];
  const FRAME_MAX_LEN = 200;
  const FRAME_RX_MAX_BYTES = 4096;
  const FRAME_MSG_TYPE_CODE = {
    CMD: 0x01,
    TEST: 0x02,
    TELEMETRY: 0x03,
    TRACTION_OUT: 0x04,
  };

  const configDefaults = {
    bridgeMode: "TB6612",
    encoderMode: "Quadrature x4",
    motorDirection: "Normal",
    encoderDirection: "Normal",
    rpmMax: 199.89,
    pwmFreq: 20000,
    countsPerRev: 44,
    gearRatio: 45,
    pullupMode: "Enabled",
    notes: "Bench profile / default firmware values",
  };

  function defaultCurveRpmForPwm(pwm) {
    if (pwm <= 40) return 0;
    if (pwm >= 100) return 199.89;
    return 0.5855839 * Math.pow(pwm - 40, 1.42);
  }

  function buildCurveDefaults() {
    return CURVE_PWM_POINTS.map((pwm) => ({
      output: pwm,
      rpm: Number(defaultCurveRpmForPwm(pwm).toFixed(2)),
    }));
  }

  const bridgeMode = document.getElementById("bridgeMode");
  const encoderMode = document.getElementById("encoderMode");
  const motorDirection = document.getElementById("motorDirection");
  const encoderDirection = document.getElementById("encoderDirection");
  const rpmMax = document.getElementById("rpmMax");
  const pwmFreq = document.getElementById("pwmFreq");
  const countsPerRev = document.getElementById("countsPerRev");
  const gearRatio = document.getElementById("gearRatio");
  const pullupMode = document.getElementById("pullupMode");
  const notes = document.getElementById("notes");
  const configStatus = document.getElementById("configStatus");
  const serialStatus = document.getElementById("serialStatus");

  const cfgBridgeValue = document.getElementById("cfgBridgeValue");
  const cfgEncoderValue = document.getElementById("cfgEncoderValue");
  const cfgRpmValue = document.getElementById("cfgRpmValue");
  const cfgPwmValue = document.getElementById("cfgPwmValue");
  const cfgCountsValue = document.getElementById("cfgCountsValue");
  const cfgGearValue = document.getElementById("cfgGearValue");

  const curveBody = document.getElementById("curveBody");
  const curveCount = document.getElementById("curveCount");
  const curveMaxRpm = document.getElementById("curveMaxRpm");
  const curveSlope = document.getElementById("curveSlope");

  const connectBtn = document.getElementById("connectBtn");
  const readCurveBtn = document.getElementById("readCurveBtn");
  const writeCurveBtn = document.getElementById("writeCurveBtn");
  const generateCurveBtn = document.getElementById("generateCurveBtn");
  const saveConfigBtn = document.getElementById("saveConfigBtn");
  const resetConfigBtn = document.getElementById("resetConfigBtn");
  const resetCurveBtn = document.getElementById("resetCurveBtn");

  let port = null;
  let reader = null;
  let writer = null;
  let buffer = "";
  let frameSeq = 0;
  let frameRxBytes = [];
  let closingSerial = false;
  let connectingSerial = false;
  let curveRun = false;
  let latestTelem = { rpm: 0, ts: 0 };
  let pendingCurvePromise = null;
  let pendingCfgPromise = null;
  let pendingSavePromise = null;

  function saveStatus(text) {
    configStatus.textContent = text;
  }

  function nextFrameSeq() {
    frameSeq = (frameSeq + 1) & 0x00ffffff;
    return frameSeq;
  }

  function messageTypeForCommand(text) {
    const upper = String(text || "").trim().toUpperCase();
    if (upper.startsWith("SET OUT") || upper.startsWith("CLR OUT")) {
      return "TRACTION_OUT";
    }
    return "CMD";
  }

  function appendFrameRxChunk(value) {
    if (!value || !value.length) return;
    for (const b of value) {
      frameRxBytes.push(b & 0xff);
    }
    if (frameRxBytes.length > FRAME_RX_MAX_BYTES) {
      frameRxBytes.splice(0, frameRxBytes.length - FRAME_RX_MAX_BYTES);
    }
  }

  function trimFrameRxToSyncPrefix() {
    if (!frameRxBytes.length) return;
    let keep = 0;
    const maxPrefix = Math.min(frameRxBytes.length, FRAME_SYNC_BYTES.length - 1);
    for (let prefixLen = maxPrefix; prefixLen > 0; prefixLen--) {
      let matches = true;
      for (let i = 0; i < prefixLen; i++) {
        if (frameRxBytes[frameRxBytes.length - prefixLen + i] !== FRAME_SYNC_BYTES[i]) {
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
      frameRxBytes.splice(0, frameRxBytes.length - keep);
    } else {
      frameRxBytes = [];
    }
  }

  function findFrameSyncStart() {
    const maxStart = frameRxBytes.length - FRAME_SYNC_BYTES.length;
    for (let i = 0; i <= maxStart; i++) {
      let ok = true;
      for (let j = 0; j < FRAME_SYNC_BYTES.length; j++) {
        if (frameRxBytes[i + j] !== FRAME_SYNC_BYTES[j]) {
          ok = false;
          break;
        }
      }
      if (ok) return i;
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
        frameRxBytes.splice(0, syncStart);
      }

      const base = FRAME_SYNC_BYTES.length;
      if (frameRxBytes.length < base + 1) {
        return;
      }

      const msgLen = frameRxBytes[base];
      if (msgLen <= 0 || msgLen > FRAME_MAX_LEN) {
        frameRxBytes.splice(0, 1);
        continue;
      }

      const frameLen = base + 1 + msgLen + 1 + 3;
      if (frameRxBytes.length < frameLen) {
        return;
      }

      const frame = frameRxBytes.splice(0, frameLen);
      const payloadStart = base + 1;
      const payloadEnd = payloadStart + msgLen;
      const payload = frame.slice(payloadStart, payloadEnd);
      const text = decoder.decode(Uint8Array.from(payload)).trim();
      if (text) {
        onMessageText(text);
      }
    }
  }

  function setSerialStatus(text) {
    serialStatus.textContent = text;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function bridgeLabelFromValue(value) {
    return Number(value) === 1 ? "DRV8833" : "TB6612";
  }

  function bridgeValueFromLabel(label) {
    return label === "DRV8833" ? 1 : 0;
  }

  function encoderLabelFromValue(value) {
    const mode = Number(value);
    if (mode === 1) return "Quadrature x2";
    if (mode === 2) return "Single Channel";
    return "Quadrature x4";
  }

  function encoderValueFromLabel(label) {
    if (label === "Quadrature x2") return 1;
    if (label === "Single Channel") return 2;
    return 0;
  }

  function collectConfig() {
    return {
      bridgeMode: bridgeMode.value,
      encoderMode: encoderMode.value,
      motorDirection: motorDirection.value,
      encoderDirection: encoderDirection.value,
      rpmMax: Number(rpmMax.value) || 0,
      pwmFreq: Number(pwmFreq.value) || 0,
      countsPerRev: Number(countsPerRev.value) || 0,
      gearRatio: Number(gearRatio.value) || 0,
      pullupMode: pullupMode.value,
      notes: notes.value.trim(),
    };
  }

  function fillConfig(cfg) {
    bridgeMode.value = cfg.bridgeMode;
    encoderMode.value = cfg.encoderMode;
    motorDirection.value = cfg.motorDirection;
    encoderDirection.value = cfg.encoderDirection;
    rpmMax.value = Number(cfg.rpmMax || 0).toFixed(2);
    pwmFreq.value = Number(cfg.pwmFreq || 0);
    countsPerRev.value = Number(cfg.countsPerRev || 0);
    gearRatio.value = Number(cfg.gearRatio || 0);
    pullupMode.value = cfg.pullupMode;
    notes.value = cfg.notes || "";
    syncConfigTable();
  }

  function syncConfigTable() {
    const cfg = collectConfig();
    cfgBridgeValue.textContent = `${cfg.bridgeMode} / Motor ${cfg.motorDirection}`;
    cfgEncoderValue.textContent = `${cfg.encoderMode} / Encoder ${cfg.encoderDirection}`;
    cfgRpmValue.textContent = cfg.rpmMax.toFixed(2);
    cfgPwmValue.textContent = `${cfg.pwmFreq} Hz`;
    cfgCountsValue.textContent = String(cfg.countsPerRev);
    cfgGearValue.textContent = String(cfg.gearRatio);
  }

  function normalizeCurveRows(rows) {
    const byOutput = new Map(Array.isArray(rows) ? rows.map((row) => [Number(row && row.output), row]) : []);
    return CURVE_PWM_POINTS.map((output) => {
      const match = byOutput.get(output);
      return { output, rpm: Number(match && match.rpm) || 0 };
    });
  }

  function getCurveRows() {
    return Array.from(curveBody.querySelectorAll("tr")).map((row) => ({
      output: Number(row.querySelector("[data-field='output']").value) || 0,
      rpm: Number(row.querySelector("[data-field='rpm']").value) || 0,
    }));
  }

  function syncCurveSummary() {
    const rows = getCurveRows();
    curveCount.textContent = String(rows.length);
    curveMaxRpm.textContent = rows.reduce((max, row) => Math.max(max, row.rpm), 0).toFixed(1);

    let slopeSum = 0;
    let slopeCountLocal = 0;
    for (let i = 1; i < rows.length; i++) {
      const deltaOut = rows[i].output - rows[i - 1].output;
      const deltaRpm = rows[i].rpm - rows[i - 1].rpm;
      if (deltaOut > 0) {
        slopeSum += deltaRpm / deltaOut;
        slopeCountLocal++;
      }
    }
    curveSlope.textContent = (slopeCountLocal ? slopeSum / slopeCountLocal : 0).toFixed(2);
  }

  function syncRpmMaxFromCurve() {
    const rows = getCurveRows();
    if (!rows.length) return;
    rpmMax.value = Number(rows[rows.length - 1].rpm || 0).toFixed(2);
    syncConfigTable();
  }

  function syncCurveLastPointFromRpmMax() {
    const rows = curveBody.querySelectorAll("tr");
    if (!rows.length) return;
    const lastInput = rows[rows.length - 1].querySelector("[data-field='rpm']");
    if (!lastInput) return;
    lastInput.value = Number(rpmMax.value || 0).toFixed(2);
    syncCurveSummary();
  }

  function renderCurve(rows) {
    curveBody.innerHTML = "";
    normalizeCurveRows(rows).forEach((row, index) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${index + 1}</td>
        <td><input data-field="output" type="number" value="${row.output}" readonly></td>
        <td><input data-field="rpm" type="number" min="0" step="0.01" value="${row.rpm.toFixed(2)}"></td>
      `;
      curveBody.appendChild(tr);
    });
    syncCurveSummary();
    syncRpmMaxFromCurve();
  }

  function sendLine(text) {
    if (!writer) return;
    const payloadText = String(text || "").trim();
    if (!payloadText) return;
    const rawPayload = encoder.encode(payloadText);
    const payload = rawPayload.length > FRAME_MAX_LEN ? rawPayload.slice(0, FRAME_MAX_LEN) : rawPayload;
    const msgTypeName = messageTypeForCommand(payloadText);
    const msgTypeCode = FRAME_MSG_TYPE_CODE[msgTypeName] || FRAME_MSG_TYPE_CODE.CMD;
    const seq = nextFrameSeq();

    const frameLen = FRAME_SYNC_BYTES.length + 1 + payload.length + 1 + 3;
    const frame = new Uint8Array(frameLen);
    let i = 0;
    for (const b of FRAME_SYNC_BYTES) frame[i++] = b;
    frame[i++] = payload.length & 0xff;
    frame.set(payload, i);
    i += payload.length;
    frame[i++] = msgTypeCode & 0xff;
    frame[i++] = (seq >> 16) & 0xff;
    frame[i++] = (seq >> 8) & 0xff;
    frame[i++] = seq & 0xff;

    writer.write(frame);
  }

  function rejectPendingCurve(message) {
    if (!pendingCurvePromise) return;
    clearTimeout(pendingCurvePromise.timer);
    pendingCurvePromise.reject(new Error(message));
    pendingCurvePromise = null;
  }

  function resolvePendingCurve(rows) {
    if (!pendingCurvePromise) return;
    clearTimeout(pendingCurvePromise.timer);
    pendingCurvePromise.resolve(rows);
    pendingCurvePromise = null;
  }

  function rejectPendingCfg(message) {
    if (!pendingCfgPromise) return;
    clearTimeout(pendingCfgPromise.timer);
    pendingCfgPromise.reject(new Error(message));
    pendingCfgPromise = null;
  }

  function resolvePendingCfgIfReady() {
    if (!pendingCfgPromise || !pendingCfgPromise.gotCfg || !pendingCfgPromise.gotNotes) return;
    clearTimeout(pendingCfgPromise.timer);
    pendingCfgPromise.resolve(pendingCfgPromise.cfg);
    pendingCfgPromise = null;
  }

  function rejectPendingSave(message) {
    if (!pendingSavePromise) return;
    clearTimeout(pendingSavePromise.timer);
    pendingSavePromise.reject(new Error(message));
    pendingSavePromise = null;
  }

  function resolvePendingSave() {
    if (!pendingSavePromise) return;
    clearTimeout(pendingSavePromise.timer);
    pendingSavePromise.resolve();
    pendingSavePromise = null;
  }

  function requestCurveSnapshot(timeoutMs = SNAPSHOT_TIMEOUT_MS) {
    if (!writer) return Promise.reject(new Error("Controller is not connected"));
    rejectPendingCurve("Replaced by newer curve request");
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pendingCurvePromise = null;
        reject(new Error("Timeout waiting for curve snapshot"));
      }, timeoutMs);
      pendingCurvePromise = { resolve, reject, timer };
      sendLine("GET CURVE");
    });
  }

  function requestConfigSnapshot(timeoutMs = SNAPSHOT_TIMEOUT_MS) {
    if (!writer) return Promise.reject(new Error("Controller is not connected"));
    rejectPendingCfg("Replaced by newer config request");
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pendingCfgPromise = null;
        reject(new Error("Timeout waiting for config snapshot"));
      }, timeoutMs);
      pendingCfgPromise = {
        resolve,
        reject,
        timer,
        cfg: null,
        gotCfg: false,
        gotNotes: false,
      };
      sendLine("GET CFG");
    });
  }

  function waitForSave(timeoutMs = SAVE_TIMEOUT_MS) {
    rejectPendingSave("Replaced by newer save request");
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pendingSavePromise = null;
        reject(new Error("Timeout waiting for save completion"));
      }, timeoutMs);
      pendingSavePromise = { resolve, reject, timer };
    });
  }

  function handleCurveLine(parts) {
    if (parts.length < 11) return;
    const rows = CURVE_PWM_POINTS.map((output, index) => ({
      output,
      rpm: Number(parts[index + 1]) || 0,
    }));
    renderCurve(rows);
    saveStatus("Curve table updated from controller.");
    resolvePendingCurve(rows);
  }

  function handleCfgLine(parts) {
    if (parts.length < 10) return;
    const cfg = {
      bridgeMode: bridgeLabelFromValue(parts[1]),
      encoderMode: encoderLabelFromValue(parts[2]),
      motorDirection: Number(parts[3]) ? "Inverted" : "Normal",
      encoderDirection: Number(parts[4]) ? "Inverted" : "Normal",
      pullupMode: Number(parts[5]) ? "Enabled" : "Disabled",
      pwmFreq: Number(parts[6]) || 0,
      countsPerRev: Number(parts[7]) || 0,
      gearRatio: Number(parts[8]) || 0,
      rpmMax: Number(parts[9]) || 0,
      notes: notes.value.trim(),
    };

    if (pendingCfgPromise) {
      pendingCfgPromise.cfg = { ...(pendingCfgPromise.cfg || configDefaults), ...cfg };
      pendingCfgPromise.gotCfg = true;
      resolvePendingCfgIfReady();
      return;
    }

    fillConfig(cfg);
    saveStatus("Configuration updated from controller.");
  }

  function handleCfgNotesLine(line) {
    const noteText = line.slice(5);
    if (pendingCfgPromise) {
      pendingCfgPromise.cfg = { ...(pendingCfgPromise.cfg || configDefaults), notes: noteText };
      pendingCfgPromise.gotNotes = true;
      resolvePendingCfgIfReady();
      return;
    }

    const cfg = collectConfig();
    cfg.notes = noteText;
    fillConfig(cfg);
    saveStatus("Configuration updated from controller.");
  }

  function handleTelemLine(parts) {
    if (parts.length < 5) return;
    const rpm = parseFloat(parts[2]);
    latestTelem = {
      rpm: Number.isFinite(rpm) ? Math.abs(rpm) : 0,
      ts: Date.now(),
    };
  }

  function handleLine(line) {
    if (line.includes("|CFGN,")) {
      const [cfgLine, notesLine] = line.split("|CFGN,", 2);
      handleLine(cfgLine);
      handleLine(`CFGN,${notesLine || ""}`);
      return;
    }

    if (line === "OK" || line === "S,ENQ" || line === "S,START" || line === "S,END") return;
    if (line === "ERR") {
      setSerialStatus("Controller returned ERR.");
      return;
    }
    if (line.startsWith("S,OK")) {
      resolvePendingSave();
      setSerialStatus("Controller save completed.");
      return;
    }
    if (line.startsWith("S,ERR")) {
      rejectPendingSave(line);
      setSerialStatus("Controller save failed: " + line);
      return;
    }
    if (line.startsWith("CV,")) return handleCurveLine(line.split(","));
    if (line.startsWith("CFG,")) return handleCfgLine(line.split(","));
    if (line.startsWith("CFGN,")) return handleCfgNotesLine(line);
    if (line.startsWith("T,")) handleTelemLine(line.split(","));
  }

  async function readLoop() {
    try {
      while (reader) {
        const { value, done } = await reader.read();
        if (done) break;
        appendFrameRxChunk(value);
        consumeFramedMessages((line) => handleLine(line));
      }
    } catch (err) {
      if (!closingSerial) {
        setSerialStatus("Serial RX error: " + (err && err.message ? err.message : err));
      }
    } finally {
      if (!closingSerial && (port || reader || writer)) {
        await closeSerial();
      }
    }
  }

  async function closeSerial() {
    if (closingSerial) return;
    closingSerial = true;
    rejectPendingCurve("Controller disconnected");
    rejectPendingCfg("Controller disconnected");
    rejectPendingSave("Controller disconnected");
    try {
      if (reader) {
        try { await reader.cancel(); } catch (_err) {}
        try { reader.releaseLock(); } catch (_err) {}
        reader = null;
      }
      if (writer) {
        try { writer.releaseLock(); } catch (_err) {}
        writer = null;
      }
      if (port) {
        const openedPort = port;
        port = null;
        try { await openedPort.close(); } catch (_err) {}
      }
    } finally {
      buffer = "";
      frameRxBytes = [];
      closingSerial = false;
      connectBtn.textContent = "Connect Controller";
      setSerialStatus("Controller disconnected.");
    }
  }

  async function refreshControllerState() {
    const [cfg, curve] = await Promise.all([requestConfigSnapshot(), requestCurveSnapshot()]);
    fillConfig(cfg);
    renderCurve(curve);
  }

  async function connect() {
    if (connectingSerial || closingSerial) return;
    connectingSerial = true;
    try {
      if (!("serial" in navigator)) {
        setSerialStatus("Web Serial is not available in this browser.");
        return;
      }

      port = await navigator.serial.requestPort();
      await port.open({ baudRate: 115200 });
      reader = port.readable.getReader();
      writer = port.writable.getWriter();
      connectBtn.textContent = "Disconnect Controller";
      setSerialStatus("Controller connected.");
      void readLoop();

      try {
        await refreshControllerState();
        saveStatus("Configuration and curve loaded from controller.");
      } catch (err) {
        const message = err && err.message ? err.message : err;
        setSerialStatus(`Controller connected. Initial read failed: ${message}`);
        saveStatus("Controller connected. Use Read Curve or Save Config to retry commands.");
      }
    } catch (err) {
      setSerialStatus("Connect failed: " + (err && err.message ? err.message : err));
      await closeSerial();
    } finally {
      connectingSerial = false;
    }
  }

  async function sampleAverageRpm(durationMs) {
    const samples = [];
    const deadline = Date.now() + durationMs;
    while (Date.now() < deadline) {
      sendLine("GET TELEM");
      await sleep(CURVE_SAMPLE_PERIOD_MS);
      if (Date.now() - latestTelem.ts < RX_TIMEOUT_MS) {
        samples.push(latestTelem.rpm);
      }
    }
    if (!samples.length) return 0;
    return samples.reduce((sum, value) => sum + value, 0) / samples.length;
  }

  async function saveConfigToController() {
    if (!writer) throw new Error("Controller is not connected");

    syncCurveLastPointFromRpmMax();
    const cfg = collectConfig();
    const commands = [
      `SET CFG BRIDGE ${bridgeValueFromLabel(cfg.bridgeMode)}`,
      `SET CFG ENCODER_MODE ${encoderValueFromLabel(cfg.encoderMode)}`,
      `SET CFG MOTOR_INV ${cfg.motorDirection === "Inverted" ? 1 : 0}`,
      `SET CFG ENCODER_INV ${cfg.encoderDirection === "Inverted" ? 1 : 0}`,
      `SET CFG PULLUP ${cfg.pullupMode === "Enabled" ? 1 : 0}`,
      `SET CFG PWM_FREQ ${Math.max(1, Math.round(cfg.pwmFreq))}`,
      `SET CFG COUNTS_PER_REV ${Math.max(1, Math.round(cfg.countsPerRev))}`,
      `SET CFG GEAR_RATIO ${Math.max(1, Math.round(cfg.gearRatio))}`,
      `SET CFG RPM_MAX ${Math.max(0, cfg.rpmMax).toFixed(2)}`,
      `SET CFG NOTES ${cfg.notes}`,
    ];

    for (const cmd of commands) {
      sendLine(cmd);
      await sleep(70);
    }

    const savePromise = waitForSave();
    sendLine("SAVE CFG");
    await savePromise;

    fillConfig(await requestConfigSnapshot());
    saveStatus("Configuration saved to controller NVS.");
  }

  async function writeCurveToController(rows) {
    if (!writer) throw new Error("Controller is not connected");
    const normalized = normalizeCurveRows(rows);

    for (const row of normalized) {
      sendLine(`SET CURVE ${row.output} ${Math.max(0, row.rpm).toFixed(2)}`);
      await sleep(70);
    }

    const savePromise = waitForSave();
    sendLine("SAVE CURVE");
    await savePromise;

    renderCurve(await requestCurveSnapshot());
    await saveConfigToController();
    setSerialStatus("Controller curve updated.");
  }

  function setCurveButtonsBusy(active) {
    curveRun = active;
    connectBtn.disabled = active;
    readCurveBtn.disabled = active;
    writeCurveBtn.disabled = active;
    resetCurveBtn.disabled = active;
    saveConfigBtn.disabled = active;
    resetConfigBtn.disabled = active;
    generateCurveBtn.textContent = active ? "Creating Curve..." : "Create Curve";
  }

  async function generateCurve() {
    if (!writer) {
      setSerialStatus("Connect to the controller before creating the curve.");
      return;
    }

    setCurveButtonsBusy(true);
    try {
      sendLine("STOP PID POS SINE");
      sendLine("STOP PID POS");
      sendLine("SET PID RPM SP 0");
      sendLine("CLR OUT");
      await sleep(CURVE_PREP_MS);

      const rows = [];
      for (const pwm of CURVE_PWM_POINTS) {
        setSerialStatus(`Creating curve at ${pwm}% applied PWM...`);
        sendLine(`SET OUT RAW ${pwm.toFixed(2)}`);
        await sleep(CURVE_SETTLE_MS);
        rows.push({
          output: pwm,
          rpm: Number((await sampleAverageRpm(CURVE_SAMPLE_MS)).toFixed(2)),
        });
        renderCurve(rows);
      }

      sendLine("CLR OUT");
      renderCurve(rows);
      await writeCurveToController(rows);
      setSerialStatus("Curve generated and saved to controller.");
    } catch (err) {
      setSerialStatus("Curve generation failed: " + (err && err.message ? err.message : err));
    } finally {
      sendLine("CLR OUT");
      setCurveButtonsBusy(false);
    }
  }

  [bridgeMode, encoderMode, motorDirection, encoderDirection, rpmMax, pwmFreq, countsPerRev, gearRatio, pullupMode, notes]
    .forEach((el) => {
      el.addEventListener("input", syncConfigTable);
      el.addEventListener("change", syncConfigTable);
    });

  rpmMax.addEventListener("input", syncCurveLastPointFromRpmMax);
  rpmMax.addEventListener("change", syncCurveLastPointFromRpmMax);

  connectBtn.addEventListener("click", () => {
    if (port) {
      void closeSerial();
    } else {
      void connect();
    }
  });

  readCurveBtn.addEventListener("click", () => {
    requestCurveSnapshot()
      .then((rows) => {
        renderCurve(rows);
        setSerialStatus("Curve loaded from controller.");
      })
      .catch((err) => setSerialStatus(err.message));
  });

  writeCurveBtn.addEventListener("click", () => {
    void writeCurveToController(getCurveRows())
      .then(() => saveStatus("Curve written to controller NVS."))
      .catch((err) => setSerialStatus(err.message));
  });

  generateCurveBtn.addEventListener("click", () => {
    void generateCurve();
  });

  saveConfigBtn.addEventListener("click", () => {
    void saveConfigToController().catch((err) => setSerialStatus(err.message));
  });

  resetConfigBtn.addEventListener("click", () => {
    fillConfig(configDefaults);
    saveStatus("Configuration reset to firmware defaults. Save to push it to NVS.");
  });

  resetCurveBtn.addEventListener("click", () => {
    renderCurve(buildCurveDefaults());
    saveStatus("Curve reset to firmware defaults in the page. Write Curve to push it to NVS.");
  });

  curveBody.addEventListener("input", () => {
    syncCurveSummary();
    syncRpmMaxFromCurve();
    saveStatus("Curve edited in the page. Write Curve to push it to NVS.");
  });

  if ("serial" in navigator) {
    navigator.serial.addEventListener("disconnect", () => {
      void closeSerial();
    });
  }

  fillConfig(configDefaults);
  renderCurve(buildCurveDefaults());
  saveStatus("Connect to the controller to load NVS values.");
})();
