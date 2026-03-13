/**
 * sensors.js — Tapeinos Sensor Page
 * ===================================
 * - Add sensor modal (type select → port scan → confirm)
 * - Render sensor cards from /sensors/list
 * - Toggle switch → start / stop sensor node
 * - Per-card SSE terminal
 * - Action buttons → /sensors/action/<id>
 * - Remove sensor
 */

"use strict";

/* ── State ───────────────────────────────────────────────── */
let _selectedType = null;
const _sensorEventSources = {};   // sensor_id → EventSource

/* ══════════════════════════════════════════════════════════
   MODAL
══════════════════════════════════════════════════════════ */

function openAddModal() {
  _selectedType = null;
  document.getElementById("step-type").classList.remove("modal-step--hidden");
  document.getElementById("step-config").classList.add("modal-step--hidden");
  document.getElementById("modalConfirmBtn").disabled = true;
  document.getElementById("scanHint").textContent = "";
  document.getElementById("portSelect").innerHTML = "<option value=''>— scan to populate —</option>";
  document.getElementById("cameraSelect").innerHTML = "<option value=''>— scan to populate —</option>";
  document.getElementById("sensorName").value = "";
  document.querySelectorAll(".type-card").forEach(c => c.classList.remove("type-card--selected"));

  document.getElementById("modalOverlay").classList.add("modal-overlay--visible");
  document.getElementById("addModal").classList.add("modal--visible");
}

function closeAddModal() {
  document.getElementById("modalOverlay").classList.remove("modal-overlay--visible");
  document.getElementById("addModal").classList.remove("modal--visible");
}

function selectType(type) {
  _selectedType = type;
  document.querySelectorAll(".type-card").forEach(c => c.classList.remove("type-card--selected"));
  document.getElementById(`type-${type}`).classList.add("type-card--selected");

  document.getElementById("step-type").classList.add("modal-step--hidden");
  document.getElementById("step-config").classList.remove("modal-step--hidden");

  // Show relevant port/device field
  if (type === "ultrasonic") {
    document.getElementById("portField").classList.remove("modal-step--hidden");
    document.getElementById("cameraField").classList.add("modal-step--hidden");
    document.getElementById("sensorName").placeholder = "e.g. Front Ultrasonic";
  } else {
    document.getElementById("portField").classList.add("modal-step--hidden");
    document.getElementById("cameraField").classList.remove("modal-step--hidden");
    document.getElementById("sensorName").placeholder = "e.g. Top Camera";
  }

  // Auto-scan on type selection
  if (type === "ultrasonic") scanPorts();
  else scanCameras();
}

async function scanPorts() {
  const btn  = document.getElementById("scanBtn");
  const hint = document.getElementById("scanHint");
  const sel  = document.getElementById("portSelect");

  btn.classList.add("modal__scan-btn--scanning");
  hint.textContent = "scanning…";
  hint.style.color = "";

  try {
    const res  = await fetch("/sensors/ports");
    const data = await res.json();
    const ports = data.ports || [];

    sel.innerHTML = ports.length
      ? ports.map(p => `<option value="${p.port}">${p.port} — ${p.description}</option>`).join("")
      : "<option value=''>No ports found</option>";

    hint.textContent = ports.length
      ? `${ports.length} port(s) found`
      : "No serial ports detected";
    hint.style.color = ports.length ? "var(--running)" : "var(--danger)";

    _updateConfirmBtn();
  } catch (e) {
    hint.textContent = `Scan failed: ${e.message}`;
    hint.style.color = "var(--danger)";
  } finally {
    btn.classList.remove("modal__scan-btn--scanning");
  }
}

async function scanCameras() {
  const hint = document.getElementById("scanHint");
  const sel  = document.getElementById("cameraSelect");

  hint.textContent = "scanning cameras…";
  hint.style.color = "";

  try {
    const res  = await fetch("/sensors/cameras");
    const data = await res.json();
    const cams = data.cameras || [];

    sel.innerHTML = cams.length
      ? cams.map(c => `<option value="${c.index}">${c.label}</option>`).join("")
      : "<option value=''>No cameras found</option>";

    hint.textContent = cams.length ? `${cams.length} camera(s) found` : "No cameras detected";
    hint.style.color = cams.length ? "var(--running)" : "var(--danger)";

    _updateConfirmBtn();
  } catch (e) {
    hint.textContent = `Scan failed: ${e.message}`;
    hint.style.color = "var(--danger)";
  }
}

