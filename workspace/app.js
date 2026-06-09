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

// ---- duplicates review: AI proposals + deterministic candidates ----
let dupItems = [], dupIndex = 0, dupPicks = {};
let dupMode = "bulk";                                       // "bulk" (default) | "one"

async function postJSON(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

// Keep the nav badge + the "N to review" subtitle in sync with the live queue. Called on load AND
// after every review (advance), so the header can't go stale as items are merged/rejected. The AI
// count is derived from the remaining items, not a load-time snapshot.
function updateDupCounts() {
  const total = dupItems.length;
  const ai = dupItems.filter((i) => i.kind === "proposal").length;
  if (els.dupBadge) els.dupBadge.textContent = total || "—";
  const sub = $("#dup-sub");
  if (sub) sub.textContent = total
    ? `${total} possible duplicate${total === 1 ? "" : "s"}${ai ? ` · ${ai} from AI 🤖` : ""}`
    : "none found";
}

async function loadDuplicates() {
  try {
    const [props, cands] = await Promise.all([api("/api/proposals"), api("/api/candidates")]);
    const proposals = props.proposals || [];
    const propKeys = new Set(proposals.map((p) => p.cluster_key).filter(Boolean));
    const aiItems = proposals.map((p) => ({ kind: "proposal", ...p }));
    const detItems = (cands.clusters || [])
      .filter((c) => !propKeys.has(c.key))          // dedup: a pending proposal already covers this cluster
      .map((c) => ({ kind: "candidate", ...c }));
    dupItems = aiItems.concat(detItems);            // AI proposals first — review the AI's work, then the detector
    dupIndex = 0;
    updateDupCounts();
    if (dupMode === "bulk") renderBulkGroups(); else showItem();
  } catch { /* not ready */ }
}

async function showItem() {
  const merge = $("#merge"), prog = $("#dup-progress");
  if (dupIndex >= dupItems.length) {
    if (prog) prog.hidden = true;
    merge.innerHTML = `<div class="empty"><svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg><h2 class="serif">All caught up</h2><p>No more duplicates to review.</p></div>`;
    return;
  }
  const item = dupItems[dupIndex];
  if (prog) { prog.hidden = false; $("#dup-pos").textContent = dupIndex + 1; $("#dup-total").textContent = dupItems.length; $("#dup-tier").textContent = item.kind === "proposal" ? "ai proposal" : item.tier; }
  dupPicks = {};
  merge.innerHTML = `<p class="ph" style="text-align:center;margin-top:40px">Loading…</p>`;
  const preview = await api(`/api/merge-preview?ids=${(item.member_ids || []).join(",")}`);
  (item.kind === "proposal" ? renderProposalCard : renderMergeCard)(item, preview);
}

function memberChips(members) {
  return (members || []).map((m) =>
    `<span class="member"><span class="avatar">${esc(initial(m.name))}</span><span class="who"><b>${esc(m.name || "(no name)")}</b> <span>${esc(srcLabel(m.source))}</span></span></span>`).join("");
}

// aiPicks {field: chosenValue} → conflicts show the AI's choice selected (read-only); null → interactive.
function diffRows(preview, aiPicks) {
  return preview.fields.filter((f) => !NOISE.has(f.name)).map((f) => {
    if (f.kind === "conflict") {
      const opts = f.options.map((o) => {
        const sel = aiPicks && aiPicks[f.name] === o.value ? " sel" : "";
        const data = aiPicks ? "" : ` data-field="${esc(f.name)}" data-val="${esc(o.value)}" data-src="${esc(o.source)}"`;
        return `<label class="opt${sel}"${data}><span class="pick"></span><span class="v">${esc(o.value)}</span><span class="from">${esc(srcLabel(o.source))}</span></label>`;
      }).join("");
      return `<tr><th class="f">${esc(f.name)}</th><td><div class="conflict"><div class="clabel">${aiPicks ? "⚠ differ — the AI chose:" : "⚠ these differ — choose which to keep"}</div><div class="opts">${opts}</div></div></td></tr>`;
    }
    if (f.kind === "union" && f.values.length > 1) {
      const items = f.values.map((v) => `<div class="u"><span class="plus">+</span><span>${esc(v.value)}</span><span class="from">${esc(srcLabel(v.source))}</span></div>`).join("");
      return `<tr><th class="f">${esc(f.name)}</th><td><div class="union">${items}</div><div class="keepall">both kept</div></td></tr>`;
    }
    const v = f.values[0];
    return `<tr><th class="f">${esc(f.name)}</th><td><div class="single">${esc(v.value)} <span class="from mono">${esc(srcLabel(v.source))}</span></div></td></tr>`;
  }).join("");
}

// candidate (manual flow): pick conflicts, build the changeset on approve
function renderMergeCard(cl, preview) {
  $("#merge").innerHTML =
    `<div class="mergecard"><div class="mc-head"><div class="eyebrow">${esc(cl.tier)} · ${esc((cl.signals || []).join(", "))}</div>` +
    `<h3 class="serif">These look like the same person</h3><div class="members">${memberChips(preview.members)}</div></div>` +
    `<table class="difftbl"><tbody>${diffRows(preview, null)}</tbody></table>` +
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
  $("#dup-approve").addEventListener("click", async () => {
    const resolutions = Object.entries(dupPicks).map(([field, v]) => ({ field, chosen_value: v.value, chosen_source: v.source, rule: "user" }));
    await postJSON("/api/merge", { member_ids: cl.member_ids, into: cl.member_ids[0], resolutions });
    advance();
  });
  $("#dup-reject").addEventListener("click", async () => { await postJSON("/api/reject", { key: cl.key }); advance(); });
  $("#dup-skip").addEventListener("click", () => { dupIndex++; showItem(); });
}

// AI proposal: review the AI's proposed merge + its choices, approve or reject the whole proposal
function renderProposalCard(p, preview) {
  const aiPicks = {};
  (p.operations || []).forEach((op) => { if (op.op === "resolve_field") aiPicks[op.field] = op.chosen_value; });
  $("#merge").innerHTML =
    `<div class="mergecard"><div class="mc-head"><div class="eyebrow"><span class="botbadge">🤖 AI proposal</span> · ${esc(p.created_by)}</div>` +
    `<h3 class="serif">The AI proposes merging these</h3>` +
    (p.rationale ? `<p class="prationale">“${esc(p.rationale)}”</p>` : "") +
    `<div class="members">${memberChips(preview.members)}</div></div>` +
    `<table class="difftbl"><tbody>${diffRows(preview, aiPicks)}</tbody></table>` +
    `<div class="mc-foot"><span class="reassure">↺ Reversible — undo anytime</span><span class="spacer"></span>` +
    `<button class="btn ghost" id="dup-reject">Reject</button>` +
    `<button class="btn ghost" id="dup-skip">Skip</button>` +
    `<button class="btn primary" id="dup-approve">Approve merge</button></div></div>`;
  $("#dup-approve").addEventListener("click", async () => { await postJSON("/api/apply-proposal", { proposal_id: p.proposal_id }); advance(); });
  $("#dup-reject").addEventListener("click", async () => { await postJSON("/api/dismiss-proposal", { proposal_id: p.proposal_id }); advance(); });
  $("#dup-skip").addEventListener("click", () => { dupIndex++; showItem(); });
}

function advance() {
  dupItems.splice(dupIndex, 1);     // reviewed item done; the next slides into this index
  updateDupCounts();                // badge + "N to review" subtitle, both kept in sync (not just the badge)
  showItem();
  refreshStats();
  browse();          // contacts changed
}

// ---- bulk approve: select groups → flip & spot-check (inline conflict resolution) → merge in one batch ----
const GROUP_META = {
  confA:  { title: "Same email or phone", sub: "exact contact-info match", klass: "safe", order: 1 },
  confL:  { title: "Same LinkedIn profile", sub: "identical profile URL", klass: "safe", order: 2 },
  ai:     { title: "AI proposed", sub: "each carries the AI’s rationale", klass: "safe", ai: true, order: 3 },
  strong: { title: "Name + same company", sub: "strong signal, not exact", klass: "caution", order: 4 },
  fuzzy:  { title: "Name-only", sub: "weakest signal — review with care", klass: "caution", order: 5 },
  review: { title: "Needs a closer look", sub: "oversized / low-cohesion cluster", klass: "caution", order: 6 },
};
const groupOf = (it) => it.kind === "proposal" ? "ai"
  : it.tier === "confident" ? ((it.signals || []).includes("profile_url") ? "confL" : "confA") : it.tier;
const itemConflicts = (it) => it.kind === "candidate" ? (it.conflicts || []) : [];

let bulkQueue = [], bulkIdx = 0, bulkSelected = new Set(), bulkInit = false, bulkPending = null;
const selectedItems = () => dupItems.filter((it) => bulkSelected.has(groupOf(it)));

function bulkStep(n) {
  $("#b-step1").hidden = n !== 1; $("#b-step2").hidden = n !== 2; $("#b-step3").hidden = n !== 3;
  $("#b-commit").classList.toggle("show", n === 2);
  document.querySelectorAll("#b-rail .st").forEach((s) => {
    const sn = +s.dataset.step; s.classList.toggle("on", sn === n); s.classList.toggle("done", sn < n);
  });
  document.querySelectorAll("#b-rail .bar").forEach((bar, i) => bar.classList.toggle("fill", i < n - 1));
}

function renderBulkGroups() {
  bulkStep(1);
  const buckets = {};
  dupItems.forEach((it) => { (buckets[groupOf(it)] ||= []).push(it); });
  if (!bulkInit && Object.keys(buckets).length) {            // confident + AI pre-checked on first entry
    Object.keys(buckets).forEach((g) => { if (GROUP_META[g] && GROUP_META[g].klass === "safe") bulkSelected.add(g); });
    bulkInit = true;
  }
  [...bulkSelected].forEach((g) => { if (!buckets[g]) bulkSelected.delete(g); });   // prune vanished groups

  const safe = $("#b-safe"), caut = $("#b-caution");
  safe.innerHTML = ""; caut.innerHTML = "";
  const groups = Object.keys(buckets).sort((a, b) => (GROUP_META[a]?.order || 99) - (GROUP_META[b]?.order || 99));
  let i = 0, anyCaution = false;
  groups.forEach((g) => {
    const meta = GROUP_META[g] || { title: g, sub: "", klass: "caution", order: 99 };
    (meta.klass === "safe" ? safe : caut).appendChild(groupCard(g, meta, buckets[g], i++));
    if (meta.klass !== "safe") anyCaution = true;
  });
  $("#b-cautionlabel").hidden = !anyCaution;
  $("#b-cautionnote").hidden = !anyCaution;
  if (!dupItems.length) {
    safe.innerHTML = `<div class="empty" style="grid-column:1/-1;margin:20px auto"><svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg><h2 class="serif">All caught up</h2><p>No duplicates to review.</p></div>`;
  }
  updateGoBtn();
}

function groupCard(g, meta, items, idx) {
  const nConf = items.filter((it) => itemConflicts(it).length).length;
  const on = bulkSelected.has(g);
  const safePill = meta.klass === "safe" ? `<span class="pill safe">safe to bulk-merge</span>` : `<span class="pill caution">spot-check each</span>`;
  const pickPill = nConf ? `<span class="pill pick">${nConf} need a pick</span>` : "";
  const title = meta.ai ? `<span class="botbadge">🤖 AI proposed</span>` : esc(meta.title);
  const el = document.createElement("div");
  el.className = `groupcard ${meta.klass}${on ? " on" : ""}`;
  el.style.setProperty("--i", idx);
  el.innerHTML =
    `<div class="gtop"><span class="gcheck"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg></span>` +
    `<div class="gbody"><div class="gtitle">${title}</div><div class="gsub">${esc(meta.sub)}</div>` +
    `<div class="gmeta"><span class="gcount"><b>${items.length}</b> merge${items.length === 1 ? "" : "s"}</span>${safePill}${pickPill}</div></div></div>`;
  el.addEventListener("click", () => toggleGroup(g, meta, items.length));
  return el;
}

function toggleGroup(g, meta, count) {
  if (bulkSelected.has(g)) { bulkSelected.delete(g); renderBulkGroups(); return; }
  if (meta.klass === "caution") {                           // opt-in confirm for higher-risk groups
    bulkPending = g;
    $("#b-toast-msg").innerHTML = `<b>${esc(meta.title)}</b> — ${count} name-based match${count === 1 ? "" : "es"}. You’ll spot-check and resolve each before it merges. Add to the batch?`;
    $("#b-toast").classList.add("show");
    return;
  }
  bulkSelected.add(g); renderBulkGroups();
}

function updateGoBtn() {
  const n = selectedItems().length;
  const b = $("#b-go");
  b.textContent = `Spot-check ${n} merge${n === 1 ? "" : "s"} →`;
  b.disabled = n === 0;
}

// readiness: a card is ready when not excluded and every conflicting field has a pick
const bReady = (it) => !it._excluded && (it._conflicts || []).every((f) => it._picks[f] != null);
const bStatus = (it) => it._excluded ? "excl" : (bReady(it) ? "ready" : "pick");

async function startSpotCheck() {
  bulkQueue = selectedItems();
  bulkStep(2);
  $("#b-total").textContent = bulkQueue.length;
  $("#b-stage").innerHTML = `<p class="ph" style="text-align:center;margin-top:40px">Loading previews…</p>`;
  await Promise.all(bulkQueue.map(async (it) => {           // prefetch previews → know conflicts up front
    it._preview = await api(`/api/merge-preview?ids=${(it.member_ids || []).join(",")}`);
    it._conflicts = it._preview.conflicts || [];
    it._aiPicks = {};
    if (it.kind === "proposal") (it.operations || []).forEach((op) => {
      if (op.op === "resolve_field") it._aiPicks[op.field] = { value: op.chosen_value, source: op.chosen_source };
    });
    it._picks = { ...it._aiPicks };                         // start from the AI's picks (editable)
    it._excluded = false;
  }));
  bulkIdx = 0;
  showBulkCard(0);
  updateTally();
}

function renderBDots() {
  const d = $("#b-dots"); d.innerHTML = "";
  bulkQueue.forEach((it, i) => {
    const st = bStatus(it), s = document.createElement("span");
    s.className = `d ${st}${i === bulkIdx ? " cur" : ""}`;
    s.style.color = st === "ready" ? "var(--accent)" : st === "pick" ? "var(--clay)" : "var(--faint)";
    s.title = `#${i + 1}`;
    s.addEventListener("click", () => showBulkCard(i));
    d.appendChild(s);
  });
}

function bulkDiffRows(it) {
  return it._preview.fields.filter((f) => !NOISE.has(f.name)).map((f) => {
    if (f.kind === "conflict") {
      const pick = it._picks[f.name], ai = it._aiPicks[f.name], resolved = pick != null;
      const opts = f.options.map((o) => {
        const sel = pick && pick.value === o.value ? " sel" : "";
        const aiTag = ai && ai.value === o.value ? `<span class="aitag">AI’s pick</span>` : "";
        return `<label class="opt${sel}" data-field="${esc(f.name)}" data-val="${esc(o.value)}" data-src="${esc(o.source)}"><span class="pick"></span><span class="v">${esc(o.value)}</span>${aiTag}<span class="from">${esc(srcLabel(o.source))}</span></label>`;
      }).join("");
      const lbl = resolved ? `✓ keeping “${esc(pick.value)}”` : (ai ? "⚠ differ — confirm the AI’s pick or change it" : "⚠ these differ — choose which to keep");
      return `<tr><th class="f">${esc(f.name)}</th><td><div class="conflict${resolved ? " resolved" : ""}"><div class="clabel">${lbl}</div><div class="opts">${opts}</div></div></td></tr>`;
    }
    if (f.kind === "union" && f.values.length > 1) {
      const items = f.values.map((v) => `<div class="u"><span class="plus">+</span><span>${esc(v.value)}</span><span class="from">${esc(srcLabel(v.source))}</span></div>`).join("");
      return `<tr><th class="f">${esc(f.name)}</th><td><div class="union">${items}</div><div class="keepall">both kept</div></td></tr>`;
    }
    const v = f.values[0];
    return `<tr><th class="f">${esc(f.name)}</th><td><div class="single">${esc(v.value)} <span class="from mono">${esc(srcLabel(v.source))}</span></div></td></tr>`;
  }).join("");
}

function showBulkCard(i, dir) {
  bulkIdx = Math.max(0, Math.min(bulkQueue.length - 1, i));
  const it = bulkQueue[bulkIdx], isAI = it.kind === "proposal", preview = it._preview, into = it.into;
  $("#b-pos").textContent = bulkIdx + 1;
  const ct = $("#b-tier");
  ct.textContent = isAI ? "ai proposal" : it.tier;
  ct.className = "tierpill" + (isAI || it.tier === "confident" ? " confident" : "");
  $("#b-prev").disabled = bulkIdx === 0; $("#b-next").disabled = bulkIdx === bulkQueue.length - 1;

  const eyebrow = isAI ? `<span class="botbadge">🤖 AI proposal</span> · ${esc(it.created_by)}` : `${esc(it.tier)} · ${esc((it.signals || []).join(", "))}`;
  const head = isAI ? "The AI proposes merging these" : "These look like the same person";
  const memberHtml = (preview.members || []).map((m) => {
    const keep = m.id === into;
    return `<span class="member${keep ? " into" : ""}"><span class="avatar">${esc(initial(m.name))}</span><span class="who"><b>${esc(m.name || "(no name)")}</b> <span>${esc(srcLabel(m.source))}</span></span>${keep ? '<span class="keepbadge">keep</span>' : ""}</span>`;
  }).join("");

  $("#b-stage").innerHTML =
    `<div class="mergecard flip${it._excluded ? " excluded" : ""}" style="--dir:${dir === "prev" ? "-16px" : "16px"}">` +
    `<div class="mc-head"><div class="eyebrow">${eyebrow}</div><h3 class="serif">${head}</h3>` +
    (isAI && it.rationale ? `<p class="prationale">“${esc(it.rationale)}”</p>` : "") +
    `<div class="members">${memberHtml}</div></div>` +
    `<table class="difftbl"><tbody>${bulkDiffRows(it)}</tbody></table>` +
    `<div class="mc-foot"><span class="reassure">↺ Reversible — part of one batch</span><span class="spacer"></span>` +
    `<button class="btn ${it._excluded ? "" : "danger-ghost"}" id="b-exclbtn">${it._excluded ? "↩ Include in batch" : "✕ Exclude this"}</button>` +
    `<button class="btn" id="b-cardnext">Next →</button></div></div>`;

  $("#b-stage").querySelectorAll(".opt").forEach((opt) => opt.addEventListener("click", () => {
    it._picks[opt.dataset.field] = { value: opt.dataset.val, source: opt.dataset.src };
    showBulkCard(bulkIdx); updateTally();
  }));
  $("#b-exclbtn").addEventListener("click", () => { it._excluded = !it._excluded; showBulkCard(bulkIdx); updateTally(); });
  $("#b-cardnext").addEventListener("click", () => { if (bulkIdx < bulkQueue.length - 1) showBulkCard(bulkIdx + 1, "next"); });
  renderBDots();
}

function updateTally() {
  const r = bulkQueue.filter(bReady).length;
  const p = bulkQueue.filter((it) => !it._excluded && !bReady(it)).length;
  const e = bulkQueue.filter((it) => it._excluded).length;
  $("#b-ready").textContent = r; $("#b-pick").textContent = p; $("#b-excl").textContent = e;
  const btn = $("#b-mergeall");
  btn.disabled = r === 0; btn.textContent = `Merge ${r} ready`;
  $("#b-hint").textContent = p > 0 ? `${p} still need a pick — resolve or exclude them` : (r > 0 ? "all set" : "");
  renderBDots();
}

async function mergeBatch() {
  const ready = bulkQueue.filter(bReady);
  const items = ready.map((it) => {
    if (it.kind === "proposal") {
      const resolutions = (it._conflicts || []).filter((f) => {           // only overrides of the AI's pick
        const ai = it._aiPicks[f], pk = it._picks[f];
        return pk && (!ai || ai.value !== pk.value);
      }).map((f) => ({ field: f, chosen_value: it._picks[f].value, chosen_source: it._picks[f].source, rule: "user" }));
      return { kind: "proposal", proposal_id: it.proposal_id, resolutions };
    }
    const resolutions = (it._conflicts || []).map((f) => ({ field: f, chosen_value: it._picks[f].value, chosen_source: it._picks[f].source, rule: "user" }));
    return { kind: "candidate", member_ids: it.member_ids, into: it.into, resolutions };
  });
  let res;
  try { res = await postJSON("/api/merge-batch", { items, rationale: "bulk approve" }); }
  catch (err) { alert("Merge failed: " + err.message); return; }
  const n = res.merged != null ? res.merged : ready.length;
  $("#b-done-h").textContent = `Merged ${n} duplicate set${n === 1 ? "" : "s"}`;
  $("#b-done-p").textContent = `${n} contact${n === 1 ? " was" : "s were"} folded into ${n === 1 ? "its" : "their"} canonical record, in one transaction. Your imported source records were never changed.`;
  $("#b-undoline").textContent = `↺ One Undo reverses all ${n} merges `;
  bulkStep(3);
  refreshStats(); browse();
}

document.querySelectorAll("#dupmode button").forEach((b) =>
  b.addEventListener("click", () => {
    dupMode = b.dataset.mode;
    document.querySelectorAll("#dupmode button").forEach((x) => x.classList.toggle("on", x === b));
    $("#dupone").hidden = dupMode !== "one";
    $("#dupbulk").hidden = dupMode !== "bulk";
    loadDuplicates();                                       // refetch + dispatch to the active mode
  }));
$("#b-go").addEventListener("click", startSpotCheck);
$("#b-prev").addEventListener("click", () => { if (bulkIdx > 0) showBulkCard(bulkIdx - 1, "prev"); });
$("#b-next").addEventListener("click", () => { if (bulkIdx < bulkQueue.length - 1) showBulkCard(bulkIdx + 1, "next"); });
$("#b-back").addEventListener("click", renderBulkGroups);
$("#b-mergeall").addEventListener("click", mergeBatch);
$("#b-done-again").addEventListener("click", loadDuplicates);
$("#b-done-undo").addEventListener("click", async () => { await postJSON("/api/undo", {}); await loadDuplicates(); refreshStats(); browse(); });
$("#b-toast-yes").addEventListener("click", () => { if (bulkPending) { bulkSelected.add(bulkPending); renderBulkGroups(); } $("#b-toast").classList.remove("show"); bulkPending = null; });
$("#b-toast-no").addEventListener("click", () => { $("#b-toast").classList.remove("show"); bulkPending = null; });
document.addEventListener("keydown", (e) => {              // ← → flip through the spot-check gallery
  if (dupMode !== "bulk" || !$("#duplicates").classList.contains("show") || $("#b-step2").hidden) return;
  if (e.key === "ArrowLeft" && bulkIdx > 0) showBulkCard(bulkIdx - 1, "prev");
  if (e.key === "ArrowRight" && bulkIdx < bulkQueue.length - 1) showBulkCard(bulkIdx + 1, "next");
});

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
