// ColdChain console — vanilla JS single-page app (no build step, no CDN dependency).
// Talks to the FastAPI backend (/api/*) and listens on /ws for live pushes.
"use strict";

const S = {
  token: sessionStorage.getItem("cc_token"),
  user: JSON.parse(sessionStorage.getItem("cc_user") || "null"),
  ws: null, wsOk: false, dash: null, fridges: [], refreshTimer: null,
};
const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");
const can = p => !!(S.user && S.user.permissions.includes(p));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toFixed(d);
const timeOf = ts => ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
const dateTimeOf = ts => ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–";
const STATUS_WORD = { SAFE: "Safe", WARNING: "Warning", BREACH: "Breach", FAULT: "Sensor fault", AT_RISK: "At risk", DISCARD: "Discard" };

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401 && path !== "/api/auth/login") { logout("Your session ended. Log in again."); throw new Error("Logged out"); }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail && (typeof data.detail === "string" ? data.detail : "Check the form fields")) || `Request failed (${res.status})`);
  return data;
}

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "critical" ? 9000 : 4500);
}

function logout(msg) {
  sessionStorage.clear();
  S.token = null; S.user = null;
  if (S.ws) { S.ws.onclose = null; S.ws.close(); S.ws = null; }
  location.hash = "#/login";
  if (msg) setTimeout(() => toast(msg), 50);
}