function _updateConfirmBtn() {
  const btn = document.getElementById("modalConfirmBtn");
  if (_selectedType === "ultrasonic") {
    const port = document.getElementById("portSelect").value;
    btn.disabled = !port;
  } else if (_selectedType === "camera") {
    const cam = document.getElementById("cameraSelect").value;
    btn.disabled = cam === "" || cam === undefined;
  } else {
    btn.disabled = true;
  }
}

// Wire up select change → enable confirm
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("portSelect").addEventListener("change", _updateConfirmBtn);
  document.getElementById("cameraSelect").addEventListener("change", _updateConfirmBtn);
});

async function confirmAddSensor() {
  const name = document.getElementById("sensorName").value.trim();
  let port, baudrate;

  if (_selectedType === "ultrasonic") {
    port     = document.getElementById("portSelect").value;
    baudrate = parseInt(document.getElementById("baudrateSelect").value);
  } else {
    port     = document.getElementById("cameraSelect").value;
    baudrate = 0;
  }

  if (!port) return;

  const btn = document.getElementById("modalConfirmBtn");
  btn.disabled = true;
  btn.textContent = "Adding…";

  try {
    const res  = await fetch("/sensors/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: _selectedType, name, port, baudrate }),
    });
    const data = await res.json();
    if (data.status === "created") {
      closeAddModal();
      renderSensorCard(data.sensor);
      updateEmptyState();
    } else {
      alert(`Error: ${data.error || "unknown"}`);
    }
  } catch (e) {
    alert(`Network error: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Add Sensor";
  }
}

/* ══════════════════════════════════════════════════════════
   CARD RENDERING
══════════════════════════════════════════════════════════ */

function renderSensorCard(sensor) {
  const tmpl  = document.getElementById("sensorCardTemplate");
  const card  = tmpl.content.cloneNode(true).querySelector(".sensor-card");
  const sid   = sensor.id;

  card.dataset.sensorId = sid;

  // Icon
  const iconWrap = card.querySelector(".sensor-card__icon-wrap");
  iconWrap.classList.add(`sensor-card__icon-wrap--${sensor.type}`);
  iconWrap.innerHTML = sensor.type === "ultrasonic"
    ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/><circle cx="12" cy="12" r="4"/></svg>`
    : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`;

  // Name / meta
  card.querySelector(".sensor-card__name").textContent = sensor.name || sensor.type;
  card.querySelector(".sensor-card__meta").textContent =
    sensor.type === "ultrasonic"
      ? `${sensor.port} · ${sensor.baudrate} baud`
      : `Camera ${sensor.port}`;

  // Badge
  const badge = card.querySelector(".sensor-card__badge");
  badge.id = `sensor-badge-${sid}`;

  // Toggle
  const toggle = card.querySelector(".sensor-card__toggle-input");
  toggle.dataset.sensorId = sid;
  toggle.addEventListener("change", handleSensorToggle);

  // Actions panel
  const actionsTmplId = sensor.type === "ultrasonic"
    ? "ultrasonicActionsTemplate"
    : "cameraActionsTemplate";
  const actionsTmpl = document.getElementById(actionsTmplId);
  const actionsClone = actionsTmpl.content.cloneNode(true);
  card.querySelector(".sensor-card__actions").appendChild(actionsClone);

  // Wire action buttons
  card.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", () => handleSensorAction(sid, btn));
  });

  // Terminal
  card.querySelector(".sensor-terminal__title").textContent =
    `${sensor.name || sensor.type} — bash`;
  card.querySelector(".sensor-terminal__cmd").textContent =
    sensor.type === "ultrasonic"
      ? `ultrasonic_node --port ${sensor.port}`
      : `camera_node --device ${sensor.port}`;
  card.querySelector(".sensor-terminal__body").id = `sensor-term-body-${sid}`;

  // Refresh button
  card.querySelector(".sensor-terminal__refresh").addEventListener("click", () => {
    refreshSensorTerminal(sid);
  });

  // Restore running state + threshold display
  if (sensor.was_running) {
    toggle.checked = true;
    _setSensorBadge(sid, true);
    card.classList.add("sensor-card--active");
    openSensorLogStream(sid);
  } else {
    _setSensorBadge(sid, false);
  }

  if (sensor.threshold !== null && sensor.threshold !== undefined) {
    const inp = card.querySelector("[data-action-input='threshold']");
    if (inp) inp.value = sensor.threshold;
    const statusEl = card.querySelector("[data-action-status='threshold']");
    if (statusEl) {
      statusEl.textContent = `Active: ${sensor.threshold} cm`;
      statusEl.className = "action-status action-status--ok";
    }
  }

  document.getElementById("sensorCards").appendChild(card);
}

function toggleCard(headerEl) {
  const card = headerEl.closest(".sensor-card");
  card.classList.toggle("sensor-card--open");
}

/* ══════════════════════════════════════════════════════════
   TOGGLE — start / stop sensor node
══════════════════════════════════════════════════════════ */

async function handleSensorToggle(event) {
  const checkbox = event.target;
  const sid      = checkbox.dataset.sensorId;
  const wantsOn  = checkbox.checked;

  const toggleEl = checkbox.closest(".toggle");
  toggleEl.classList.add("toggle--busy");
  checkbox.disabled = true;

  try {
    if (wantsOn) {
      const res  = await fetch(`/sensors/start/${sid}`, { method: "POST" });
      const data = await res.json();
      if (data.status === "started" || data.status === "already_running") {
        _setSensorBadge(sid, true);
        _setSensorCardActive(sid, true);
        openSensorLogStream(sid);
      } else {
        checkbox.checked = false;
        _setSensorBadge(sid, false);
        _appendSensorTerm(sid, `[error] ${data.message || data.error}`);
      }
    } else {
      await fetch(`/sensors/stop/${sid}`, { method: "POST" });
      closeSensorLogStream(sid);
      _setSensorBadge(sid, false);
      _setSensorCardActive(sid, false);
      _appendSensorTerm(sid, "[stopped]");
    }
  } catch (e) {
    checkbox.checked = !wantsOn;
    _appendSensorTerm(sid, `[network error] ${e.message}`);
  } finally {
    checkbox.disabled = false;
    toggleEl.classList.remove("toggle--busy");
  }
}

/* ══════════════════════════════════════════════════════════
   ACTIONS
══════════════════════════════════════════════════════════ */

async function handleSensorAction(sid, btn) {
  const action    = btn.dataset.action;
  const card      = document.querySelector(`.sensor-card[data-sensor-id="${sid}"]`);
  const statusKey = btn.closest(".action-section__form")
    ?.querySelector("[data-action-status]")?.dataset.actionStatus;

  // Build payload
  const payload = { action };

  if (action === "set_threshold") {
    const inp = card.querySelector("[data-action-input='threshold']");
    const val = parseFloat(inp?.value);
    if (!inp || isNaN(val) || val <= 0) {
      _setActionStatus(card, statusKey, "Enter a valid threshold (cm)", "error");
      return;
    }
    payload.threshold = val;
  }

  btn.disabled = true;
  const origText = btn.textContent;
  btn.textContent = "…";

  try {
    const res  = await fetch(`/sensors/action/${sid}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    });
    const data = await res.json();

    if (res.ok) {
      if (action === "set_threshold") {
        _setActionStatus(card, statusKey, `Active: ${data.threshold} cm`, "ok");
      } else if (action === "clear_threshold") {
        _setActionStatus(card, statusKey, "Cleared", "ok");
        const inp = card.querySelector("[data-action-input='threshold']");
        if (inp) inp.value = "";
      } else {
        _appendSensorTerm(sid, `[action] ${action} → ok`);
      }
    } else {
      _setActionStatus(card, statusKey, data.error || "error", "error");
    }
  } catch (e) {
    _setActionStatus(card, statusKey, `Network error: ${e.message}`, "error");
  } finally {
    btn.disabled  = false;
    btn.textContent = origText;
  }
}

