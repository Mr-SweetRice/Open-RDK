const state = {
  devices: [],
  selectedSerial: null,
  events: [],
  ws: null,
  clearAfterLine: 0,
  autoScrollEnabled: true,
  activeMessageType: "CMD",
  supportedMessageTypes: [],
  activeBaudRate: 115200,
  supportedBaudRates: [],
  activeTractionOutValue: 0,
  tractionOutSending: false,
  activeCmdText: "GET PID RPM",
  cmdSending: false,
};

const TRACTION_OUT_MESSAGE_TYPE = "TRACTION_OUT";
const CMD_MESSAGE_TYPE = "CMD";

const elements = {
  devices: document.getElementById("devices"),
  deviceCount: document.getElementById("deviceCount"),
  eventCount: document.getElementById("eventCount"),
  selectedTitle: document.getElementById("selectedTitle"),
  commsTable: document.getElementById("commsTable"),
  wsStatus: document.getElementById("wsStatus"),
  clearStream: document.getElementById("clearStream"),
  autoScrollToggle: document.getElementById("autoScrollToggle"),
  messageTypeSelect: document.getElementById("messageTypeSelect"),
  baudRateSelect: document.getElementById("baudRateSelect"),
  telemetryStart: document.getElementById("telemetryStart"),
  telemetryStop: document.getElementById("telemetryStop"),
  clearDevices: document.getElementById("clearDevices"),
  tractionOutValueInput: document.getElementById("tractionOutValueInput"),
  tractionOutSend: document.getElementById("tractionOutSend"),
  cmdInput: document.getElementById("cmdInput"),
  cmdSend: document.getElementById("cmdSend"),
};

function setWsStatus(isOnline) {
  elements.wsStatus.textContent = isOnline ? "online" : "offline";
  elements.wsStatus.className = `status ${isOnline ? "online" : "offline"}`;
}

function resolveModule(device) {
  return device.module_type || "NOT-RDK-MODULE";
}

function resolveName(device) {
  return (device.name || "").trim() || resolveModule(device);
}

function resolvePort(device) {
  return device.device_node || "not attached";
}

function selectedDevice() {
  if (!state.selectedSerial) {
    return null;
  }
  return state.devices.find((item) => item.serial_number === state.selectedSerial) || null;
}

function isOnlineConnected(device) {
  return normalizeState(device?.status) === "online connected";
}

function activeTelemetryDevice() {
  return selectedDevice();
}

function normalizeState(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizeMessageType(value) {
  return String(value || "").trim().toUpperCase();
}

function isTelemetryMessageType(value) {
  return normalizeMessageType(value) === "TELEMETRY";
}

function isTractionOutMessageType(value) {
  return normalizeMessageType(value) === TRACTION_OUT_MESSAGE_TYPE;
}

function isCmdMessageType(value) {
  return normalizeMessageType(value) === CMD_MESSAGE_TYPE;
}

function resolveDeviceMessageType(device) {
  const normalized = normalizeMessageType(device?.message_type);
  if (normalized) {
    return normalized;
  }
  return "CMD";
}

function syncSelectedDeviceMessageType() {
  const selected = selectedDevice();
  if (!selected?.serial_number) {
    state.activeMessageType = "CMD";
    return;
  }
  state.activeMessageType = resolveDeviceMessageType(selected);
}

function normalizeTractionOutValue(value) {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  if (parsed < 0) {
    return 0;
  }
  if (parsed > 100) {
    return 100;
  }
  return parsed;
}

function normalizeCmdText(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "GET PID RPM";
  }
  return text.slice(0, 200);
}

function resolveDeviceTractionOutValue(device) {
  return normalizeTractionOutValue(device?.traction_out_value ?? 0);
}

function syncSelectedDeviceTractionOutValue() {
  const selected = selectedDevice();
  if (!selected?.serial_number) {
    state.activeTractionOutValue = 0;
    return;
  }
  state.activeTractionOutValue = resolveDeviceTractionOutValue(selected);
}

function normalizeBaudRate(value) {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 115200;
  }
  return parsed;
}

function statusStateClass(status) {
  const value = normalizeState(status);
  if (value === "online connected") {
    return "is-live";
  }
  if (value === "offline disconnected") {
    return "is-offline";
  }
  return "is-neutral";
}

