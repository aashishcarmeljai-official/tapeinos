"use strict";

/* ── State ───────────────────────────────────────────────── */
let _selectedType  = null;
let _selectedColor = "red";
const _sseMap = {};          // sensor_id → EventSource
const _statusPollers = {};   // sensor_id → interval id

/* ══════════════════════════════════════════════════════════
   MODAL
══════════════════════════════════════════════════════════ */

function openAddModal() {
  _selectedType  = null;
  _selectedColor = "red";
  document.getElementById("step-type").classList.remove("modal-step--hidden");
  document.getElementById("step-config").classList.add("modal-step--hidden");
  document.getElementById("modalConfirmBtn").disabled = true;
  document.getElementById("scanHint").textContent = "";
  document.getElementById("sensorName").value = "";
  document.querySelectorAll(".type-card").forEach(c => c.classList.remove("type-card--selected"));
  document.querySelectorAll(".modal__color-swatch").forEach(s => s.classList.remove("modal__color-swatch--selected"));
  const red = document.querySelector(".modal__color-swatch--red");
  if (red) red.classList.add("modal__color-swatch--selected");
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

  const portField   = document.getElementById("portField");
  const cameraField = document.getElementById("cameraField");
  const colorField  = document.getElementById("colorField");

  if (type === "ultrasonic") {
    portField.classList.remove("modal-step--hidden");
    cameraField.classList.add("modal-step--hidden");
    colorField.classList.add("modal-step--hidden");
    scanPorts();
  } else {
    portField.classList.add("modal-step--hidden");
    cameraField.classList.remove("modal-step--hidden");
    colorField.classList.remove("modal-step--hidden");
    scanCameras();
  }
}

function selectModalColor(color, el) {
  _selectedColor = color;
  document.querySelectorAll(".modal__color-swatch").forEach(s => s.classList.remove("modal__color-swatch--selected"));
  el.classList.add("modal__color-swatch--selected");
}

async function scanPorts() {
  const btn  = document.getElementById("scanBtn");
  const hint = document.getElementById("scanHint");
  const sel  = document.getElementById("portSelect");
  btn.classList.add("modal__scan-btn--scanning");
  hint.textContent = "scanning…";
  try {
    const data  = await fetch("/sensors/ports").then(r => r.json());
    const ports = data.ports || [];
    sel.innerHTML = ports.length
      ? ports.map(p => `<option value="${p.port}">${p.port} — ${p.description}</option>`).join("")
      : "<option value=''>No ports found</option>";
    hint.textContent = `${ports.length} port(s) found`;
    hint.style.color = ports.length ? "var(--running)" : "var(--danger)";
    _updateConfirmBtn();
  } catch(e) {
    hint.textContent = `Scan error: ${e.message}`; hint.style.color = "var(--danger)";
  } finally { btn.classList.remove("modal__scan-btn--scanning"); }
}

async function scanCameras() {
  const hint = document.getElementById("scanHint");
  const sel  = document.getElementById("cameraSelect");
  hint.textContent = "scanning cameras…";
  try {
    const data = await fetch("/sensors/cameras").then(r => r.json());
    const cams = data.cameras || [];
    sel.innerHTML = cams.length
      ? cams.map(c => `<option value="${c.index}">${c.label}</option>`).join("")
      : "<option value=''>No cameras found</option>";
    hint.textContent = `${cams.length} camera(s) found`;
    hint.style.color = cams.length ? "var(--running)" : "var(--danger)";
    _updateConfirmBtn();
  } catch(e) {
    hint.textContent = `Scan error: ${e.message}`; hint.style.color = "var(--danger)";
  }
}

