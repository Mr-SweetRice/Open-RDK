const state = {
  devices: [],
  selectedSerial: null,
  events: [],
  ws: null,
  clearAfterLine: 0,
  autoScrollEnabled: true,
};

const elements = {
  devices: document.getElementById("devices"),
  deviceCount: document.getElementById("deviceCount"),
  eventCount: document.getElementById("eventCount"),
  selectedTitle: document.getElementById("selectedTitle"),
  commsTable: document.getElementById("commsTable"),
  wsStatus: document.getElementById("wsStatus"),
  clearStream: document.getElementById("clearStream"),
  autoScrollToggle: document.getElementById("autoScrollToggle"),
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

function normalizeState(value) {
  return String(value || "").trim().toLowerCase();
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
  return event.sender === state.selectedSerial || event.sender === "host";
}

function filteredEvents() {
  const list = state.events
    .filter(isEventForSelectedDevice)
    .filter((event) => Number(event.line || 0) > state.clearAfterLine);
  return list.slice(-1000);
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
    const direction = event.sender === "host" ? "tx" : "rx";
    const displayLine = i;
    row.className = "row";
    row.innerHTML = `
      <span class="line">#${displayLine}</span>
      <span class="dir ${direction}">${direction.toUpperCase()}</span>
      <span class="sender">${event.sender}</span>
      <span class="raw">${event.raw_hex}</span>
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
    `;
    node.addEventListener("click", () => {
      state.selectedSerial =
        state.selectedSerial === device.serial_number ? null : device.serial_number;
      updateSelectedTitle();
      renderDevices();
      renderEvents();
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
    updateSelectedTitle();
    renderDevices();
    renderEvents();
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
      updateSelectedTitle();
      renderDevices();
      renderEvents();
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
  elements.clearStream.addEventListener("click", clearVisualStream);
  elements.autoScrollToggle.addEventListener("click", toggleAutoScroll);
  refreshDevices();
  setInterval(refreshDevices, 2000);
  connectWebSocket();
}

start();