function _setActionStatus(card, key, msg, type) {
  if (!key || !card) return;
  const el = card.querySelector(`[data-action-status="${key}"]`);
  if (!el) return;
  el.textContent = msg;
  el.className   = `action-status${type === "ok" ? " action-status--ok" : type === "error" ? " action-status--error" : ""}`;
}

/* ══════════════════════════════════════════════════════════
   REMOVE
══════════════════════════════════════════════════════════ */

async function removeSensor(event, btn) {
  event.stopPropagation();
  const card = btn.closest(".sensor-card");
  const sid  = card.dataset.sensorId;

  if (!confirm("Remove this sensor?")) return;

  closeSensorLogStream(sid);
  try {
    await fetch(`/sensors/remove/${sid}`, { method: "POST" });
  } catch (_) {}

  card.style.transition = "opacity .25s, transform .25s";
  card.style.opacity    = "0";
  card.style.transform  = "translateY(-8px)";
  setTimeout(() => { card.remove(); updateEmptyState(); }, 260);
}

/* ══════════════════════════════════════════════════════════
   SSE LOG STREAMS
══════════════════════════════════════════════════════════ */

function openSensorLogStream(sid) {
  closeSensorLogStream(sid);
  const es = new EventSource(`/sensors/logs/${sid}`);
  _sensorEventSources[sid] = es;
  es.onmessage = e  => _appendSensorTerm(sid, e.data);
  es.onerror   = () => { es.close(); delete _sensorEventSources[sid]; };
}

