/**
 * Tapeinos — scripts.js
 * =====================
 * - Dark / light theme toggle (persisted)
 * - Global terminal drawer (slide-down) with tabs
 * - Toggle switches → Flask API start / stop
 * - SSE log streaming → drawer terminals
 * - ROS log colour coding
 * - Live topbar clock
 * - Esc closes drawer
 * - Jog control pad + physical keyboard binding
 * - Per-terminal refresh button (reconnects SSE — replays server buffer)
 */

"use strict";

/* ── Config ──────────────────────────────────────────────── */
const MAX_TERM_LINES = 500;
const PANELS = ["microros", "servo", "moveit", "jog"];

/** Open SSE EventSource handles, keyed by panel name. */
const eventSources = {};

/** Whether the global terminal drawer is open. */
let drawerOpen = false;

/** Active tab in the drawer. */
let drawerTab = "microros";

/* ══════════════════════════════════════════════════════════
   THEME
══════════════════════════════════════════════════════════ */

function toggleTheme() {
  const html  = document.documentElement;
  const next  = html.getAttribute("data-theme") === "dark" ? "light" : "dark";
  html.setAttribute("data-theme", next);
  try { localStorage.setItem("tapeinos-theme", next); } catch(_) {}
}

function applySavedTheme() {
  try {
    const saved = localStorage.getItem("tapeinos-theme");
    if (saved === "dark" || saved === "light")
      document.documentElement.setAttribute("data-theme", saved);
  } catch(_) {}
}

/* ══════════════════════════════════════════════════════════
   GLOBAL TERMINAL DRAWER
══════════════════════════════════════════════════════════ */

function toggleTermDrawer() {
  const drawer = document.getElementById("termDrawer");
  const btn    = document.getElementById("termDrawerBtn");
  if (!drawer) return;

  drawerOpen = !drawerOpen;
  drawer.classList.toggle("drawer--open", drawerOpen);
  drawer.setAttribute("aria-hidden", String(!drawerOpen));
  btn.setAttribute("aria-expanded", String(drawerOpen));
  btn.classList.toggle("btn--active", drawerOpen);

  if (drawerOpen) scrollDrawerBody();
}

function switchDrawerTab(tab, clickedBtn) {
  drawerTab = tab;

  document.querySelectorAll(".term-drawer__tab").forEach(b => b.classList.remove("tab--active"));
  clickedBtn.classList.add("tab--active");

  ["dt-microros","dt-servo","dt-moveit","dt-jog","dt-all"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
  });

  const targetId = tab === "all" ? "dt-all" : `dt-${tab}`;
  const target = document.getElementById(targetId);
  if (target) target.style.display = "flex";

  scrollDrawerBody();
}

function scrollDrawerBody() {
  setTimeout(() => {
    if (drawerTab === "all") {
      PANELS.forEach(p => {
        const b = document.getElementById(`dterm-body-all-${p}`);
        if (b) b.scrollTop = b.scrollHeight;
      });
    } else {
      const b = document.getElementById(`dterm-body-${drawerTab}`);
      if (b) b.scrollTop = b.scrollHeight;
    }
  }, 40);
}

/* ══════════════════════════════════════════════════════════
   TERMINAL HELPERS
══════════════════════════════════════════════════════════ */

function classifyLine(text) {
  const t = text.toLowerCase();
  if (t.includes("[error]") || t.includes("error:") || t.includes("fatal"))    return "terminal__line--error";
  if (t.includes("[warn]")  || t.includes("warning"))                           return "terminal__line--warn";
  if (t.includes("[info]")  || t.includes("started") || t.includes("launched")) return "terminal__line--info";
  if (t.includes("[ok]")    || t.includes("success")  || t.includes("ready"))   return "terminal__line--ok";
  if (t.startsWith("[stop") || t.startsWith("[start"))                           return "terminal__line--info";
  if (t.startsWith("[cmd]"))                                                     return "terminal__line--info";
  return "";
}

function appendToBody(body, text, shouldScroll) {
  if (!body) return;
  const idle = body.querySelector(".terminal__idle");
  if (idle) idle.remove();

  const line = document.createElement("span");
  line.className = "terminal__line " + classifyLine(text);
  line.textContent = text;
  body.appendChild(line);
  body.appendChild(document.createTextNode("\n"));

  // Trim old lines
  const lines = body.querySelectorAll(".terminal__line");
  if (lines.length > MAX_TERM_LINES) {
    const excess = lines.length - MAX_TERM_LINES;
    for (let i = 0; i < excess; i++) {
      const node = lines[i], next = node.nextSibling;
      node.remove();
      if (next && next.nodeType === Node.TEXT_NODE) next.remove();
    }
  }
  if (shouldScroll) body.scrollTop = body.scrollHeight;
}

