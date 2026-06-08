// PRM workspace — SPA over the daemon's JSON API (v0.1).
// "The personal archive" shell: menu + Contacts over the canonical projection (search / browse /
// detail with per-value provenance). The Duplicates review UI lands next (M3d-ii); its backend
// (/api/candidates + merge/reject/undo) is already live — the nav shows the live candidate count.
// No external requests.
"use strict";

const $ = (s) => document.querySelector(s);
const els = {
  q: $("#q"), rows: $("#rows"), detail: $("#detail"), listhead: $("#listhead"),
  topsub: $("#topsub"), reset: $("#reset"), dupBadge: $("#dup-badge"),
  statContacts: $("#stat-contacts"), statSources: $("#stat-sources"), statImport: $("#stat-import"),
};

const PAGE = 50;
let browseOffset = 0;

const SOURCE_LABEL = {
  apple_icloud: "apple", google_takeout: "google", google_csv: "google csv",
  linkedin: "linkedin", facebook: "facebook", vcard: "vcard",
};
const srcLabel = (s) => SOURCE_LABEL[s] || s || "";
const NOISE = new Set(["version", "x-ablabel"]);   // structural / vendor-label fields, hidden in detail

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

// ---- contacts list ----
function rowItem(c) {
  const li = document.createElement("li");
  li.className = "row";
  li.dataset.id = c.id;
  const sources = (c.sources && c.sources.length) ? c.sources : (c.source ? [c.source] : []);
  const chips = sources.slice(0, 3).map((s) => `<span class="chip src">${esc(srcLabel(s))}</span>`).join("");
  const merged = c.member_count > 1 ? `<span class="chip merged">${c.member_count}×</span>` : "";
  li.innerHTML =
    `<span class="avatar">${esc(initial(c.name))}</span>` +
    `<span class="nm">${esc(c.name || "(no name)")}${merged}</span>` +
    `<span class="sub">${esc(c.email || c.org || "—")}</span>` +
    `<span class="srcs">${chips}</span>`;
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

// ---- contact detail (canonical projection: union/single fields, per-value source) ----
async function selectContact(id, li) {
  document.querySelectorAll(".row.active").forEach((r) => r.classList.remove("active"));
  if (li) li.classList.add("active");
  const c = await api(`/api/contact/${encodeURIComponent(id)}`);

  const rows = (c.fields || [])
    .filter((f) => !NOISE.has(f.name))
    .map((f) => {
      const vals = (f.values || []).filter((v) => v.value !== "" && v.value != null);
      if (!vals.length) return "";
      const valHtml = vals.map((v) =>
        `<div class="fval">${esc(v.value)}${v.source ? ` <span class="vsrc mono">${esc(srcLabel(v.source))}</span>` : ""}</div>`).join("");
      const tag = (f.kind === "union" && vals.length > 1) ? ` <span class="kindtag">both kept</span>` : "";
      return `<tr><th>${esc(f.name)}${tag}</th><td>${valHtml}</td></tr>`;
    })
    .join("");

  const srcChips = (c.sources || []).map((s) => `<span class="chip dot">${esc(srcLabel(s))}</span>`).join("");
  const n = c.member_count || (c.sources || []).length || 1;
  els.detail.innerHTML =
    `<div class="dhead"><span class="avatar">${esc(initial(c.fn))}</span><div>` +
    `<h2 class="serif">${esc(c.fn || "(no name)")}</h2>` +
    `<div class="dmeta">${srcChips}<span>·</span><span>merged from ${n} record${n > 1 ? "s" : ""}</span></div>` +
    `</div></div>` +
    `<div class="seg">Fields · with provenance</div>` +
    `<table class="kv">${rows}</table>`;
}

// ---- duplicates review (one at a time, confident-first, neutral, reversible) ----
let dupClusters = [], dupIndex = 0, dupPicks = {};

async function postJSON(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function loadDuplicates() {
  try {
    const { clusters } = await api("/api/candidates");   // detector orders confident → review → strong → fuzzy
    dupClusters = clusters;
    dupIndex = 0;
    if (els.dupBadge) els.dupBadge.textContent = clusters.length || "—";
    const conf = clusters.filter((c) => c.tier === "confident").length;
    const sub = $("#dup-sub");
    if (sub) sub.textContent = clusters.length ? `${clusters.length} possible · ${conf} confident · one at a time` : "none found";
    showCluster();
  } catch { /* detection not ready */ }
}

async function showCluster() {
  const merge = $("#merge"), prog = $("#dup-progress");
  if (dupIndex >= dupClusters.length) {
    if (prog) prog.hidden = true;
    merge.innerHTML = `<div class="empty"><svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg><h2 class="serif">All caught up</h2><p>No more duplicates to review.</p></div>`;
    return;
  }
  const cl = dupClusters[dupIndex];
  if (prog) { prog.hidden = false; $("#dup-pos").textContent = dupIndex + 1; $("#dup-total").textContent = dupClusters.length; $("#dup-tier").textContent = cl.tier; }
  dupPicks = {};
  merge.innerHTML = `<p class="ph" style="text-align:center;margin-top:40px">Loading…</p>`;
  renderMergeCard(cl, await api(`/api/merge-preview?ids=${cl.member_ids.join(",")}`));
}

function renderMergeCard(cl, preview) {
  const memberChips = cl.members.map((m) =>
    `<span class="member"><span class="avatar">${esc(initial(m.name))}</span><span class="who"><b>${esc(m.name || "(no name)")}</b> <span>${esc(srcLabel(m.source))}</span></span></span>`).join("");
  const rows = preview.fields.filter((f) => !NOISE.has(f.name)).map((f) => {
    if (f.kind === "conflict") {
      const opts = f.options.map((o) =>
        `<label class="opt" data-field="${esc(f.name)}" data-val="${esc(o.value)}" data-src="${esc(o.source)}"><span class="pick"></span><span class="v">${esc(o.value)}</span><span class="from">${esc(srcLabel(o.source))}</span></label>`).join("");
      return `<tr><th class="f">${esc(f.name)}</th><td><div class="conflict"><div class="clabel">⚠ these differ — choose which to keep</div><div class="opts">${opts}</div></div></td></tr>`;
    }
    if (f.kind === "union" && f.values.length > 1) {
      const items = f.values.map((v) => `<div class="u"><span class="plus">+</span><span>${esc(v.value)}</span><span class="from">${esc(srcLabel(v.source))}</span></div>`).join("");
      return `<tr><th class="f">${esc(f.name)}</th><td><div class="union">${items}</div><div class="keepall">both kept</div></td></tr>`;
    }
    const v = f.values[0];
    return `<tr><th class="f">${esc(f.name)}</th><td><div class="single">${esc(v.value)} <span class="from mono">${esc(srcLabel(v.source))}</span></div></td></tr>`;
  }).join("");

  $("#merge").innerHTML =
    `<div class="mergecard"><div class="mc-head"><div class="eyebrow">${esc(cl.tier)} · ${esc(cl.signals.join(", "))}</div>` +
    `<h3 class="serif">These look like the same person</h3><div class="members">${memberChips}</div></div>` +
    `<table class="difftbl"><tbody>${rows}</tbody></table>` +
    `<div class="mc-foot"><span class="reassure">↺ Reversible — undo anytime</span><span class="spacer"></span>` +
    `<button class="btn ghost" id="dup-reject">Not a duplicate</button>` +
    `<button class="btn ghost" id="dup-skip">Skip</button>` +
    `<button class="btn primary" id="dup-approve"${preview.conflicts.length ? " disabled" : ""}>Approve merge</button></div></div>`;

  document.querySelectorAll("#merge .opt").forEach((opt) => opt.addEventListener("click", () => {
    opt.parentElement.querySelectorAll(".opt").forEach((o) => o.classList.remove("sel"));
    opt.classList.add("sel");
    dupPicks[opt.dataset.field] = { value: opt.dataset.val, source: opt.dataset.src };
    $("#dup-approve").disabled = preview.conflicts.some((c) => !(c in dupPicks));
  }));
  $("#dup-approve").addEventListener("click", () => approveCluster(cl));
  $("#dup-reject").addEventListener("click", () => rejectCluster(cl));
  $("#dup-skip").addEventListener("click", () => { dupIndex++; showCluster(); });
}

async function approveCluster(cl) {
  const resolutions = Object.entries(dupPicks).map(([field, v]) => ({ field, chosen_value: v.value, chosen_source: v.source, rule: "user" }));
  await postJSON("/api/merge", { member_ids: cl.member_ids, into: cl.member_ids[0], resolutions });
  dupClusters.splice(dupIndex, 1);     // merged cluster done; next one slides into this index
  afterReviewChange();
}
async function rejectCluster(cl) {
  await postJSON("/api/reject", { key: cl.key });
  dupClusters.splice(dupIndex, 1);
  afterReviewChange();
}
function afterReviewChange() {
  if (els.dupBadge) els.dupBadge.textContent = dupClusters.length || "—";
  showCluster();
  refreshStats();
  browse();          // contacts changed
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

async function refreshStats() {
  const s = await api("/api/status");
  if (!s.shared_db) return false;
  const contacts = s.contacts != null ? s.contacts : (s.records || 0);    // canonical (post-merge) count
  const sources = Object.keys(s.by_source || {}).length;
  els.statContacts.textContent = contacts.toLocaleString();
  els.statSources.textContent = sources;
  els.statImport.textContent = s.last_ingested_at ? String(s.last_ingested_at).slice(0, 10) : "—";
  els.topsub.textContent = `${contacts.toLocaleString()} across ${sources} sources`;
  return true;
}

const undoBtn = $("#undo-btn");
if (undoBtn) undoBtn.addEventListener("click", async () => {        // restore the last snapshot
  await postJSON("/api/undo", {});
  await loadDuplicates();
  refreshStats();
  browse();
});

(async function init() {
  try {
    if (!(await refreshStats())) {
      els.statContacts.textContent = "0";
      emptyState('Run <code>prm import &lt;file&gt;</code>, or seed the demo with <code>prm init --demo</code>, then reload.');
      return;
    }
    await browse();
    loadDuplicates();
  } catch (err) {
    emptyState(`Couldn’t reach the workspace API (${esc(err.message)}).`);
  }
})();
