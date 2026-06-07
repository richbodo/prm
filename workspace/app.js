// PRM workspace — read-only SPA over the daemon's JSON API (v0.1 M2).
// Search / browse / detail over shared.db. No build step, no framework, no external requests.
"use strict";

const $ = (sel) => document.querySelector(sel);
const els = {
  q: $("#q"), results: $("#results"), more: $("#more"),
  detail: $("#detail"), stat: $("#stat"), reset: $("#reset"),
};

const PAGE = 50;
let browseOffset = 0;   // paging cursor for the empty-query browse list
let mode = "browse";    // "browse" | "search"

async function api(path) {
  const res = await fetch(path, { headers: { "Accept": "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

function text(el, value) { el.textContent = value == null ? "" : String(value); }

function rowItem(c) {
  const li = document.createElement("li");
  li.className = "row";
  li.dataset.id = c.id;
  li.innerHTML =
    `<span class="name">${escapeHtml(c.name || "(no name)")}</span>` +
    `<span class="sub">${escapeHtml(c.email || c.org || "—")}</span>` +
    `<span class="src">${escapeHtml(c.source || "")}</span>`;
  li.addEventListener("click", () => showDetail(c.id, li));
  return li;
}

function renderList(items, { append = false } = {}) {
  if (!append) els.results.innerHTML = "";
  for (const c of items) els.results.appendChild(rowItem(c));
  if (!items.length && !append) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No matches.";
    els.results.appendChild(li);
  }
}

async function browse({ append = false } = {}) {
  mode = "browse";
  if (!append) browseOffset = 0;
  const data = await api(`/api/contacts?limit=${PAGE}&offset=${browseOffset}`);
  renderList(data.records, { append });
  browseOffset += data.records.length;
  els.more.hidden = browseOffset >= data.total;
  text(els.stat, `${data.total} contact(s)`);
}

async function search(q) {
  mode = "search";
  const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=100`);
  renderList(data.results);
  els.more.hidden = true;
  text(els.stat, `${data.results.length} result(s) for “${q}”`);
}

async function showDetail(id, li) {
  document.querySelectorAll(".row.active").forEach((r) => r.classList.remove("active"));
  if (li) li.classList.add("active");
  const c = await api(`/api/contact/${encodeURIComponent(id)}`);
  const fields = c.fields
    .filter((f) => f.name !== "version")
    .map((f) => `<tr><th>${escapeHtml(f.name)}</th><td>${escapeHtml(flatten(f.values))}</td></tr>`)
    .join("");
  const prov = c.provenance
    .map((p) => `<tr><th>${escapeHtml(p.field)}</th><td>${escapeHtml(p.value)}</td>` +
                `<td class="muted">${escapeHtml(p.observed_at || "")}</td></tr>`)
    .join("");
  els.detail.innerHTML =
    `<h2>${escapeHtml(c.fn || "(no name)")}</h2>` +
    `<p class="muted">source <b>${escapeHtml(c.source)}</b> · id <code>${escapeHtml(c.id)}</code> · ` +
    `imported ${escapeHtml(c.ingested_at || "—")}</p>` +
    `<h3>Fields</h3><table class="kv">${fields}</table>` +
    `<h3>Provenance <span class="muted">(per field · INV-7)</span></h3>` +
    (prov ? `<table class="kv">${prov}</table>` : `<p class="muted">none recorded</p>`);
}

function flatten(values) {
  return values
    .map((v) => (Array.isArray(v) ? v.filter(Boolean).join(", ") : v))
    .filter((v) => v !== "" && v != null)
    .join(" · ");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function resetView() {
  els.q.value = "";
  els.detail.innerHTML = `<p class="placeholder">Select a contact to see its fields and provenance.</p>`;
  browse();
  els.q.focus();
}

els.q.addEventListener("input", debounce((e) => {
  const q = e.target.value.trim();
  q ? search(q) : browse();
}, 150));
els.more.addEventListener("click", () => browse({ append: true }));
els.reset.addEventListener("click", resetView);            // AC-6: always-reachable escape

(async function init() {
  try {
    const s = await api("/api/status");
    if (!s.shared_db) {
      text(els.stat, "no shared.db yet — run `prm import` first");
      return;
    }
    await browse();
  } catch (err) {
    text(els.stat, `error: ${err.message}`);
  }
})();