function linkStateClass(linkStatus) {
  const value = normalizeState(linkStatus);
  if (value === "live") {
    return "is-live";
  }
  if (value === "not live") {
    return "is-offline";
  }
  return "is-neutral";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function isEventForSelectedDevice(event) {
  if (!state.selectedSerial) {
    return true;
  }
  const sender = String(event?.sender || "").trim();
  const deviceSerial = String(event?.device_serial || "").trim();
  return sender === state.selectedSerial || deviceSerial === state.selectedSerial;
}

function filteredEvents() {
  const list = state.events
    .filter(isEventForSelectedDevice)
    .filter((event) => Number(event.line || 0) > state.clearAfterLine);
  return list.slice(-1000);
}

function resolveDirection(event) {
  const explicit = normalizeState(event.direction);
  if (explicit === "tx" || explicit === "rx") {
    return explicit;
  }
  return event.sender === "host" ? "tx" : "rx";
}

function resolveEventType(event) {
  const messageType = normalizeMessageType(event.message_type);
  if (messageType) {
    return messageType;
  }
  return "-";
}

function resolveEventMessage(event) {
  const message = event.message;
  if (typeof message === "string" && message.length > 0) {
    return message;
  }
  return event.raw_hex || "";
}

function resolveEventSeq(event) {
  const absolute = Number(event.seq_abs);
  if (Number.isFinite(absolute)) {
    return absolute;
  }
  const wrapped = Number(event.seq);
  if (Number.isFinite(wrapped)) {
    return wrapped;
  }
  return "-";
}

function resolveEventLatency(event) {
  const value = Number(event.latency_ms);
  if (!Number.isFinite(value) || value < 0) {
    return "-";
  }
  return `${value.toFixed(3)} ms`;
}

function isUnstableEvent(event) {
  if (event?.retry === true) {
    return true;
  }
  const errorKind = normalizeState(event?.error_kind);
  return errorKind.includes("timeout");
}

function renderEvents() {
  const list = filteredEvents();
  elements.eventCount.textContent = `${list.length} lines`;
  elements.commsTable.innerHTML = "";

  if (list.length === 0) {
    elements.commsTable.innerHTML = `<div class="empty">No comms data for current filter.</div>`;
    return;
  }

  const fragment = document.createDocumentFragment();
  for (let i = 0; i < list.length; i += 1) {
    const event = list[i];
    const row = document.createElement("div");
    const direction = resolveDirection(event);
    const displayLine = i;
    const seq = resolveEventSeq(event);
    const latency = resolveEventLatency(event);
    const unstable = isUnstableEvent(event);
    row.className = `row ${unstable ? "unstable" : ""}`.trim();
    row.innerHTML = `
      <span class="line">#${displayLine}</span>
      <span class="dir ${direction}">${direction.toUpperCase()}</span>
      <span class="sender">${escapeHtml(event.sender || "-")}</span>
      <span class="mtype">${escapeHtml(resolveEventType(event))}</span>
      <span class="seq">${escapeHtml(seq)}</span>
      <span class="lat">${escapeHtml(latency)}</span>
      <span class="raw">${escapeHtml(resolveEventMessage(event))}</span>
    `;
    fragment.appendChild(row);
  }
  elements.commsTable.appendChild(fragment);

  if (state.autoScrollEnabled) {
    elements.commsTable.scrollTop = elements.commsTable.scrollHeight;
  }
}

function renderDevices() {
  elements.devices.innerHTML = "";
  elements.deviceCount.textContent = `${state.devices.length}`;

  const fragment = document.createDocumentFragment();
  for (const device of state.devices) {
    const selected = state.selectedSerial === device.serial_number;
    const node = document.createElement("div");
    node.className = `device-item ${selected ? "selected" : ""}`;
    const lastErrorKind = String(device.last_error_kind || "").trim() || "-";
    const lastErrorAt = String(device.last_error_at || "").trim() || "-";
    node.innerHTML = `
      <div class="device-title">
        <span class="device-name">${escapeHtml(resolveName(device))}</span>
        <span class="module">${escapeHtml(resolveModule(device))}</span>
      </div>
      <div class="device-subtitle">
        <span class="serial">${escapeHtml(device.serial_number || "-")}</span>
        <button class="rename-btn" type="button">Rename</button>
      </div>
      <div class="device-meta">
        <span class="state ${statusStateClass(device.status)}">${escapeHtml(
      device.status || "-",
    )}</span>
        <span class="state ${linkStateClass(device.link_status)}">${escapeHtml(
      device.link_status || "-",
    )}</span>
      </div>
      <div class="device-port">port: ${escapeHtml(resolvePort(device))}</div>
      <div class="device-extra">
        mode: ${escapeHtml(resolveDeviceMessageType(device))} |
        out: ${escapeHtml(resolveDeviceTractionOutValue(device))}% |
        errors(streak): ${escapeHtml(Number(device.error_count || 0))} |
        last_error: ${escapeHtml(lastErrorKind)} @ ${escapeHtml(lastErrorAt)} |
        telemetry: ${escapeHtml(device.telemetry_active ? "active" : "idle")}
      </div>
    `;
    node.addEventListener("click", () => {
      state.selectedSerial =
        state.selectedSerial === device.serial_number ? null : device.serial_number;
      syncSelectedDeviceMessageType();
      syncSelectedDeviceTractionOutValue();
      updateSelectedTitle();
      renderDevices();
      renderEvents();
      renderMessageTypeSelect();
      renderTractionOutInput();
      renderTelemetryControls();
    });
    const renameButton = node.querySelector(".rename-btn");
    if (renameButton) {
      renameButton.addEventListener("click", (event) => {
        event.stopPropagation();
        renameDevice(device);
      });
    }
    fragment.appendChild(node);
  }
  elements.devices.appendChild(fragment);
  renderTractionOutInput();
  renderCmdInput();
  renderTelemetryControls();
}

function updateSelectedTitle() {
  if (!state.selectedSerial) {
    elements.selectedTitle.textContent = "Comms Stream (all devices)";
    return;
  }
  elements.selectedTitle.textContent = `Comms Stream (${state.selectedSerial})`;
}

function clearVisualStream() {
  const last = state.events[state.events.length - 1];
  state.clearAfterLine = Number(last?.line || 0);
  renderEvents();
}

function renderAutoScrollToggle() {
  const enabled = state.autoScrollEnabled;
  elements.autoScrollToggle.textContent = enabled
    ? "Auto-scroll: On"
    : "Auto-scroll: Off";
  elements.autoScrollToggle.classList.toggle("is-on", enabled);
}

function toggleAutoScroll() {
  state.autoScrollEnabled = !state.autoScrollEnabled;
  renderAutoScrollToggle();
  if (state.autoScrollEnabled) {
    elements.commsTable.scrollTop = elements.commsTable.scrollHeight;
  }
}

function renderMessageTypeSelect() {
  if (!elements.messageTypeSelect) {
    return;
  }
  const selected = selectedDevice();
  const hasSelected = Boolean(selected?.serial_number);
  const supported = Array.isArray(state.supportedMessageTypes)
    ? state.supportedMessageTypes
    : [];
  const options = supported
    .map((item) => normalizeMessageType(item?.name))
    .filter((item) => Boolean(item));
  if (options.length === 0) {
    options.push(normalizeMessageType(state.activeMessageType || "TEST"));
  }

  const unique = [...new Set(options)];
  elements.messageTypeSelect.innerHTML = "";
  for (const typeName of unique) {
    const option = document.createElement("option");
    option.value = typeName;
    option.textContent = typeName;
    elements.messageTypeSelect.appendChild(option);
  }

  const active = normalizeMessageType(state.activeMessageType);
  if (unique.includes(active)) {
    elements.messageTypeSelect.value = active;
  } else if (unique.length > 0) {
    elements.messageTypeSelect.value = unique[0];
    state.activeMessageType = unique[0];
  }
  elements.messageTypeSelect.disabled = !hasSelected;
}

function renderTelemetryControls() {
  const modeIsTelemetry = isTelemetryMessageType(state.activeMessageType);
  const device = activeTelemetryDevice();
  const enabled = modeIsTelemetry && Boolean(device?.serial_number);

  if (elements.telemetryStart) {
    elements.telemetryStart.disabled = !enabled;
  }
  if (elements.telemetryStop) {
    elements.telemetryStop.disabled = !enabled;
  }
}

function renderTractionOutInput() {
  if (!elements.tractionOutValueInput) {
    return;
  }
  const hasSelected = Boolean(selectedDevice()?.serial_number);
  const enabled = hasSelected && isTractionOutMessageType(state.activeMessageType);
  elements.tractionOutValueInput.disabled = !enabled;
  const isTyping = document.activeElement === elements.tractionOutValueInput;
  if (!isTyping) {
    elements.tractionOutValueInput.value = String(
      normalizeTractionOutValue(state.activeTractionOutValue),
    );
  }
  if (elements.tractionOutSend) {
    elements.tractionOutSend.disabled = !enabled || state.tractionOutSending;
    elements.tractionOutSend.textContent = state.tractionOutSending ? "Sending..." : "OK";
  }
}

function renderCmdInput() {
  if (!elements.cmdInput) {
    return;
  }
  const hasSelected = Boolean(selectedDevice()?.serial_number);
  const enabled = hasSelected && isCmdMessageType(state.activeMessageType);
  elements.cmdInput.disabled = !enabled;
  const isTyping = document.activeElement === elements.cmdInput;
  if (!isTyping) {
    elements.cmdInput.value = String(normalizeCmdText(state.activeCmdText));
  }
  if (elements.cmdSend) {
    elements.cmdSend.disabled = !enabled || state.cmdSending;
    elements.cmdSend.textContent = state.cmdSending ? "Sending..." : "Send";
  }
}

function renderBaudRateSelect() {
  if (!elements.baudRateSelect) {
    return;
  }
  const supported = Array.isArray(state.supportedBaudRates)
    ? state.supportedBaudRates
    : [];
  const options = supported
    .map((item) => normalizeBaudRate(item))
    .filter((item) => item > 0);
  if (options.length === 0) {
    options.push(normalizeBaudRate(state.activeBaudRate || 115200));
  }

  const unique = [...new Set(options)].sort((a, b) => a - b);
  elements.baudRateSelect.innerHTML = "";
  for (const baudRate of unique) {
    const option = document.createElement("option");
    option.value = String(baudRate);
    option.textContent = String(baudRate);
    elements.baudRateSelect.appendChild(option);
  }

  const active = normalizeBaudRate(state.activeBaudRate);
  if (unique.includes(active)) {
    elements.baudRateSelect.value = String(active);
  } else if (unique.length > 0) {
    elements.baudRateSelect.value = String(unique[0]);
    state.activeBaudRate = unique[0];
  }
}

async function updateActiveMessageType(nextType) {
  const normalized = normalizeMessageType(nextType);
  if (!normalized) {
    return;
  }
  const selected = selectedDevice();
  if (!selected?.serial_number) {
    renderMessageTypeSelect();
    renderTractionOutInput();
    renderCmdInput();
    return;
  }
  try {
    const response = await fetch(
      `/api/devices/${encodeURIComponent(selected.serial_number)}/config/message-type`,
      {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_type: normalized }),
      },
    );
    if (!response.ok) {
      throw new Error(`message type update failed with status ${response.status}`);
    }
    const payload = await response.json();
    state.activeMessageType = normalizeMessageType(payload.active_message_type || normalized);
    if (payload?.device?.serial_number) {
      state.devices = state.devices.map((item) =>
        item.serial_number === payload.device.serial_number ? payload.device : item,
      );
    } else {
      const current = selectedDevice();
      if (current?.serial_number) {
        current.message_type = state.activeMessageType;
      }
    }
    state.supportedMessageTypes = Array.isArray(payload.supported_message_types)
      ? payload.supported_message_types
      : state.supportedMessageTypes;
    syncSelectedDeviceTractionOutValue();
    updateSelectedTitle();
    renderDevices();
    renderEvents();
    renderMessageTypeSelect();
    renderTractionOutInput();
    renderCmdInput();
    renderTelemetryControls();
  } catch (_err) {
    renderMessageTypeSelect();
    renderTractionOutInput();
    renderCmdInput();
    renderTelemetryControls();
    window.alert("Failed to update message type.");
  }
}