// ---------------------------------------------------------------- live updates
function connectWS() {
  if (!S.token || S.ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(S.token)}`);
  S.ws = ws;
  ws.onopen = () => { S.wsOk = true; setLive(); };
  ws.onclose = () => { S.wsOk = false; S.ws = null; setLive(); if (S.token) setTimeout(connectWS, 3000); };
  ws.onmessage = ev => {
    const e = JSON.parse(ev.data);
    if (e.type === "alert" && ["critical", "warning"].includes(e.severity)) toast(e.message, e.severity === "critical" ? "critical" : "");
    scheduleRefresh();
  };
  setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 25000);
}
function setLive() {
  const el = $("#live");
  if (el) el.innerHTML = `<span class="dot ${S.wsOk ? "on" : ""}"></span>${S.wsOk ? "Live" : "Reconnecting…"}`;
}
function scheduleRefresh() {
  clearTimeout(S.refreshTimer);
  S.refreshTimer = setTimeout(() => { const v = currentView(); if (["dashboard", "inventory"].includes(v)) render(true); }, 700);
}
setInterval(() => { if (S.token && !S.wsOk && currentView() === "dashboard") render(true); }, 10000);

// ---------------------------------------------------------------- shell + router
const NAV = [
  { id: "dashboard", label: "Dashboard", perm: "dashboard.view", icon: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"/>' },
  { id: "inventory", label: "Inventory", perm: "inventory.view", icon: '<path d="M4 7h16M4 12h16M4 17h10"/>' },
  { id: "logs", label: "Logs", perm: "logs.view", icon: '<path d="M7 3h10l4 4v14H7zM11 11h6M11 15h6"/>' },
  { id: "ai", label: "AI", perm: "ai.view", icon: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M6 18l2-2M16 8l2-2"/><circle cx="12" cy="12" r="3"/>' },
  { id: "admin", label: "Admin", perm: "users.manage", icon: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>' },
];
const LOGO = '<svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="var(--glacier)"/><path d="M16 6v20M8 11l16 10M24 11L8 21" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>';

function currentView() { return (location.hash.replace(/^#\//, "").split("/")[0]) || "dashboard"; }

function shell(view, title, inner) {
  return `<div class="shell">
    <aside class="side">
      <div class="brand">${LOGO}<span>ColdChain</span></div>
      <nav class="nav" aria-label="Main">
        ${NAV.filter(n => can(n.perm)).map(n => `<a href="#/${n.id}" ${n.id === view ? 'aria-current="page"' : ""}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${n.icon}</svg>${n.label}</a>`).join("")}
      </nav>
      <div class="who"><strong>${esc(S.user.username)}</strong>${esc(S.user.role)}<br><button class="btn small" id="logout" style="margin-top:8px">Log out</button></div>
    </aside>
    <main class="main">
      <div class="topline"><h1>${esc(title)}</h1><span class="live" id="live"></span></div>
      ${inner}
    </main>
  </div>`;
}

async function render(soft = false) {
  if (!S.token) return renderLogin();
  const view = currentView();
  const views = { dashboard: viewDashboard, inventory: viewInventory, logs: viewLogs, ai: viewAI, admin: viewAdmin };
  const fn = views[view] || viewDashboard;
  try {
    const keepScroll = window.scrollY;
    await fn(soft);
    if (soft) window.scrollTo(0, keepScroll);
    setLive();
    const lo = $("#logout"); if (lo) lo.onclick = () => logout();
  } catch (e) {
    if (e.message !== "Logged out") toast(e.message, "critical");
  }
}
window.addEventListener("hashchange", () => render());

// ---------------------------------------------------------------- login
function renderLogin() {
  if (location.hash !== "#/login") history.replaceState(null, "", "#/login");
  app.innerHTML = `<div class="login"><form class="panel" id="login-form">
      <div class="brand">${LOGO}<span>ColdChain</span></div>
      <p class="muted" style="margin:0">Vaccine and medicine storage monitor</p>
      <label>Username<input name="username" autocomplete="username" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <div class="error" id="login-error"></div>
      <button class="btn primary" type="submit">Log in</button>
    </form></div>`;
  $("#login-form").onsubmit = async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
      S.token = r.access_token; S.user = r.user;
      sessionStorage.setItem("cc_token", S.token);
      sessionStorage.setItem("cc_user", JSON.stringify(S.user));
      location.hash = "#/dashboard";
      connectWS();
    } catch (e) { $("#login-error").textContent = e.message; }
  };
}

// ---------------------------------------------------------------- dashboard
const SCALE_MIN = 0, SCALE_MAX = 16;
const pos = t => Math.max(0, Math.min(100, (t - SCALE_MIN) / (SCALE_MAX - SCALE_MIN) * 100));

function stripHTML(f) {
  const ticks = [0, 2, 4, 6, 8, 10, 12, 14, 16];
  return `<div class="strip" role="img" aria-label="Temperature ${fmt(f.temp)} °C on a 0 to 16 °C scale; safe band 2 to 8 °C${f.pred_temp_30 != null ? `; forecast ${fmt(f.pred_temp_30)} °C in 30 minutes` : ""}">
    <div class="track"></div>
    <div class="safe" style="left:${pos(2)}%;width:${pos(8) - pos(2)}%"></div>
    ${f.pred_temp_30 != null ? `<div class="fc" style="left:${pos(f.pred_temp_30)}%" title="Forecast in 30 min: ${fmt(f.pred_temp_30)} °C"></div>` : ""}
    ${f.temp != null ? `<div class="now" style="left:${pos(f.temp)}%"></div>` : ""}
    ${ticks.map(t => `<span class="tick" style="left:${pos(t)}%">${t}</span>`).join("")}
  </div>`;
}

function healthMeter(score) {
  if (score == null) return `<span class="muted">Learning…</span>`;
  const cls = score >= 70 ? "" : score >= 40 ? "mid" : "low";
  return `<div style="display:grid;grid-template-columns:90px auto;gap:8px;align-items:center"><div class="meter ${cls}"><i style="width:${score}%"></i></div><strong>${fmt(score, 0)}</strong></div>`;
}

function fridgePanel(f) {
  const st = f.online ? (f.state || "SAFE") : null;
  const doorTxt = f.door ? `Open ${f.door_open_s}s` : "Closed";
  const lockout = f.comp ? "Running" : "Off";
  const power = f.power === "BATTERY" ? `Battery ${f.batt ?? "–"}%` : `Mains${f.batt != null && f.batt < 100 ? ` · ${f.batt}%` : ""}`;
  const batches = f.batches.length ? f.batches.map(b => `
      <div class="batch">
        <div><div class="name">${esc(b.vaccine)}</div><div class="sub">Lot ${esc(b.lot_no)} · ${b.quantity} units · expires ${esc(b.expiry_date)}${b.expired ? " (expired)" : ""}</div></div>
        <span class="chip ${esc(b.status)}">${STATUS_WORD[b.status]}</span>
        <div class="budget"><div class="meter ${b.remaining_pct < 20 ? "low" : b.remaining_pct < 50 ? "mid" : ""}"><i style="width:${b.remaining_pct}%"></i></div><span>${fmt(b.remaining_min, 0)} min left</span></div>
      </div>`).join("") : `<p class="muted">No batches stored here.</p>`;
  return `<section class="panel fridge" aria-labelledby="h-${esc(f.id)}">
    <div class="fridge-head">
      <h2 id="h-${esc(f.id)}">${esc(f.name)}</h2>
      ${f.online ? `<span class="chip ${esc(st)}">${STATUS_WORD[st] || esc(st)}</span>` : `<span class="chip off">Offline${f.seen_ago_s != null ? ` · ${f.seen_ago_s}s` : ""}</span>`}
    </div>
    <div class="reading">
      <div class="temp ${f.online ? esc(st) : ""}">${fmt(f.temp)}<small>°C</small></div>
      <div class="side-info muted">
        <span>Updated ${timeOf(f.state_ts)}${f.backlog > 1 ? ` · syncing ${f.backlog} buffered` : ""}</span>
        ${f.led_latch ? `<span class="chip DISCARD">Red LED latched (discard)</span>` : ""}
      </div>
    </div>
    ${stripHTML(f)}
    <dl class="facts">
      <div><dt>Door</dt><dd class="${f.door && f.door_open_s > 60 ? "alarm" : ""}">${doorTxt}</dd></div>
      <div><dt>Compressor</dt><dd>${lockout}</dd></div>
      <div><dt>Power</dt><dd class="${f.power === "BATTERY" ? "alarm" : ""}">${power}</dd></div>
      <div><dt>Stock</dt><dd>${f.units_used}/${f.capacity_units}</dd></div>
    </dl>
    <div class="ai-line ${f.pred_warning ? "warn" : ""}">
      <span><strong>30-min forecast:</strong> ${esc(f.pred_note || "Collecting 30 min of data…")}</span>
      <span>${f.pred_temp_30 != null ? `${fmt(f.pred_temp_30)} °C` : ""}</span>
      <span><strong>Compressor health</strong> <span class="muted">${esc(f.health_note || "")}</span></span>
      ${healthMeter(f.health_score)}
    </div>
    <canvas class="spark" data-fridge="${esc(f.id)}" aria-label="Last 60 minutes of temperature for ${esc(f.name)}"></canvas>
    <div>${batches}</div>
  </section>`;
}

function relocBanner(r) {
  const p = r.plan;
  const moves = p.moves.map(m => `<li><strong>${esc(m.vaccine)}</strong> lot ${esc(m.lot_no)} (${m.quantity} units, ${fmt(m.remaining_pct, 0)}% budget) → ${esc(m.to_name)}</li>`).join("");
  const unplaced = p.unplaced.map(m => `<li><strong>${esc(m.vaccine)}</strong> lot ${esc(m.lot_no)}: no safe fridge with room</li>`).join("");
  return `<section class="reloc" aria-live="assertive">
    <h2>${esc(p.source_name)} may become unsafe</h2>
    <div>${p.reasons.map(esc).join("; ")}.</div>
    <ul>${moves}${unplaced}</ul>
    <div class="muted" style="color:var(--ink)">${esc(p.advice)}</div>
    ${can("relocation.confirm") ? `<div class="actions">
      <button class="btn primary" data-confirm="${r.id}" ${p.moves.length ? "" : "disabled"}>Confirm and move ${p.moves.length} batch${p.moves.length === 1 ? "" : "es"}</button>
      <button class="btn" data-dismiss="${r.id}">Dismiss</button></div>` : `<div class="muted">A pharmacist must confirm this move.</div>`}
  </section>`;
}

function alertsPanel(alerts) {
  if (!alerts.length) return `<p class="muted">No alerts yet.</p>`;
  return alerts.slice(0, 12).map(a => {
    const sent = a.delivery ? Object.entries(a.delivery).map(([k, v]) => `${k}: ${v}`).join(", ") : "";
    return `<div class="alert"><span class="sev ${esc(a.severity)}"></span>
      <div>${esc(a.message)}<time>${dateTimeOf(a.ts)}${sent ? ` · ${esc(sent)}` : ""}${a.ack_by ? ` · acknowledged by ${esc(a.ack_by.replace("user:", ""))}` : ""}</time></div>
      ${!a.ack_by && can("alert.ack") && a.severity !== "info" ? `<button class="btn small" data-ack="${a.id}">Acknowledge</button>` : "<span></span>"}
    </div>`;
  }).join("");
}

async function viewDashboard() {
  const d = await api("/api/dashboard");
  S.dash = d;
  const s = d.summary;
  app.innerHTML = shell("dashboard", "Storage overview", `<div class="stack">
    ${d.relocations.map(relocBanner).join("")}
    <p class="muted" style="margin:0">${s.SAFE} batches safe, ${s.AT_RISK} at risk, ${s.DISCARD} to discard.</p>
    <div class="grid2">${d.fridges.map(fridgePanel).join("")}</div>
    <section class="panel"><h2>Alerts</h2>${alertsPanel(d.alerts)}</section>
  </div>`);
  app.querySelectorAll("[data-confirm]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api(`/api/relocations/${b.dataset.confirm}/confirm`, { method: "POST" });
      toast(`Moved ${r.applied.length} batch(es)${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`);
      render(true);
    } catch (e) { toast(e.message, "critical"); b.disabled = false; }
  });
  app.querySelectorAll("[data-dismiss]").forEach(b => b.onclick = async () => {
    await api(`/api/relocations/${b.dataset.dismiss}/dismiss`, { method: "POST", body: { reason: "dismissed from dashboard" } });
    render(true);
  });
  app.querySelectorAll("[data-ack]").forEach(b => b.onclick = async () => {
    await api(`/api/alerts/${b.dataset.ack}/ack`, { method: "POST" });
    render(true);
  });
  for (const c of app.querySelectorAll("canvas.spark")) {
    api(`/api/fridges/${encodeURIComponent(c.dataset.fridge)}/history?minutes=60`).then(h => drawSpark(c, h)).catch(() => {});
  }
}

// Temperature over the last hour with the 2–8 °C band and door-open marks
function drawSpark(canvas, h) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, ht = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = ht * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const col = n => css.getPropertyValue(n).trim();
  const rows = h.readings.filter(r => r.temp != null);
  const now = Date.now() / 1000, t0 = now - 3600;
  const temps = rows.map(r => r.temp);
  const lo = Math.min(1, ...temps), hi = Math.max(10, ...temps);
  const X = t => 28 + (t - t0) / 3600 * (w - 32), Y = v => 6 + (hi - v) / (hi - lo) * (ht - 24);
  g.fillStyle = col("--band"); g.fillRect(28, Y(8), w - 32, Y(2) - Y(8));
  g.fillStyle = col("--slate"); g.font = "11px system-ui"; g.textAlign = "right";
  [2, 8].forEach(v => g.fillText(String(v), 24, Y(v) + 4));
  g.textAlign = "left"; g.fillText("60 min ago", 28, ht - 4); g.textAlign = "right"; g.fillText("now", w - 4, ht - 4);
  g.fillStyle = col("--warn");
  h.readings.forEach(r => { if (r.door) g.fillRect(X(r.ts), ht - 18, 2, 5); });
  if (rows.length < 2) { g.textAlign = "center"; g.fillStyle = col("--slate"); g.fillText("Waiting for data", w / 2, ht / 2); return; }
  g.strokeStyle = col("--ink"); g.lineWidth = 1.6; g.beginPath();
  rows.forEach((r, i) => i ? g.lineTo(X(r.ts), Y(r.temp)) : g.moveTo(X(r.ts), Y(r.temp)));
  g.stroke();
}

// ---------------------------------------------------------------- inventory
async function viewInventory() {
  const [batches, fridges] = await Promise.all([api("/api/batches"), api("/api/fridges")]);
  S.fridges = fridges;
  const fOpts = sel => fridges.map(f => `<option value="${esc(f.id)}" ${f.id === sel ? "selected" : ""}>${esc(f.name)}</option>`).join("");
  const form = can("inventory.edit") ? `<section class="panel"><h2>Receive a batch</h2>
    <form id="add-batch" class="form-row">
      <label>Vaccine or medicine<input name="vaccine" required maxlength="80"></label>
      <label>Lot number<input name="lot_no" required maxlength="40"></label>
      <label>Units<input name="quantity" type="number" min="1" max="100000" required></label>
      <label>Expiry<input name="expiry_date" type="date" required></label>
      <label>Fridge<select name="fridge_id">${fOpts()}</select></label>
      <button class="btn primary" type="submit">Add batch</button>
    </form><div class="error" id="add-error"></div></section>` : "";
  const rows = batches.map(b => `<tr>
      <td><strong>${esc(b.vaccine)}</strong><div class="muted">Lot ${esc(b.lot_no)}</div></td>
      <td class="num">${b.quantity}</td>
      <td>${esc(fridges.find(f => f.id === b.fridge_id)?.name || b.fridge_id)}</td>
      <td>${esc(b.expiry_date)}${b.expired ? ` <span class="chip bad">Expired</span>` : ""}</td>
      <td><span class="chip ${esc(b.status)}">${STATUS_WORD[b.status]}</span></td>
      <td style="min-width:130px"><div class="budget"><div class="meter ${b.remaining_pct < 20 ? "low" : b.remaining_pct < 50 ? "mid" : ""}"><i style="width:${b.remaining_pct}%"></i></div><span>${fmt(b.remaining_min, 0)} min</span></div></td>
      <td><div class="row-actions">
        ${can("batch.move") && b.status !== "DISCARD" ? `<select data-move="${b.id}" aria-label="Move ${esc(b.vaccine)} to"><option value="">Move to…</option>${fridges.filter(f => f.id !== b.fridge_id).map(f => `<option value="${esc(f.id)}">${esc(f.name)}</option>`).join("")}</select>` : ""}
        <button class="btn small" data-qr="${b.id}" data-pub="${esc(b.public_id)}">QR</button>
        ${can("inventory.edit") ? `<button class="btn small danger" data-remove="${b.id}">Remove</button>` : ""}
      </div></td></tr>`).join("");
  app.innerHTML = shell("inventory", "Inventory", `<div class="stack">${form}
    <section class="panel"><h2>Batches in storage</h2><div class="table-wrap"><table>
      <thead><tr><th>Batch</th><th>Units</th><th>Fridge</th><th>Expiry</th><th>Status</th><th>Heat budget left</th><th><span class="sr">Actions</span></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="7" class="muted">No batches yet. Receive one with the form above.</td></tr>`}</tbody>
    </table></div></section></div>`);
  const add = $("#add-batch");
  if (add) add.onsubmit = async ev => {
    ev.preventDefault();
    const f = Object.fromEntries(new FormData(add));
    f.quantity = Number(f.quantity);
    try { await api("/api/batches", { method: "POST", body: f }); toast("Batch added"); render(); }
    catch (e) { $("#add-error").textContent = e.message; }
  };
  app.querySelectorAll("[data-move]").forEach(s => s.onchange = async () => {
    if (!s.value) return;
    try {
      const r = await api(`/api/batches/${s.dataset.move}/move`, { method: "POST", body: { to_fridge: s.value } });
      toast(r.warning || "Batch moved"); render(true);
    } catch (e) { toast(e.message, "critical"); s.value = ""; }
  });
  app.querySelectorAll("[data-remove]").forEach(b => b.onclick = async () => {
    const reason = prompt("Why is this batch leaving storage? (used, destroyed, transferred)");
    if (!reason) return;
    await api(`/api/batches/${b.dataset.remove}/remove`, { method: "POST", body: { reason } });
    toast("Batch removed"); render(true);
  });
  app.querySelectorAll("[data-qr]").forEach(b => b.onclick = async () => {
    const svg = await api(`/api/batches/${b.dataset.qr}/qr.svg`);
    const url = `${location.origin}/check/${b.dataset.pub}`;
    const dlg = $("#dialog");
    dlg.innerHTML = `<h2>Public check code</h2><div class="qr">${svg}</div>
      <p class="muted">Anyone can scan this to see the batch status and expiry, without logging in.</p>
      <p><a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a></p>
      <div style="display:flex;gap:8px"><button class="btn" onclick="window.print()">Print</button><button class="btn primary" id="close-dlg">Close</button></div>`;
    dlg.showModal();
    $("#close-dlg").onclick = () => dlg.close();
  });
}

