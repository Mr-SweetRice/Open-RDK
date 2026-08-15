const requestedSerial = new URLSearchParams(window.location.search).get("serial") || "";
const COLOR_STUDIO_PAGE_ID = document.body.dataset.pageId || "color-studio";
const COLOR_STUDIO_PAGE_VERSION = document.body.dataset.pageVersion || "1.1a";

const state = {
  devices: [],
  selectedSerial: requestedSerial || null,
  snapshot: null,
  calibration: null,
  profile: null,
  palettes: {},
  ws: null,
  telemetryLines: [],
  eventLines: [],
  telemetryStartedHere: false,
};

const elements = {
  deviceSelect: document.getElementById("deviceSelect"),
  firmwareVersionValue: document.getElementById("firmwareVersionValue"),
  pageCompatibilityWarning: document.getElementById("pageCompatibilityWarning"),
  refreshSnapshotBtn: document.getElementById("refreshSnapshotBtn"),
  startTelemetryBtn: document.getElementById("startTelemetryBtn"),
  stopTelemetryBtn: document.getElementById("stopTelemetryBtn"),
  currentSwatch: document.getElementById("currentSwatch"),
  currentColorName: document.getElementById("currentColorName"),
  currentColorConfidence: document.getElementById("currentColorConfidence"),
  paletteModeText: document.getElementById("paletteModeText"),
  relayModeText: document.getElementById("relayModeText"),
  sensorStateText: document.getElementById("sensorStateText"),
  clearValue: document.getElementById("clearValue"),
  gainValue: document.getElementById("gainValue"),
  integrationValue: document.getElementById("integrationValue"),
  targetClearValue: document.getElementById("targetClearValue"),
  topCandidatesList: document.getElementById("topCandidatesList"),
  rawRBar: document.getElementById("rawRBar"),
  rawGBar: document.getElementById("rawGBar"),
  rawBBar: document.getElementById("rawBBar"),
  rawRValue: document.getElementById("rawRValue"),
  rawGValue: document.getElementById("rawGValue"),
  rawBValue: document.getElementById("rawBValue"),
  normRValue: document.getElementById("normRValue"),
  normGValue: document.getElementById("normGValue"),
  normBValue: document.getElementById("normBValue"),
  labLValue: document.getElementById("labLValue"),
  labAValue: document.getElementById("labAValue"),
  labBValue: document.getElementById("labBValue"),
  sensorNameInput: document.getElementById("sensorNameInput"),
  paletteModeInput: document.getElementById("paletteModeInput"),
  samplePeriodInput: document.getElementById("samplePeriodInput"),
  ledModeInput: document.getElementById("ledModeInput"),
  gainModeInput: document.getElementById("gainModeInput"),
  gainInput: document.getElementById("gainInput"),
  integrationInput: document.getElementById("integrationInput"),
  classifierInput: document.getElementById("classifierInput"),
  confidenceInput: document.getElementById("confidenceInput"),
  targetClearInput: document.getElementById("targetClearInput"),
  patchSamplesInput: document.getElementById("patchSamplesInput"),
  blackThresholdInput: document.getElementById("blackThresholdInput"),
  brightThresholdInput: document.getElementById("brightThresholdInput"),
  saveConfigBtn: document.getElementById("saveConfigBtn"),
  persistConfigBtn: document.getElementById("persistConfigBtn"),
  selftestBtn: document.getElementById("selftestBtn"),
  selftestResult: document.getElementById("selftestResult"),
  telemetryFeed: document.getElementById("telemetryFeed"),
  eventFeed: document.getElementById("eventFeed"),
  calibrationModeSelect: document.getElementById("calibrationModeSelect"),
  calibrationStatus: document.getElementById("calibrationStatus"),
  manualTargetSelect: document.getElementById("manualTargetSelect"),
  manualCaptureBtn: document.getElementById("manualCaptureBtn"),
  persistCalibrationBtn: document.getElementById("persistCalibrationBtn"),
  paletteEditor: document.getElementById("paletteEditor"),
  saveLabelsBtn: document.getElementById("saveLabelsBtn"),
  reloadCalibrationBtn: document.getElementById("reloadCalibrationBtn"),
  exportProfileBtn: document.getElementById("exportProfileBtn"),
  importProfileBtn: document.getElementById("importProfileBtn"),
  restoreDefaultsBtn: document.getElementById("restoreDefaultsBtn"),
  profileNote: document.getElementById("profileNote"),
  manualCmdInput: document.getElementById("manualCmdInput"),
  manualCmdSend: document.getElementById("manualCmdSend"),
  manualCmdResponse: document.getElementById("manualCmdResponse"),
  importProfileInput: document.getElementById("importProfileInput"),
};

