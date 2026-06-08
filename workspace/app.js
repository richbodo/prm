// PRM workspace — read-only SPA over the daemon's JSON API (v0.1).
// "The personal archive" shell: menu + Contacts (search/browse/detail with provenance).
// Duplicates is a designed placeholder until candidate detection lands (M3). No external requests.
"use strict";

const $ = (s) => document.querySelector(s);
const els = {
  q: $("#q"), rows: $("#rows"), detail: $("#detail"), listhead: $("#listhead"),
  topsub: $("#topsub"), reset: $("#reset"),
  statContacts: $("#stat-contacts"), statSources: $("#stat-sources"), statImport: $("#stat-import"),
};

const PAGE = 50;
let browseOffset = 0;

// Source label map (id → short display). Falls back to the raw id.
const SOURCE_LABEL = {
  apple_icloud: "apple", google_takeout: "google", google_csv: "google csv",
  linkedin: "linkedin", facebook: "facebook", vcard: "vcard",
};
const srcLabel = (s) => SOURCE_LABEL[s] || s || "";

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function initial(name) { const m = String(name || "").trim(); return m ? m[0].toUpperCase() : "?"; }
function flatten(values) {
  return (values || [])
    .map((v) => (Array.isArray(v) ? v.filter(Boolean).join(", ") : v))
    .filter((v) => v !== "" && v != null)
    .join(" · ");
}

// ---- contacts list ----
function rowItem(c) {
  const li = document.createElement("li");
  li.className = "row";
  li.dataset.id = c.id;
  li.innerHTML =
    `<span class="avatar">${esc(initial(c.name))}</span>` +
    `<span class="nm">${esc(c.name || "(no name)")}</span>` +
    `<span class="sub">${esc(c.email || c.org || "—")}</span>` +
    `<span class="srcs">${c.source ? `<span class="chip src">${esc(srcLabel(c.source))}</span>` : ""}</span>`;
  li.addEventListener("click", () => selectContact(c.id, li));
  return li;
}
function renderRows(records, { append = false } = {}) {
  if (!append) els.rows.innerHTML = "";
  records.forEach((c) => els.rows.appendChild(rowItem(c)));
  if (!records.length && !append) {
    const li = document.createElement("li");
    li.style.cssText = "padding:18px 16px;color:var(--faint)";
    li.textContent = "No matches.";
    els.rows.appendChild(li);
  }
}

async function browse({ append = false } = {}) {
  if (!append) browseOffset = 0;
  const data = await api(`/api/contacts?limit=${PAGE}&offset=${browseOffset}`);
  renderRows(data.records, { append });
  browseOffset += data.records.length;
  els.listhead.textContent = `All contacts · ${data.total.toLocaleString()}`;
}

async function search(q) {
  const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=100`);
  renderRows(data.results);
  els.listhead.textContent = `${data.results.length} result(s) for “${q}”`;
}

// ---- contact detail (jCard fields + provenance) ----
async function selectContact(id, li) {
  document.querySelectorAll(".row.active").forEach((r) => r.classList.remove("active"));
  if (li) li.classList.add("active");
  const c = await api(`/api/contact/${encodeURIComponent(id)}`);

  // Provenance is per-field; in v0.1 a contact is one source record, so source/date are shared.
  const provByField = {};
  (c.provenance || []).forEach((p) => { if (!(p.field in provByField)) provByField[p.field] = p.observed_at; });
  const year = (iso) => (iso ? String(iso).slice(0, 4) : "");
  const label = srcLabel(c.source);

  // Hide structural / vendor-label noise (vCard VERSION, Apple's grouped X-ABLabel artifacts).
  const NOISE = new Set(["version", "x-ablabel"]);
  const rows = (c.fields || [])
    .filter((f) => !NOISE.has(f.name))
    .map((f) => {
      const val = flatten(f.values);
      if (val === "") return "";
      const when = year(provByField[f.name] || c.ingested_at);
      const prov = `from <span class="mono">${esc(label)}</span>${when ? ` · ${esc(when)}` : ""}`;
      return `<tr><th>${esc(f.name)}</th><td><div class="val">${esc(val)}</div><div class="prov">${prov}</div></td></tr>`;
    })
    .join("");

  els.detail.innerHTML =
    `<div class="dhead"><span class="avatar">${esc(initial(c.fn))}</span><div>` +
    `<h2 class="serif">${esc(c.fn || "(no name)")}</h2>` +
    `<div class="dmeta"><span class="chip dot">${esc(label)}</span><span>·</span>` +
    `<span>contributed by 1 source</span></div></div></div>` +
    `<div class="seg">Fields · with provenance</div>` +
    `<table class="kv">${rows}</table>`;
}

// ---- nav / reset ----
function show(view) {
  document.querySelectorAll(".navitem[data-view]").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("show", v.id === view));
}
document.querySelectorAll(".navitem[data-view]").forEach((n) =>
  n.addEventListener("click", () => show(n.dataset.view)));

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
els.q.addEventListener("input", debounce((e) => {
  const q = e.target.value.trim();
  q ? search(q) : browse();
}, 150));

els.reset.addEventListener("click", () => {        // AC-6: always-reachable escape
  els.q.value = "";
  document.querySelectorAll(".row.active").forEach((r) => r.classList.remove("active"));
  els.detail.innerHTML = `<p class="ph">Select a contact to see its fields and where each value came from.</p>`;
  show("contacts");
  browse();
});

function emptyState(msg) {
  els.rows.innerHTML = "";
  els.detail.innerHTML =
    `<div class="empty"><h2 class="serif">No contacts yet</h2><p>${msg}</p>` +
    `<span class="next">import some contacts</span></div>`;
  els.listhead.textContent = "All contacts";
}

(async function init() {
  try {
    const s = await api("/api/status");
    if (!s.shared_db) {
      els.statContacts.textContent = "0";
      emptyState('Run <code>prm import &lt;file&gt;</code>, or seed the demo with <code>prm init --demo</code>, then reload.');
      return;
    }
    els.statContacts.textContent = (s.records || 0).toLocaleString();
    els.statSources.textContent = Object.keys(s.by_source || {}).length;
    els.statImport.textContent = s.last_ingested_at ? String(s.last_ingested_at).slice(0, 10) : "—";
    els.topsub.textContent = `${(s.records || 0).toLocaleString()} across ${Object.keys(s.by_source || {}).length} sources`;
    await browse();
  } catch (err) {
    emptyState(`Couldn’t reach the workspace API (${esc(err.message)}).`);
  }
})();