// ---------------------------------------------------------------- logs
// One readable line per audit entry; the full JSON stays in the tooltip.
function summarize(d) {
  const parts = Object.entries(d || {}).filter(([, v]) => typeof v !== "object" || v === null)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`);
  const text = parts.join(", ");
  return text.length > 160 ? text.slice(0, 157) + "…" : text || "–";
}
async function viewLogs() {
  const tab = new URLSearchParams(location.hash.split("?")[1] || "").get("tab") || "audit";
  const data = tab === "security" && can("security.view") ? await api("/api/security-events") : await api("/api/audit?limit=200");
  const table = tab === "security"
    ? `<table><thead><tr><th>Time</th><th>Device</th><th>Rejected because</th><th>Detail</th></tr></thead><tbody>
        ${data.map(e => `<tr><td>${dateTimeOf(e.ts)}</td><td>${esc(e.device_id)}</td><td><span class="chip bad">${esc(e.kind.replaceAll("_", " "))}</span></td><td class="muted">${esc(e.detail)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No rejected messages.</td></tr>`}</tbody></table>`
    : `<table><thead><tr><th>#</th><th>Time</th><th>Who</th><th>Action</th><th>Details</th></tr></thead><tbody>
        ${data.map(e => `<tr><td class="num">${e.id}</td><td>${dateTimeOf(e.ts)}</td><td>${esc(e.actor)}</td><td>${esc(e.action)}</td><td><pre class="details" title="${esc(JSON.stringify(e.details))}">${esc(summarize(e.details))}</pre></td></tr>`).join("")}</tbody></table>`;
  app.innerHTML = shell("logs", "Logs and integrity", `<div class="stack">
    <section class="panel"><h2>Verify integrity</h2>
      <p class="muted" style="margin-top:0">Every reading and log entry is chained to the one before it and sealed with a server key. Verifying recomputes the whole chain and points to any record that was edited, deleted or rewritten.</p>
      <button class="btn primary" id="verify">Verify integrity</button><div id="verdict" style="margin-top:14px"></div></section>
    <section class="panel">
      <div class="tabs" role="tablist">
        <a class="btn small" role="tab" href="#/logs?tab=audit" aria-selected="${tab !== "security"}">Audit trail</a>
        ${can("security.view") ? `<a class="btn small" role="tab" href="#/logs?tab=security" aria-selected="${tab === "security"}">Security events</a>` : ""}
      </div><div class="table-wrap">${table}</div></section></div>`);
  app.querySelectorAll(".tabs a").forEach(a => { if (a.getAttribute("aria-selected") === "true") a.style.cssText = "background:var(--glacier-soft);border-color:var(--glacier);color:var(--glacier)"; a.style.textDecoration = "none"; });
  $("#verify").onclick = async () => {
    const r = await api("/api/integrity/verify", { method: "POST" });
    const names = { readings: "Sensor readings", audit_log: "Audit trail" };
    $("#verdict").innerHTML = `<div class="verdict ${r.ok ? "ok" : "bad"}">${r.ok ? "Intact: no record has been changed." : "Tampering detected."}</div>
      <ul>${r.chains.map(c => `<li><strong>${names[c.table]}</strong>: ${c.records_checked} records checked. ${c.ok ? "OK" :
        c.problems.slice(0, 5).map(p => `Record #${p.id}: ${esc(p.meaning)}`).join("; ")}</li>`).join("")}</ul>`;
  };
}

// ---------------------------------------------------------------- AI
async function viewAI() {
  const d = await api("/api/ai");
  const m = d.metrics;
  const metrics = m ? `<div class="table-wrap"><table>
      <thead><tr><th>30-min forecast</th><th class="num">MAE (°C)</th><th class="num">RMSE (°C)</th></tr></thead><tbody>
      <tr><td>Our model</td><td class="num">${m.regression.model.mae}</td><td class="num">${m.regression.model.rmse}</td></tr>
      <tr><td>No-change baseline</td><td class="num">${m.regression.persistence_baseline.mae}</td><td class="num">${m.regression.persistence_baseline.rmse}</td></tr>
      <tr><td>Linear trend baseline</td><td class="num">${m.regression.linear_trend_baseline.mae}</td><td class="num">${m.regression.linear_trend_baseline.rmse}</td></tr>
      </tbody></table></div>
    <div class="table-wrap" style="margin-top:14px"><table>
      <thead><tr><th>Breach warning (T+30 > 8 °C)</th><th class="num">Precision</th><th class="num">Recall</th><th class="num">F1</th></tr></thead><tbody>
      <tr><td>Our upper-bound rule (live)</td><td class="num">${m.warning_T30_above_8C.model_upper.precision}</td><td class="num">${m.warning_T30_above_8C.model_upper.recall}</td><td class="num">${m.warning_T30_above_8C.model_upper.f1}</td></tr>
      <tr><td>Linear trend</td><td class="num">${m.warning_T30_above_8C.linear_trend_baseline.precision}</td><td class="num">${m.warning_T30_above_8C.linear_trend_baseline.recall}</td><td class="num">${m.warning_T30_above_8C.linear_trend_baseline.f1}</td></tr>
      </tbody></table></div>
    <p class="muted">Dataset: ${m.dataset.runs} runs (${m.dataset.synthetic_runs} physics-simulated, ${m.dataset.recorded_runs} recorded), ${m.dataset.rows.toLocaleString()} rows, ${esc(m.dataset.test_split)}. Health score error: ${m.health_estimator.mae_points} points across ${m.health_estimator.fridges} fridges with known wear.</p>`
    : `<p class="muted">No trained model yet. Run <code>python -m ml.train</code>.</p>`;
  app.innerHTML = shell("ai", "Breach prediction and compressor health", `<div class="stack">
    <div class="grid2">${d.fridges.map(f => `<section class="panel"><h2>${esc(f.name)}</h2>
      <p>${esc(f.pred_note || "Collecting data…")}</p>
      <div style="display:grid;grid-template-columns:auto 1fr;gap:8px 14px;align-items:center">
        <span class="muted">Now</span><strong>${fmt(f.temp)} °C</strong>
        <span class="muted">In 30 min</span><strong>${fmt(f.pred_temp_30)} °C <span class="muted">(upper ${fmt(f.pred_upper_30)} °C)</span></strong>
        <span class="muted">Compressor</span>${healthMeter(f.health_score)}
      </div><p class="muted">${esc(f.health_note || "")}</p></section>`).join("")}</div>
    <section class="panel"><h2>Model</h2><p class="muted" style="margin-top:0">${d.model.loaded ? `${esc(d.model.mode)}, trained ${esc(d.model.trained_at)}` : `Fallback active: ${esc(d.model.error || "")}`}</p>${metrics}</section>
    <section class="panel"><h2>Report figures</h2><div class="grid2">
      <img src="/report/ai/forecast_trace.png" alt="Forecast versus measured temperature on a held-out run" style="width:100%">
      <img src="/report/ai/feature_importance.png" alt="Feature importance" style="width:100%">
      <img src="/report/ai/pred_vs_actual.png" alt="Predicted versus actual temperature" style="width:100%">
      <img src="/report/ai/health_estimator.png" alt="Estimated versus true compressor health" style="width:100%">
    </div><p><a href="/report/ai_report.md" target="_blank" rel="noopener">Open the full AI report</a></p></section></div>`);
}

// ---------------------------------------------------------------- admin
async function viewAdmin() {
  const [users, dash] = await Promise.all([api("/api/users"), api("/api/dashboard")]);
  app.innerHTML = shell("admin", "Administration", `<div class="stack">
    <section class="panel"><h2>Fridges</h2><div class="table-wrap"><table>
      <thead><tr><th>Fridge</th><th>Red LED</th><th>Messages accepted / duplicates / rejected</th><th></th></tr></thead><tbody>
      ${dash.fridges.map(f => `<tr><td><strong>${esc(f.name)}</strong><div class="muted">${esc(f.device_id)}</div></td>
        <td>${f.led_latch ? `<span class="chip DISCARD">Latched</span>` : `<span class="chip off">Off</span>`}</td>
        <td class="num">${f.device ? `${f.device.accepted} / ${f.device.duplicates} / ${f.device.rejected}` : "–"}</td>
        <td><div class="row-actions"><button class="btn small" data-reset="${esc(f.id)}" ${f.led_latch ? "" : "disabled"}>Reset discard LED</button>
        <button class="btn small" data-recal="${esc(f.id)}">Recalibrate health</button></div></td></tr>`).join("")}
      </tbody></table></div>
      <p><button class="btn" id="test-alert">Send test alert</button></p></section>
    <section class="panel"><h2>Users</h2>
      <form id="add-user" class="form-row">
        <label>Username<input name="username" required minlength="3" maxlength="32"></label>
        <label>Password<input name="password" type="password" required minlength="8"></label>
        <label>Role<select name="role"><option value="pharmacist">Pharmacist</option><option value="inspector">Inspector</option><option value="admin">Admin</option></select></label>
        <button class="btn primary">Add user</button></form><div class="error" id="user-error"></div>
      <div class="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
      ${users.map(u => `<tr><td>${esc(u.username)}</td><td>${esc(u.role)}</td><td>${u.active ? "Active" : "Disabled"}</td>
        <td><button class="btn small" data-toggle="${u.id}" data-active="${u.active}">${u.active ? "Disable" : "Enable"}</button></td></tr>`).join("")}
      </tbody></table></div></section></div>`);
  app.querySelectorAll("[data-reset]").forEach(b => b.onclick = async () => {
    const r = await api(`/api/fridges/${b.dataset.reset}/reset-discard-led`, { method: "POST" });
    toast(r.note || "Discard LED cleared"); render(true);
  });
  app.querySelectorAll("[data-recal]").forEach(b => b.onclick = async () => {
    await api(`/api/fridges/${b.dataset.recal}/recalibrate-health`, { method: "POST" }); toast("Health baseline will be relearned");
  });
  $("#test-alert").onclick = async () => {
    const r = await api("/api/alerts/test", { method: "POST" });
    toast(r.channels.length ? `Test alert sent via ${r.channels.join(" and ")}` : "No alert channel configured. Set Telegram or e-mail in .env");
  };
  $("#add-user").onsubmit = async ev => {
    ev.preventDefault();
    try { await api("/api/users", { method: "POST", body: Object.fromEntries(new FormData(ev.target)) }); toast("User added"); render(); }
    catch (e) { $("#user-error").textContent = e.message; }
  };
  app.querySelectorAll("[data-toggle]").forEach(b => b.onclick = async () => {
    try { await api(`/api/users/${b.dataset.toggle}`, { method: "PATCH", body: { active: b.dataset.active !== "1" } }); render(true); }
    catch (e) { toast(e.message, "critical"); }
  });
}

// ---------------------------------------------------------------- boot
if (S.token) connectWS();
render();
