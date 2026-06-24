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
  statBuild: $("#stat-build"),
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

// An avatar is the real photo when the contact has one, else the monogram. The bytes come from the
// daemon's local photo endpoint (never the contact JSON); a broken/missing image falls back to the
// initial via the delegated `onAvatarError` handler. `bust` cache-busts after an upload replaces it.
function avatarHTML(id, name, present, bust) {
  const ini = esc(initial(name));
  if (!present || id == null) return `<span class="avatar">${ini}</span>`;
  const q = bust ? `?t=${bust}` : "";
  return `<span class="avatar has-img"><img src="/api/contact/${encodeURIComponent(id)}/photo${q}" alt="" data-ini="${ini}" loading="lazy"></span>`;
}
function onAvatarError(e) {
  const img = e.target;
  if (img && img.tagName === "IMG" && img.dataset && img.dataset.ini != null) {
    const span = img.parentElement;
    span.textContent = img.dataset.ini;       // a 404/decoding failure → show the monogram instead
    span.classList.remove("has-img");
  }
}

// ---- contacts list ----
function rowItem(c) {
  const li = document.createElement("li");
  li.className = "row";
  li.dataset.id = c.id;
  const sources = (c.sources && c.sources.length) ? c.sources : (c.source ? [c.source] : []);
  const chips = sources.slice(0, 3).map((s) => `<span class="chip src">${esc(srcLabel(s))}</span>`).join("");
  const merged = c.member_count > 1 ? `<span class="chip merged">${c.member_count}×</span>` : "";
  li.innerHTML =
    avatarHTML(c.id, c.name, c.has_photo) +
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

// ---- contact detail: read + edit. Edits write the PRIVATE store (overrides for source fields, values
// for the custom schema), never shared.db (INV-2) — the override model, reversible via Undo. ----
const EDITABLE_KINDS = new Set(["text", "long_text", "number", "date", "url", "boolean", "single_select"]);
const valFirst = (f) => { const v = ((f && f.values) || []).find((x) => x.value !== "" && x.value != null); return v ? v.value : ""; };

async function selectContact(id, li) {
  document.querySelectorAll(".row.active").forEach((r) => r.classList.remove("active"));
  if (li) li.classList.add("active");
  renderContactRead(await api(`/api/contact/${encodeURIComponent(id)}`));
}

function dHead(c, editing) {
  const srcChips = (c.sources || []).map((s) => `<span class="chip dot">${esc(srcLabel(s))}</span>`).join("");
  const n = c.member_count || (c.sources || []).length || 1;
  const btn = editing ? "" : `<button class="btn ghost" id="edit-contact" title="Edit this contact">✎ Edit</button>`;
  return `<div class="dhead">${avatarHTML(c.id, c.fn, c.photo && c.photo.present, editing ? Date.now() : 0)}<div class="dhbody">` +
    `<h2 class="serif">${esc(c.fn || "(no name)")}</h2>` +
    `<div class="dmeta">${srcChips}<span>·</span><span>merged from ${n} record${n > 1 ? "s" : ""}</span></div>` +
    `</div>${btn}</div>`;
}

function renderContactRead(c) {
  const srcRows = (c.fields || []).filter((f) => !NOISE.has(f.name) && !f.custom).map((f) => {
    const vals = (f.values || []).filter((v) => v.value !== "" && v.value != null);
    if (!vals.length) return "";
    const valHtml = vals.map((v) =>
      `<div class="fval">${esc(v.value)}${v.source ? ` <span class="vsrc mono">${esc(srcLabel(v.source))}</span>` : ""}</div>`).join("");
    const tag = (f.kind === "union" && vals.length > 1) ? ` <span class="kindtag">both kept</span>` : "";
    return `<tr><th>${esc(f.name)}${tag}</th><td>${valHtml}</td></tr>`;
  }).join("");

  const rel = (c.fields || []).filter((f) => f.custom && (f.values || []).some((v) => v.value));
  const relRows = rel.map((f) => {
    const vals = (f.values || []).filter((v) => v.value !== "" && v.value != null);
    const body = f.kind === "multi_select"
      ? `<div class="tagchips">${vals.map((v) => `<span class="tagchip">${esc(v.value)}</span>`).join("")}</div>`
      : vals.map((v) => `<div class="fval">${esc(v.value)}</div>`).join("");
    return `<tr><th>${esc(f.label || f.name)}</th><td>${body}</td></tr>`;
  }).join("");

  els.detail.innerHTML =
    dHead(c, false) +
    `<div class="seg">Fields · with provenance</div><table class="kv">${srcRows}</table>` +
    (relRows ? `<div class="seg">Your notes &amp; fields</div><table class="kv">${relRows}</table>` : "");
  const eb = $("#edit-contact");
  if (eb) eb.addEventListener("click", () => enterEditMode(c.id));
  wireAvatarClick(c.id, false);                              // click the avatar to set/change the photo
}

async function enterEditMode(id) {
  const [c, sch] = await Promise.all([api(`/api/contact/${encodeURIComponent(id)}`), api("/api/schema")]);
  renderContactEdit(c, sch.fields || []);
}

function editInput(tag, kind, value, opts) {
  const a = `data-field="${esc(tag)}"`;
  if (kind === "long_text") return `<textarea rows="3" ${a}>${esc(value)}</textarea>`;
  if (kind === "boolean") return `<input type="checkbox" ${a}${value ? " checked" : ""}>`;
  if (kind === "single_select") {
    const o = (opts || []).map((op) =>
      `<option value="${esc(op.value)}"${op.value === value ? " selected" : ""}>${esc(op.label || op.value)}</option>`).join("");
    return `<select ${a}><option value="">—</option>${o}</select>`;
  }
  const type = kind === "number" ? "number" : kind === "date" ? "date" : kind === "url" ? "url" : "text";
  return `<input type="${type}" ${a} value="${esc(value)}">`;
}

const multiVals = (f) => ((f && f.values) || []).map((v) => v.value);
const cssEsc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&");

function tagPicker(sf, selected) {
  const opts = (sf.config || {}).options || [];
  const sel = new Set(selected);
  const chip = (val, label, desc, on) =>
    `<span class="tagchip pick${on ? " on" : ""}" data-tag="${esc(val)}"${desc ? ` title="${esc(desc)}"` : ""}>${esc(label)}</span>`;
  const known = opts.map((o) => chip(o.value, o.label || o.value, o.description, sel.has(o.value))).join("");
  const extra = selected.filter((v) => !opts.some((o) => o.value === v)).map((v) => chip(v, v, "", true)).join("");
  return `<div class="tagpick" data-pick="${esc(sf.field_id)}">${known}${extra}` +
    `<span class="tagchip add" data-add="${esc(sf.field_id)}">+ new</span></div>` +
    `<button class="btn ghost tiny" data-managetags="${esc(sf.field_id)}">Manage ${esc((sf.label || "").toLowerCase())}…</button>`;
}

function renderContactEdit(c, schemaFields) {
  const fields = c.fields || [];
  const byId = Object.fromEntries(fields.map((f) => [f.name, f]));
  const srcRows = fields.filter((f) => !NOISE.has(f.name) && !f.custom && f.kind === "single").map((f) =>
    `<tr><th>${esc(f.name)}</th><td>${editInput("src:" + f.name, "text", valFirst(f))}</td></tr>`).join("");
  const customRows = schemaFields.map((sf) => {
    if (sf.kind === "multi_select")
      return `<tr><th>${esc(sf.label)}</th><td>${tagPicker(sf, multiVals(byId[sf.field_id]))}</td></tr>`;
    if (sf.kind === "image")
      return `<tr><th>${esc(sf.label)}</th><td>${photoControl(c)}</td></tr>`;
    if (!EDITABLE_KINDS.has(sf.kind))
      return `<tr><th>${esc(sf.label)}</th><td><span class="hint">${esc(sf.kind)}</span></td></tr>`;
    return `<tr><th>${esc(sf.label)}</th><td>${editInput("cf:" + sf.field_id, sf.kind, valFirst(byId[sf.field_id]), (sf.config || {}).options)}</td></tr>`;
  }).join("");
  const unionRows = fields.filter((f) => !NOISE.has(f.name) && !f.custom && f.kind === "union").map((f) =>
    `<tr><th>${esc(f.name)}</th><td>${(f.values || []).map((v) => `<div class="fval muted">${esc(v.value)}</div>`).join("")}<div class="hint">multi-value editing — soon</div></td></tr>`).join("");

  els.detail.innerHTML =
    dHead(c, true) +
    `<div class="seg">Edit fields</div><table class="kv editkv">${srcRows}${customRows}${unionRows}</table>` +
    `<div class="editbar"><span class="reassure">↺ Reversible — saved to your private store, never the source</span>` +
    `<span class="spacer"></span><button class="btn" id="edit-cancel">Cancel</button>` +
    `<button class="btn primary" id="edit-save">Save changes</button></div>`;
  els.detail.querySelectorAll(".tagchip.pick").forEach((ch) => ch.addEventListener("click", () => ch.classList.toggle("on")));
  els.detail.querySelectorAll(".tagchip.add").forEach((a) => a.addEventListener("click", () => addTagInline(a, schemaFields)));
  els.detail.querySelectorAll("[data-managetags]").forEach((b) =>
    b.addEventListener("click", () => openTagModal(b.dataset.managetags, () => enterEditMode(c.id))));
  const pin = els.detail.querySelector("[data-photo-input]");
  if (pin) pin.addEventListener("change", () => uploadPhoto(c.id, pin.files && pin.files[0]));
  const prm = els.detail.querySelector("[data-photo-remove]");
  if (prm) prm.addEventListener("click", () => removePhoto(c.id));
  $("#edit-cancel").addEventListener("click", () => selectContact(c.id));
  $("#edit-save").addEventListener("click", () => saveContactEdit(c, byId));
  wireAvatarClick(c.id, true);
}

// Image field (the `photo` avatar) in edit mode: preview + upload/replace + remove. The bytes go
// straight to /api/set-photo (an audited, Undo-able write); the rest of the edit form posts separately.
function photoControl(c) {
  const present = !!(c.photo && c.photo.present);
  const preview = avatarHTML(c.id, c.fn, present, Date.now()).replace('class="avatar', 'class="avatar lg');
  const remove = present ? `<button type="button" class="btn ghost tiny" data-photo-remove>Remove</button>` : "";
  const note = (c.photo && c.photo.source === "import")
    ? `<span class="hint">from the import — upload to override it</span>` : "";
  return `<div class="photoctl">${preview}<div class="photoact">` +
    `<label class="btn ghost tiny photoup">Upload…<input type="file" accept="image/*" data-photo-input hidden></label>` +
    `${remove}${note}</div></div>`;
}

async function uploadPhoto(id, file) {
  if (!file) return;
  if (file.size > 16 * 1024 * 1024) { alert("Image too large (max 16 MB)."); return; }
  let img;
  try { img = await imageForUpload(file); } catch { alert("Couldn't read that file."); return; }
  try { await postJSON("/api/set-photo", { contact_id: id, ...img }); }
  catch (e) { alert("Upload failed: " + e.message); return; }
  await enterEditMode(id); browse();                         // refresh the preview + the list avatar
}

async function removePhoto(id) {
  try { await postJSON("/api/clear-value", { contact_id: id, field_id: "photo" }); }
  catch (e) { alert("Remove failed: " + e.message); return; }
  await enterEditMode(id); browse();
}

// The contact-detail avatar is a click target for setting/changing the photo — so you don't have to
// open Edit mode just to add one. In read view it re-renders the detail; in Edit mode it behaves like
// the Upload… button (re-renders the form). The list-row avatars are NOT wired (a row click opens it).
function wireAvatarClick(contactId, inEdit) {
  const av = els.detail.querySelector(".dhead .avatar");
  if (!av) return;
  av.classList.add("editable");
  av.title = "Click to change photo";
  av.addEventListener("click", () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.addEventListener("change", () => {
      const f = input.files[0];
      if (f) (inEdit ? uploadPhoto : changeAvatar)(contactId, f);
    });
    input.click();                                           // inside the click handler → a user gesture
  });
}