function _updateConfirmBtn() {
  const btn = document.getElementById("modalConfirmBtn");
  if (_selectedType === "ultrasonic") {
    btn.disabled = !document.getElementById("portSelect").value;
  } else if (_selectedType === "camera") {
    const v = document.getElementById("cameraSelect").value;
    btn.disabled = (v === "" || v == null);
  } else {
    btn.disabled = true;
  }
}

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
  if (!port && port !== "0") return;

  const btn = document.getElementById("modalConfirmBtn");
  btn.disabled = true; btn.textContent = "Adding…";
  try {
    const res  = await fetch("/sensors/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: _selectedType, name, port, baudrate, color: _selectedColor }),
    });
    const data = await res.json();
    if (data.status === "created") {
      closeAddModal();
      renderSensorCard(data.sensor);
      updateEmptyState();
    } else {
      alert(`Error: ${data.error || "unknown"}`);
    }
  } catch(e) { alert(`Network error: ${e.message}`); }
  finally { btn.disabled = false; btn.textContent = "Add Sensor"; }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("portSelect").addEventListener("change", _updateConfirmBtn);
  document.getElementById("cameraSelect").addEventListener("change", _updateConfirmBtn);
});

/* ══════════════════════════════════════════════════════════
   CARD RENDERING
══════════════════════════════════════════════════════════ */

function renderSensorCard(sensor) {
  const tmpl = document.getElementById("sensorCardTemplate");
  const card = tmpl.content.cloneNode(true).querySelector(".sensor-card");
  const sid  = sensor.id;
  card.dataset.sensorId = sid;

  // Icon
  const iconWrap = card.querySelector(".sensor-card__icon-wrap");
  iconWrap.classList.add(`sensor-card__icon-wrap--${sensor.type}`);
  iconWrap.innerHTML = sensor.type === "ultrasonic"
    ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/><circle cx="12" cy="12" r="4"/></svg>`
    : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>`;

  card.querySelector(".sensor-card__name").textContent = sensor.name || sensor.type;
  const metaEl = card.querySelector(".sensor-card__meta");
  if (sensor.type === "camera") {
    const color = sensor.color || "red";
    metaEl.innerHTML = `<span class="sensor-card__color-dot color-dot--${color}"></span>Camera ${sensor.port} · ${color}`;
  } else {
    metaEl.textContent = `${sensor.port} · ${sensor.baudrate} baud`;
  }

  // Badge
  const badge = card.querySelector(".sensor-card__badge");
  badge.id = `sensor-badge-${sid}`;

  // Toggle
  const toggle = card.querySelector(".sensor-card__toggle-input");
  toggle.dataset.sensorId = sid;
  toggle.addEventListener("change", handleSensorToggle);

  // Actions panel
  const tmplId = sensor.type === "ultrasonic" ? "ultrasonicActionsTemplate" : "cameraActionsTemplate";
  const actClone = document.getElementById(tmplId).content.cloneNode(true);
  card.querySelector(".sensor-card__actions").appendChild(actClone);

  // Camera init
  if (sensor.type === "camera") {
    _initCameraCard(card, sensor, sid);
  }

  // Wire action buttons
  card.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", () => handleSensorAction(sid, btn));
  });

  // Terminal
  card.querySelector(".sensor-terminal__title").textContent = `${sensor.name || sensor.type}`;
  card.querySelector(".sensor-terminal__cmd").textContent =
    sensor.type === "camera" ? `cam_pub --camera-index ${sensor.port}` : `ultrasonic_node --port ${sensor.port}`;
  card.querySelector(".sensor-terminal__body").id = `sensor-term-body-${sid}`;
  card.querySelector(".sensor-terminal__refresh").addEventListener("click", () => refreshSensorTerminal(sid));

  // Restore state
  if (sensor.was_running) {
    toggle.checked = true;
    _setSensorBadge(sid, true);
    card.classList.add("sensor-card--active");
    openSensorLogStream(sid);
  }

  if (sensor.threshold != null) {
    const inp = card.querySelector("[data-action-input='threshold']");
    if (inp) inp.value = sensor.threshold;
    _setActionStatus(card, "threshold", `Active: ${sensor.threshold} cm`, "ok");
  }

  document.getElementById("sensorCards").appendChild(card);

  // Camera: refresh file badges + start poller
  if (sensor.type === "camera") {
    _refreshFileBadges(card, sensor);
    _startStatusPoller(sid);
  }
}

