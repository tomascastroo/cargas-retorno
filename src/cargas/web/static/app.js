// Interfaz mínima sin dependencias: chat estilo WhatsApp, configuración e historial.
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  token: localStorage.getItem("cargas.token"),
  conversationId: localStorage.getItem("cargas.conversation"),
};

const ars = (v) => "$" + Math.round(Number(v)).toLocaleString("es-AR");
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
const fmtDateTime = (iso) => new Date(iso).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(state.token ? { authorization: `Bearer ${state.token}` } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 401 && state.token) { logout(); throw new Error("Sesión vencida"); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `Error ${res.status}`);
  }
  return data;
}

// ------------------------------------------------------------------ acceso

function setSession(data) {
  state.token = data.token;
  localStorage.setItem("cargas.token", data.token);
  state.conversationId = null;
  localStorage.removeItem("cargas.conversation");
  start();
}

function logout() {
  state.token = null;
  localStorage.removeItem("cargas.token");
  localStorage.removeItem("cargas.conversation");
  $("#app").hidden = true;
  $("#auth").hidden = false;
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    setSession(await api("/api/auth/login", { method: "POST", body: { email: f.get("email"), password: f.get("password") } }));
  } catch (err) { $("#auth-error").textContent = err.message; }
});

$("#signup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  if (!body.cuit) delete body.cuit;
  try { setSession(await api("/api/companies", { method: "POST", body })); }
  catch (err) { $("#auth-error").textContent = err.message; }
});

$("#logout").addEventListener("click", logout);

// ------------------------------------------------------------------ pestañas

document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.id !== `tab-${btn.dataset.tab}`; });
  if (btn.dataset.tab === "config") loadConfig();
  if (btn.dataset.tab === "history") loadHistory();
}));

// ------------------------------------------------------------------ chat

const LABELS = { rentabilidad: "Rentabilidad", desvio: "Desvío", fecha: "Fecha", ocupacion: "Ocupación", confiabilidad: "Confiabilidad" };

function optionCard(opt) {
  const l = opt.load;
  const bars = Object.entries(opt.components).map(([k, v]) =>
    `<span>${LABELS[k] || k}</span><div class="bar"><span style="width:${Math.round(v * 100)}%"></span></div><span>${Math.round(v * 100)}%</span>`).join("");
  return `<div class="option">
    <div class="head"><span>${opt.number}) ${esc(l.origin)} → ${esc(l.destination)}</span><span class="score">${Math.round(opt.score)}/100</span></div>
    <div>${esc(l.description)} · <b>${ars(l.price_ars)}</b> · ${esc(l.shipper_name)}</div>
    <div class="small muted">Desvío ${Math.round(opt.metrics.detour_km)} km · ${Math.round(opt.metrics.loaded_km)} km cargado · fuente ${esc(l.source)}${l.source_is_demo ? " (demo)" : ""}</div>
    <div class="bars">${bars}</div>
    <div class="small">${esc(opt.explanation)}</div>
    <div class="actions"><button data-say="${opt.number}">Elegir</button><button class="secondary" data-say="descartar ${opt.number}">Descartar</button></div>
  </div>`;
}

function renderBubble(direction, text, payload, createdAt) {
  const div = document.createElement("div");
  div.className = `bubble ${direction}`;
  let html = esc(text);
  if (payload && payload.kind === "options") {
    // El texto plano es lo que recibiría WhatsApp; en la web se muestran tarjetas.
    const header = text.split("\n")[0];
    html = esc(header) + payload.options.map(optionCard).join("") +
      `<div class="small muted" style="margin-top:6px">Respondé con el número o tocá un botón.</div>`;
  }
  if (payload && payload.kind === "negotiation" && payload.transcript) {
    html += `<div class="transcript">${payload.transcript.map((t) => `<div><b>${esc(t.de)}:</b> ${esc(t.texto)}</div>`).join("")}</div>`;
  }
  html += `<time>${fmtTime(createdAt || new Date().toISOString())}</time>`;
  div.innerHTML = html;
  $("#messages").appendChild(div);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

async function loadConversation() {
  $("#messages").innerHTML = "";
  if (!state.conversationId) {
    renderBubble("out", "Hola 👋 Contame cuándo y desde dónde volvés vacío y te busco carga para el regreso.");
    return;
  }
  try {
    const msgs = await api(`/api/conversations/${state.conversationId}/messages`);
    msgs.forEach((m) => renderBubble(m.direction, m.text, m.payload, m.created_at));
  } catch {
    state.conversationId = null;
    localStorage.removeItem("cargas.conversation");
    loadConversation();
  }
}

async function send(text) {
  if (!text.trim()) return;
  renderBubble("in", text);
  $("#chat-input").value = "";
  try {
    const res = await api("/api/chat/messages", { method: "POST", body: { text, conversation_id: state.conversationId } });
    state.conversationId = res.conversation_id;
    localStorage.setItem("cargas.conversation", res.conversation_id);
    res.replies.forEach((r) => renderBubble("out", r.text, r.payload));
  } catch (err) { renderBubble("out", `⚠️ ${err.message}`); }
}

$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); send($("#chat-input").value); });
$("#messages").addEventListener("click", (e) => { if (e.target.dataset.say) send(e.target.dataset.say); });
$("#examples").addEventListener("click", (e) => { if (e.target.tagName === "LI") { $("#chat-input").value = e.target.textContent; $("#chat-input").focus(); } });
$("#new-chat").addEventListener("click", () => {
  state.conversationId = null;
  localStorage.removeItem("cargas.conversation");
  loadConversation();
});