function selectedDevice() {
  if (!state.selectedSerial) {
    return null;
  }
  return state.devices.find((item) => item.serial_number === state.selectedSerial) || null;
}

function renderPageCompatibility() {
  const device = selectedDevice();
  if (elements.firmwareVersionValue) {
    elements.firmwareVersionValue.textContent = device?.firmware_version || "legacy / unknown";
  }
  if (!elements.pageCompatibilityWarning) return;
  const expectedPage = String(device?.expected_page || "");
  const expectedVersion = String(device?.expected_page_version || "");
  const mismatch = Boolean(device && expectedPage && expectedVersion &&
    (expectedPage !== COLOR_STUDIO_PAGE_ID || expectedVersion !== COLOR_STUDIO_PAGE_VERSION));
  elements.pageCompatibilityWarning.hidden = !mismatch;
  elements.pageCompatibilityWarning.textContent = mismatch
    ? `WARNING: Firmware ${device.firmware_version || "unknown"} expects ${expectedPage} page v${expectedVersion}, but page v${COLOR_STUDIO_PAGE_VERSION} is open.`
    : "";
}

function streamIsOwned() {
  const device = selectedDevice();
  return Boolean(device?.telemetry_requested || device?.telemetry_active);
}

function updateTelemetryControls() {
  const hasDevice = Boolean(state.selectedSerial);
  elements.startTelemetryBtn.disabled = !hasDevice || streamIsOwned();
  elements.stopTelemetryBtn.disabled = !hasDevice || !state.telemetryStartedHere;
}

function showStreamOwnedWarning() {
  appendFeed(
    elements.eventFeed,
    state.eventLines,
    "Another program already owns this communication stream. Stop that code and run devices.py for configuration.",
  );
}

function syncSelectedSerialInUrl() {
  const url = new URL(window.location.href);
  if (state.selectedSerial) {
    url.searchParams.set("serial", state.selectedSerial);
  } else {
    url.searchParams.delete("serial");
  }
  window.history.replaceState({}, "", url);
}

function selectedModeKey() {
  const snapshotMode = String(state.snapshot?.cfg?.palette_mode || "");
  return snapshotMode || String(elements.paletteModeInput.value || "8");
}

function paletteEntries(modeKey) {
  return Array.isArray(state.palettes?.[String(modeKey)]) ? state.palettes[String(modeKey)] : [];
}

function profileForSelectedDevice() {
  if (state.profile) {
    return state.profile;
  }
  return selectedDevice()?.color_profile || null;
}

function modeProfile(modeKey) {
  const profile = profileForSelectedDevice();
  return profile?.modes?.[String(modeKey)] || null;
}

function labelForSlot(modeKey, slot) {
  if (!Number.isInteger(slot) || slot < 0) {
    if (slot === -2) return "dark";
    if (slot === -1) return "bright";
    return "unknown";
  }
  const mode = modeProfile(modeKey);
  if (mode?.labels) {
    const item = mode.labels.find((entry) => Number(entry.slot) === Number(slot));
    if (item?.name) {
      return item.name;
    }
  }
  const defaults = paletteEntries(modeKey);
  const fallback = defaults.find((entry) => Number(entry.slot) === Number(slot));
  return fallback?.name || `slot-${slot}`;
}

function colorForSlot(modeKey, slot) {
  const mode = modeProfile(modeKey);
  if (mode?.labels) {
    const item = mode.labels.find((entry) => Number(entry.slot) === Number(slot));
    if (item?.hex) {
      return item.hex;
    }
  }
  const defaults = paletteEntries(modeKey);
  const fallback = defaults.find((entry) => Number(entry.slot) === Number(slot));
  return fallback?.hex || "#6b7280";
}

function formatPercentFromMilli(value) {
  return `${(Number(value || 0) / 10).toFixed(1)}%`;
}

async function apiGet(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    const payload = await safeJson(response);
    throw new Error(String(payload?.detail || `GET ${url} failed with ${response.status}`));
  }
  return response.json();
}

async function apiPost(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  if (!response.ok) {
    const payload = await safeJson(response);
    throw new Error(String(payload?.detail || `POST ${url} failed with ${response.status}`));
  }
  return response.json();
}

async function safeJson(response) {
  try {
    return await response.json();
  } catch (_err) {
    return null;
  }
}

function appendFeed(target, lines, text, max = 120) {
  const stamp = new Date().toLocaleTimeString();
  lines.push(`[${stamp}] ${text}`);
  if (lines.length > max) {
    lines.splice(0, lines.length - max);
  }
  target.textContent = lines.join("\n");
  target.scrollTop = target.scrollHeight;
}

function setCalibrationStatus(text) {
  elements.calibrationStatus.textContent = text;
}