async function updateActiveBaudRate(nextBaudRate) {
  const normalized = normalizeBaudRate(nextBaudRate);
  if (!normalized) {
    return;
  }
  try {
    const response = await fetch("/api/config/serial", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baud_rate: normalized }),
    });
    if (!response.ok) {
      throw new Error(`baud rate update failed with status ${response.status}`);
    }
    const payload = await response.json();
    state.activeBaudRate = normalizeBaudRate(payload.active_baud_rate || normalized);
    state.supportedBaudRates = Array.isArray(payload.supported_baud_rates)
      ? payload.supported_baud_rates
      : state.supportedBaudRates;
    renderBaudRateSelect();
  } catch (_err) {
    renderBaudRateSelect();
    window.alert("Failed to update baud rate.");
  }
}

async function updateTractionOutValue(nextValue) {
  const selected = selectedDevice();
  if (!selected?.serial_number) {
    renderTractionOutInput();
    return;
  }
  const normalized = normalizeTractionOutValue(nextValue);
  try {
    const response = await fetch(
      `/api/devices/${encodeURIComponent(selected.serial_number)}/config/traction-out`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: normalized }),
      },
    );
    if (!response.ok) {
      throw new Error(`traction out update failed with status ${response.status}`);
    }
    const payload = await response.json();
    const resolved = normalizeTractionOutValue(payload.traction_out_value);
    state.activeTractionOutValue = resolved;
    state.devices = state.devices.map((item) =>
      item.serial_number === selected.serial_number
        ? { ...item, traction_out_value: resolved }
        : item,
    );
    renderDevices();
    renderTractionOutInput();
  } catch (_err) {
    renderTractionOutInput();
    window.alert("Failed to update output value.");
  }
}

