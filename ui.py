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
  main { max-width:860px; margin:0 auto; padding:16px 20px 60px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin-bottom:14px; }
  .card h2 { font-size:14px; margin:0 0 10px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
  input, select, textarea, button { font:inherit; }
  input[type=text], input[type=datetime-local], select, textarea { padding:7px 9px; border:1px solid var(--line); border-radius:6px; width:100%; background:#fff; }
  #ai { font-size:22px; width:220px; }
  button { padding:8px 14px; border-radius:6px; border:1px solid var(--accent); background:var(--accent); color:#fff; cursor:pointer; }
  button.secondary { background:#fff; color:var(--accent); }
  button.danger { background:var(--bad); border-color:var(--bad); }
  button:disabled { opacity:.5; cursor:default; }
  button.link { background:none; border:none; color:var(--accent); padding:4px 6px; text-decoration:underline; }
  #formCard .grid > div b { font-size:16px; }
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
    <form id="lookupForm" class="row">
      <input id="ai" type="text" autocomplete="off" placeholder="AI number, e.g. 1948" autofocus>
      <button type="submit">Look up</button>
    </form>
    <div id="lookupMsgs"></div>
    <div id="lotPicker"></div>
  </div>

  <div class="card hidden" id="formCard">
    <div class="grid">
      <label>Customer</label>
      <div>
        <b id="fPartner">&mdash;</b> <button type="button" class="link" id="changePartner">change</button>
        <div id="partnerBox" class="hidden">
          <div class="row" style="margin-top:6px">
            <input id="partnerQ" type="text" placeholder="Search existing customers (name, company, CUIT)&hellip;" style="max-width:420px">
            <button type="button" class="secondary" id="partnerBtn">Search</button>
          </div>
          <div class="src">Only existing Odoo contacts. This tool never creates customers.</div>
          <div id="partnerResults"></div>
        </div>
      </div>
      <label>Equipment</label><div><b id="fProduct"></b></div>
      <label>Serial number</label><div><b id="fLot"></b> <span class="src" id="fLotSrc"></span></div>
      <label class="dup hidden">Open repair</label><div class="dup hidden"><span class="msg warn" style="padding:2px 6px" id="dupText"></span> <label><input type="checkbox" id="fDup"> create anyway</label></div>
    </div>

    <div class="row" style="margin-top:14px">
      <button type="button" id="createBtn">Create repair order</button>
      <button type="button" class="link" id="previewBtn">preview what would be sent</button>
    </div>
    <div id="createMsgs"></div>
    <details id="payloadBox" class="hidden"><summary class="src">Technical detail: exact data sent to Odoo</summary><pre id="payload"></pre></details>

    <details style="margin-top:12px"><summary>More options (date, tags, warranty, notes)</summary>
      <div class="grid" style="margin-top:8px">
        <label>Scheduled date</label><div><input id="fDate" type="datetime-local" style="max-width:260px"></div>
        <label>Responsible</label><div id="fUser">&mdash;</div>
        <label>Tags</label><div class="tags" id="fTags"><span class="src">none available</span></div>
        <label>Under warranty</label><div><input id="fWarranty" type="checkbox"></div>
        <label>Notes</label><div><textarea id="fNotes" rows="3" placeholder="Reported fault, accessories received&hellip;"></textarea></div>
      </div>
    </details>
    <details style="margin-top:6px"><summary>Unit history and where the data came from</summary><div id="history" style="margin-top:8px"></div></details>
  </div>

  <details class="card"><summary>Diagnostics</summary><div class="row" style="margin-top:8px"><button type="button" class="secondary" id="diagBtn">Load fields_get</button><button type="button" class="secondary" id="reloadBtn">Reload config + spreadsheet</button></div><pre id="diag" class="hidden"></pre></details>
</main>
<script>
const TOKEN = __TOKEN__;
let LIVE = __LIVE__;
let state = { status:null, lookup:null, lotEntry:null, partner:null };
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const m2o = v => Array.isArray(v) ? v[1] : "";
const productName = e => e.product ? e.product.display_name : m2o(e.lot.product_id);

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
  if (LIVE) { b.className = "banner live"; b.textContent = "LIVE MODE — creating writes to Odoo. Repair orders cannot be deleted."; $("createBtn").className = "danger"; $("previewBtn").classList.remove("hidden"); }
  else { b.className = "banner dry"; b.textContent = "Dry-run: lookups are real, nothing is created."; $("createBtn").textContent = "Create repair order (dry-run)"; $("previewBtn").classList.add("hidden"); }
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    state.status = s; LIVE = s.live_mode; renderBanner();
    const sheet = s.spreadsheet_error ? "spreadsheet ERROR: " + s.spreadsheet_error : `${s.spreadsheet.ais} AIs in spreadsheet`;
    $("status").textContent = s.odoo_ok ? `${s.user_name || s.username} · ${sheet}` : `Odoo NOT connected: ${s.odoo_error} · ${sheet}`;
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
  $("lotPicker").innerHTML = ""; $("formCard").classList.add("hidden");
  $("payloadBox").classList.add("hidden"); $("createMsgs").innerHTML = "";
  try {
    const r = await api("/api/lookup?ai=" + encodeURIComponent(ai));
    out.innerHTML = "";
    if (!r.ok) { msgs(out, [r.error], "bad"); return; }
    state.lookup = r; state.lotEntry = null; state.partner = null;
    msgs(out, r.warnings.filter(w => !/lots match; pick|^No lot\/serial found/.test(w)), "warn");
    if (!r.lots.length) {
      const serial = r.sheet.serial ? ` (spreadsheet serial ${r.sheet.serial})` : "";
      msgs(out, [`AI ${r.ai} has no lot in Odoo${serial}. Create this repair order by hand in Odoo.`], "bad");
    } else if (r.lots.length === 1) pickLot(0);
    else renderPicker(r);
  } catch (e) { out.innerHTML = ""; msgs(out, [e.message], "bad"); }
  $("ai").select();
};

function renderPicker(r) {
  $("lotPicker").innerHTML = `<div class="src" style="margin-top:8px">${r.lots.length} units match — pick one:</div><table>` +
    r.lots.map((e, i) => `<tr class="pick" id="lot${i}" onclick="pickLot(${i})"><td><b>${esc(e.lot.name)}</b></td><td>${esc(productName(e))}</td>
      <td>${e.customer ? esc(e.customer.name) : '<span class="src">no customer yet</span>'}</td>
      <td class="src">${e.repairs.length} repair(s)${e.match.some(m => m.startsWith("possible")) ? " · partial match" : ""}</td></tr>`).join("") + `</table>`;
}

function pickLot(i) {
  const e = state.lookup.lots[i]; state.lotEntry = e;
  document.querySelectorAll("#lotPicker tr.pick").forEach(tr => tr.classList.remove("sel"));
  if ($("lot" + i)) $("lot" + i).classList.add("sel");
  $("formCard").classList.remove("hidden"); $("createMsgs").innerHTML = ""; $("payloadBox").classList.add("hidden");
  $("fProduct").textContent = productName(e);
  $("fLot").textContent = e.lot.name;
  $("fLotSrc").textContent = `AI ${state.lookup.ai}`;
  setPartner(e.customer ? { id: e.customer.id, name: e.customer.name } : null);
  $("partnerBox").classList.toggle("hidden", !!e.customer);
  const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  if (!$("fDate").value) $("fDate").value = d.toISOString().slice(0, 16);
  document.querySelectorAll(".dup").forEach(x => x.classList.toggle("hidden", !e.open_repairs.length));
  $("dupText").textContent = e.open_repairs.join(", ") + " still open";
  $("fDup").checked = false;
  renderHistory(e);
}

function renderHistory(e) {
  const r = state.lookup;
  let h = `<div class="src"><b>Customer:</b> ${esc(e.customer_source || "not found in Odoo")}<br>
    <b>Equipment and serial:</b> from the Odoo lot — ${esc(e.match.join("; "))}${e.note ? " — " + esc(e.note) : ""}${e.lot.ref ? ` — lot ref ${esc(e.lot.ref)}` : ""}</div>`;
  h += e.repairs.length ? `<table style="margin-top:8px"><tr><th>Previous repair</th><th>Customer</th><th>State</th><th>Created</th></tr>` +
    e.repairs.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(m2o(x.partner_id))}</td><td>${esc(x.state)}</td><td>${esc((x.create_date || "").slice(0, 10))}</td></tr>`).join("") + `</table>`
    : `<div class="src" style="margin-top:8px">No previous repairs for this unit.</div>`;
  h += r.sheet.rows.length ? `<table style="margin-top:8px"><tr><th>Spreadsheet row</th><th>Model</th><th>Serial</th><th>Customer (as typed)</th></tr>` +
    r.sheet.rows.map(x => `<tr><td>${x.row}</td><td>${esc(x.model)}</td><td>${esc(x.serial)}</td><td>${esc(x.customer)}</td></tr>`).join("") + `</table>`
    : `<div class="src" style="margin-top:8px">AI not in the spreadsheet.</div>`;
  h += `<details style="margin-top:8px"><summary>Search steps</summary><pre>${esc(r.steps.join("\n"))}</pre></details>`;
  $("history").innerHTML = h;
}

function setPartner(p) {
  state.partner = p;
  $("fPartner").textContent = p ? p.name : "— none: search below —";
}
$("changePartner").onclick = () => { $("partnerBox").classList.toggle("hidden"); $("partnerQ").focus(); };

async function searchPartners() {
  const q = $("partnerQ").value.trim(); const box = $("partnerResults");
  if (q.length < 2) { box.innerHTML = '<div class="src">Type at least 2 characters.</div>'; return; }
  box.innerHTML = '<div class="src">Searching&hellip;</div>';
  try {
    const rows = await api("/api/partners?q=" + encodeURIComponent(q));
    box.innerHTML = rows.length ? `<table>` + rows.map(p =>
      `<tr class="pick" data-id="${p.id}" data-name="${esc(p.display_name)}"><td>${esc(p.display_name)}</td><td class="src">${p.is_company ? "company" : "contact"}${p.vat ? " · " + esc(p.vat) : ""}${p.city ? " · " + esc(p.city) : ""}</td></tr>`).join("") + `</table>`
      : '<div class="msg warn">No existing customer matches. New customers are created in Odoo by whoever manages contacts.</div>';
    box.querySelectorAll("tr.pick").forEach(tr => tr.onclick = () => {
      setPartner({ id: parseInt(tr.dataset.id, 10), name: tr.dataset.name });
      box.innerHTML = ""; $("partnerBox").classList.add("hidden");
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
  const out = $("createMsgs"); out.innerHTML = ""; $("payloadBox").classList.add("hidden");
  if (!state.lotEntry) { msgs(out, ["Pick a unit first."], "bad"); return; }
  if (!state.partner) { msgs(out, ["Choose an existing customer first."], "bad"); return; }
  const body = formBody();
  if (LIVE && !preview) {
    const typed = window.prompt(`LIVE MODE: this creates a repair order in Odoo that CANNOT be deleted.\n\n` +
      `Customer: ${state.partner.name}\nEquipment: ${$("fProduct").textContent}\nSerial: ${state.lotEntry.lot.name}\n\n` +
      `Type the AI number (${state.lookup.ai}) to confirm:`);
    if (typed === null) return;
    body.confirm = true; body.confirm_ai = typed.trim();
  }
  if (preview) body.preview = true;
  $("createBtn").disabled = $("previewBtn").disabled = true;
  try {
    const r = await api("/api/create", body);
    msgs(out, r.warnings || [], "warn");
    const what = `${state.partner.name} · ${$("fProduct").textContent} · serial ${state.lotEntry.lot.name}`;
    if (r.dry_run) msgs(out, [`OK — Odoo would accept this repair order: ${what}. Nothing was created (dry-run).`], "ok");
    else msgs(out, [`Created ${r.name || ("repair.order id " + r.id)} in Odoo: ${what}.`], "ok");
    let shown = JSON.stringify(r.payload, null, 2);
    if (r.odoo_would_fill && Object.keys(r.odoo_would_fill).length)
      shown += "\n\n// Odoo fills in by itself (simulated, nothing saved):\n" + JSON.stringify(r.odoo_would_fill, null, 2);
    $("payload").textContent = shown; $("payloadBox").classList.remove("hidden");
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