function renderDeviceSelect() {
  const previous = state.selectedSerial;
  elements.deviceSelect.innerHTML = "";
  if (state.devices.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No color_module detected";
    elements.deviceSelect.appendChild(option);
    elements.deviceSelect.disabled = true;
    state.selectedSerial = null;
    return;
  }

  elements.deviceSelect.disabled = false;
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select a color module";
  elements.deviceSelect.appendChild(placeholder);
  for (const device of state.devices) {
    const option = document.createElement("option");
    option.value = device.serial_number;
    option.textContent = `${device.name || device.serial_number} (${device.serial_number})`;
    elements.deviceSelect.appendChild(option);
  }

  if (previous && state.devices.some((item) => item.serial_number === previous)) {
    state.selectedSerial = previous;
  } else {
    state.selectedSerial = null;
  }
  elements.deviceSelect.value = state.selectedSerial || "";
  syncSelectedSerialInUrl();
  updateTelemetryControls();
  renderPageCompatibility();
}

function renderSnapshot() {
  const snapshot = state.snapshot;
  const device = selectedDevice();
  if (!snapshot || !device) {
    elements.currentColorName.textContent = "No data";
    elements.currentColorConfidence.textContent = "0.0%";
    return;
  }

  const cfg = snapshot.cfg;
  const data = snapshot.data;
  const modeKey = String(cfg.palette_mode);
  const detectedSlot = Number(data.detected_slot);
  const colorName = detectedSlot >= 0 ? labelForSlot(modeKey, detectedSlot) : "unclassified";
  const colorHex = detectedSlot >= 0 ? colorForSlot(modeKey, detectedSlot) : "#374151";
  const health = data.health || {};

  elements.currentSwatch.style.background = colorHex;
  elements.currentColorName.textContent = colorName;
  elements.currentColorConfidence.textContent = formatPercentFromMilli(data.confidence_milli);
  elements.paletteModeText.textContent = `Palette ${cfg.palette_mode}`;
  elements.relayModeText.textContent = `Mode ${device.message_type || "CMD"}`;
  elements.sensorStateText.textContent = health.sensor_ok
    ? (Number(data.led_active || 0) ? "Sensor ready / LED on" : "Sensor ready")
    : "Sensor offline";
  elements.clearValue.textContent = String(data.raw.c);
  elements.gainValue.textContent = `${data.gain}x`;
  elements.integrationValue.textContent = `${data.integration_ms} ms`;
  elements.targetClearValue.textContent = String(cfg.target_clear);

  elements.rawRValue.textContent = String(data.raw.r);
  elements.rawGValue.textContent = String(data.raw.g);
  elements.rawBValue.textContent = String(data.raw.b);
  elements.normRValue.textContent = `${data.norm_rgb_milli.r}`;
  elements.normGValue.textContent = `${data.norm_rgb_milli.g}`;
  elements.normBValue.textContent = `${data.norm_rgb_milli.b}`;
  elements.labLValue.textContent = (Number(data.lab_l_centi || 0) / 100).toFixed(2);
  elements.labAValue.textContent = (Number(data.lab_a_centi || 0) / 100).toFixed(2);
  elements.labBValue.textContent = (Number(data.lab_b_centi || 0) / 100).toFixed(2);

  const rawMax = Math.max(1, data.raw.r, data.raw.g, data.raw.b, data.raw.c);
  elements.rawRBar.style.width = `${(data.raw.r / rawMax) * 100}%`;
  elements.rawGBar.style.width = `${(data.raw.g / rawMax) * 100}%`;
  elements.rawBBar.style.width = `${(data.raw.b / rawMax) * 100}%`;

  elements.topCandidatesList.innerHTML = "";
  for (const candidate of data.top || []) {
    if (!candidate || Number(candidate.slot) < 0) {
      continue;
    }
    const item = document.createElement("div");
    item.className = "candidate-item";
    item.innerHTML = `
      <span>${labelForSlot(modeKey, Number(candidate.slot))}</span>
      <strong>${formatPercentFromMilli(candidate.confidence_milli)}</strong>
    `;
    elements.topCandidatesList.appendChild(item);
  }
  if (!elements.topCandidatesList.children.length) {
    elements.topCandidatesList.innerHTML = `<div class="candidate-item"><span>No valid candidates</span><strong>0.0%</strong></div>`;
  }
}