/** Write a log line into all drawer terminals for a panel. */
function appendTermLine(panel, text) {
  appendToBody(
    document.getElementById(`dterm-body-${panel}`),
    text,
    drawerOpen && drawerTab === panel
  );
  appendToBody(
    document.getElementById(`dterm-body-all-${panel}`),
    text,
    drawerOpen && drawerTab === "all"
  );
}

/** Full reset — used when a process is (re)started. */
function resetTerminal(panel) {
  const idle = `<span class="terminal__idle">▸ toggle ON to start process…</span>`;
  [`dterm-body-${panel}`, `dterm-body-all-${panel}`].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = idle;
  });
  const dpid = document.getElementById(`dpid-${panel}`);
  if (dpid) dpid.textContent = "";
}

/**
 * Refresh a terminal — closes the existing SSE connection, wipes the
 * visible output, then reopens the SSE stream.  Because the server keeps
 * a ring-buffer of the last 200 lines, the new connection immediately
 * replays all recent output so nothing is lost.
 *
 * If the panel is not currently running the function falls back to a
 * simple visual clear so the button never appears broken.
 *
 * Called by the ⟳ button in each terminal titlebar.
 */
function refreshTerminal(panel) {
  // Animate every refresh button associated with this panel
  document.querySelectorAll(`.terminal__refresh[data-panel="${panel}"]`).forEach(btn => {
    btn.classList.add("terminal__refresh--spinning");
    setTimeout(() => btn.classList.remove("terminal__refresh--spinning"), 600);
  });

  // Wipe visible output in both the single-panel view and the "all" view
  const reloadMsg = `<span class="terminal__idle">▸ reloading log…</span>`;
  [`dterm-body-${panel}`, `dterm-body-all-${panel}`].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = reloadMsg;
  });

  // Only reconnect SSE if the process is actually running
  const cb = document.querySelector(`.toggle__input[data-panel="${panel}"]`);
  if (cb && cb.checked) {
    // Close the old stream then open a fresh one — the server replays its
    // ring-buffer on every new connection, so history comes back instantly.
    closeLogStream(panel);
    openLogStream(panel);
  } else {
    // Process is stopped — just show a friendly idle message after a tick
    setTimeout(() => {
      [`dterm-body-${panel}`, `dterm-body-all-${panel}`].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = `<span class="terminal__idle">▸ toggle ON to start process…</span>`;
      });
    }, 400);
  }
}

/* ══════════════════════════════════════════════════════════
   BADGE + CARD ACTIVE STATE
══════════════════════════════════════════════════════════ */

function setBadge(panel, running) {
  const badge = document.getElementById(`badge-${panel}`);
  if (badge) {
    badge.textContent = running ? "● RUNNING" : "● STOPPED";
    badge.className   = running ? "badge badge--running" : "badge badge--stopped";
  }
}

function setCardActive(panel, active) {
  const card = document.getElementById(`card-${panel}`);
  if (card) card.classList.toggle("card--active", active);
  const label = document.getElementById(`tlabel-${panel}`);
  if (label) {
    label.textContent = active ? "ON" : "OFF";
    label.className   = active
      ? "toggle__label toggle__label--on"
      : "toggle__label toggle__label--off";
  }
}

/* ══════════════════════════════════════════════════════════
   SSE LOG STREAMING
══════════════════════════════════════════════════════════ */

function openLogStream(panel) {
  closeLogStream(panel);
  const es = new EventSource(`/logs/${panel}`);
  eventSources[panel] = es;
  es.onmessage = e  => { appendTermLine(panel, e.data); };
  es.onerror   = () => { es.close(); delete eventSources[panel]; };
}

function closeLogStream(panel) {
  if (eventSources[panel]) {
    eventSources[panel].close();
    delete eventSources[panel];
  }
}

/* ══════════════════════════════════════════════════════════
   API
══════════════════════════════════════════════════════════ */

async function postCommand(url) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/* ══════════════════════════════════════════════════════════
   TOGGLE HANDLER
══════════════════════════════════════════════════════════ */