// ------------------------------------------------------------------ configuración

async function loadConfig() {
  const [trucks, agent] = await Promise.all([api("/api/trucks"), api("/api/agent")]);
  $("#trucks-table tbody").innerHTML = trucks.map((t) => `<tr>
    <td>${esc(t.plate)}</td><td>${esc(t.vehicle_type)}</td><td>${esc(t.body_type)}${t.refrigerated ? " ❄️" : ""}</td>
    <td>${t.capacity_pallets}</td><td>${t.capacity_kg}</td>
    <td>${t.active ? `<button class="link" data-deactivate="${t.id}">Dar de baja</button>` : '<span class="pill">baja</span>'}</td></tr>`).join("")
    || '<tr><td colspan="6" class="muted">Sin camiones. Agregá uno para que el agente pueda buscar.</td></tr>';
  const form = $("#agent-form");
  for (const [k, v] of Object.entries(agent)) {
    if (form.elements[k]) form.elements[k].value = Array.isArray(v) ? v.join(", ") : v;
  }
  form.dataset.usualRoutes = JSON.stringify(agent.usual_routes || []);
  form.dataset.name = agent.name;
}

$("#trucks-table").addEventListener("click", async (e) => {
  if (!e.target.dataset.deactivate) return;
  await api(`/api/trucks/${e.target.dataset.deactivate}`, { method: "DELETE" });
  loadConfig();
});

$("#truck-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  body.capacity_pallets = Number(body.capacity_pallets);
  body.capacity_kg = Number(body.capacity_kg);
  try {
    await api("/api/trucks", { method: "POST", body });
    e.target.reset();
    $("#truck-error").textContent = "";
    loadConfig();
  } catch (err) { $("#truck-error").textContent = err.message; }
});

$("#agent-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const body = {
    name: e.target.dataset.name || "Agente de retorno",
    min_rate_per_km: Number(f.min_rate_per_km.value),
    target_rate_per_km: Number(f.target_rate_per_km.value),
    cost_per_km: Number(f.cost_per_km.value),
    max_detour_km: Number(f.max_detour_km.value),
    date_flex_days: Number(f.date_flex_days.value),
    excluded_cargo_types: f.excluded_cargo_types.value.split(",").map((s) => s.trim()).filter(Boolean),
    usual_routes: JSON.parse(e.target.dataset.usualRoutes || "[]"),
    max_negotiation_rounds: Number(f.max_negotiation_rounds.value),
    max_counter_uplift_pct: Number(f.max_counter_uplift_pct.value),
  };
  try { await api("/api/agent", { method: "PUT", body }); $("#agent-saved").textContent = "Guardado ✓"; }
  catch (err) { $("#agent-saved").textContent = err.message; }
  setTimeout(() => { $("#agent-saved").textContent = ""; }, 2500);
});

// ------------------------------------------------------------------ historial

const STATUS_PILL = { booked: "ok", options_sent: "warn", no_results: "err", simulated_confirmed: "ok", ok: "ok", error: "err" };
const pill = (s) => `<span class="pill ${STATUS_PILL[s] || ""}">${esc(s)}</span>`;

async function loadHistory() {
  const [trips, bookings, sources, audit] = await Promise.all([
    api("/api/trips"), api("/api/bookings"), api("/api/sources"), api("/api/audit-events?limit=50"),
  ]);
  $("#trips").innerHTML = trips.length ? `<table><tr><th>Viaje</th><th>Salida</th><th>Estado</th><th>Parser</th></tr>${trips.map((t) =>
    `<tr><td>${esc(t.origin_city)} → ${esc(t.dest_city)}</td><td>${t.departure_date}</td><td>${pill(t.status)}</td><td>${esc(t.parse_method)}</td></tr>`).join("")}</table>`
    : '<p class="muted">Todavía no hay búsquedas.</p>';
  $("#bookings").innerHTML = bookings.length ? `<table><tr><th>Precio</th><th>Estado</th><th>Aprobada</th></tr>${bookings.map((b) =>
    `<tr><td>${ars(b.agreed_price)}</td><td>${pill(b.status)}</td><td>${fmtDateTime(b.approved_at)}</td></tr>`).join("")}</table>`
    : '<p class="muted">Sin operaciones confirmadas.</p>';
  $("#sources").innerHTML = `<table><tr><th>Fuente</th><th>Estado</th><th>Última corrida</th></tr>${sources.map((s) =>
    `<tr><td>${esc(s.name)}${s.enabled ? "" : " (deshabilitada)"}</td><td>${s.last_status ? pill(s.last_status) : "-"} ${esc(s.last_error || "")}</td><td>${s.last_run_at ? fmtDateTime(s.last_run_at) : "-"}</td></tr>`).join("")}</table>`;
  $("#audit").innerHTML = `<table><tr><th>Momento</th><th>Acción</th><th>Datos</th></tr>${audit.map((a) =>
    `<tr><td>${fmtDateTime(a.created_at)}</td><td>${esc(a.action)}</td><td class="small">${esc(JSON.stringify(a.data))}</td></tr>`).join("")}</table>`;
}

// ------------------------------------------------------------------ inicio

async function start() {
  try {
    const me = await api("/api/me");
    $("#who").textContent = `${me.full_name} · ${me.company_name}`;
    $("#auth").hidden = true;
    $("#app").hidden = false;
    $("#auth-error").textContent = "";
    loadConversation();
  } catch { logout(); }
}

if (state.token) start();
