(function () {
  "use strict";

  const MODULE_TYPE = "distance_sensor_module";
  const SELECTED_KEY = "rdk.selectedDistanceSerial";
  const HISTORY_WINDOW_MS = 30_000;

  const querySerial = new URLSearchParams(window.location.search).get("serial") || "";
  const state = {
    devices: [],
    selectedSerial: querySerial,
    snapshot: null,
    unit: "cm",
    history: [],
    lastHistoryKey: "",
    lastValidMm: null,
    lastDataClientAt: 0,
    baseAgeMs: 0,
    samplesThisSecond: 0,
    requestBusy: false,
    snapshotInFlight: false,
    streamRequestedFor: "",
    streamStoppedFor: "",
    refreshedSerial: "",
    ws: null,
    wsDisabled: false,
  };

  if (!state.selectedSerial) {
    try {
      state.selectedSerial = localStorage.getItem(SELECTED_KEY) || "";
    } catch (_err) {
    }
  }

  const $ = (id) => document.getElementById(id);
  const elements = {
    deviceSelect: $("deviceSelect"),
    connectionBadge: $("connectionBadge"),
    dataBadge: $("dataBadge"),
    rateBadge: $("rateBadge"),
    offlineBanner: $("offlineBanner"),
    refreshBtn: $("refreshBtn"),
    startStreamBtn: $("startStreamBtn"),
    stopStreamBtn: $("stopStreamBtn"),
    unitSelect: $("unitSelect"),
    distanceValue: $("distanceValue"),
    distanceUnitLabel: $("distanceUnitLabel"),
    measurementStatus: $("measurementStatus"),
    rangeTrack: $("rangeTrack"),
    rangeFill: $("rangeFill"),
    rangeMarker: $("rangeMarker"),
    rangeScaleMax: $("rangeScaleMax"),
    lastValidNote: $("lastValidNote"),
    rawDistanceValue: $("rawDistanceValue"),
    echoUsValue: $("echoUsValue"),
    sampleAgeValue: $("sampleAgeValue"),
    historyCanvas: $("historyCanvas"),
    historyEmpty: $("historyEmpty"),
    clearHistoryBtn: $("clearHistoryBtn"),
    healthValid: $("healthValid"),
    healthNoEcho: $("healthNoEcho"),
    healthEchoStuck: $("healthEchoStuck"),
    healthBelowMin: $("healthBelowMin"),
    healthAboveMax: $("healthAboveMax"),
    healthFilter: $("healthFilter"),
    healthConfig: $("healthConfig"),
    selftestBtn: $("selftestBtn"),
    selftestResult: $("selftestResult"),
    nameInput: $("nameInput"),
    sampleMsInput: $("sampleMsInput"),
    maxMmInput: $("maxMmInput"),
    filterWindowInput: $("filterWindowInput"),
    applyConfigBtn: $("applyConfigBtn"),
    actionStatus: $("actionStatus"),
    sensorModelValue: $("sensorModelValue"),
    triggerGpioValue: $("triggerGpioValue"),
    echoGpioValue: $("echoGpioValue"),
    moduleIdValue: $("moduleIdValue"),
  };

  function selectedDevice() {
    return state.devices.find(
      (device) => String(device.serial_number || "") === state.selectedSerial,
    ) || null;
  }

  function isOnline(device = selectedDevice()) {
    return String(device?.status || "").trim().toLowerCase() === "online connected";
  }

  function persistSelection() {
    try {
      if (state.selectedSerial) {
        localStorage.setItem(SELECTED_KEY, state.selectedSerial);
      } else {
        localStorage.removeItem(SELECTED_KEY);
      }
    } catch (_err) {
    }
    const url = new URL(window.location.href);
    if (state.selectedSerial) {
      url.searchParams.set("serial", state.selectedSerial);
    } else {
      url.searchParams.delete("serial");
    }
    window.history.replaceState(null, "", url);
  }

  async function apiGet(url) {
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    return payload;
  }

  async function apiPost(url, body = {}) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    return payload;
  }

  function healthFromFlags(value) {
    const flags = Math.max(0, Number(value) || 0);
    return {
      value: flags,
      valid: Boolean(flags & (1 << 0)),
      no_echo: Boolean(flags & (1 << 1)),
      echo_stuck: Boolean(flags & (1 << 2)),
      below_min: Boolean(flags & (1 << 3)),
      above_max: Boolean(flags & (1 << 4)),
      filter_active: Boolean(flags & (1 << 5)),
      config_loaded: Boolean(flags & (1 << 6)),
    };
  }

  function statusFromData(data) {
    const health = data?.health || healthFromFlags(data?.health_flags);
    if (data?.valid) return "OK";
    if (health.echo_stuck) return "ECHO_STUCK";
    if (health.no_echo) return "NO_ECHO";
    if (health.below_min) return "BELOW_MIN";
    if (health.above_max) return "ABOVE_MAX";
    return "NOT_READY";
  }

  function parseDistanceLine(line) {
    const parts = String(line || "").split(",").map((part) => part.trim());
    if (parts.length < 7 || parts[0] !== "DS") return null;
    const filteredMm = Number.parseInt(parts[1], 10);
    const rawMm = Number.parseInt(parts[2], 10);
    const echoUs = Number.parseInt(parts[3], 10);
    const validRaw = Number.parseInt(parts[4], 10);
    const healthFlags = Number(parts[5]);
    const sampleTimestampMs = Number.parseInt(parts[6], 10);
    if (
      !Number.isFinite(filteredMm)
      || !Number.isFinite(rawMm)
      || !Number.isFinite(echoUs)
      || (validRaw !== 0 && validRaw !== 1)
      || !Number.isFinite(healthFlags)
      || !Number.isFinite(sampleTimestampMs)
      || (validRaw === 1 && filteredMm < 0)
    ) {
      return null;
    }
    const valid = validRaw === 1;
    const data = {
      distance_mm: valid && filteredMm >= 0 ? filteredMm : null,
      distance_cm: valid && filteredMm >= 0 ? filteredMm / 10 : null,
      distance_m: valid && filteredMm >= 0 ? filteredMm / 1000 : null,
      raw_mm: rawMm >= 0 ? rawMm : null,
      echo_us: Math.max(0, echoUs),
      valid,
      health_flags: healthFlags,
      health: healthFromFlags(healthFlags),
      sample_timestamp_ms: sampleTimestampMs,
      age_ms: 0,
      raw: String(line || "").trim(),
    };
    data.status = statusFromData(data);
    return data;
  }

  function setBadge(element, text, kind) {
    if (!element) return;
    element.textContent = text;
    element.className = `badge ${kind || ""}`.trim();
  }

  function setBusy(active, text = "") {
    state.requestBusy = Boolean(active);
    if (text || !active) elements.actionStatus.textContent = text;
    renderControls();
  }

  function renderControls() {
    const online = isOnline();
    const hasDevice = Boolean(selectedDevice());
    elements.refreshBtn.disabled = !online || state.requestBusy;
    elements.startStreamBtn.disabled = !online || state.requestBusy;
    elements.stopStreamBtn.disabled = !hasDevice || state.requestBusy;
    elements.applyConfigBtn.disabled = !online || state.requestBusy;
    elements.selftestBtn.disabled = !online || state.requestBusy;
    for (const input of [
      elements.nameInput,
      elements.sampleMsInput,
      elements.maxMmInput,
      elements.filterWindowInput,
    ]) {
      input.disabled = !online || state.requestBusy;
    }
  }

  function resetViewState() {
    state.snapshot = null;
    state.history = [];
    state.lastHistoryKey = "";
    state.lastValidMm = null;
    state.lastDataClientAt = 0;
    state.baseAgeMs = 0;
    state.samplesThisSecond = 0;
    state.refreshedSerial = "";
    state.streamRequestedFor = "";
    state.streamStoppedFor = "";
    renderData();
    renderConfig();
    renderInfo();
    renderSelftest();
    drawHistory();
  }

  function renderDevices() {
    const previous = state.selectedSerial;
    elements.deviceSelect.innerHTML = "";
    if (state.devices.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No distance sensor detected";
      elements.deviceSelect.appendChild(option);
      elements.deviceSelect.disabled = true;
      state.selectedSerial = "";
      resetViewState();
      persistSelection();
      renderConnection();
      return;
    }

    elements.deviceSelect.disabled = false;
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select a distance sensor";
    elements.deviceSelect.appendChild(placeholder);
    for (const device of state.devices) {
      const option = document.createElement("option");
      option.value = String(device.serial_number || "");
      const name = String(device.name || MODULE_TYPE).trim() || MODULE_TYPE;
      const live = isOnline(device) ? "online" : "offline";
      option.textContent = `${name} · ${device.serial_number} · ${live}`;
      elements.deviceSelect.appendChild(option);
    }

    if (!state.devices.some((device) => device.serial_number === previous)) {
      state.selectedSerial = "";
      resetViewState();
    }
    elements.deviceSelect.value = state.selectedSerial || "";
    persistSelection();
    renderConnection();
  }

  function renderConnection() {
    const device = selectedDevice();
    const online = isOnline(device);
    if (!device) {
      setBadge(elements.connectionBadge, "disconnected", "nodata");
      elements.offlineBanner.hidden = false;
      elements.offlineBanner.classList.remove("is-waiting");
      elements.offlineBanner.textContent =
        "No echo / disconnected. Connect a distance sensor module to begin.";
    } else if (!online) {
      setBadge(elements.connectionBadge, "offline", "nodata");
      elements.offlineBanner.hidden = false;
      elements.offlineBanner.classList.remove("is-waiting");
      elements.offlineBanner.textContent =
        `${device.name || MODULE_TYPE} is disconnected. Cached readings remain view-only.`;
    } else {
      setBadge(elements.connectionBadge, "online", "live");
      const data = state.snapshot?.data;
      if (data && !data.valid) {
        elements.offlineBanner.hidden = false;
        elements.offlineBanner.classList.add("is-waiting");
        elements.offlineBanner.textContent =
          "Module connected, but no valid echo is currently available.";
      } else {
        elements.offlineBanner.hidden = true;
      }
    }
    renderControls();
  }

  function formatDistance(mm, unit = state.unit) {
    if (!Number.isFinite(Number(mm))) return "—";
    const value = Number(mm);
    if (unit === "mm") return `${Math.round(value)}`;
    if (unit === "m") return (value / 1000).toFixed(3);
    return (value / 10).toFixed(1);
  }

  function humanStatus(status) {
    const labels = {
      OK: "Valid echo",
      NO_ECHO: "No echo received",
      ECHO_STUCK: "ECHO signal stuck / pulse timeout",
      BELOW_MIN: "Object is below the 20 mm minimum",
      ABOVE_MAX: "Object is beyond the configured range",
      NOT_READY: "Sensor is not ready",
    };
    return labels[status] || String(status || "No measurement");
  }

  function effectiveAgeMs() {
    if (!state.lastDataClientAt) return null;
    return state.baseAgeMs + (performance.now() - state.lastDataClientAt);
  }

  function renderAge() {
    const age = effectiveAgeMs();
    elements.sampleAgeValue.textContent =
      age == null ? "—" : age < 1000 ? `${Math.round(age)} ms` : `${(age / 1000).toFixed(1)} s`;
    if (!state.snapshot?.data) {
      setBadge(
        elements.dataBadge,
        isOnline() ? "no data" : "disconnected",
        "nodata",
      );
      return;
    }
    if (!isOnline()) {
      setBadge(elements.dataBadge, "disconnected", "nodata");
    } else if (age == null) {
      setBadge(elements.dataBadge, "no data", "nodata");
    } else if (age < 1500) {
      setBadge(elements.dataBadge, state.snapshot.data.valid ? "live" : "no echo", state.snapshot.data.valid ? "live" : "stale");
    } else if (age < 5000) {
      setBadge(elements.dataBadge, "stale", "stale");
    } else {
      setBadge(elements.dataBadge, "no data", "nodata");
    }
  }

  function renderHealth(data) {
    const health = data?.health || healthFromFlags(data?.health_flags);
    const rows = [
      [elements.healthValid, "valid", Boolean(data?.valid || health.valid), false],
      [elements.healthNoEcho, "noEcho", health.no_echo, true],
      [elements.healthEchoStuck, "echoStuck", health.echo_stuck, true],
      [elements.healthBelowMin, "belowMin", health.below_min, true],
      [elements.healthAboveMax, "aboveMax", health.above_max, true],
      [elements.healthFilter, "filterActive", health.filter_active, false],
      [elements.healthConfig, "configLoaded", health.config_loaded, false],
    ];
    for (const [valueElement, flagName, active, fault] of rows) {
      valueElement.textContent = active ? "on" : "off";
      const row = document.querySelector(`.health-item[data-flag="${flagName}"]`);
      row?.classList.toggle("is-on", Boolean(active));
      row?.classList.toggle("is-fault", Boolean(fault));
    }
  }

  function renderData() {
    const data = state.snapshot?.data;
    const cfg = state.snapshot?.cfg;
    const maxMm = Math.max(20, Number(cfg?.max_mm) || 4000);
    elements.rangeScaleMax.textContent = `${maxMm} mm`;
    elements.rangeTrack.setAttribute("aria-valuemax", String(maxMm));

    if (!data) {
      elements.distanceValue.textContent = "—";
      elements.distanceValue.classList.add("is-invalid");
      elements.distanceUnitLabel.textContent = state.unit;
      elements.measurementStatus.textContent = isOnline()
        ? "Waiting for a measurement"
        : "No echo / disconnected";
      elements.measurementStatus.className = "measurement-status is-error";
      elements.rawDistanceValue.textContent = "—";
      elements.echoUsValue.textContent = "—";
      elements.rangeFill.style.width = "0%";
      elements.rangeMarker.style.opacity = "0";
      elements.rangeTrack.removeAttribute("aria-valuenow");
      elements.rangeTrack.setAttribute("aria-valuetext", "No valid measurement");
      elements.lastValidNote.textContent = "No valid echo received yet.";
      renderHealth(null);
      renderAge();
      return;
    }

    const valid = Boolean(data.valid) && Number.isFinite(Number(data.distance_mm));
    const status = data.status || statusFromData(data);
    elements.distanceValue.textContent = valid
      ? formatDistance(Number(data.distance_mm))
      : "—";
    elements.distanceValue.classList.toggle("is-invalid", !valid);
    elements.distanceUnitLabel.textContent = state.unit;
    elements.measurementStatus.textContent = humanStatus(status);
    elements.measurementStatus.className =
      `measurement-status ${valid ? "is-ok" : "is-error"}`;
    elements.rawDistanceValue.textContent =
      data.raw_mm != null && Number.isFinite(Number(data.raw_mm))
        ? `${Math.round(Number(data.raw_mm))} mm`
        : "—";
    elements.echoUsValue.textContent =
      Number.isFinite(Number(data.echo_us)) && Number(data.echo_us) > 0
        ? `${Math.round(Number(data.echo_us))} µs`
        : "—";

    if (valid) {
      const percent = Math.max(0, Math.min(100, Number(data.distance_mm) / maxMm * 100));
      elements.rangeFill.style.width = `${percent}%`;
      elements.rangeMarker.style.left = `${percent}%`;
      elements.rangeMarker.style.opacity = "1";
      elements.rangeTrack.setAttribute(
        "aria-valuenow",
        String(Math.round(Number(data.distance_mm))),
      );
      elements.rangeTrack.setAttribute(
        "aria-valuetext",
        `${formatDistance(Number(data.distance_mm))} ${state.unit}`,
      );
      state.lastValidMm = Number(data.distance_mm);
    } else {
      elements.rangeFill.style.width = "0%";
      elements.rangeMarker.style.opacity = "0";
      elements.rangeTrack.removeAttribute("aria-valuenow");
      elements.rangeTrack.setAttribute("aria-valuetext", humanStatus(status));
    }

    elements.lastValidNote.textContent = state.lastValidMm == null
      ? "No valid echo received yet."
      : `Last valid echo: ${formatDistance(state.lastValidMm)} ${state.unit}`;
    renderHealth(data);
    renderAge();
    renderConnection();
  }

  function setInputValue(input, value) {
    if (!input || document.activeElement === input || value == null) return;
    input.value = String(value);
  }

  function renderConfig() {
    const cfg = state.snapshot?.cfg;
    if (!cfg) {
      elements.nameInput.value = "";
      elements.sampleMsInput.value = "100";
      elements.maxMmInput.value = "4000";
      elements.filterWindowInput.value = "3";
      return;
    }
    setInputValue(elements.nameInput, cfg.name);
    setInputValue(elements.sampleMsInput, cfg.sample_ms);
    setInputValue(elements.maxMmInput, cfg.max_mm);
    setInputValue(elements.filterWindowInput, cfg.filter_window);
  }

  function renderInfo() {
    const info = state.snapshot?.info;
    if (!info) {
      elements.sensorModelValue.textContent = "HC-SR04";
      elements.triggerGpioValue.textContent = "GPIO 3";
      elements.echoGpioValue.textContent = "GPIO 10";
      elements.moduleIdValue.textContent = "0x14";
      return;
    }
    elements.sensorModelValue.textContent = info.sensor_model || "HC-SR04";
    elements.triggerGpioValue.textContent =
      Number.isFinite(Number(info.trigger_gpio)) ? `GPIO ${info.trigger_gpio}` : "GPIO 3";
    elements.echoGpioValue.textContent =
      Number.isFinite(Number(info.echo_gpio)) ? `GPIO ${info.echo_gpio}` : "GPIO 10";
    elements.moduleIdValue.textContent =
      Number.isFinite(Number(info.module_id))
        ? `0x${Number(info.module_id).toString(16).toUpperCase().padStart(2, "0")}`
        : "0x14";
  }

  function renderSelftest() {
    const selftest = state.snapshot?.selftest;
    if (!selftest) {
      elements.selftestResult.textContent = "Self-test not run.";
      elements.selftestResult.classList.remove("is-error");
      return;
    }
    const distance = selftest.distance_mm != null && Number.isFinite(Number(selftest.distance_mm))
      ? ` at ${selftest.distance_mm} mm`
      : "";
    elements.selftestResult.textContent =
      selftest.ok ? `Passed${distance}.` : "Failed. Check wiring and ECHO level.";
    elements.selftestResult.classList.toggle("is-error", !selftest.ok);
  }

  function historyKey(data) {
    return [
      data?.sample_timestamp_ms,
      data?.distance_mm,
      data?.raw_mm,
      data?.health_flags,
    ].join(":");
  }

  function recordData(data) {
    if (!data) return;
    const key = historyKey(data);
    if (key === state.lastHistoryKey) return;
    state.lastHistoryKey = key;
    state.samplesThisSecond += 1;
    state.history.push({
      at: Date.now(),
      value: data.valid && Number.isFinite(Number(data.distance_mm))
        ? Number(data.distance_mm)
        : null,
    });
    const cutoff = Date.now() - HISTORY_WINDOW_MS;
    state.history = state.history.filter((item) => item.at >= cutoff);
    drawHistory();
  }

  function drawHistory() {
    const canvas = elements.historyCanvas;
    const bounds = canvas.getBoundingClientRect();
    const width = Math.max(1, bounds.width);
    const height = Math.max(1, bounds.height);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const now = Date.now();
    const cutoff = now - HISTORY_WINDOW_MS;
    state.history = state.history.filter((item) => item.at >= cutoff);
    elements.historyEmpty.hidden = state.history.length > 0;
    if (state.history.length === 0) return;

    const styles = getComputedStyle(document.documentElement);
    const gridColor = styles.getPropertyValue("--border").trim() || "#2b342b";
    const lineColor = styles.getPropertyValue("--accent").trim() || "#a8d5a8";
    const faultColor = styles.getPropertyValue("--danger").trim() || "#f28b82";
    const pad = { left: 42, right: 12, top: 14, bottom: 24 };
    const graphWidth = Math.max(1, width - pad.left - pad.right);
    const graphHeight = Math.max(1, height - pad.top - pad.bottom);
    const maxMm = Math.max(20, Number(state.snapshot?.cfg?.max_mm) || 4000);

    ctx.lineWidth = 1;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = styles.getPropertyValue("--text-dim").trim() || "#8d9587";
    ctx.font = "10px ui-monospace, monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i += 1) {
      const y = pad.top + graphHeight * i / 4;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(width - pad.right, y);
      ctx.stroke();
      const label = Math.round(maxMm * (1 - i / 4));
      ctx.fillText(`${label}`, pad.left - 7, y);
    }

    const xFor = (timestamp) =>
      pad.left + Math.max(0, Math.min(1, (timestamp - cutoff) / HISTORY_WINDOW_MS)) * graphWidth;
    const yFor = (value) =>
      pad.top + (1 - Math.max(0, Math.min(1, value / maxMm))) * graphHeight;

    ctx.lineWidth = 2;
    ctx.strokeStyle = lineColor;
    ctx.beginPath();
    let drawing = false;
    for (const sample of state.history) {
      if (sample.value == null) {
        drawing = false;
        continue;
      }
      const x = xFor(sample.at);
      const y = yFor(sample.value);
      if (!drawing) {
        ctx.moveTo(x, y);
        drawing = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();

    ctx.fillStyle = faultColor;
    for (const sample of state.history) {
      if (sample.value != null) continue;
      const x = xFor(sample.at);
      ctx.fillRect(x - 1, pad.top + graphHeight - 5, 2, 5);
    }

    ctx.fillStyle = styles.getPropertyValue("--text-dim").trim() || "#8d9587";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText("−30 s", pad.left, height - pad.bottom + 8);
    ctx.fillText("now", width - pad.right, height - pad.bottom + 8);
  }

  function applySnapshot(payload, options = {}) {
    if (!payload || String(payload.serial || "") !== state.selectedSerial) return;
    const previous = state.snapshot || {};
    state.snapshot = {
      ...previous,
      ...payload,
      data: payload.data != null ? payload.data : previous.data,
      cfg: payload.cfg != null ? payload.cfg : previous.cfg,
      info: payload.info != null ? payload.info : previous.info,
      selftest: payload.selftest != null ? payload.selftest : previous.selftest,
    };
    if (payload.data) {
      payload.data.status = payload.data.status || statusFromData(payload.data);
      payload.data.health = payload.data.health || healthFromFlags(payload.data.health_flags);
      state.baseAgeMs = Math.max(0, Number(payload.data.age_ms) || 0);
      state.lastDataClientAt = performance.now();
      if (options.record !== false) recordData(payload.data);
    }
    renderData();
    renderConfig();
    renderInfo();
    renderSelftest();
    drawHistory();
  }

  async function loadSnapshot() {
    if (!state.selectedSerial || state.snapshotInFlight) return;
    state.snapshotInFlight = true;
    try {
      const payload = await apiGet(
        `/api/devices/${encodeURIComponent(state.selectedSerial)}/distance-sensor/snapshot`,
      );
      applySnapshot(payload);
    } catch (err) {
      elements.actionStatus.textContent = `Snapshot unavailable: ${err.message}`;
    } finally {
      state.snapshotInFlight = false;
    }
  }

  async function refreshFromDevice(silent = false) {
    if (!state.selectedSerial || !isOnline()) return;
    if (!silent) setBusy(true, "Reading module…");
    try {
      const payload = await apiPost(
        `/api/devices/${encodeURIComponent(state.selectedSerial)}/distance-sensor/refresh`,
      );
      applySnapshot(payload);
      if (!silent) elements.actionStatus.textContent = "Module refreshed.";
    } catch (err) {
      if (!silent) elements.actionStatus.textContent = `Refresh failed: ${err.message}`;
    } finally {
      if (!silent) setBusy(false, elements.actionStatus.textContent);
    }
  }

  async function applyConfig() {
    if (!state.selectedSerial || !isOnline()) return;
    const payload = {
      name: String(elements.nameInput.value || "").trim(),
      sample_ms: Number.parseInt(elements.sampleMsInput.value, 10),
      max_mm: Number.parseInt(elements.maxMmInput.value, 10),
      filter_window: Number.parseInt(elements.filterWindowInput.value, 10),
      save: true,
    };
    setBusy(true, "Applying configuration…");
    try {
      const response = await apiPost(
        `/api/devices/${encodeURIComponent(state.selectedSerial)}/distance-sensor/config`,
        payload,
      );
      applySnapshot(response);
      elements.actionStatus.textContent = "Configuration applied and saved.";
    } catch (err) {
      elements.actionStatus.textContent = `Configuration failed: ${err.message}`;
    } finally {
      setBusy(false, elements.actionStatus.textContent);
    }
  }

  async function runSelftest() {
    if (!state.selectedSerial || !isOnline()) return;
    setBusy(true, "Running self-test…");
    try {
      const payload = await apiPost(
        `/api/devices/${encodeURIComponent(state.selectedSerial)}/distance-sensor/selftest`,
      );
      applySnapshot(payload);
      elements.actionStatus.textContent = payload.selftest?.ok
        ? "Self-test passed."
        : "Self-test completed with a fault.";
    } catch (err) {
      elements.actionStatus.textContent = `Self-test failed: ${err.message}`;
    } finally {
      setBusy(false, elements.actionStatus.textContent);
    }
  }

  async function startStream(silent = false) {
    if (!state.selectedSerial || !isOnline()) return;
    if (!silent) setBusy(true, "Starting stream…");
    try {
      await apiPost(
        `/api/devices/${encodeURIComponent(state.selectedSerial)}/distance-sensor/stream/start`,
      );
      state.streamRequestedFor = state.selectedSerial;
      state.streamStoppedFor = "";
      if (!silent) elements.actionStatus.textContent = "Telemetry stream started.";
    } catch (err) {
      if (!silent) elements.actionStatus.textContent = `Stream start failed: ${err.message}`;
    } finally {
      if (!silent) setBusy(false, elements.actionStatus.textContent);
    }
  }

  async function stopStream() {
    if (!state.selectedSerial) return;
    setBusy(true, "Stopping stream…");
    try {
      await apiPost(
        `/api/devices/${encodeURIComponent(state.selectedSerial)}/distance-sensor/stream/stop`,
      );
      state.streamRequestedFor = "";
      state.streamStoppedFor = state.selectedSerial;
      elements.actionStatus.textContent = "Telemetry stream stopped.";
    } catch (err) {
      elements.actionStatus.textContent = `Stream stop failed: ${err.message}`;
    } finally {
      setBusy(false, elements.actionStatus.textContent);
    }
  }

  async function refreshDevices() {
    try {
      const payload = await apiGet("/api/devices");
      const before = state.selectedSerial;
      state.devices = (Array.isArray(payload.devices) ? payload.devices : [])
        .filter((device) =>
          String(device.module_type || "").trim().toLowerCase() === MODULE_TYPE,
        );
      renderDevices();
      if (!state.selectedSerial) {
        state.snapshot = null;
        renderData();
        return;
      }
      if (before !== state.selectedSerial) {
        state.snapshot = null;
      }
      await loadSnapshot();
      if (isOnline() && state.refreshedSerial !== state.selectedSerial) {
        state.refreshedSerial = state.selectedSerial;
        await refreshFromDevice(true);
      }
      if (
        isOnline()
        && state.streamRequestedFor !== state.selectedSerial
        && state.streamStoppedFor !== state.selectedSerial
      ) {
        await startStream(true);
      }
    } catch (_err) {
      state.devices = [];
      renderDevices();
      renderData();
    }
  }

  function connectWebSocket() {
    if (state.wsDisabled) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${location.host}/ws/comms`);
    state.ws = ws;
    ws.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_err) {
        return;
      }
      if (payload.type === "disabled") {
        state.wsDisabled = true;
        state.ws = null;
        try {
          ws.close();
        } catch (_err) {
        }
        return;
      }
      if (payload.type === "snapshot" && Array.isArray(payload.devices)) {
        state.devices = payload.devices.filter((device) =>
          String(device.module_type || "").trim().toLowerCase() === MODULE_TYPE,
        );
        renderDevices();
        return;
      }
      if (payload.type !== "comms" || !payload.event) return;
      const message = String(payload.event.message || "").trim();
      const eventSerial = String(
        payload.event.device_serial || payload.event.sender || "",
      ).trim();
      if (eventSerial !== state.selectedSerial || !message.startsWith("DS,")) return;
      const data = parseDistanceLine(message);
      if (!data) return;
      applySnapshot(
        {
          serial: state.selectedSerial,
          data,
        },
        { record: true },
      );
    };
    ws.onclose = () => {
      if (state.ws === ws && !state.wsDisabled) {
        window.setTimeout(connectWebSocket, 1200);
      }
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch (_err) {
      }
    };
  }

  async function switchDevice(serial) {
    state.selectedSerial = String(serial || "");
    resetViewState();
    persistSelection();
    renderConnection();
    if (!state.selectedSerial) return;
    await loadSnapshot();
    if (isOnline()) {
      state.refreshedSerial = state.selectedSerial;
      await refreshFromDevice(true);
      await startStream(true);
    }
  }

  elements.deviceSelect.addEventListener("change", (event) => {
    void switchDevice(event.target.value);
  });
  elements.unitSelect.addEventListener("change", (event) => {
    state.unit = String(event.target.value || "cm");
    renderData();
  });
  elements.refreshBtn.addEventListener("click", () => void refreshFromDevice(false));
  elements.startStreamBtn.addEventListener("click", () => void startStream(false));
  elements.stopStreamBtn.addEventListener("click", () => void stopStream());
  elements.applyConfigBtn.addEventListener("click", () => void applyConfig());
  elements.selftestBtn.addEventListener("click", () => void runSelftest());
  elements.clearHistoryBtn.addEventListener("click", () => {
    state.history = [];
    state.lastHistoryKey = "";
    drawHistory();
  });

  const historyResizeObserver = new ResizeObserver(drawHistory);
  historyResizeObserver.observe(elements.historyCanvas.parentElement);

  async function start() {
    renderConnection();
    renderData();
    renderControls();
    drawHistory();
    await refreshDevices();
    connectWebSocket();
    window.setInterval(() => void refreshDevices(), 2000);
    window.setInterval(() => void loadSnapshot(), 400);
    window.setInterval(renderAge, 250);
    window.setInterval(() => {
      elements.rateBadge.textContent = `${state.samplesThisSecond} Hz`;
      state.samplesThisSecond = 0;
    }, 1000);
  }

  void start();
})();
