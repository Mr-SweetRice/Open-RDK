/* Shell controller: rail toggle + landing/workspace switching.
 * Hooks into app.js by observing the .device-item.selected class.
 */
(function () {
  "use strict";

  const body   = document.body;
  const rail   = document.getElementById("rail");
  const toggle = document.getElementById("railToggle");
  const brand  = document.getElementById("brandReset");
  const devicesEl = document.getElementById("devices");

  // ---- rail collapse/expand ----
  const RAIL_KEY = "rdk.rail.collapsed";
  function applyRail(state) {
    body.setAttribute("data-rail", state);
    try { localStorage.setItem(RAIL_KEY, state); } catch (_e) {}
    if (toggle) {
      toggle.title = state === "collapsed" ? "Expand rail" : "Collapse rail";
      toggle.setAttribute("aria-label", toggle.title);
    }
  }
  try {
    const stored = localStorage.getItem(RAIL_KEY);
    if (stored === "collapsed" || stored === "expanded") applyRail(stored);
  } catch (_e) {}
  if (toggle) {
    toggle.addEventListener("click", () => {
      applyRail(body.getAttribute("data-rail") === "collapsed" ? "expanded" : "collapsed");
    });
  }

  // ---- landing vs workspace ----
  function syncLanding() {
    const hasSelection = !!devicesEl?.querySelector(".device-item.selected");
    body.classList.toggle("has-selection", hasSelection);
    const ws = document.getElementById("workspace");
    if (ws) ws.hidden = !hasSelection;
  }
  // initial
  syncLanding();
  // observe device list for selection changes
  if (devicesEl) {
    const obs = new MutationObserver(syncLanding);
    obs.observe(devicesEl, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  }

  // ---- brand resets selection ----
  if (brand) {
    brand.addEventListener("click", () => {
      const selected = devicesEl?.querySelector(".device-item.selected");
      if (selected) {
        // app.js toggles selection on click; simulate that to clear it
        selected.click();
      }
      body.classList.remove("has-selection");
      const ws = document.getElementById("workspace");
      if (ws) ws.hidden = true;
    });
  }
})();


// ---- Contextual tools rail on the host page ----
(function () {
  const railTools = document.getElementById("railTools");
  const toolsTitle = document.getElementById("toolsTitle");
  const toolsTitleText = document.getElementById("toolsTitleText");
  if (!railTools) return;
  const devicesEl = document.getElementById("devices");
  if (!devicesEl) return;

  const ICON_SPEED  = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="14" r="8"/><path d="M12 14L8 10"/><path d="M3 14a9 9 0 0 1 18 0"/></svg>';
  const ICON_TARGET = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>';
  const ICON_GEAR   = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v3M12 20v3M4 12H1m22 0h-3M19 19l-2-2M5 19l2-2M19 5l-2 2M5 5l2 2"/></svg>';
  const ICON_LINE   = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="4" cy="12" r="1.5" fill="currentColor"/><circle cx="9" cy="12" r="1.5" fill="currentColor"/><circle cx="14" cy="12" r="2.4" fill="currentColor"/><circle cx="19" cy="12" r="1.5" fill="currentColor"/></svg>';
  const ICON_COLOR  = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22a10 10 0 1 1 10-10c0 2.5-2.5 4-5 4h-1a2 2 0 0 0-2 2c0 1 1 1.5 1 2.5A2.5 2.5 0 0 1 12 22z"/><circle cx="13.5" cy="6.5" r="1.5" fill="currentColor"/><circle cx="17.5" cy="10.5" r="1.5" fill="currentColor"/></svg>';

  function link(href, label, icon) {
    return '<a class="rail-nav-link" href="' + href + '">' + icon + '<span>' + label + '</span></a>';
  }

  function renderRailTools() {
    const selected = devicesEl.querySelector(".device-item.selected");
    if (!selected) {
      railTools.hidden = true;
      toolsTitle.hidden = true;
      railTools.innerHTML = "";
      return;
    }
    // Detect module type from the avatar class
    const avatar = selected.querySelector(".device-avatar");
    const cls = avatar ? avatar.className : "";
    let tools = [];
    let title = "Tools";
    if (cls.includes("kind-traction")) {
      title = "Traction Tools";
      tools = [
        ['/traction-pid-tuner',      'Speed Tuner',  ICON_SPEED],
        ['/traction-position-tuner', 'Position',     ICON_TARGET],
        ['/traction-motor-config',   'Motor Config', ICON_GEAR],
      ];
    } else if (cls.includes("kind-line")) {
      title = "Sensor Tools";
      const serial = selected.querySelector(".serial")?.textContent?.trim() || "";
      tools = [
        ['/line-sensor?serial=' + encodeURIComponent(serial), 'Live Monitor', ICON_LINE],
      ];
    } else if (cls.includes("kind-color")) {
      title = "Color Tools";
      tools = [
        ['/color', 'Color Studio', ICON_COLOR],
      ];
    } else {
      railTools.hidden = true;
      toolsTitle.hidden = true;
      railTools.innerHTML = "";
      return;
    }
    toolsTitleText.textContent = title;
    railTools.innerHTML = tools.map((t) => link(t[0], t[1], t[2])).join("");
    railTools.hidden = false;
    toolsTitle.hidden = false;
  }

  renderRailTools();
  const obs = new MutationObserver(renderRailTools);
  obs.observe(devicesEl, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
})();


// ---- Landing canvas: drifting sage constellation, mouse-repulsion ----
(function () {
  const landing = document.getElementById("landing");
  if (!landing) return;

  const canvas = document.createElement("canvas");
  canvas.className = "landing-canvas";
  canvas.id = "landingCanvas";
  landing.insertBefore(canvas, landing.firstChild);

  const ctx = canvas.getContext("2d");
  const dots = [];
  let DOT_COUNT = 70;
  let width = 0, height = 0;
  let mouseX = -9999, mouseY = -9999;
  let running = true;

  function resize() {
    const r = landing.getBoundingClientRect();
    width  = r.width;
    height = r.height;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width  = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    canvas.style.width  = width  + "px";
    canvas.style.height = height + "px";
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    DOT_COUNT = Math.max(30, Math.min(110, Math.floor((width * height) / 14000)));
  }

  function seed() {
    dots.length = 0;
    for (let i = 0; i < DOT_COUNT; i++) {
      dots.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.25,
        vy: (Math.random() - 0.5) * 0.25,
        r: 0.8 + Math.random() * 1.6,
        a: 0.25 + Math.random() * 0.45,
      });
    }
  }

  function tick() {
    if (!running) { requestAnimationFrame(tick); return; }
    ctx.clearRect(0, 0, width, height);

    // step + mouse repel
    for (const d of dots) {
      d.x += d.vx;
      d.y += d.vy;
      if (d.x < 0 || d.x > width)  d.vx *= -1;
      if (d.y < 0 || d.y > height) d.vy *= -1;

      const dx = d.x - mouseX;
      const dy = d.y - mouseY;
      const dist2 = dx * dx + dy * dy;
      if (dist2 < 18000) {
        const dist = Math.sqrt(dist2) || 1;
        const force = (1 - dist / 134) * 2.2;
        d.x += (dx / dist) * force;
        d.y += (dy / dist) * force;
      }

      // soft glow halo
      const grad = ctx.createRadialGradient(d.x, d.y, 0, d.x, d.y, d.r * 4);
      grad.addColorStop(0, `rgba(168, 213, 168, ${d.a})`);
      grad.addColorStop(1, "rgba(168, 213, 168, 0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(d.x, d.y, d.r * 4, 0, Math.PI * 2);
      ctx.fill();

      // crisp center
      ctx.fillStyle = `rgba(220, 240, 220, ${Math.min(1, d.a + 0.25)})`;
      ctx.beginPath();
      ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
      ctx.fill();
    }

    // constellation links
    ctx.lineWidth = 1;
    for (let i = 0; i < dots.length; i++) {
      for (let j = i + 1; j < dots.length; j++) {
        const a = dots[i], b = dots[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy;
        if (dist2 < 11000) {
          const dist = Math.sqrt(dist2);
          const alpha = (1 - dist / 105) * 0.32;
          ctx.strokeStyle = `rgba(168, 213, 168, ${alpha})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    // (mouse aura disabled per user preference)

    requestAnimationFrame(tick);
  }

  function onMove(e) {
    const r = landing.getBoundingClientRect();
    mouseX = e.clientX - r.left;
    mouseY = e.clientY - r.top;
  }
  function onLeave() { mouseX = mouseY = -9999; }

  const stage = document.getElementById("stage");
  (stage || landing).addEventListener("mousemove", onMove);
  (stage || landing).addEventListener("mouseleave", onLeave);

  // pause when landing is hidden (workspace visible)
  const bodyObs = new MutationObserver(() => {
    running = !document.body.classList.contains("has-selection");
    canvas.style.display = running ? "" : "none";
  });
  bodyObs.observe(document.body, { attributes: true, attributeFilter: ["class"] });

  // reflow handling
  const ro = new ResizeObserver(() => { resize(); });
  ro.observe(landing);

  resize();
  seed();
  tick();
})();