async function sendTractionOutCommand() {
  const selected = selectedDevice();
  if (!selected?.serial_number) {
    window.alert("Select a device first.");
    return;
  }
  if (!isTractionOutMessageType(resolveDeviceMessageType(selected))) {
    window.alert("Set this device Type to TRACTION_OUT first.");
    return;
  }
  if (state.tractionOutSending) {
    return;
  }

  const inputValue = elements.tractionOutValueInput
    ? elements.tractionOutValueInput.value
    : state.activeTractionOutValue;
  const normalized = normalizeTractionOutValue(inputValue);
  state.tractionOutSending = true;
  renderTractionOutInput();

  try {
    const response = await fetch(
      `/api/devices/${encodeURIComponent(selected.serial_number)}/traction-out/send`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: normalized }),
      },
    );
    if (!response.ok) {
      let detail = `status ${response.status}`;
      try {
        const payload = await response.json();
        if (payload?.detail) {
          detail = String(payload.detail);
        }
      } catch (_err) {
      }
      throw new Error(detail);
    }

    const payload = await response.json();
    const resolved = normalizeTractionOutValue(
      payload.traction_out_value ?? normalized,
    );
    state.activeTractionOutValue = resolved;
    state.devices = state.devices.map((item) =>
      item.serial_number === selected.serial_number
        ? { ...item, traction_out_value: resolved }
        : item,
    );
    renderDevices();
    renderTractionOutInput();
  } catch (err) {
    const detail = err instanceof Error ? err.message : "unknown error";
    window.alert(`Failed to send output command: ${detail}.`);
  } finally {
    state.tractionOutSending = false;
    renderTractionOutInput();
  }
}

