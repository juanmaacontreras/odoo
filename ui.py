"""Single-page HTML UI served by server.py."""

import json

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Equipment Intake</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1d2330; --muted:#667085; --line:#d9dde5;
          --accent:#1f5fbf; --ok:#1a7f37; --warn:#9a6700; --warnbg:#fff8e1; --bad:#b42318; --badbg:#fdecea; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 system-ui, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--ink); }
  header { padding:10px 20px; background:#24324a; color:#fff; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; }
  header .st { font-size:12px; opacity:.85; }
  .banner { padding:10px 20px; font-weight:600; }
  .banner.dry { background:#e7f0ff; color:#123d7a; }
  .banner.live { background:var(--bad); color:#fff; font-size:16px; }
  main { max-width:1100px; margin:0 auto; padding:16px 20px 60px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin-bottom:14px; }
  .card h2 { font-size:14px; margin:0 0 10px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
  input, select, textarea, button { font:inherit; }
  input[type=text], input[type=datetime-local], select, textarea { padding:7px 9px; border:1px solid var(--line); border-radius:6px; width:100%; background:#fff; }
  #ai { font-size:22px; width:220px; }
  button { padding:8px 14px; border-radius:6px; border:1px solid var(--accent); background:var(--accent); color:#fff; cursor:pointer; }
  button.secondary { background:#fff; color:var(--accent); }
  button.danger { background:var(--bad); border-color:var(--bad); }
  button:disabled { opacity:.5; cursor:default; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .grid { display:grid; grid-template-columns: 180px 1fr; gap:8px 14px; align-items:start; }
  .grid > label { color:var(--muted); padding-top:7px; }
  .src { font-size:12px; color:var(--muted); }
  .msg { padding:8px 10px; border-radius:6px; margin:6px 0; }
  .msg.warn { background:var(--warnbg); color:var(--warn); }
  .msg.bad { background:var(--badbg); color:var(--bad); }
  .msg.ok { background:#e6f4ea; color:var(--ok); }
  table { border-collapse:collapse; width:100%; }
  th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); font-size:13px; vertical-align:top; }
  th { color:var(--muted); font-weight:600; }
  tr.pick { cursor:pointer; } tr.pick:hover { background:#f0f5ff; } tr.sel { background:#e7f0ff; }
  pre { background:#0f1726; color:#d7e2f5; padding:10px; border-radius:6px; overflow:auto; font-size:12px; }
  .tags label { display:inline-flex; gap:4px; margin-right:12px; }
  .hidden { display:none; }
  details summary { cursor:pointer; color:var(--muted); }
  #partnerResults { max-height:220px; overflow:auto; margin-top:6px; }
  @media (max-width:700px) { .grid { grid-template-columns:1fr; } .grid > label { padding-top:0; } }
</style>
</head>
<body>
<header><h1>Equipment Intake &rarr; Odoo Repair</h1><span class="st" id="status">Connecting&hellip;</span></header>
<div id="banner" class="banner dry"></div>
<main>
  <div class="card">
    <h2>1. AI number</h2>
    <form id="lookupForm" class="row">
      <input id="ai" type="text" autocomplete="off" placeholder="e.g. 1948" autofocus>
      <button type="submit">Look up</button>
      <span class="src">Type or scan. Accepts 1948, AI1948, 01948.</span>
    </form>
    <div id="lookupMsgs"></div>
  </div>

  <div class="card hidden" id="sheetCard">
    <h2>Spreadsheet (reference only)</h2>
    <div id="sheetInfo"></div>
  </div>

  <div class="card hidden" id="lotsCard">
    <h2>2. Unit in Odoo</h2>
    <div id="lotsInfo"></div>
  </div>

  <div class="card hidden" id="formCard">
    <h2>3. Verify and create repair order</h2>
    <div class="grid">
      <label>Product to repair</label><div><b id="fProduct"></b><div class="src" id="fProductSrc"></div></div>
      <label>Lot / serial</label><div><b id="fLot"></b><div class="src" id="fLotSrc"></div></div>
      <label>Customer</label>
      <div>
        <b id="fPartner">&mdash;</b> <span class="src" id="fPartnerSrc"></span>
        <div class="row" style="margin-top:6px">
          <input id="partnerQ" type="text" placeholder="Search existing customers (name, company, CUIT)&hellip;" style="max-width:420px">
          <button type="button" class="secondary" id="partnerBtn">Search</button>
        </div>
        <div class="src">Only existing Odoo contacts can be chosen. This tool never creates customers.</div>
        <div id="partnerResults"></div>
      </div>
      <label>Scheduled date</label><div><input id="fDate" type="datetime-local" style="max-width:260px"></div>
      <label>Responsible</label><div id="fUser">&mdash;</div>
      <label>Tags</label><div class="tags" id="fTags"><span class="src">none available</span></div>
      <label>Under warranty</label><div><input id="fWarranty" type="checkbox"></div>
      <label>Notes</label><div><textarea id="fNotes" rows="3" placeholder="Reported fault, accessories received&hellip;"></textarea></div>
      <label class="dup hidden">Open repair exists</label><div class="dup hidden"><label><input type="checkbox" id="fDup"> create anyway</label></div>
    </div>
    <div class="row" style="margin-top:14px">
      <button type="button" class="secondary" id="previewBtn">Preview payload (dry-run)</button>
      <button type="button" id="createBtn">Create repair order</button>
    </div>
    <div id="createMsgs"></div>
    <pre id="payload" class="hidden"></pre>
  </div>

  <details class="card"><summary>Diagnostics (fields_get)</summary><div class="row" style="margin-top:8px"><button type="button" class="secondary" id="diagBtn">Load</button><button type="button" class="secondary" id="reloadBtn">Reload config + spreadsheet</button></div><pre id="diag" class="hidden"></pre></details>
</main>
<script>
const TOKEN = __TOKEN__;
let LIVE = __LIVE__;
let state = { status:null, lookup:null, lotEntry:null, partner:null };
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const m2o = v => Array.isArray(v) ? v[1] : "";

async function api(path, body) {
  const opts = { headers: { "X-Intake-Token": TOKEN } };
  if (body !== undefined) { opts.method = "POST"; opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({ error: "Bad response (" + r.status + ")" }));
  if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}
function msgs(el, list, cls) { el.insertAdjacentHTML("beforeend", list.map(m => `<div class="msg ${cls}">${esc(m)}</div>`).join("")); }

function renderBanner() {
  const b = $("banner");
  if (LIVE) { b.className = "banner live"; b.textContent = "LIVE MODE — 'Create repair order' writes to Odoo. Repair orders cannot be deleted."; $("createBtn").className = "danger"; }
  else { b.className = "banner dry"; b.textContent = "Dry-run mode — every lookup is real, but nothing is created. The exact payload is shown instead."; $("createBtn").textContent = "Create repair order (dry-run)"; }
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    state.status = s; LIVE = s.live_mode; renderBanner();
    const sheet = s.spreadsheet_error ? "spreadsheet ERROR: " + s.spreadsheet_error
      : `spreadsheet: ${s.spreadsheet.ais} AIs, ${s.spreadsheet.with_serial} with serial`;
    $("status").textContent = s.odoo_ok
      ? `Odoo ${s.server_version || ""} · ${s.database} · ${s.user_name || s.username} · ${sheet}`
      : `Odoo NOT connected: ${s.odoo_error} · ${sheet}`;
    $("fUser").textContent = s.user_name || "—";
    const tags = s.tags || [];
    if (tags.length) $("fTags").innerHTML = tags.map(t =>
      `<label><input type="checkbox" value="${t.id}" ${(s.default_tag_ids||[]).includes(t.id) ? "checked" : ""}>${esc(t.name)}</label>`).join("");
  } catch (e) { $("status").textContent = "Error: " + e.message; }
}

$("lookupForm").onsubmit = async ev => {
  ev.preventDefault();
  const ai = $("ai").value.trim(); if (!ai) return;
  const out = $("lookupMsgs"); out.innerHTML = '<div class="src">Looking up&hellip;</div>';
  ["sheetCard","lotsCard","formCard"].forEach(id => $(id).classList.add("hidden"));
  $("payload").classList.add("hidden"); $("createMsgs").innerHTML = "";
  try {
    const r = await api("/api/lookup?ai=" + encodeURIComponent(ai));
    out.innerHTML = "";
    if (!r.ok) { msgs(out, [r.error], "bad"); return; }
    state.lookup = r; state.lotEntry = null; state.partner = null;
    msgs(out, r.warnings, "warn");
    renderSheet(r); renderLots(r);
    if (r.lots.length === 1) pickLot(0);
  } catch (e) { out.innerHTML = ""; msgs(out, [e.message], "bad"); }
  $("ai").select();
};

function renderSheet(r) {
  $("sheetCard").classList.remove("hidden");
  if (!r.sheet.rows.length) { $("sheetInfo").innerHTML = `<div class="msg warn">AI ${r.ai} is not in the spreadsheet.</div>`; return; }
  $("sheetInfo").innerHTML = `<table><tr><th>Row</th><th>AI</th><th>Model</th><th>Serial (manufacturer)</th><th>Version</th><th>Status</th><th>Date</th><th>Customer (as typed)</th></tr>` +
    r.sheet.rows.map(x => `<tr><td>${x.row}</td><td>${x.ai}</td><td>${esc(x.model)}</td><td><b>${esc(x.serial)}</b></td><td>${esc(x.version)}</td><td>${esc(x.status)}</td><td>${esc(x.date)}</td><td>${esc(x.customer)}</td></tr>`).join("") +
    `</table><div class="src">The spreadsheet is only used to get the manufacturer serial. Product and customer come from Odoo.</div>`;
}

function renderLots(r) {
  $("lotsCard").classList.remove("hidden");
  let h = r.lots.length
    ? `<div class="src">${r.lots.length > 1 ? "Several lots match — click the right one." : "One lot matches."}</div>
       <table><tr><th>Lot / serial</th><th>Why it matched</th><th>Product</th><th>Customer (Odoo)</th><th>Repairs</th></tr>` +
       r.lots.map((e, i) => `<tr class="pick" id="lot${i}" onclick="pickLot(${i})"><td><b>${esc(e.lot.name)}</b>${e.lot.ref ? `<div class="src">ref ${esc(e.lot.ref)}</div>` : ""}${e.note ? `<div class="src">${esc(e.note)}</div>` : ""}</td>
         <td class="src">${e.match.map(m => m.startsWith("possible") ? `<span class="msg warn" style="padding:1px 5px">${esc(m)}</span>` : esc(m)).join("<br>")}</td>
         <td>${esc(e.product ? e.product.display_name : m2o(e.lot.product_id))}</td>
         <td>${e.customer ? esc(e.customer.name) + `<div class="src">${esc(e.customer_source)}</div>` : '<span class="src">unknown</span>'}</td>
         <td>${e.repairs.length}${e.open_repairs.length ? ` <span class="msg warn" style="padding:1px 5px">open: ${esc(e.open_repairs.join(", "))}</span>` : ""}</td></tr>`).join("") + `</table>`
    : `<div class="msg bad">No matching lot/serial in Odoo. Nothing can be created for this unit until the lot exists (this tool never creates lots).</div>`;
  h += `<details style="margin-top:8px"><summary>Search steps</summary><pre>${esc(r.steps.join("\n"))}</pre></details>`;
  $("lotsInfo").innerHTML = h;
}

function pickLot(i) {
  const e = state.lookup.lots[i]; state.lotEntry = e;
  document.querySelectorAll("tr.pick").forEach(tr => tr.classList.remove("sel")); $("lot" + i).classList.add("sel");
  $("formCard").classList.remove("hidden");
  $("fProduct").textContent = e.product ? e.product.display_name : m2o(e.lot.product_id);
  $("fProductSrc").textContent = "from Odoo (product of the lot)";
  $("fLot").textContent = e.lot.name;
  const sheetSerial = state.lookup.sheet.serial;
  $("fLotSrc").textContent = "from Odoo (" + e.match.join("; ") + ") · " + (sheetSerial ? `spreadsheet says ${sheetSerial}` : "no serial in spreadsheet");
  setPartner(e.customer ? { id: e.customer.id, name: e.customer.name } : null, e.customer_source || "not found in Odoo — search below");
  const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  if (!$("fDate").value) $("fDate").value = d.toISOString().slice(0, 16);
  document.querySelectorAll(".dup").forEach(x => x.classList.toggle("hidden", !e.open_repairs.length));
  $("fDup").checked = false;
  let rh = e.repairs.length ? `<h2 style="margin-top:12px">Previous repairs of this unit</h2><table><tr><th>Ref</th><th>Customer</th><th>State</th><th>Scheduled</th><th>Created</th></tr>` +
    e.repairs.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(m2o(x.partner_id))}</td><td>${esc(x.state)}</td><td>${esc(x.schedule_date || "")}</td><td>${esc(x.create_date || "")}</td></tr>`).join("") + `</table>` : `<div class="src" style="margin-top:8px">No previous repairs for this lot.</div>`;
  const old = $("repairsHist"); if (old) old.remove();
  $("lotsInfo").insertAdjacentHTML("beforeend", `<div id="repairsHist">${rh}</div>`);
}

function setPartner(p, src) {
  state.partner = p;
  $("fPartner").textContent = p ? p.name : "— none selected —";
  $("fPartnerSrc").textContent = src ? "(" + src + ")" : "";
}

async function searchPartners() {
  const q = $("partnerQ").value.trim(); const box = $("partnerResults");
  if (q.length < 2) { box.innerHTML = '<div class="src">Type at least 2 characters.</div>'; return; }
  box.innerHTML = '<div class="src">Searching&hellip;</div>';
  try {
    const rows = await api("/api/partners?q=" + encodeURIComponent(q));
    box.innerHTML = rows.length ? `<table>` + rows.map(p =>
      `<tr class="pick" data-id="${p.id}" data-name="${esc(p.display_name)}"><td>${esc(p.display_name)}</td><td class="src">${p.is_company ? "company" : "contact"}${p.vat ? " · " + esc(p.vat) : ""}${p.city ? " · " + esc(p.city) : ""} · id ${p.id}</td></tr>`).join("") + `</table>`
      : '<div class="msg warn">No existing customer matches. Refine the search — new customers must be created in Odoo by whoever manages contacts.</div>';
    box.querySelectorAll("tr.pick").forEach(tr => tr.onclick = () => {
      setPartner({ id: parseInt(tr.dataset.id, 10), name: tr.dataset.name }, "chosen by you from Odoo"); box.innerHTML = "";
    });
  } catch (e) { box.innerHTML = `<div class="msg bad">${esc(e.message)}</div>`; }
}
$("partnerBtn").onclick = searchPartners;
$("partnerQ").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); searchPartners(); } };

function formBody() {
  return {
    ai: String(state.lookup.ai), lot_id: state.lotEntry.lot.id,
    partner_id: state.partner ? state.partner.id : null,
    schedule_date: $("fDate").value || null,
    tag_ids: [...document.querySelectorAll("#fTags input:checked")].map(x => parseInt(x.value, 10)),
    under_warranty: $("fWarranty").checked, notes: $("fNotes").value,
    allow_open_duplicate: $("fDup").checked,
  };
}

async function submit(preview) {
  const out = $("createMsgs"); out.innerHTML = ""; $("payload").classList.add("hidden");
  if (!state.lotEntry) { msgs(out, ["Pick a lot first."], "bad"); return; }
  if (!state.partner) { msgs(out, ["Choose an existing customer first."], "bad"); return; }
  const body = formBody();
  if (LIVE && !preview) {
    const typed = window.prompt(`LIVE MODE: this creates a repair order in Odoo that CANNOT be deleted.\n\n` +
      `Unit: ${$("fProduct").textContent} — lot ${state.lotEntry.lot.name}\nCustomer: ${state.partner.name}\n\n` +
      `Type the AI number (${state.lookup.ai}) to confirm:`);
    if (typed === null) return;
    body.confirm = true; body.confirm_ai = typed.trim();
  }
  if (preview) body.preview = true;
  $("createBtn").disabled = $("previewBtn").disabled = true;
  try {
    const r = await api("/api/create", body);
    msgs(out, r.warnings || [], "warn");
    if (r.dry_run) msgs(out, ["Dry-run: nothing was created. This is exactly what would be sent to repair.order.create:"], "ok");
    else msgs(out, [`Created ${r.name || ("repair.order id " + r.id)} in Odoo.`], "ok");
    let shown = JSON.stringify(r.payload, null, 2);
    if (r.odoo_would_fill && Object.keys(r.odoo_would_fill).length)
      shown += "\n\n// Odoo fills in by itself (simulated, nothing saved):\n" + JSON.stringify(r.odoo_would_fill, null, 2);
    $("payload").textContent = shown; $("payload").classList.remove("hidden");
  } catch (e) { msgs(out, [e.message], "bad"); }
  finally { setTimeout(() => { $("createBtn").disabled = $("previewBtn").disabled = false; }, LIVE && !preview ? 3000 : 0); }
}
$("previewBtn").onclick = () => submit(true);
$("createBtn").onclick = () => submit(false);

$("diagBtn").onclick = async () => {
  const d = $("diag"); d.classList.remove("hidden"); d.textContent = "Loading…";
  try { d.textContent = JSON.stringify(await api("/api/diagnose"), null, 2); } catch (e) { d.textContent = e.message; }
};
$("reloadBtn").onclick = async () => { await api("/api/reload", {}); loadStatus(); };

renderBanner(); loadStatus();
</script>
</body>
</html>
"""


def render(token, live_mode):
    return PAGE.replace("__TOKEN__", json.dumps(token)).replace("__LIVE__", "true" if live_mode else "false")