function closeSensorLogStream(sid) {
  if (_sensorEventSources[sid]) {
    _sensorEventSources[sid].close();
    delete _sensorEventSources[sid];
  }
}

function refreshSensorTerminal(sid) {
  const body = document.getElementById(`sensor-term-body-${sid}`);
  if (body) body.innerHTML = `<span class="terminal__idle">▸ reloading log…</span>`;

  const card   = document.querySelector(`.sensor-card[data-sensor-id="${sid}"]`);
  const toggle = card?.querySelector(".sensor-card__toggle-input");
  if (toggle?.checked) {
    closeSensorLogStream(sid);
    openSensorLogStream(sid);
  } else {
    setTimeout(() => {
      if (body) body.innerHTML = `<span class="terminal__idle">▸ toggle ON to start sensor…</span>`;
    }, 400);
  }
}

/* ══════════════════════════════════════════════════════════
   TERMINAL HELPERS
══════════════════════════════════════════════════════════ */

function _appendSensorTerm(sid, text) {
  const body = document.getElementById(`sensor-term-body-${sid}`);
  if (!body) return;

  const idle = body.querySelector(".terminal__idle");
  if (idle) idle.remove();

  const line = document.createElement("span");
  line.className = "terminal__line " + _classifyLine(text);
  line.textContent = text;
  body.appendChild(line);
  body.appendChild(document.createTextNode("\n"));

  // Trim
  const lines = body.querySelectorAll(".terminal__line");
  if (lines.length > 500) {
    const excess = lines.length - 500;
    for (let i = 0; i < excess; i++) {
      const n = lines[i], next = n.nextSibling;
      n.remove();
      if (next?.nodeType === Node.TEXT_NODE) next.remove();
    }
  }
  body.scrollTop = body.scrollHeight;
}

function _classifyLine(text) {
  const t = text.toLowerCase();
  if (t.includes("[error]") || t.includes("error:"))    return "terminal__line--error";
  if (t.includes("[warn]")  || t.includes("warning"))   return "terminal__line--warn";
  if (t.includes("[stop]")  || t.includes("[stopped]")) return "terminal__line--warn";
  if (t.includes("[ok]")    || t.includes("✓"))         return "terminal__line--ok";
  if (t.includes("[info]")  || t.includes("[started]")) return "terminal__line--info";
  if (t.includes("[cmd]")   || t.includes("[action]"))  return "terminal__line--info";
  if (t.includes("[clear]"))                            return "terminal__line--ok";
  return "";
}

/* ══════════════════════════════════════════════════════════
   BADGE + CARD STATE
══════════════════════════════════════════════════════════ */

function _setSensorBadge(sid, running) {
  const badge = document.getElementById(`sensor-badge-${sid}`);
  if (!badge) return;
  badge.textContent = running ? "● RUNNING" : "● STOPPED";
  badge.className   = running ? "badge badge--running sensor-card__badge"
                               : "badge badge--stopped sensor-card__badge";
}

function _setSensorCardActive(sid, active) {
  const card = document.querySelector(`.sensor-card[data-sensor-id="${sid}"]`);
  if (!card) return;
  card.classList.toggle("sensor-card--active", active);
}

/* ══════════════════════════════════════════════════════════
   EMPTY STATE
══════════════════════════════════════════════════════════ */

function updateEmptyState() {
  const cards   = document.getElementById("sensorCards");
  const empty   = document.getElementById("sensorsEmpty");
  const hasCards = cards.children.length > 0;
  empty.style.display  = hasCards ? "none" : "";
  cards.style.display  = hasCards ? "" : "none";
}

/* ══════════════════════════════════════════════════════════
   INITIAL LOAD — fetch persisted sensors
══════════════════════════════════════════════════════════ */

async function loadSensors() {
  try {
    const res  = await fetch("/sensors/list");
    const data = await res.json();
    const sensors = data.sensors || [];
    sensors.forEach(s => renderSensorCard(s));
    updateEmptyState();
  } catch (e) {
    console.warn("Failed to load sensors:", e);
  }
}

/* ══════════════════════════════════════════════════════════
   KEYBOARD — Esc closes modal
══════════════════════════════════════════════════════════ */

document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeAddModal();
});

/* ══════════════════════════════════════════════════════════
   BOOT
══════════════════════════════════════════════════════════ */

document.addEventListener("DOMContentLoaded", () => {
  applySavedTheme();
  startClock();
  loadSensors();
});