async function handleToggle(event) {
  const checkbox = event.target;
  const panel    = checkbox.dataset.panel;
  const startUrl = checkbox.dataset.start;
  const stopUrl  = checkbox.dataset.stop;
  const wantsOn  = checkbox.checked;

  const toggleEl = checkbox.closest(".toggle");
  toggleEl.classList.add("toggle--busy");
  checkbox.disabled = true;

  try {
    if (wantsOn) {
      resetTerminal(panel);
      openLogStream(panel);

      if (!drawerOpen) toggleTermDrawer();
      const tabBtn = document.querySelector(`.term-drawer__tab[data-target="dt-${panel}"]`);
      if (tabBtn) switchDrawerTab(panel, tabBtn);

      const data = await postCommand(startUrl);
      if (data.status === "error") {
        checkbox.checked = false;
        setBadge(panel, false);
        setCardActive(panel, false);
        appendTermLine(panel, `[ERROR] ${data.message}`);
      } else {
        setBadge(panel, true);
        setCardActive(panel, true);
        if (data.pid) {
          const dpid = document.getElementById(`dpid-${panel}`);
          if (dpid) dpid.textContent = `PID ${data.pid}`;
        }
      }
    } else {
      await postCommand(stopUrl);
      closeLogStream(panel);
      setBadge(panel, false);
      setCardActive(panel, false);
      appendTermLine(panel, "[process terminated]");
      const dpid = document.getElementById(`dpid-${panel}`);
      if (dpid) dpid.textContent = "";
    }
  } catch (err) {
    checkbox.checked = !wantsOn;
    setBadge(panel, !wantsOn);
    setCardActive(panel, !wantsOn);
    appendTermLine(panel, `[NETWORK ERROR] ${err.message}`);
  } finally {
    checkbox.disabled = false;
    toggleEl.classList.remove("toggle--busy");
  }
}

/* ══════════════════════════════════════════════════════════
   INITIAL STATUS SYNC
══════════════════════════════════════════════════════════ */

async function syncInitialStatus() {
  try {
    const res = await fetch("/status");
    if (!res.ok) return;
    const data = await res.json();
    PANELS.forEach(panel => {
      const info    = data[panel] || {};
      const running = info.state === "running";

      // Restore toggle + badge + card state
      const cb = document.querySelector(`.toggle__input[data-panel="${panel}"]`);
      if (cb) cb.checked = running;
      setBadge(panel, running);
      setCardActive(panel, running);

      // Restore terminal log content from the server-side ring buffer
      if (info.logs && info.logs.length > 0) {
        info.logs.forEach(line => appendTermLine(panel, line));
      }

      // Update PID display if running
      if (running && info.pid) {
        const dpid = document.getElementById(`dpid-${panel}`);
        if (dpid) dpid.textContent = `PID ${info.pid}`;
      }

      // Re-open SSE stream so new lines keep arriving
      if (running) openLogStream(panel);
    });
  } catch(e) { console.warn("Status sync failed:", e); }
}

/* ══════════════════════════════════════════════════════════
   LIVE CLOCK
══════════════════════════════════════════════════════════ */

function startClock() {
  const el = document.getElementById("clock");
  if (!el) return;
  const tick = () => {
    const d = new Date();
    el.textContent = [
      String(d.getHours()).padStart(2,"0"),
      String(d.getMinutes()).padStart(2,"0"),
      String(d.getSeconds()).padStart(2,"0"),
    ].join(":");
  };
  tick(); setInterval(tick, 1000);
}

/* ══════════════════════════════════════════════════════════
   JOG CONTROL
══════════════════════════════════════════════════════════ */

async function sendJogCmd(key) {
  const btn = document.querySelector(`.jog-btn[data-key="${CSS.escape(key)}"]`);
  if (btn) {
    btn.classList.add("jog-btn--active");
    setTimeout(() => btn.classList.remove("jog-btn--active"), 180);
  }
  try {
    await fetch(`/jog_cmd/${encodeURIComponent(key)}`, { method: "POST" });
  } catch(e) {
    console.warn("jog_cmd failed:", e);
  }
}

function initJogKeyboard() {
  const JOG_KEYS = new Set([
    "w","s","a","d","r","f",
    "c","j",
    "o","p",
    "1","2","3","4","5","6",
    "!","@","#","$","%","^",
  ]);

  document.addEventListener("keydown", e => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    const jogCb = document.querySelector('.toggle__input[data-panel="jog"]');
    if (!jogCb || !jogCb.checked) return;
    if (JOG_KEYS.has(e.key)) {
      e.preventDefault();
      sendJogCmd(e.key);
    }
  });
}

/* ══════════════════════════════════════════════════════════
   KEYBOARD (global)
══════════════════════════════════════════════════════════ */

document.addEventListener("keydown", e => {
  if (e.key === "Escape" && drawerOpen) { toggleTermDrawer(); return; }
  if (e.key === "`" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); toggleTermDrawer(); }
});

/* ══════════════════════════════════════════════════════════
   BOOT
══════════════════════════════════════════════════════════ */

document.addEventListener("DOMContentLoaded", () => {
  applySavedTheme();
  document.querySelectorAll(".toggle__input").forEach(cb => cb.addEventListener("change", handleToggle));
  syncInitialStatus();
  startClock();
  initJogKeyboard();
  console.info("Tapeinos ready. Ctrl+` to open terminal drawer.");
});