function renderConfigInputs() {
  const snapshot = state.snapshot;
  if (!snapshot) {
    return;
  }
  const cfg = snapshot.cfg;
  elements.sensorNameInput.value = cfg.sensor_name || "";
  elements.paletteModeInput.value = String(cfg.palette_mode);
  elements.samplePeriodInput.value = String(cfg.sample_period_ms);
  elements.ledModeInput.value = String(cfg.led_mode);
  elements.gainModeInput.value = String(cfg.gain_mode);
  elements.gainInput.value = String(cfg.gain);
  elements.integrationInput.value = String(cfg.integration_ms);
  elements.classifierInput.value = String(cfg.classifier);
  elements.confidenceInput.value = (Number(cfg.confidence_milli || 0) / 1000).toFixed(2);
  elements.targetClearInput.value = String(cfg.target_clear);
  elements.patchSamplesInput.value = String(cfg.patch_sample_count || 12);
  elements.blackThresholdInput.value = (Number(cfg.black_threshold_milli ?? 120) / 1000).toFixed(2);
  elements.brightThresholdInput.value = (Number(cfg.bright_threshold_milli ?? 880) / 1000).toFixed(2);
  elements.calibrationModeSelect.value = String(cfg.palette_mode);
}

function renderPaletteEditor() {
  const modeKey = selectedModeKey();
  const mode = modeProfile(modeKey);
  const labels = Array.isArray(mode?.labels) ? mode.labels : paletteEntries(modeKey);
  elements.paletteEditor.innerHTML = "";

  for (const item of labels) {
    const row = document.createElement("div");
    row.className = "palette-item";
    row.innerHTML = `
      <div class="slot-chip" style="background:${item.hex || "#6b7280"}"></div>
      <input type="text" value="${escapeHtml(item.name || "")}" data-slot="${Number(item.slot)}" />
      <input type="color" value="${normalizeHex(item.hex || "#6b7280")}" data-hex-slot="${Number(item.slot)}" />
      <label class="palette-toggle">
        <input type="checkbox" ${item.enabled !== false ? "checked" : ""} data-enabled-slot="${Number(item.slot)}" />
        Enabled
      </label>
    `;
    elements.paletteEditor.appendChild(row);
  }
}