async function changeAvatar(contactId, file) {               // upload from read view, then re-render it
  if (file.size > 16 * 1024 * 1024) { alert("Image too large (max 16 MB)."); return; }
  let img;
  try { img = await imageForUpload(file); } catch { alert("Couldn't read that file."); return; }
  try { await postJSON("/api/set-photo", { contact_id: contactId, ...img }); }
  catch (e) { alert("Change failed: " + e.message); return; }
  renderContactRead(await api(`/api/contact/${encodeURIComponent(contactId)}`));
  browse();
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");   // drop the "data:<mime>;base64," prefix
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

// Downscale on upload (client-side, so no Python image dependency): draw the image to a canvas at a
// thumbnail cap and re-encode as JPEG, so even a camera photo lands at tens of KB. Falls back to the raw
// bytes when it can't decode (e.g. SVG) so nothing is ever lost.
function downscaleToBase64(file, maxDim = 512, quality = 0.85) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, maxDim / Math.max(img.width, img.height || 1));
      const w = Math.max(1, Math.round(img.width * scale)), h = Math.max(1, Math.round(img.height * scale));
      const cv = document.createElement("canvas");
      cv.width = w; cv.height = h;
      try {
        cv.getContext("2d").drawImage(img, 0, 0, w, h);
        resolve({ data_base64: cv.toDataURL("image/jpeg", quality).split(",", 2)[1] || "", mime: "image/jpeg" });
      } catch (e) { reject(e); }
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("decode failed")); };
    img.src = url;
  });
}

async function imageForUpload(file) {
  if (/\.(jpe?g|png|gif|webp|bmp)$/i.test(file.name) || /^image\/(jpeg|png|gif|webp|bmp)$/.test(file.type)) {
    try { return await downscaleToBase64(file); } catch { /* unreadable → store the original */ }
  }
  return { data_base64: await fileToBase64(file), mime: file.type || "" };
}