function _initCameraCard(card, sensor, sid) {
  const color = sensor.color || "red";
  card.querySelectorAll(".color-swatch").forEach(sw => {
    sw.classList.toggle("color-swatch--selected", sw.dataset.color === color);
    sw.addEventListener("click", () => {
      card.querySelectorAll(".color-swatch").forEach(s => s.classList.remove("color-swatch--selected"));
      sw.classList.add("color-swatch--selected");
      _doAction(sid, { action: "set_color", color: sw.dataset.color });
    });
  });

  const paramMap = {
    target_z:       sensor.target_z       ?? 0.18,
    step_size:      sensor.step_size       ?? 0.05,
    place_offset_x: sensor.place_offset_x ?? 0.08,
    place_offset_y: sensor.place_offset_y ?? 0.08,
  };
  Object.entries(paramMap).forEach(([k, v]) => {
    const inp = card.querySelector(`[data-param="${k}"]`);
    if (inp) inp.value = v;
  });
}

/* ── File badges ─────────────────────────────────────────── */

const FILE_BADGE_LABELS = {
  "camera_params.npz":     { ok: "✓ Calibrated",      missing: "○ Not calibrated" },
  "homography_points.npz": { ok: "✓ Points saved",    missing: "○ No points" },
  "homography.npz":        { ok: "✓ H matrix",        missing: "○ No H matrix" },
  "homography.txt":        { ok: "✓ H.txt ready",     missing: "○ No H.txt" },
};

function _refreshFileBadges(card, status) {
  // status can be a sensor record or a live-status dict
  const fileMap = {
    "camera_params.npz":     status.calibrated        ?? status["calibrated"],
    "homography_points.npz": status.homography_points ?? status["homography_points"],
    "homography.npz":        status.homography_npz    ?? status["homography_npz"],
    "homography.txt":        status.homography_ready  ?? status["homography_ready"],
  };

  Object.entries(fileMap).forEach(([file, exists]) => {
    const badge = card.querySelector(`[data-file-badge="${file}"]`);
    if (!badge) return;
    const labels = FILE_BADGE_LABELS[file];
    badge.textContent = exists ? labels.ok : labels.missing;
    badge.className   = `calib-badge ${exists ? "calib-badge--ok" : "calib-badge--missing"}`;
  });

  // Update dependency-gated buttons
  _updateActionDeps(card, fileMap);
}

function _updateActionDeps(card, fileMap) {
  card.querySelectorAll("[data-requires]").forEach(btn => {
    const req = btn.dataset.requires;
    if (!req) return;   // no requirement — always enabled
    const met = !!fileMap[req];
    btn.disabled = !met;
    btn.title    = met ? "" : FILE_BADGE_LABELS[req]?.missing.replace("○ ", "") + " first";
  });
}

/* ── Status poller (updates badges after actions complete) ─ */

function _startStatusPoller(sid) {
  if (_statusPollers[sid]) clearInterval(_statusPollers[sid]);
  _statusPollers[sid] = setInterval(async () => {
    const card = document.querySelector(`.sensor-card[data-sensor-id="${sid}"]`);
    if (!card) { clearInterval(_statusPollers[sid]); return; }
    try {
      const data = await fetch("/sensors/list").then(r => r.json());
      const rec  = (data.sensors || []).find(s => s.id === sid);
      if (rec) {
        const status = rec.live || {};
        _refreshFileBadges(card, { ...rec, ...status });
        // Sync action_running state
        _syncActionRunning(card, status.action_running);
      }
    } catch(_) {}
  }, 3000);
}