function normalizeHex(value) {
  const text = String(value || "").trim();
  return /^#[0-9a-fA-F]{6}$/.test(text) ? text : "#6b7280";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

function collectEditedProfile() {
  const profile = deepClone(profileForSelectedDevice() || { modes: {} });
  const modeKey = selectedModeKey();
  profile.modes ||= {};
  profile.modes[modeKey] ||= { labels: [], patches: [], summary: null };
  profile.modes[modeKey].labels = [];

  const nameInputs = elements.paletteEditor.querySelectorAll("input[data-slot]");
  for (const input of nameInputs) {
    const slot = Number(input.dataset.slot);
    const hexInput = elements.paletteEditor.querySelector(`input[data-hex-slot="${slot}"]`);
    const enabledInput = elements.paletteEditor.querySelector(`input[data-enabled-slot="${slot}"]`);
    profile.modes[modeKey].labels.push({
      slot,
      name: String(input.value || "").trim() || labelForSlot(modeKey, slot),
      hex: normalizeHex(hexInput?.value || colorForSlot(modeKey, slot)),
      enabled: Boolean(enabledInput?.checked),
    });
  }
  return profile;
}

async function refreshPalettes() {
  const payload = await apiGet("/api/color/palettes");
  state.palettes = payload.palettes || {};
}

async function refreshDevices() {
  const previousSelected = state.selectedSerial;
  const payload = await apiGet("/api/color/devices");
  state.devices = Array.isArray(payload.devices) ? payload.devices : [];
  renderDeviceSelect();
  if (state.selectedSerial && state.selectedSerial !== previousSelected) {
    await refreshSnapshot();
    await refreshCalibration();
    await refreshProfile();
  }
}

async function refreshProfile() {
  if (!state.selectedSerial) {
    return;
  }
  const payload = await apiGet(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/profile`);
  state.profile = payload.profile || null;
  renderPaletteEditor();
  const modeKey = selectedModeKey();
  const last = modeProfile(modeKey)?.last_calibrated_at || "host profile not saved";
  elements.profileNote.textContent = `Host profile loaded. Last calibration for mode ${modeKey}: ${last}.`;
}

async function refreshCalibration() {
  if (!state.selectedSerial) {
    return;
  }
  const payload = await apiGet(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/calibration`);
  state.calibration = payload;
  if (payload.profile) {
    state.profile = payload.profile;
  }
  renderPaletteEditor();
  renderSingleTargetOptions();
}

async function refreshSnapshot() {
  if (!state.selectedSerial) {
    return;
  }
  const payload = await apiGet(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/snapshot`);
  state.snapshot = payload;
  if (payload.profile) {
    state.profile = payload.profile;
  }
  renderSnapshot();
  renderConfigInputs();
  renderPaletteEditor();
  renderSingleTargetOptions();
}

async function setDeviceMessageType(messageType) {
  if (!state.selectedSerial) {
    return;
  }
  const payload = await apiPost(
    `/api/devices/${encodeURIComponent(state.selectedSerial)}/config/message-type`,
    { message_type: String(messageType || "CMD") },
  );
  if (payload?.device?.serial_number) {
    const serial = String(payload.device.serial_number);
    state.devices = state.devices.map((item) => (
      item.serial_number === serial ? { ...item, ...payload.device } : item
    ));
  }
}

async function updateConfigFromInputs() {
  if (!state.selectedSerial) {
    return;
  }
  const payload = {
    sensor_name: elements.sensorNameInput.value,
    palette_mode: Number(elements.paletteModeInput.value),
    sample_period_ms: Number(elements.samplePeriodInput.value),
    led_mode: Number(elements.ledModeInput.value),
    gain_mode: Number(elements.gainModeInput.value),
    gain: Number(elements.gainInput.value),
    integration_ms: Number(elements.integrationInput.value),
    classifier: Number(elements.classifierInput.value),
    confidence_milli: Math.round(Number(elements.confidenceInput.value) * 1000),
    target_clear: Number(elements.targetClearInput.value),
    patch_sample_count: Number(elements.patchSamplesInput.value),
    black_threshold_milli: Math.round(Number(elements.blackThresholdInput.value) * 1000),
    bright_threshold_milli: Math.round(Number(elements.brightThresholdInput.value) * 1000),
  };
  const payloadResponse = await apiPost(
    `/api/devices/${encodeURIComponent(state.selectedSerial)}/color/config`,
    payload,
  );
  state.snapshot = payloadResponse;
  renderSnapshot();
  renderConfigInputs();
  renderSingleTargetOptions();
  appendFeed(elements.eventFeed, state.eventLines, "Applied color configuration via CMD.");
}

async function persistConfig() {
  if (!state.selectedSerial) {
    return;
  }
  await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/save`, {
    persist_cfg: true,
    persist_cal: false,
  });
  appendFeed(elements.eventFeed, state.eventLines, "Configuration saved to firmware NVS.");
}

async function runSelftest() {
  if (!state.selectedSerial) {
    return;
  }
  const payload = await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/selftest`, {});
  elements.selftestResult.textContent = payload?.result?.message
    ? `Selftest: ${payload.result.ok ? "OK" : "ERR"} (${payload.result.message})`
    : "Selftest finished.";
  if (payload?.snapshot) {
    state.snapshot = payload.snapshot;
    renderSnapshot();
  }
}

async function startTelemetry() {
  if (!state.selectedSerial) {
    return;
  }
  if (streamIsOwned()) {
    showStreamOwnedWarning();
    return;
  }
  await setDeviceMessageType("TELEMETRY");
  await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/telemetry/start`, {});
  state.telemetryStartedHere = true;
  await refreshDevices();
  appendFeed(elements.eventFeed, state.eventLines, "Telemetry start requested.");
}