async function sendCmdCommand() {
  const selected = selectedDevice();
  if (!selected?.serial_number) {
    window.alert("Select a device first.");
    return;
  }
  if (!isCmdMessageType(resolveDeviceMessageType(selected))) {
    window.alert("Set this device Type to CMD first.");
    return;
  }
  if (state.cmdSending) {
    return;
  }

  const inputValue = elements.cmdInput ? elements.cmdInput.value : state.activeCmdText;
  const commandText = normalizeCmdText(inputValue);
  state.cmdSending = true;
  state.activeCmdText = commandText;
  renderCmdInput();

  try {
    const response = await fetch(
      `/api/devices/${encodeURIComponent(selected.serial_number)}/cmd/send`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: commandText }),
      },
    );
    if (!response.ok) {
      let detail = `status ${response.status}`;
      try {
        const payload = await response.json();
        if (payload?.detail) {
          detail = String(payload.detail);
        }
      } catch (_err) {
      }
      throw new Error(detail);
    }

    const payload = await response.json();
    state.activeCmdText = normalizeCmdText(payload.command ?? commandText);
    renderCmdInput();
  } catch (err) {
    const detail = err instanceof Error ? err.message : "unknown error";
    window.alert(`Failed to send CMD: ${detail}.`);
  } finally {
    state.cmdSending = false;
    renderCmdInput();
  }
}