// ---- R7c: the guided photo matcher. Point it at a folder of loose images; step through one at a time,
// confirming a suggested contact (or searching). Files are read LOCALLY in the browser — nothing leaves
// the device; only the chosen, downscaled photo is POSTed to the local daemon. ----
async function openPhotoMatcher(fileList) {
  const queue = [...fileList].filter((f) =>
    /^image\//.test(f.type) || /\.(jpe?g|png|gif|webp|heic|bmp)$/i.test(f.name));
  if (!queue.length) { alert("No image files found there."); return; }
  let i = 0, assigned = 0, skipped = 0, objUrl = null;
  const el = document.createElement("div");
  el.className = "matchoverlay";
  document.body.appendChild(el);

  const freeUrl = () => { if (objUrl) { URL.revokeObjectURL(objUrl); objUrl = null; } };
  function close() { freeUrl(); el.remove(); document.removeEventListener("keydown", onKey); browse(); refreshStats(); }
  function onKey(e) {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") advance();
    else if (e.key === "ArrowLeft" && i > 0) { i--; render(); }
  }
  document.addEventListener("keydown", onKey);
  function advance() { i++; (i >= queue.length) ? finish() : render(); }

  async function assign(contactId) {
    let img;
    try { img = await imageForUpload(queue[i]); } catch { alert("Couldn't read that image."); return; }
    try { await postJSON("/api/set-photo", { contact_id: contactId, ...img }); }
    catch (e) { alert("Assign failed: " + e.message); return; }
    assigned++; advance();
  }

  const row = (c, badge) =>
    `<button class="matchrow" data-assign="${esc(c.id)}">${avatarHTML(c.id, c.name, false)}` +
    `<span class="mrtext"><span class="mrname">${esc(c.name || "(no name)")}</span>` +
    `<span class="mrsub">${esc(c.email || "—")}</span></span>` +
    (badge ? `<span class="mrbadge">${esc(badge)}</span>` : "") + "</button>";
  const wire = (c) => c.querySelectorAll("[data-assign]").forEach((b) =>
    b.addEventListener("click", () => assign(b.dataset.assign)));

  function finish() {
    freeUrl();
    el.innerHTML = `<div class="matchcard"><div class="abouthead"><b class="serif">Photos matched</b></div>` +
      `<div class="matchdone"><p>Assigned <b>${assigned}</b> · skipped <b>${skipped}</b> of ${queue.length} image(s).</p>` +
      `<button class="btn primary" data-done>Done</button></div></div>`;
    el.querySelector("[data-done]").addEventListener("click", close);
  }

  async function render() {
    freeUrl();
    const myI = i, file = queue[i];
    objUrl = URL.createObjectURL(file);
    el.innerHTML =
      `<div class="matchcard"><div class="abouthead"><b class="serif">Match photos</b>` +
      `<span class="matchcount">${i + 1} of ${queue.length}</span><span class="spacer"></span>` +
      `<button class="btn" data-close>Close</button></div>` +
      `<div class="matchbody"><div class="matchimg"><img src="${objUrl}" alt=""><div class="matchfn mono">${esc(file.name)}</div></div>` +
      `<div class="matchpick"><div class="hint">Who is this? — pick a suggestion, or search.</div>` +
      `<div id="match-sugg" class="matchrows"><div class="hint">…</div></div>` +
      `<input id="match-q" type="search" placeholder="Search for a contact…" autocomplete="off">` +
      `<div id="match-res" class="matchrows"></div>` +
      `<div class="editbar"><button class="btn ghost" data-skip>Skip</button>` +
      `<button class="btn ghost" data-discard>Not a contact</button></div></div></div>`;
    el.querySelector("[data-close]").addEventListener("click", close);
    el.querySelector("[data-skip]").addEventListener("click", () => { skipped++; advance(); });
    el.querySelector("[data-discard]").addEventListener("click", advance);
    const q = el.querySelector("#match-q");
    q.addEventListener("input", debounce(async () => {
      const term = q.value.trim();
      let res = [];
      if (term) { try { res = (await api(`/api/search?q=${encodeURIComponent(term)}&limit=8`)).results || []; } catch {} }
      const c = el.querySelector("#match-res"); if (c) { c.innerHTML = res.map((r) => row(r, "")).join(""); wire(c); }
    }, 160));
    let sugg = [];
    try { sugg = (await api(`/api/suggest-photo-match?name=${encodeURIComponent(file.name)}`)).suggestions || []; } catch {}
    if (i !== myI) return;                                    // user already advanced — drop stale suggestions
    const sc = el.querySelector("#match-sugg");
    sc.innerHTML = sugg.length ? sugg.map((s) => row(s, s.basis)).join("") : `<div class="hint">No suggestion — search below.</div>`;
    wire(sc);
  }

  render();
}

async function addTagInline(addChip, schemaFields) {
  const fid = addChip.dataset.add;
  const val = (prompt("New tag:") || "").trim();
  if (!val) return;
  const sf = schemaFields.find((f) => f.field_id === fid) || {};
  const opts = ((sf.config || {}).options || []).slice();
  if (!opts.some((o) => o.value === val)) {                 // append to the vocabulary (persists immediately)
    opts.push({ value: val, label: val, description: "" });
    try { await postJSON("/api/update-field", { field_id: fid, config: { options: opts } }); }
    catch (e) { alert("Couldn't add tag: " + e.message); return; }
    sf.config = sf.config || {}; sf.config.options = opts;
  }
  const pick = addChip.parentElement;
  const existing = pick.querySelector(`.tagchip.pick[data-tag="${cssEsc(val)}"]`);
  if (existing) { existing.classList.add("on"); return; }
  const chip = document.createElement("span");
  chip.className = "tagchip pick on"; chip.dataset.tag = val; chip.textContent = val;
  chip.addEventListener("click", () => chip.classList.toggle("on"));
  pick.insertBefore(chip, addChip);
}

async function saveContactEdit(c, byId) {
  // Collect every change, then apply as ONE atomic changeset (one Undo) — never N concurrent writes,
  // which would collide on the single-holder file-lock.
  const resolutions = [], values = [], clears = [], multi = [];
  els.detail.querySelectorAll("[data-field]").forEach((el) => {
    const tag = el.dataset.field;
    const newVal = el.type === "checkbox" ? (el.checked ? "true" : "") : el.value.trim();
    if (tag.startsWith("src:")) {
      const field = tag.slice(4);
      if (newVal !== valFirst(byId[field])) resolutions.push({ field, value: newVal });
    } else if (tag.startsWith("cf:")) {
      const fid = tag.slice(3);
      if (newVal === valFirst(byId[fid])) return;
      if (newVal === "") clears.push(fid); else values.push({ field_id: fid, value: newVal });
    }
  });
  els.detail.querySelectorAll(".tagpick").forEach((pick) => {
    const fid = pick.dataset.pick;
    const selected = [...pick.querySelectorAll(".tagchip.pick.on")].map((ch) => ch.dataset.tag);
    const orig = multiVals(byId[fid]);
    const key = (a) => a.slice().sort().join(" ");
    if (key(selected) !== key(orig)) multi.push({ field_id: fid, values: selected });
  });
  if (!resolutions.length && !values.length && !clears.length && !multi.length) { selectContact(c.id); return; }
  try { await postJSON("/api/edit-contact", { contact_id: c.id, resolutions, values, clears, multi }); }
  catch (e) { alert("Save failed: " + e.message); return; }
  await selectContact(c.id);
  refreshStats(); browse();
}

// Tag-management modal: edit each tag's name + description, add, remove; saves the whole vocabulary.
async function openTagModal(fieldId, onSaved) {
  const sch = await api("/api/schema");
  const sf = (sch.fields || []).find((f) => f.field_id === fieldId) || { label: "Tags" };
  const opts = (((sf.config || {}).options) || []).map((o) => ({ ...o }));
  const el = document.createElement("div");
  el.className = "aboutoverlay";
  const rowHtml = (o, i) =>
    `<div class="tagrow"><input class="tname" data-i="${i}" value="${esc(o.value)}" placeholder="name" />` +
    `<input class="tdesc" data-i="${i}" value="${esc(o.description || "")}" placeholder="description" />` +
    `<button class="btn ghost trm" data-i="${i}" title="remove">✕</button></div>`;
  function paint() {
    el.querySelector("#tm-rows").innerHTML = opts.map(rowHtml).join("") || `<p class="hint">No tags yet — add one.</p>`;
    el.querySelectorAll(".trm").forEach((b) => b.addEventListener("click", () => { opts.splice(+b.dataset.i, 1); paint(); }));
    el.querySelectorAll(".tname").forEach((inp) => inp.addEventListener("input", () => { opts[+inp.dataset.i].value = inp.value; }));
    el.querySelectorAll(".tdesc").forEach((inp) => inp.addEventListener("input", () => { opts[+inp.dataset.i].description = inp.value; }));
  }
  el.innerHTML =
    `<div class="aboutcard tagmodal"><div class="abouthead"><b class="serif">Manage ${esc(sf.label)}</b>` +
    `<span class="spacer"></span><button class="btn" id="tm-close">Close</button></div>` +
    `<div class="aboutbody"><div id="tm-rows"></div>` +
    `<button class="btn tiny" id="tm-add">+ Add</button>` +
    `<div class="editbar"><span class="hint">Edits to a tag's name apply to the vocabulary, not yet to contacts already tagged.</span>` +
    `<span class="spacer"></span><button class="btn primary" id="tm-save">Save</button></div></div></div>`;
  document.body.appendChild(el);
  paint();
  const close = () => el.remove();
  el.querySelector("#tm-close").addEventListener("click", close);
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  el.querySelector("#tm-add").addEventListener("click", () => { opts.push({ value: "", label: "", description: "" }); paint(); });
  el.querySelector("#tm-save").addEventListener("click", async () => {
    const clean = opts.filter((o) => (o.value || "").trim()).map((o) => ({ value: o.value.trim(), label: o.value.trim(), description: (o.description || "").trim() }));
    try { await postJSON("/api/update-field", { field_id: fieldId, config: { options: clean } }); }
    catch (e) { alert("Couldn't save tags: " + e.message); return; }
    close();
    if (onSaved) onSaved();
  });
}

// ---- schema builder (R6): define your own relationship fields. INV-3 — authored only here. ----
const SCHEMA_KINDS = ["text", "long_text", "number", "date", "boolean", "single_select", "multi_select", "url", "image"];
let schemaCache = [];

async function loadSchema() {
  try { schemaCache = (await api("/api/schema")).fields || []; } catch { schemaCache = []; }
  renderSchema();
}

function fieldBadges(f) {
  const seal = f.disclosure_tier === "private-sealed"
    ? `<span class="fbadge sealed">sealed</span>` : `<span class="fbadge share">shareable</span>`;
  return `<span class="fbadge kind">${esc(f.kind)}</span>${seal}` +
    (f.required ? `<span class="fbadge">required</span>` : "") +
    (f.class === "builtin" ? `<span class="fbadge lock">built-in</span>` : "");
}

function renderSchema() {
  const sub = $("#schema-sub"); if (sub) sub.textContent = `${schemaCache.length} field${schemaCache.length === 1 ? "" : "s"}`;
  const body = $("#schema-body");
  body.innerHTML = schemaCache.map((f) => {
    const actions = `<button class="btn ghost tiny" data-edit="${esc(f.field_id)}">Edit</button>` +
      (f.class === "builtin" ? "" : `<button class="btn ghost tiny danger-ghost" data-remove="${esc(f.field_id)}">Remove</button>`);
    return `<div class="fielddef"><div class="fdbody"><div class="fdlabel">${esc(f.label)}` +
      `<span class="fdslug mono">${esc(f.field_id)}</span></div><div class="fdmeta">${fieldBadges(f)}</div></div>` +
      `<div class="fdactions">${actions}</div></div>`;
  }).join("") || `<p class="hint">No fields yet.</p>`;
  body.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () =>
    openFieldModal(schemaCache.find((x) => x.field_id === b.dataset.edit), loadSchema)));
  body.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", async () => {
    const f = schemaCache.find((x) => x.field_id === b.dataset.remove);
    if (!confirm(`Remove the field “${f.label}” and all values stored in it? You can Undo this.`)) return;
    try { await postJSON("/api/remove-field", { field_id: f.field_id }); }
    catch (e) { alert("Remove failed: " + e.message); return; }
    loadSchema(); refreshStats();
  }));
}