function _syncActionRunning(card, actionRunning) {
  card.querySelectorAll(".action-launch-btn").forEach(btn => {
    const stopBtn = btn.parentElement.querySelector(".action-stop-btn");
    if (stopBtn) stopBtn.style.display = actionRunning ? "" : "none";
  });
}

/* ══════════════════════════════════════════════════════════
   CARD TOGGLE
══════════════════════════════════════════════════════════ */

function toggleCard(headerEl) {
  headerEl.closest(".sensor-card").classList.toggle("sensor-card--open");
}

/* ══════════════════════════════════════════════════════════
   SENSOR ON/OFF TOGGLE
══════════════════════════════════════════════════════════ */

async function handleSensorToggle(event) {
  const checkbox = event.target;
  const sid      = checkbox.dataset.sensorId;
  const wantsOn  = checkbox.checked;
  const wrap     = checkbox.closest(".toggle");
  wrap.classList.add("toggle--busy");
  checkbox.disabled = true;

  try {
    if (wantsOn) {
      const data = await fetch(`/sensors/start/${sid}`, { method: "POST" }).then(r => r.json());
      if (data.status === "started" || data.status === "already_running") {
        _setSensorBadge(sid, true);
        _setSensorCardActive(sid, true);
        openSensorLogStream(sid);
      } else {
        checkbox.checked = false;
        _setSensorBadge(sid, false);
        _appendTerm(sid, `[error] ${data.message || data.error}`);
      }
    } else {
      await fetch(`/sensors/stop/${sid}`, { method: "POST" });
      closeSensorLogStream(sid);
      _setSensorBadge(sid, false);
      _setSensorCardActive(sid, false);
      _appendTerm(sid, "[stopped]");
    }
  } catch(e) {
    checkbox.checked = !wantsOn;
    _appendTerm(sid, `[network error] ${e.message}`);
  } finally {
    checkbox.disabled = false;
    wrap.classList.remove("toggle--busy");
  }
}

/* ══════════════════════════════════════════════════════════
   ACTIONS
══════════════════════════════════════════════════════════ */

async function handleSensorAction(sid, btn) {
  const action = btn.dataset.action;
  const card   = document.querySelector(`.sensor-card[data-sensor-id="${sid}"]`);

  // Build payload
  const payload = { action };

  if (action === "set_threshold") {
    const inp = card.querySelector("[data-action-input='threshold']");
    const val = parseFloat(inp?.value);
    if (isNaN(val) || val <= 0) { _setActionStatus(card, "threshold", "Enter valid cm value", "error"); return; }
    payload.threshold = val;
  }

  if (action === "set_tracker_params") {
    ["target_z","step_size","place_offset_x","place_offset_y"].forEach(k => {
      const inp = card.querySelector(`[data-param="${k}"]`);
      if (inp) payload[k] = parseFloat(inp.value);
    });
  }

  // Show stop button for launch actions
  if (btn.classList.contains("action-launch-btn")) {
    const stopBtn = btn.parentElement.querySelector(".action-stop-btn");
    if (stopBtn) stopBtn.style.display = "";
  }

  await _doAction(sid, payload, btn, card);
}