async function refreshMessageTypeConfig() {
  const selected = selectedDevice();
  if (!selected?.serial_number) {
    syncSelectedDeviceMessageType();
    syncSelectedDeviceTractionOutValue();
    renderMessageTypeSelect();
    renderTractionOutInput();
    renderCmdInput();
    renderTelemetryControls();
    return;
  }
  try {
    const response = await fetch(
      `/api/devices/${encodeURIComponent(selected.serial_number)}/config/message-type`,
      { cache: "no-store" },
    );
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    state.activeMessageType = normalizeMessageType(
      payload.active_message_type || state.activeMessageType,
    );
    const current = selectedDevice();
    if (current?.serial_number) {
      current.message_type = state.activeMessageType;
    }
    state.supportedMessageTypes = Array.isArray(payload.supported_message_types)
      ? payload.supported_message_types
      : [];
    syncSelectedDeviceTractionOutValue();
    updateSelectedTitle();
    renderDevices();
    renderEvents();
    renderMessageTypeSelect();
    renderTractionOutInput();
    renderCmdInput();
    renderTelemetryControls();
  } catch (_err) {
  }
}

async function refreshSerialConfig() {
  try {
    const response = await fetch("/api/config/serial", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    state.activeBaudRate = normalizeBaudRate(
      payload.active_baud_rate || state.activeBaudRate,
    );
    state.supportedBaudRates = Array.isArray(payload.supported_baud_rates)
      ? payload.supported_baud_rates
      : [];
    renderBaudRateSelect();
  } catch (_err) {
  }
}

async function sendTelemetryCommand(action) {
  const device = activeTelemetryDevice();
  if (!device?.serial_number) {
    window.alert("Select a device first.");
    return;
  }
  const deviceMode = resolveDeviceMessageType(device);
  if (!isTelemetryMessageType(deviceMode)) {
    window.alert("Set this device Type to TELEMETRY first.");
    return;
  }
  const normalizedAction = String(action || "").trim().toLowerCase();
  if (normalizedAction !== "start" && normalizedAction !== "stop") {
    return;
  }

  try {
    const response = await fetch(
      `/api/devices/${encodeURIComponent(device.serial_number)}/telemetry/${normalizedAction}`,
      {
        method: "POST",
      },
    );
    if (!response.ok) {
      throw new Error(`telemetry ${normalizedAction} failed with status ${response.status}`);
    }
    await refreshDevices();
  } catch (_err) {
    window.alert(`Failed to ${normalizedAction} telemetry.`);
  } finally {
    renderTelemetryControls();
  }
}

async function renameDevice(device) {
  const serial = device?.serial_number;
  if (!serial) {
    return;
  }
  const currentName = resolveName(device);
  const nextName = window.prompt(`Set display name for ${serial}`, currentName);
  if (nextName === null) {
    return;
  }

  try {
    const response = await fetch(
      `/api/devices/${encodeURIComponent(serial)}/name`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nextName.trim() }),
      },
    );
    if (!response.ok) {
      throw new Error(`rename failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (payload?.device?.serial_number) {
      state.devices = state.devices.map((item) =>
        item.serial_number === payload.device.serial_number ? payload.device : item,
      );
    } else {
      await refreshDevices();
      return;
    }
    renderDevices();
    renderEvents();
  } catch (_err) {
    window.alert("Failed to rename device.");
  }
}

async function clearDevicesRegistry() {
  const confirmed = window.confirm(
    "Clear all devices from host JSON?\nConnected devices can appear again after detection.",
  );
  if (!confirmed) {
    return;
  }
  try {
    const response = await fetch("/api/devices/clear", { method: "POST" });
    if (!response.ok) {
      throw new Error(`clear devices failed with status ${response.status}`);
    }
    const payload = await response.json();
    state.devices = Array.isArray(payload.devices) ? payload.devices : [];
    state.selectedSerial = null;
    syncSelectedDeviceMessageType();
    syncSelectedDeviceTractionOutValue();
    updateSelectedTitle();
    renderDevices();
    renderEvents();
    renderMessageTypeSelect();
    renderTractionOutInput();
    renderCmdInput();
    renderTelemetryControls();
  } catch (_err) {
    window.alert("Failed to clear devices JSON.");
  }
}

async function refreshDevices() {
  try {
    const response = await fetch("/api/devices", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    if (!Array.isArray(payload.devices)) {
      return;
    }
    state.devices = payload.devices;
    if (
      state.selectedSerial &&
      !state.devices.find((item) => item.serial_number === state.selectedSerial)
    ) {
      state.selectedSerial = null;
    }
    syncSelectedDeviceMessageType();
    syncSelectedDeviceTractionOutValue();
    updateSelectedTitle();
    renderDevices();
    renderEvents();
    renderMessageTypeSelect();
    renderTractionOutInput();
    renderCmdInput();
    renderTelemetryControls();
  } catch (_err) {
  }
}

function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const url = `${protocol}://${location.host}/ws/comms`;
  const ws = new WebSocket(url);
  state.ws = ws;

  ws.onopen = () => setWsStatus(true);

  ws.onmessage = (message) => {
    let data;
    try {
      data = JSON.parse(message.data);
    } catch (_err) {
      return;
    }

    if (data.type === "snapshot") {
      if (Array.isArray(data.devices)) {
        state.devices = data.devices;
      }
      if (Array.isArray(data.events)) {
        state.events = data.events.slice(-3000);
      }
      if (Array.isArray(data.supported_message_types)) {
        state.supportedMessageTypes = data.supported_message_types;
      }
      if (Array.isArray(data.supported_baud_rates)) {
        state.supportedBaudRates = data.supported_baud_rates;
      }
      if (data.active_baud_rate) {
        state.activeBaudRate = normalizeBaudRate(data.active_baud_rate);
      }
      syncSelectedDeviceMessageType();
      syncSelectedDeviceTractionOutValue();
      updateSelectedTitle();
      renderDevices();
      renderEvents();
      renderMessageTypeSelect();
      renderTractionOutInput();
      renderCmdInput();
      renderBaudRateSelect();
      renderTelemetryControls();
      return;
    }

    if (data.type === "comms" && data.event) {
      state.events.push(data.event);
      if (state.events.length > 3000) {
        state.events = state.events.slice(-3000);
      }
      renderEvents();
    }
  };

  ws.onclose = () => {
    setWsStatus(false);
    window.setTimeout(connectWebSocket, 1000);
  };

  ws.onerror = () => {
    setWsStatus(false);
  };
}

function start() {
  updateSelectedTitle();
  renderDevices();
  renderEvents();
  renderAutoScrollToggle();
  renderMessageTypeSelect();
  renderTractionOutInput();
  renderCmdInput();
  renderBaudRateSelect();
  renderTelemetryControls();
  elements.clearStream.addEventListener("click", clearVisualStream);
  elements.autoScrollToggle.addEventListener("click", toggleAutoScroll);
  if (elements.messageTypeSelect) {
    elements.messageTypeSelect.addEventListener("change", (event) => {
      updateActiveMessageType(event.target.value);
    });
  }
  if (elements.baudRateSelect) {
    elements.baudRateSelect.addEventListener("change", (event) => {
      updateActiveBaudRate(event.target.value);
    });
  }
  if (elements.tractionOutValueInput) {
    elements.tractionOutValueInput.addEventListener("change", (event) => {
      updateTractionOutValue(event.target.value);
    });
    elements.tractionOutValueInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        sendTractionOutCommand();
      }
    });
  }
  if (elements.tractionOutSend) {
    elements.tractionOutSend.addEventListener("click", () => sendTractionOutCommand());
  }
  if (elements.cmdInput) {
    elements.cmdInput.addEventListener("change", (event) => {
      state.activeCmdText = normalizeCmdText(event.target.value);
      renderCmdInput();
    });
    elements.cmdInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        sendCmdCommand();
      }
    });
  }
  if (elements.cmdSend) {
    elements.cmdSend.addEventListener("click", () => sendCmdCommand());
  }
  if (elements.telemetryStart) {
    elements.telemetryStart.addEventListener("click", () => sendTelemetryCommand("start"));
  }
  if (elements.telemetryStop) {
    elements.telemetryStop.addEventListener("click", () => sendTelemetryCommand("stop"));
  }
  if (elements.clearDevices) {
    elements.clearDevices.addEventListener("click", clearDevicesRegistry);
  }
  refreshMessageTypeConfig();
  refreshSerialConfig();
  refreshDevices();
  setInterval(refreshDevices, 2000);
  connectWebSocket();
}

start();