async function stopTelemetry() {
  if (!state.selectedSerial) {
    return;
  }
  if (!state.telemetryStartedHere) {
    return;
  }
  await setDeviceMessageType("TELEMETRY");
  await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/telemetry/stop`, {});
  state.telemetryStartedHere = false;
  await refreshDevices();
  appendFeed(elements.eventFeed, state.eventLines, "Telemetry stop requested.");
}

function parseTelemetryLine(message) {
  const parts = String(message || "").split(",").map((item) => item.trim());
  if (parts.length < 26 || (parts[0] !== "TEL" && parts[0] !== "DATA")) {
    return null;
  }
  const hasExtendedLab = parts.length >= 30;
  return {
    palette_mode: Number(parts[1]),
    detected_slot: Number(parts[2]),
    confidence_milli: Number(parts[3]),
    top: [
      { slot: Number(parts[4]), confidence_milli: Number(parts[5]) },
      { slot: Number(parts[6]), confidence_milli: Number(parts[7]) },
      { slot: Number(parts[8]), confidence_milli: Number(parts[9]) },
    ],
    raw: { r: Number(parts[10]), g: Number(parts[11]), b: Number(parts[12]), c: Number(parts[13]) },
    norm_rgb_milli: { r: Number(parts[14]), g: Number(parts[15]), b: Number(parts[16]) },
    lab_l_centi: Number(parts[17]),
    lab_a_centi: hasExtendedLab ? Number(parts[18]) : 0,
    lab_b_centi: hasExtendedLab ? Number(parts[19]) : 0,
    luma_milli: hasExtendedLab ? Number(parts[20]) : 0,
    gain: Number(parts[hasExtendedLab ? 21 : 18]),
    integration_ms: Number(parts[hasExtendedLab ? 22 : 19]),
    led_mode: Number(parts[hasExtendedLab ? 23 : 20]),
    led_active: hasExtendedLab ? Number(parts[24]) : 0,
    health_flags: Number(parts[hasExtendedLab ? 25 : 21]),
    classifier: Number(parts[hasExtendedLab ? 26 : 22]),
    calibration_target_slot: Number(parts[hasExtendedLab ? 27 : 23]),
    calibration_samples: Number(parts[hasExtendedLab ? 28 : 24]),
  };
}

function applyTelemetryEvent(parsed) {
  const snapshot = state.snapshot;
  if (!snapshot || !parsed) {
    return;
  }
  snapshot.data.raw = parsed.raw;
  snapshot.data.norm_rgb_milli = parsed.norm_rgb_milli;
  snapshot.data.top = parsed.top;
  snapshot.data.lab_l_centi = parsed.lab_l_centi;
  snapshot.data.lab_a_centi = parsed.lab_a_centi;
  snapshot.data.lab_b_centi = parsed.lab_b_centi;
  snapshot.data.luma_milli = parsed.luma_milli;
  snapshot.data.gain = parsed.gain;
  snapshot.data.integration_ms = parsed.integration_ms;
  snapshot.data.led_mode = parsed.led_mode;
  snapshot.data.led_active = parsed.led_active;
  snapshot.data.health_flags = parsed.health_flags;
  snapshot.data.health = {
    sensor_ok: Boolean(parsed.health_flags & (1 << 0)),
    saturated: Boolean(parsed.health_flags & (1 << 1)),
    dark_valid: Boolean(parsed.health_flags & (1 << 2)),
    white_valid: Boolean(parsed.health_flags & (1 << 3)),
    calibrating: Boolean(parsed.health_flags & (1 << 4)),
    selftest_ok: Boolean(parsed.health_flags & (1 << 5)),
    auto_exposure: Boolean(parsed.health_flags & (1 << 6)),
    sensor_present: Boolean(parsed.health_flags & (1 << 7)),
  };
  snapshot.data.palette_mode = parsed.palette_mode;
  snapshot.data.detected_slot = parsed.detected_slot;
  snapshot.data.confidence_milli = parsed.confidence_milli;
  snapshot.data.classifier = parsed.classifier;
  snapshot.data.calibration_target_slot = parsed.calibration_target_slot;
  snapshot.data.calibration_samples = parsed.calibration_samples;
  renderSnapshot();
}

async function sendManualCmd() {
  if (!state.selectedSerial) {
    return;
  }
  const command = String(elements.manualCmdInput.value || "").trim();
  if (!command) {
    return;
  }
  await setDeviceMessageType("CMD");
  const payload = await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/cmd/send`, {
    command,
  });
  elements.manualCmdResponse.textContent = payload?.response || "CMD sent.";
  appendFeed(elements.eventFeed, state.eventLines, `CMD -> ${command}`);
}

async function saveEditedLabels() {
  if (!state.selectedSerial) {
    return;
  }
  const profile = collectEditedProfile();
  const payload = await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/profile`, {
    profile,
    apply_to_firmware: false,
  });
  state.profile = payload.profile || profile;
  renderPaletteEditor();
  elements.profileNote.textContent = "Host palette labels saved.";
}

async function exportProfile() {
  if (!state.selectedSerial) {
    return;
  }
  const payload = await apiGet(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/profile`);
  const blob = new Blob([JSON.stringify(payload.profile, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${state.selectedSerial}-color-profile.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function importProfile(file) {
  if (!state.selectedSerial || !file) {
    return;
  }
  const text = await file.text();
  const profile = JSON.parse(text);
  const payload = await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/profile`, {
    profile,
    apply_to_firmware: true,
  });
  state.profile = payload.profile || profile;
  await refreshCalibration();
  await refreshSnapshot();
  elements.profileNote.textContent = "Imported profile applied to host and firmware.";
}

async function reloadFirmwareCalibration() {
  await refreshCalibration();
  await refreshSnapshot();
  elements.profileNote.textContent = "Reloaded calibration data from firmware.";
}

async function restoreDefaults() {
  if (!state.selectedSerial) {
    return;
  }
  const confirmed = window.confirm("Restore firmware defaults and replace the host profile for this device?");
  if (!confirmed) {
    return;
  }
  const payload = await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/restore-defaults`, {});
  state.snapshot = payload.snapshot || null;
  state.profile = payload.profile || null;
  renderSnapshot();
  renderConfigInputs();
  renderPaletteEditor();
  renderSingleTargetOptions();
}

async function startCalibrationSession(modeKey) {
  await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/config`, {
    palette_mode: Number(modeKey),
  });
  await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/calibration/start`, {});
}

async function waitForCaptureSamples(targetCount, timeoutMs = 6000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await refreshSnapshot();
    if (Number(state.snapshot?.data?.calibration_samples || 0) >= Number(targetCount)) {
      return true;
    }
    await delay(220);
  }
  return false;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function captureTarget(target, label) {
  setCalibrationStatus(`Capturing ${label}. Keep the sensor fixed.`);
  await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/calibration/select`, {
    target,
  });
  await delay(120);
  await refreshSnapshot();
  const patchTarget = Number(elements.patchSamplesInput.value || state.snapshot?.cfg?.patch_sample_count || 12);
  setCalibrationStatus(`Collecting ${patchTarget} samples for ${label}.`);
  const ok = await waitForCaptureSamples(patchTarget, Math.max(5000, patchTarget * 350));
  if (!ok) {
    throw new Error("capture_timeout");
  }
  const payload = await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/calibration/commit`, {
    target,
  });
  state.snapshot = payload.snapshot || state.snapshot;
  state.profile = payload.profile || state.profile;
  renderSnapshot();
  renderPaletteEditor();
  appendFeed(elements.eventFeed, state.eventLines, `Committed calibration target ${label}.`);
}