// Create / edit a field definition (a modal). `existing` null → create; otherwise edit (kind locked).
function openFieldModal(existing, onSaved) {
  const isEdit = !!existing;
  const st = {
    label: existing ? existing.label : "",
    kind: existing ? existing.kind : "text",
    required: existing ? !!existing.required : false,
    ai_write_policy: existing ? existing.ai_write_policy : "review-required",
    disclosure_tier: existing ? existing.disclosure_tier : "private-sealed",
    options: (((existing || {}).config || {}).options || []).map((o) => ({ ...o })),
  };
  const isSelect = () => st.kind === "single_select" || st.kind === "multi_select";
  const el = document.createElement("div");
  el.className = "aboutoverlay";
  const sel = (id, val, choices) =>
    `<select id="${id}">${choices.map((c) => `<option value="${c}"${c === val ? " selected" : ""}>${c}</option>`).join("")}</select>`;
  function optsBlock() {
    if (!isSelect()) return "";
    const rows = st.options.map((o, i) =>
      `<div class="tagrow"><input class="oname" data-i="${i}" value="${esc(o.value)}" placeholder="option"/>` +
      `<input class="odesc" data-i="${i}" value="${esc(o.description || "")}" placeholder="description"/>` +
      `<button class="btn ghost orm" data-i="${i}" title="remove">✕</button></div>`).join("");
    return `<div class="fmrow"><label>Options</label><div class="fmgrow"><div id="fm-opts">${rows}</div>` +
      `<button class="btn tiny" id="fm-addopt">+ option</button></div></div>`;
  }
  function readScalars() {
    st.label = el.querySelector("#fm-label").value;
    if (!isEdit) st.kind = el.querySelector("#fm-kind").value;
    st.required = el.querySelector("#fm-req").checked;
    st.ai_write_policy = el.querySelector("#fm-policy").value;
    st.disclosure_tier = el.querySelector("#fm-tier").value;
  }
  function paint() {
    el.innerHTML =
      `<div class="aboutcard fieldmodal"><div class="abouthead"><b class="serif">${isEdit ? "Edit field" : "New field"}</b>` +
      `<span class="spacer"></span><button class="btn" id="fm-close">Close</button></div><div class="aboutbody">` +
      `<div class="fmrow"><label>Label</label><input id="fm-label" value="${esc(st.label)}" placeholder="e.g. How we met"/></div>` +
      `<div class="fmrow"><label>Kind</label>${sel("fm-kind", st.kind, SCHEMA_KINDS).replace("<select ", `<select ${isEdit ? "disabled " : ""}`)}</div>` +
      optsBlock() +
      `<div class="fmrow"><label>Required</label><input type="checkbox" id="fm-req"${st.required ? " checked" : ""}/></div>` +
      `<div class="fmrow"><label>AI write</label>${sel("fm-policy", st.ai_write_policy, ["review-required", "append-only", "free-write"])}</div>` +
      `<div class="fmrow"><label>Disclosure</label>${sel("fm-tier", st.disclosure_tier, ["private-sealed", "private-shareable-on-consent"])}</div>` +
      `<div class="editbar"><span class="hint">Private + sealed by default. The AI never authors fields.</span>` +
      `<span class="spacer"></span><button class="btn primary" id="fm-save">${isEdit ? "Save" : "Create field"}</button></div></div></div>`;
    const close = () => el.remove();
    el.querySelector("#fm-close").addEventListener("click", close);
    el.addEventListener("click", (e) => { if (e.target === el) close(); });
    if (!isEdit) el.querySelector("#fm-kind").addEventListener("change", (e) => { readScalars(); st.kind = e.target.value; paint(); });
    el.querySelectorAll(".oname").forEach((inp) => inp.addEventListener("input", () => { st.options[+inp.dataset.i].value = inp.value; }));
    el.querySelectorAll(".odesc").forEach((inp) => inp.addEventListener("input", () => { st.options[+inp.dataset.i].description = inp.value; }));
    el.querySelectorAll(".orm").forEach((b) => b.addEventListener("click", () => { readScalars(); st.options.splice(+b.dataset.i, 1); paint(); }));
    const add = el.querySelector("#fm-addopt");
    if (add) add.addEventListener("click", () => { readScalars(); st.options.push({ value: "", description: "" }); paint(); });
    el.querySelector("#fm-save").addEventListener("click", save);
  }
  async function save() {
    readScalars();
    if (!st.label.trim()) { alert("A label is required."); return; }
    const config = isSelect() ? { options: st.options.filter((o) => (o.value || "").trim()) } : {};
    const payload = { label: st.label.trim(), config, required: st.required,
                      ai_write_policy: st.ai_write_policy, disclosure_tier: st.disclosure_tier };
    try {
      if (isEdit) await postJSON("/api/update-field", { field_id: existing.field_id, ...payload });
      else await postJSON("/api/define-field", { kind: st.kind, ...payload });
    } catch (e) { alert("Save failed: " + e.message); return; }
    el.remove();
    if (onSaved) onSaved();
  }
  document.body.appendChild(el);
  paint();
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

// Refetch the live duplicate set and refresh the nav badge + subtitle, WITHOUT changing the view.
// Called on its own after a merge (so the header reflects the new state while staying on the summary),
// and by loadDuplicates before it (re)renders.
async function fetchDuplicates() {
  const [props, cands] = await Promise.all([api("/api/proposals"), api("/api/candidates")]);
  const proposals = props.proposals || [];
  const propKeys = new Set(proposals.map((p) => p.cluster_key).filter(Boolean));
  const aiItems = proposals.map((p) => ({ kind: "proposal", ...p }));
  const detItems = (cands.clusters || [])
    .filter((c) => !propKeys.has(c.key))            // dedup: a pending proposal already covers this cluster
    .map((c) => ({ kind: "candidate", ...c }));
  dupItems = aiItems.concat(detItems);              // AI proposals first — review the AI's work, then the detector
  dupIndex = 0;
  updateDupCounts();
}

// Land on the duplicates "home": fresh data, then pick-groups (bulk) or one-at-a-time (review).
async function loadDuplicates() {
  try {
    await fetchDuplicates();
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
  // The commit bar is position:fixed but lives inside `.view`, whose entrance animation leaves an
  // identity-matrix transform — that makes `.view` the containing block, so translateY(110%) can't
  // push the bar off the viewport. Hard-hide it off step 2 via `hidden` (display:none) instead.
  $("#b-commit").hidden = n !== 2;
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
  const skipped = (res.skipped || []).length;
  $("#b-done-h").textContent = `Merged ${n} duplicate set${n === 1 ? "" : "s"}`;
  $("#b-done-p").textContent =
    `${n} contact${n === 1 ? " was" : "s were"} folded into ${n === 1 ? "its" : "their"} canonical record, in one transaction.` +
    (skipped ? ` ${skipped} that overlapped another merge ${skipped === 1 ? "was" : "were"} skipped — ${skipped === 1 ? "it" : "they"}’ll reappear next time.` : "") +
    ` Your imported source records were never changed.`;
  $("#b-undoline").textContent = `↺ One Undo reverses all ${n} merges `;
  bulkStep(3);                                       // show the summary; the commit bar hides here
  refreshStats(); browse();
  fetchDuplicates().catch(() => {});                 // refresh the nav badge + subtitle to the post-merge count
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

// ---- Data view: import (upload -> preview -> commit) + export (download). Uploaded bytes reach only
// the local daemon, which ingests them through the same `cli.ingest` the CLI uses; export is read-only.
// Mirrors the CLI's preview->confirm flow and its dry-run report. ----
let _impToken = null;

// Like postJSON, but surfaces the server's {"error": …} text (e.g. the lock-contention 409).
async function apiPost(path, body) {
  const res = await fetch(path, { method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${path}`);
  return data;
}

function showImpStep(step) {           // "choose" | "staging" | "preview" | "result"
  ["choose", "staging", "preview", "result"].forEach((s) => {
    const el = $("#imp-" + s);
    if (el) el.hidden = s !== step;
  });
}

function loadData() {                   // entering the Data view: reset to a clean choose step
  showImpStep("choose");
  $("#exp-hint").textContent = "";
}

const IMPORTABLE = /\.(vcf|zip|csv|json)$/i;

async function stageAndPreview(fileList) {
  const files = [...fileList].filter((f) => IMPORTABLE.test(f.name));
  if (!files.length) { alert("No importable files found (.vcf, .zip, .csv, .json)."); return; }
  _impToken = null;
  $("#imp-source").value = "";
  $("#imp-staged-total").textContent = String(files.length);
  $("#imp-staged").textContent = "0";
  $("#imp-bar").style.width = "0%";
  showImpStep("staging");
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    let b64;
    try { b64 = await fileToBase64(f); } catch { alert(`Couldn't read ${f.name}.`); showImpStep("choose"); return; }
    try {
      const res = await apiPost("/api/import/stage",
        { token: _impToken, relpath: f.webkitRelativePath || f.name, data_base64: b64 });
      _impToken = res.token;
    } catch (e) { alert("Upload failed: " + e.message); showImpStep("choose"); return; }
    $("#imp-staged").textContent = String(i + 1);
    $("#imp-bar").style.width = `${Math.round(((i + 1) / files.length) * 100)}%`;
  }
  await previewImport();
}

async function previewImport() {
  const source = $("#imp-source").value || undefined;
  try {
    const res = await apiPost("/api/import/preview", { token: _impToken, source });
    renderImpPreview(res.report);
    showImpStep("preview");
  } catch (e) { alert("Preview failed: " + e.message); showImpStep("choose"); }
}

function renderImpPreview(rep) {
  const paths = rep.paths || [];
  $("#imp-prev-rows").innerHTML = paths.map((p) => {
    const base = esc(String(p.path || "").split("/").pop());
    if (p.skipped_reason)
      return `<tr class="skip"><td class="pf">${base}</td><td colspan="3" class="sr">skipped — ${esc(p.skipped_reason)}</td></tr>`;
    return `<tr><td class="pf">${base}</td><td class="pn">${p.count}</td>` +
      `<td>${esc(srcLabel(p.source))}</td><td class="pc">${esc(p.format)} · ${esc(p.confidence)}</td></tr>`;
  }).join("") || `<tr><td colspan="4" class="sr">No importable files found.</td></tr>`;

  const total = rep.total || 0;
  $("#imp-prev-summary").innerHTML = `<b>${total.toLocaleString()}</b> contact(s) from ${paths.length} file(s)`;

  const bySrc = Object.entries(rep.by_source || {}).map(([k, v]) => `${srcLabel(k)} ${v}`).join(" · ");
  const byKind = Object.entries(rep.by_key_kind || {}).map(([k, v]) => `${k} ${v}`).join(" · ");
  const parts = [];
  if (bySrc) parts.push(`by source: ${bySrc}`);
  if (byKind) parts.push(`stable-id: ${byKind} (${rep.nameless || 0} name-less)`);
  $("#imp-prev-stats").textContent = parts.join("     ");

  const btn = $("#imp-commit");
  btn.textContent = total ? `Import ${total.toLocaleString()} contact${total > 1 ? "s" : ""}` : "Nothing to import";
  btn.disabled = !total;
}

async function commitImport() {
  const source = $("#imp-source").value || undefined;
  const btn = $("#imp-commit");
  btn.disabled = true;
  try {
    const res = await apiPost("/api/import/commit", { token: _impToken, source });
    _impToken = null;
    renderImpResult(res.report);
    showImpStep("result");
    await refreshStats(); browse(); loadDuplicates();
  } catch (e) {
    alert("Import failed: " + e.message);     // e.g. lock contention — let them retry
    btn.disabled = false;
  }
}

function renderImpResult(rep) {
  const n = rep.total || 0;
  $("#imp-result-h").textContent = n ? `Imported ${n.toLocaleString()} contact${n > 1 ? "s" : ""}` : "Nothing imported";
  const stored = rep.stored != null ? `Your store now holds ${rep.stored.toLocaleString()} record(s).` : "";
  const skipped = (rep.paths || []).filter((p) => p.skipped_reason).length;
  $("#imp-result-p").textContent = `${stored}${skipped ? ` ${skipped} file(s) skipped.` : ""}`.trim();
}

async function cancelImport() {
  if (_impToken) { try { await apiPost("/api/import/cancel", { token: _impToken }); } catch { /* best effort */ } }
  _impToken = null;
  showImpStep("choose");
}

async function downloadExport(url, filename) {
  try {
    const res = await fetch(url, { headers: { Accept: "*/*" } });
    if (!res.ok) { alert(`Export failed (${res.status}).`); return; }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
    $("#exp-hint").textContent = `Downloaded ${filename}.`;
  } catch (e) { alert("Export failed: " + (e && e.message ? e.message : e)); }
}

function initDataView() {
  const fileInput = $("#imp-file-input"), folderInput = $("#imp-folder-input"), dz = $("#dropzone");
  if (!dz) return;
  $("#imp-file").addEventListener("click", () => fileInput.click());
  $("#imp-folder").addEventListener("click", () => folderInput.click());
  fileInput.addEventListener("change", () => { if (fileInput.files.length) stageAndPreview(fileInput.files); fileInput.value = ""; });
  folderInput.addEventListener("change", () => { if (folderInput.files.length) stageAndPreview(folderInput.files); folderInput.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  dz.addEventListener("dragleave", (e) => { if (e.target === dz) dz.classList.remove("drag"); });
  dz.addEventListener("drop", (e) => {
    e.preventDefault(); dz.classList.remove("drag");
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) stageAndPreview(files);   // dropped files (a dropped folder: use Choose folder…)
  });
  $("#imp-source").addEventListener("change", () => { if (_impToken) previewImport(); });
  $("#imp-commit").addEventListener("click", commitImport);
  $("#imp-cancel").addEventListener("click", cancelImport);
  $("#imp-again").addEventListener("click", () => showImpStep("choose"));
  const date = new Date().toISOString().slice(0, 10);
  $("#exp-vcard").addEventListener("click", () => downloadExport("/api/export?format=vcard", `prm-contacts-${date}.vcf`));
  $("#exp-raw").addEventListener("click", () => downloadExport("/api/export?raw=1", `prm-backup-${date}.json`));
}

// ---- nav / reset ----
function show(view) {
  document.querySelectorAll(".navitem[data-view]").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("show", v.id === view));
}
document.querySelectorAll(".navitem[data-view]").forEach((n) =>
  n.addEventListener("click", () => {
    show(n.dataset.view);
    if (n.dataset.view === "duplicates") loadDuplicates();   // re-enter at the fresh home, not a stale step
    if (n.dataset.view === "schema") loadSchema();
    if (n.dataset.view === "data") loadData();
    if (n.dataset.view === "access") loadAccessView();
  }));
const schemaNew = $("#schema-new");
if (schemaNew) schemaNew.addEventListener("click", () => openFieldModal(null, loadSchema));

// "Match photos…" → a folder picker (read locally) → the guided matcher (R7c).
const matchBtn = $("#match-photos"), matchInput = $("#match-input");
if (matchBtn && matchInput) {
  matchBtn.addEventListener("click", () => matchInput.click());
  matchInput.addEventListener("change", () => {
    if (matchInput.files.length) openPhotoMatcher(matchInput.files);
    matchInput.value = "";                                    // allow re-picking the same folder
  });
}

// A failed avatar image falls back to the monogram. `error` on <img> doesn't bubble, so listen in the
// capture phase on the list + detail containers.
els.rows.addEventListener("error", onAvatarError, true);
els.detail.addEventListener("error", onAvatarError, true);

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
    `<button class="btn primary" id="go-import">Import some contacts</button></div>`;
  els.listhead.textContent = "All contacts";
  const b = $("#go-import");
  if (b) b.addEventListener("click", () => { show("data"); loadData(); });
}

async function refreshStats() {
  const s = await api("/api/status");
  if (els.statBuild) els.statBuild.textContent = s.build_label || "—";   // AC-15: shown even before any import
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

// ---- AC-7 diagnostics overlay (?diag) — always reachable (AC-6), sanitized, no contact data ----
async function showDiag() {
  let dump;
  try { dump = await api("/api/diag"); } catch (e) { dump = { error: String(e && e.message || e) }; }
  const text = JSON.stringify(dump, null, 2);
  const el = document.createElement("div");
  el.className = "diagoverlay";
  el.innerHTML =
    `<div class="diagcard"><div class="diaghead"><b class="serif">Diagnostics</b>` +
    `<span class="muted">sanitized · no contact data · for a bug report</span><span class="spacer"></span>` +
    `<button class="btn" id="diag-copy">Copy</button><button class="btn" id="diag-close">Close</button></div>` +
    `<pre class="diagpre">${esc(text)}</pre></div>`;
  document.body.appendChild(el);
  $("#diag-copy").addEventListener("click", () => { try { navigator.clipboard.writeText(text); } catch {} });
  $("#diag-close").addEventListener("click", () => { el.remove(); history.replaceState(null, "", location.pathname); });
}

// ---- About card — what PRM is, and a link to the upstream Personal Network Toolkit. The link is the
// only outward pointer in the workspace; it's a user-initiated navigation (rel=noreferrer keeps the
// local URL off the wire), never an automatic request — contact data still never leaves the device. ----
const PNT_URL = "https://github.com/richbodo/personal_network_toolkit";
const PRM_URL = "https://github.com/richbodo/prm";
function showAbout() {
  const el = document.createElement("div");
  el.className = "aboutoverlay";
  el.innerHTML =
    `<div class="aboutcard">` +
    `<div class="abouthead"><span class="mark serif">P<em>R</em>M</span><span class="ver">v0.1</span>` +
    `<span class="spacer"></span><button class="btn" id="about-close">Close</button></div>` +
    `<div class="aboutbody"><p>PRM is a home-cooked meal originally built for ` +
    `my friend Vaipunu. It is designed to be a relatively secure place to back up contact data and a ` +
    `highly functional application through which to triage contact data and build a store of private, ` +
    `curated relationship data. It is regularly validated against the personal network toolkit as a ` +
    `<a href="${PNT_URL}" target="_blank" rel="noopener noreferrer">personal network application</a>.</p>` +
    `<p class="repo">Source code · <a href="${PRM_URL}" target="_blank" rel="noopener noreferrer">github.com/richbodo/prm</a></p>` +
    `</div></div>`;
  document.body.appendChild(el);
  const close = () => { el.remove(); document.removeEventListener("keydown", onKey); };
  function onKey(e) { if (e.key === "Escape") close(); }
  $("#about-close").addEventListener("click", close);
  el.addEventListener("click", (e) => { if (e.target === el) close(); });   // click the backdrop to dismiss
  document.addEventListener("keydown", onKey);
}
const aboutLink = $("#about-link");
if (aboutLink) aboutLink.addEventListener("click", showAbout);

// ---- boot, with a watchdog so a wedged daemon shows diagnostics instead of hanging ----
let booted = false;
const bootWatchdog = setTimeout(() => {
  if (!booted) emptyState('Still loading after 8s — the daemon may be wedged. Open <a href="?diag">?diag</a> for ' +
    'sanitized diagnostics, or restart <code>just serve</code>.');
}, 8000);

(async function init() {
  if (/[?&]diag\b/.test(location.search)) showDiag();        // always reachable, even if boot fails
  initDataView();                                            // wire the Data view once (its DOM is always present)
  try {
    const ready = await refreshStats();
    booted = true; clearTimeout(bootWatchdog);               // the API answered — boot succeeded
    loadDisclosure();                                        // banners + PNA-mode markers (works pre-import too)
    if (!ready) {
      els.statContacts.textContent = "0";
      emptyState('Import a file or folder to get started — or seed the demo with <code>prm init --demo</code>.');
      return;
    }
    await browse();
    loadDuplicates();
  } catch (err) {
    booted = true; clearTimeout(bootWatchdog);
    emptyState(`Couldn’t reach the workspace API (${esc(err.message)}). Open <a href="?diag">?diag</a> for diagnostics.`);
  }
})();


// ============================================================================
// AI access / disclosure — the EX-CLOUD-LLM workspace handler (banners, the gate,
// the Settings hub explainer + strength profile, return-to-PNA). State is served by
// /api/disclosure + /api/connections; consent writes go through the daemon under the lock.
// ============================================================================
let disclosureState = null;

const BANNER_DEFS = {
  "cloud-exception": { cls: "danger", lead: "Not a PNA right now.",
    text: "You granted a cloud AI access to your private data, so it can leave this device (EX-CLOUD-LLM)." },
  "local-ai": { cls: "warn", lead: "Local AI access is on.",
    text: "A local model can read the fields you marked shareable. It stays on this device — but a local model could still relay it on; only you can vouch for it." },
  "network-exposed": { cls: "danger", lead: "Your contacts are reachable on your network.",
    text: "The workspace is bound to a non-loopback address, so other devices on your network can reach it." },
};

// EX-CLOUD-LLM per-dimension strength profile (PNT spec/exceptions.md) + the EX-H9 blast-radius row.
const STRENGTH = [
  ["Consent precedes turning it on", "enforced", "Sharing is blocked until you grant it here in the workspace."],
  ["A non-PNA signal shows while active", "enforced", "A persistent banner shows until you return to PNA mode."],
  ["The mode is reversible", "enforced", "“Return to PNA mode” clears the grant — the next AI read is withheld."],
  ["Which fields can cross (blast radius)", "enforced", "Sealed fields never cross; only the shareable projection you curated, and only with consent."],
  ["Servers are read-only over your data", "verifiable", "Open source; the MCP read tools never write your store."],
  ["Consent reaches you, not a proxy", "best-effort", "A cloud client is asked to relay it (MCP instructions); it can’t be compelled."],
  ["The provider won’t train on / keep it", "provider-asserted", "The provider’s policy; PRM can’t verify it."],
  ["Data already sent to the cloud", "none", "Irreversible once it has crossed — return-to-PNA can’t recall it."],
];

// Banner dismissal is client-side and keyed to the current grant, so a NEW grant re-shows it. Dismiss
// only acknowledges (EX-H3) — the grant stays on, and the AI-access view always shows it.
function bannerToken(b) {
  if (!disclosureState) return "";
  if (b === "network-exposed") return "net";
  return `${disclosureState.mode}:${disclosureState.consented_at || ""}`;
}
function isDismissed(b) { try { return localStorage.getItem(`prm_banner_${b}`) === bannerToken(b); } catch { return false; } }
function setDismissed(b) { try { localStorage.setItem(`prm_banner_${b}`, bannerToken(b)); } catch {} renderBanners(); }

function syncPnaMarkers() {
  const m = disclosureState ? disclosureState.mode : "pna";
  document.body.dataset.pnaMode = m === "cloud-exception" ? "non-pna" : "pna";
  document.body.dataset.pnaExceptions = m === "cloud-exception" ? "EX-CLOUD-LLM" : "";
  const badge = $("#access-badge");
  if (badge) badge.hidden = m === "pna";
}

function renderBanners() {
  const host = $("#banners");
  if (!host) return;
  const active = (disclosureState && disclosureState.banners) || [];
  host.innerHTML = active.filter((b) => BANNER_DEFS[b] && !isDismissed(b)).map((b) => {
    const d = BANNER_DEFS[b];
    return `<div class="pbanner ${d.cls}" role="status"${b === "cloud-exception" ? ' data-pna-exception="EX-CLOUD-LLM"' : ""}>` +
      `<span class="pb-text"><b>${esc(d.lead)}</b> ${esc(d.text)}</span>` +
      `<span class="pb-actions">` +
      `<button class="pb-link" data-access>What this means →</button>` +
      `<button class="pb-x" data-dismiss="${b}" title="Acknowledge — it stays on; manage it in AI access">Dismiss</button>` +
      `</span></div>`;
  }).join("");
  host.querySelectorAll("[data-dismiss]").forEach((x) => x.addEventListener("click", () => setDismissed(x.dataset.dismiss)));
  host.querySelectorAll("[data-access]").forEach((x) => x.addEventListener("click", () => { show("access"); loadAccessView(); }));
}

async function loadDisclosure() {
  try { disclosureState = await api("/api/disclosure"); } catch { disclosureState = null; }
  syncPnaMarkers();
  renderBanners();
}

async function loadAccessView() {
  const body = $("#access-body");
  if (body) body.innerHTML = `<p class="ph">Loading…</p>`;
  const [d, conn] = await Promise.all([
    api("/api/disclosure").catch(() => null),
    api("/api/connections").catch(() => null),
  ]);
  disclosureState = d;
  syncPnaMarkers();
  renderBanners();
  renderAccessView(d, conn);
}

function strengthRows() {
  return STRENGTH.map(([dim, cls, why]) =>
    `<tr><td class="sd">${esc(dim)}</td><td><span class="spill ${cls}">${esc(cls)}</span></td><td class="sw">${esc(why)}</td></tr>`).join("");
}

function renderAccessView(d, conn) {
  const body = $("#access-body");
  if (!body) return;
  if (!d) { body.innerHTML = `<div class="datacard"><p class="lede-sub">Couldn’t load AI-access state.</p></div>`; return; }
  const mode = d.mode;
  const counts = d.shareable_counts || { contacts: 0, values: 0 };
  const fields = d.shareable_fields || [];
  const modeLabel = mode === "cloud-exception" ? "Cloud AI — data leaves this device"
    : mode === "local-ai" ? "Local AI — data stays on this device"
    : "Local-only (PNA) — nothing private leaves";

  const grantBtn = `<button class="btn primary" id="ax-grant">${mode === "pna" ? "Grant AI access…" : "Change…"}</button>`;
  const returnBtn = mode !== "pna" ? `<button class="btn" id="ax-return">Return to PNA mode</button>` : "";
  const scopeGrew = d.scope_grew
    ? `<p class="axnote warn">You marked new fields shareable since you consented — review and re-confirm to include them.</p>` : "";
  const access =
    `<div class="datacard"><h2 class="serif dh">Current access</h2>` +
    `<div class="axmode axmode-${mode}"><span class="axdot"></span><b>${esc(modeLabel)}</b>` +
    (d.consented_at ? `<span class="axwhen">since ${esc(String(d.consented_at).slice(0, 10))}</span>` : "") + `</div>` +
    `<p class="lede-sub" style="margin:12px 0">An AI reads your data over the local MCP servers. ` +
    (mode === "pna"
      ? `Right now <b>nothing private crosses</b> — sealed fields never do, and shareable fields wait for your consent.`
      : `It can read your <b>${counts.values}</b> shareable value(s) across <b>${counts.contacts}</b> contact(s). Sealed fields never cross.`) +
    `</p>${scopeGrew}<div class="axctl">${grantBtn}${returnBtn}</div></div>`;

  const fieldList = fields.length
    ? `<div class="axchips">${fields.map((f) => `<span class="fbadge share">${esc(f)}</span>`).join("")}</div>`
    : `<p class="lede-sub">No fields are marked shareable — everything is sealed. Mark a field shareable in <b>Schema</b> to let an AI read it (with your consent).</p>`;
  const floor =
    `<div class="datacard"><h2 class="serif dh">The data-floor</h2>` +
    `<p class="lede-sub">Every relationship field is <b>sealed by default</b> and never reaches an AI. Only fields you mark ` +
    `<b>shareable-on-consent</b> can cross — and only after you grant access above.</p>${fieldList}</div>`;

  const exActive = mode === "cloud-exception";
  const exHead = exActive
    ? `<p class="axnote danger"><b>Active now.</b> This app is not a PNA while a cloud AI has access.</p>`
    : `<p class="lede-sub">Not active. This is what would happen if you grant a cloud AI access.</p>`;
  const ex =
    `<div class="datacard"><h2 class="serif dh">EX-CLOUD-LLM — what it means</h2>${exHead}` +
    `<table class="stable"><thead><tr><th>Guarantee</th><th>Strength</th><th></th></tr></thead><tbody>${strengthRows()}</tbody></table>` +
    `<p class="axnote">Reversibility is <b>mode only</b>: returning to PNA mode stops future sharing but cannot recall data already sent.</p></div>`;

  const reg = (conn && conn.mcp_registered && conn.mcp_registered.claude_desktop) || [];
  const net = !!(conn && conn.network_exposed);
  const connCard =
    `<div class="datacard"><h2 class="serif dh">Connections</h2>` +
    `<div class="axrow"><span>Workspace network exposure</span><b class="${net ? "bad" : "ok"}">${net ? "on your network" : "this device only (loopback)"}</b></div>` +
    `<div class="axrow"><span>MCP servers registered in Claude Desktop</span><b>${reg.length ? esc(reg.join(", ")) : "none detected"}</b></div>` +
    `<p class="lede-sub" style="margin-top:10px">PRM can only show what it can see — it can’t detect a client it isn’t told about. Disconnect PRM’s servers with <code>just mcp-uninstall</code>.</p></div>`;

  body.innerHTML = access + floor + ex + connCard;
  const g = $("#ax-grant"); if (g) g.addEventListener("click", openGateModal);
  const r = $("#ax-return"); if (r) r.addEventListener("click", returnToPna);
}

function gatePreviewHTML() {
  const d = disclosureState || {};
  const fields = d.shareable_fields || [];
  const counts = d.shareable_counts || { contacts: 0, values: 0 };
  if (!fields.length)
    return `<p class="gate-prev">No fields are marked <b>shareable</b> yet, so an AI would read <b>nothing</b> private — sealed fields never cross. (Mark a field shareable in <b>Schema</b> first.)</p>`;
  return `<p class="gate-prev">Granting access lets an AI read <b>${counts.values}</b> value(s) in these shareable fields, across <b>${counts.contacts}</b> contact(s):</p>` +
    `<div class="axchips">${fields.map((f) => `<span class="fbadge share">${esc(f)}</span>`).join("")}</div>` +
    `<p class="gate-sealed">Everything else stays <b>sealed</b> and never crosses.</p>`;
}

function openGateModal() {
  const el = document.createElement("div");
  el.className = "aboutoverlay";
  el.innerHTML =
    `<div class="aboutcard gatecard"><div class="abouthead"><b class="serif">Grant AI access</b>` +
    `<span class="spacer"></span><button class="btn" id="gate-close">Close</button></div>` +
    `<div class="aboutbody"></div></div>`;
  document.body.appendChild(el);
  const close = () => el.remove();
  el.querySelector("#gate-close").addEventListener("click", close);
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  renderGateChoose(el, close);
}

function renderGateChoose(el, close) {
  el.querySelector(".aboutbody").innerHTML =
    gatePreviewHTML() +
    `<div class="gate-opts">` +
    `<button class="gate-opt" id="gate-local"><span class="go-h">Local model — data stays on this device</span>` +
    `<span class="go-s">No exception; you’re still a PNA. (A local model could still relay data on — only you can vouch for it.)</span></button>` +
    `<button class="gate-opt cloud" id="gate-cloud"><span class="go-h">Cloud model — data leaves this device</span>` +
    `<span class="go-s">Raises EX-CLOUD-LLM; a persistent “not a PNA” banner shows until you return to PNA mode.</span></button>` +
    `</div>`;
  el.querySelector("#gate-local").addEventListener("click", () => grant("local-ai", close));
  el.querySelector("#gate-cloud").addEventListener("click", () => renderGateCloud(el, close));
}

function renderGateCloud(el, close) {
  el.querySelector(".aboutbody").innerHTML =
    `<p class="gate-prev"><b>Cloud model — data will leave this device.</b> Connecting a cloud AI raises the <b>EX-CLOUD-LLM</b> ` +
    `exception: this app is no longer a PNA while it’s active. You can return to PNA mode anytime — but that stops only future ` +
    `sharing; it can’t recall data already sent.</p>` +
    gatePreviewHTML() +
    `<label class="gate-check"><input type="checkbox" id="gate-ack"> I understand my contacts will be sent to a cloud provider.</label>` +
    `<div class="editbar"><button class="btn" id="gate-back">← Back</button><span class="spacer"></span>` +
    `<button class="btn primary" id="gate-confirm" disabled>Turn on cloud access</button></div>`;
  el.querySelector("#gate-ack").addEventListener("change", (e) => { el.querySelector("#gate-confirm").disabled = !e.target.checked; });
  el.querySelector("#gate-back").addEventListener("click", () => renderGateChoose(el, close));
  el.querySelector("#gate-confirm").addEventListener("click", () => grant("cloud-exception", close));
}

async function grant(mode, done) {
  try { await postJSON("/api/disclosure/consent", { mode }); }
  catch (e) { alert("Couldn’t grant access: " + e.message); return; }
  if (done) done();
  loadAccessView();
}

async function returnToPna() {
  try { await postJSON("/api/disclosure/return-to-pna", {}); }
  catch (e) { alert("Couldn’t return to PNA mode: " + e.message); return; }
  loadAccessView();
}