async function _doAction(sid, payload, btn, card) {
  const action  = payload.action;
  if (!card) card = document.querySelector(`.sensor-card[data-sensor-id="${sid}"]`);

  const statusKey = _statusKeyFor(action);

  if (btn && typeof btn.disabled !== "undefined") {
    btn.disabled = true;
    btn._orig    = btn.textContent;
    btn.textContent = "…";
  }

  try {
    const res  = await fetch(`/sensors/action/${sid}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (res.ok) {
      _handleActionSuccess(sid, action, data, card, statusKey);
    } else {
      const msg = data.error || "error";
      if (statusKey) _setActionStatus(card, statusKey, msg, "error");
      _appendTerm(sid, `[error] ${action}: ${msg}`);

      // Hide stop button if launch failed
      if (btn?.classList.contains("action-launch-btn")) {
        const stopBtn = btn?.parentElement?.querySelector(".action-stop-btn");
        if (stopBtn) stopBtn.style.display = "none";
      }
    }
  } catch(e) {
    if (statusKey) _setActionStatus(card, statusKey, `Network error: ${e.message}`, "error");
  } finally {
    if (btn && typeof btn.disabled !== "undefined") {
      btn.disabled    = false;
      btn.textContent = btn._orig || btn.textContent;
    }
  }
}

function _statusKeyFor(action) {
  const map = {
    calibrate:          "calibrate",
    collect_homography: "collect_homography",
    compute_homography: "compute_homography",
    convert_homography: "convert_homography",
    track_objects:      "track_objects",
    set_threshold:      "threshold",
    clear_threshold:    "threshold",
    set_tracker_params: "track_objects",
  };
  return map[action] || null;
}

function _handleActionSuccess(sid, action, data, card, statusKey) {
  if (action === "set_threshold") {
    _setActionStatus(card, statusKey, `Active: ${data.threshold} cm`, "ok");

  } else if (action === "clear_threshold") {
    _setActionStatus(card, statusKey, "Cleared", "ok");
    const inp = card.querySelector("[data-action-input='threshold']");
    if (inp) inp.value = "";

  } else if (action === "set_color") {
    const color = data.color;
    const metaEl = card.querySelector(".sensor-card__meta");
    if (metaEl && color) {
      const port = card.querySelector(".sensor-card__meta")?.textContent.match(/Camera (\d+)/)?.[1] || "0";
      metaEl.innerHTML = `<span class="sensor-card__color-dot color-dot--${color}"></span>Camera ${port} · ${color}`;
    }
    _appendTerm(sid, `[config] color → ${data.color}`);

  } else if (action === "set_tracker_params") {
    _setActionStatus(card, statusKey, "Params saved ✓", "ok");

  } else if (action === "stop_action") {
    _setActionStatus(card, null, "", "");
    card.querySelectorAll(".action-stop-btn").forEach(b => b.style.display = "none");

  } else if (["calibrate","collect_homography","compute_homography",
               "convert_homography","track_objects"].includes(action)) {
    _setActionStatus(card, statusKey, `${action} started`, "ok");
    _appendTerm(sid, `[${action}] ${data.message || "started"}`);

  } else if (["get_intrinsic","get_extrinsic","get_distortion"].includes(action)) {
    _setActionStatus(card, "calibrate", data.message || "done", "ok");
  }
}

/* ══════════════════════════════════════════════════════════
   REMOVE SENSOR
══════════════════════════════════════════════════════════ */

async function removeSensor(event, btn) {
  event.stopPropagation();
  const card = btn.closest(".sensor-card");
  const sid  = card.dataset.sensorId;
  if (!confirm("Remove this sensor?")) return;

  closeSensorLogStream(sid);
  if (_statusPollers[sid]) { clearInterval(_statusPollers[sid]); delete _statusPollers[sid]; }

  try { await fetch(`/sensors/remove/${sid}`, { method: "POST" }); } catch(_) {}
  card.style.opacity = "0"; card.style.transform = "translateY(-8px)";
  card.style.transition = "opacity .25s, transform .25s";
  setTimeout(() => { card.remove(); updateEmptyState(); }, 260);
}

/* ══════════════════════════════════════════════════════════
   SSE LOG STREAM
══════════════════════════════════════════════════════════ */

function openSensorLogStream(sid) {
  closeSensorLogStream(sid);
  const es = new EventSource(`/sensors/logs/${sid}`);
  _sseMap[sid] = es;
  es.onmessage = e  => _appendTerm(sid, e.data);
  es.onerror   = () => { es.close(); delete _sseMap[sid]; };
}

function closeSensorLogStream(sid) {
  if (_sseMap[sid]) { _sseMap[sid].close(); delete _sseMap[sid]; }
}

function refreshSensorTerminal(sid) {
  const body = document.getElementById(`sensor-term-body-${sid}`);
  if (body) body.innerHTML = `<span class="terminal__idle">▸ reloading…</span>`;
  const toggle = document.querySelector(`.sensor-card[data-sensor-id="${sid}"] .sensor-card__toggle-input`);
  if (toggle?.checked) { closeSensorLogStream(sid); openSensorLogStream(sid); }
}

/* ══════════════════════════════════════════════════════════
   TERMINAL
══════════════════════════════════════════════════════════ */

function _appendTerm(sid, text) {
  const body = document.getElementById(`sensor-term-body-${sid}`);
  if (!body) return;
  const idle = body.querySelector(".terminal__idle");
  if (idle) idle.remove();

  const line = document.createElement("span");
  line.className = "terminal__line " + _lineClass(text);
  line.textContent = text;
  body.appendChild(line);
  body.appendChild(document.createTextNode("\n"));

  const lines = body.querySelectorAll(".terminal__line");
  if (lines.length > 600) {
    const excess = lines.length - 600;
    for (let i = 0; i < excess; i++) {
      const n = lines[i], nx = n.nextSibling;
      n.remove(); if (nx?.nodeType === 3) nx.remove();
    }
  }
  body.scrollTop = body.scrollHeight;
}

function _lineClass(t) {
  t = t.toLowerCase();
  if (t.includes("[error]") || t.includes("error:") || t.includes("❌") || t.includes("failed")) return "terminal__line--error";
  if (t.includes("[warn]")  || t.includes("warning") || t.includes("⚠"))                         return "terminal__line--warn";
  if (t.includes("[stopped]") || t.includes("[exited"))                                            return "terminal__line--warn";
  if (t.includes("✓") || t.includes("✔") || t.includes("complete") || t.includes("[ok]"))        return "terminal__line--ok";
  if (t.includes("[") && (t.includes("started") || t.includes("config") || t.includes("tracker")
      || t.includes("calibrat") || t.includes("homograph") || t.includes("collect")
      || t.includes("compute") || t.includes("convert") || t.includes("ros")))                    return "terminal__line--info";
  return "";
}

/* ══════════════════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════════════════ */

function _setActionStatus(card, key, msg, type) {
  if (!key || !card) return;
  const el = card.querySelector(`[data-action-status="${key}"]`);
  if (!el) return;
  el.textContent = msg;
  el.className   = `action-status${type === "ok" ? " action-status--ok" : type === "error" ? " action-status--error" : ""}`;
}

function _setSensorBadge(sid, running) {
  const b = document.getElementById(`sensor-badge-${sid}`);
  if (!b) return;
  b.textContent = running ? "● RUNNING" : "● STOPPED";
  b.className   = `badge ${running ? "badge--running" : "badge--stopped"} sensor-card__badge`;
}

function _setSensorCardActive(sid, active) {
  document.querySelector(`.sensor-card[data-sensor-id="${sid}"]`)
    ?.classList.toggle("sensor-card--active", active);
}

function updateEmptyState() {
  const cards = document.getElementById("sensorCards");
  const empty = document.getElementById("sensorsEmpty");
  const has   = cards.children.length > 0;
  empty.style.display = has ? "none" : "";
  cards.style.display = has ? ""     : "none";
}

/* ══════════════════════════════════════════════════════════
   LOAD
══════════════════════════════════════════════════════════ */

async function loadSensors() {
  try {
    const data = await fetch("/sensors/list").then(r => r.json());
    (data.sensors || []).forEach(s => renderSensorCard(s));
    updateEmptyState();
  } catch(e) { console.warn("Failed to load sensors:", e); }
}

document.addEventListener("keydown", e => { if (e.key === "Escape") closeAddModal(); });
document.addEventListener("DOMContentLoaded", () => { applySavedTheme(); startClock(); loadSensors(); });