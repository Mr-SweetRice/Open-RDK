(function () {
  "use strict";

  const PAGE_FAMILY = "traction-tools";
  const CURRENT_PAGE_VERSION = "1.1";
  const serial = new URLSearchParams(window.location.search).get("serial") || "";

  const banner = document.createElement("div");
  banner.setAttribute("role", "status");
  banner.style.cssText = [
    "margin:0 0 12px",
    "padding:9px 12px",
    "border:1px solid var(--border)",
    "border-radius:10px",
    "background:var(--surface)",
    "color:var(--text-dim)",
    "font:500 12px/1.4 var(--font)",
  ].join(";");
  banner.textContent = `Traction tools page v${CURRENT_PAGE_VERSION} · firmware version unavailable`;
  const bannerHost = document.querySelector(".stage-body") || document.body;
  bannerHost.prepend(banner);

  async function renderVersionStatus() {
    if (!serial) {
      banner.textContent = `Traction tools page v${CURRENT_PAGE_VERSION} · no module selected`;
      return;
    }

    try {
      const response = await fetch("/api/devices", { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const devices = Array.isArray(payload.devices) ? payload.devices : [];
      const device = devices.find((item) => String(item.serial_number || "") === serial);
      if (!device) return;

      const firmwareVersion = String(device.firmware_version || "legacy / unknown");
      const expectedPage = String(device.expected_page || "");
      const expectedVersion = String(device.expected_page_version || "");
      const mismatch = firmwareVersion !== "1.1" ||
        (expectedPage && expectedPage !== PAGE_FAMILY) ||
        (expectedVersion && expectedVersion !== CURRENT_PAGE_VERSION);

      banner.textContent = `Firmware v${firmwareVersion} · traction tools page v${CURRENT_PAGE_VERSION}`;
      if (mismatch) {
        banner.textContent += " · WARNING: version mismatch; newest compatible tools are being shown.";
        banner.style.borderColor = "#d34b4b";
        banner.style.color = "#d34b4b";
      }
    } catch (_error) {
      // Version discovery is informational and must never prevent tool access.
    }
  }

  renderVersionStatus();
})();