async function captureSelectedTarget() {
  if (!state.selectedSerial) {
    return;
  }
  const target = String(elements.manualTargetSelect.value || "").trim();
  if (!target) {
    return;
  }
  const modeKey = String(elements.calibrationModeSelect.value || selectedModeKey());
  const isSpecialTarget = target === "DARK" || target === "WHITE";
  const titleLabel = isSpecialTarget
    ? (target === "WHITE" ? "bright" : "dark")
    : labelForSlot(modeKey, Number(target));
  const confirmed = window.confirm(
    `Confirm that the sensor is positioned over ${titleLabel} before capturing.`,
  );
  if (!confirmed) {
    setCalibrationStatus("Capture cancelled.");
    return;
  }
  try {
    await startCalibrationSession(modeKey);
    if (!isSpecialTarget) {
      await refreshSnapshot();
      const darkValid = Boolean(state.snapshot?.cal?.dark_valid);
      const whiteValid = Boolean(state.snapshot?.cal?.white_valid);
      if (!darkValid || !whiteValid) {
        setCalibrationStatus("ERROR: BEFORE CAPTURE OF COLOR IT'S NECESSARY TO CAPTURE BRIGHT AND DARK REFERENCES!");
        return;
      }
    }
    await captureTarget(target, titleLabel);
    setCalibrationStatus(`Captured ${titleLabel}.`);
  } catch (err) {
    setCalibrationStatus(`Capture failed: ${String(err?.message || err)}`);
  } finally {
    await apiPost(`/api/devices/${encodeURIComponent(state.selectedSerial)}/color/calibration/stop`, {});
    await refreshSnapshot();
  }
}

function renderSingleTargetOptions() {
  const modeKey = String(elements.calibrationModeSelect.value || selectedModeKey());
  const items = [
    { value: "DARK", label: "dark" },
    { value: "WHITE", label: "bright" },
    ...((modeProfile(modeKey)?.labels || paletteEntries(modeKey)).map((item) => ({
      value: String(item.slot),
      label: item.name,
    }))),
  ];
  const previous = elements.manualTargetSelect.value;
  elements.manualTargetSelect.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    elements.manualTargetSelect.appendChild(option);
  }
  if (items.some((item) => item.value === previous)) {
    elements.manualTargetSelect.value = previous;
  }
}

function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws/comms`);
  state.ws = ws;

  ws.onmessage = (event) => {
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch (_err) {
      return;
    }

    if (payload.type === "snapshot" && Array.isArray(payload.devices)) {
      state.devices = payload.devices.filter((item) => item.module_type === "color_module");
      renderDeviceSelect();
      return;
    }

    if (payload.type === "comms" && payload.event) {
      const commEvent = payload.event;
      const selected = selectedDevice();
      if (!selected) {
        return;
      }
      const sender = String(commEvent.sender || "").trim();
      const deviceSerial = String(commEvent.device_serial || "").trim();
      const matches = sender === selected.serial_number || deviceSerial === selected.serial_number;
      if (!matches) {
        return;
      }
      appendFeed(elements.eventFeed, state.eventLines, `${commEvent.direction || "rx"} ${commEvent.message_type || ""} ${commEvent.message || commEvent.raw_hex || ""}`);
      if (String(commEvent.message_type || "").toUpperCase() === "TELEMETRY" && typeof commEvent.message === "string") {
        const parsed = parseTelemetryLine(commEvent.message);
        if (parsed) {
          appendFeed(elements.telemetryFeed, state.telemetryLines, commEvent.message);
          applyTelemetryEvent(parsed);
        }
      }
    }
  };

  ws.onclose = () => {
    window.setTimeout(connectWebSocket, 1200);
  };
}

async function initialize() {
  await refreshPalettes();
  await refreshDevices();
  connectWebSocket();
  window.setInterval(() => {
    refreshDevices().catch(() => {});
  }, 3000);
  if (!state.selectedSerial) {
    return;
  }
  await refreshSnapshot();
  await refreshCalibration();
  await refreshProfile();
  renderSingleTargetOptions();
}

elements.deviceSelect.addEventListener("change", async (event) => {
  state.selectedSerial = String(event.target.value || "");
  syncSelectedSerialInUrl();
  state.snapshot = null;
  state.profile = null;
  state.calibration = null;
  state.telemetryLines = [];
  state.eventLines = [];
  state.telemetryStartedHere = false;
  elements.telemetryFeed.textContent = "";
  elements.eventFeed.textContent = "";
  updateTelemetryControls();
  await refreshSnapshot();
  await refreshCalibration();
  await refreshProfile();
  renderSingleTargetOptions();
});

elements.refreshSnapshotBtn.addEventListener("click", () => {
  refreshSnapshot().catch((err) => {
    appendFeed(elements.eventFeed, state.eventLines, `Snapshot refresh failed: ${err.message}`);
  });
});
elements.saveConfigBtn.addEventListener("click", () => updateConfigFromInputs().catch((err) => {
  appendFeed(elements.eventFeed, state.eventLines, `Config update failed: ${err.message}`);
}));
elements.persistConfigBtn.addEventListener("click", () => persistConfig().catch((err) => {
  appendFeed(elements.eventFeed, state.eventLines, `Save CFG failed: ${err.message}`);
}));
elements.selftestBtn.addEventListener("click", () => runSelftest().catch((err) => {
  elements.selftestResult.textContent = `Selftest failed: ${err.message}`;
}));
elements.startTelemetryBtn.addEventListener("click", () => startTelemetry().catch((err) => {
  appendFeed(elements.eventFeed, state.eventLines, `Telemetry start failed: ${err.message}`);
}));
elements.stopTelemetryBtn.addEventListener("click", () => stopTelemetry().catch((err) => {
  appendFeed(elements.eventFeed, state.eventLines, `Telemetry stop failed: ${err.message}`);
}));
elements.manualCaptureBtn.addEventListener("click", () => captureSelectedTarget().catch((err) => {
  setCalibrationStatus(`Capture failed: ${err.message}`);
}));
elements.persistCalibrationBtn.addEventListener("click", () => apiPost(
  `/api/devices/${encodeURIComponent(state.selectedSerial)}/color/save`,
  { persist_cfg: false, persist_cal: true },
).then(() => {
  appendFeed(elements.eventFeed, state.eventLines, "Calibration saved to firmware NVS.");
}).catch((err) => {
  appendFeed(elements.eventFeed, state.eventLines, `Save CAL failed: ${err.message}`);
}));
elements.saveLabelsBtn.addEventListener("click", () => saveEditedLabels().catch((err) => {
  elements.profileNote.textContent = `Failed to save labels: ${err.message}`;
}));
elements.exportProfileBtn.addEventListener("click", () => exportProfile().catch((err) => {
  elements.profileNote.textContent = `Export failed: ${err.message}`;
}));
elements.importProfileBtn.addEventListener("click", () => {
  elements.importProfileInput.click();
});
elements.importProfileInput.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) {
    return;
  }
  importProfile(file).catch((err) => {
    elements.profileNote.textContent = `Import failed: ${err.message}`;
  }).finally(() => {
    event.target.value = "";
  });
});
elements.reloadCalibrationBtn.addEventListener("click", () => reloadFirmwareCalibration().catch((err) => {
  elements.profileNote.textContent = `Reload failed: ${err.message}`;
}));
elements.restoreDefaultsBtn.addEventListener("click", () => restoreDefaults().catch((err) => {
  elements.profileNote.textContent = `Restore failed: ${err.message}`;
}));
elements.manualCmdSend.addEventListener("click", () => sendManualCmd().catch((err) => {
  elements.manualCmdResponse.textContent = `Command failed: ${err.message}`;
}));
elements.paletteModeInput.addEventListener("change", () => {
  renderPaletteEditor();
  renderSingleTargetOptions();
});
elements.calibrationModeSelect.addEventListener("change", () => {
  renderSingleTargetOptions();
});

initialize().catch((err) => {
  elements.currentColorName.textContent = "Initialization failed";
  elements.currentColorConfidence.textContent = err.message;
});
