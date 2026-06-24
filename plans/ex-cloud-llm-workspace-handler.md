# Plan — EX-CLOUD-LLM workspace handler + projection-bound data-floor (v0.2)

*Status: **draft for review** (2026-06-24). Companion to
[`../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`](../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md),
[`mcp-ex-h7-consent-handshake.md`](mcp-ex-h7-consent-handshake.md) (the v0.1 server-side down-payment this
builds on), [`../docs/roadmap.md`](../docs/roadmap.md) (v0.2 `EX-CLOUD-LLM` + data-floor), and the
upstream contract in PNT (`spec/exceptions.md`, `spec/PNA_Spec.md`,
`docs/design-notes/2026-06-data-floor-disclosure-tiers.md`). Tracks [prm#36](https://github.com/richbodo/prm/issues/36).*

## 1. The gap

Two halves of `EX-CLOUD-LLM` are already shipped; the load-bearing half is not.

- **Shipped (v0.1, server-side).** Both MCP servers carry the EX-H7 best-effort consent notice via the
  MCP `instructions` handshake (`mcp_servers/consent.py:CLOUD_LLM_NOTICE`). A first increment of the
  read-side floor exists: `projection.strip_sealed` drops `private-sealed` relationship fields, and every
  custom/built-in field defaults to `private-sealed` (`core/schema.py`), so sealed notes already cannot
  cross the MCP surface.
- **Missing (this milestone).** The **workspace handler** — the consent gate (EX-H2), the persistent
  "not a PNA right now" signal (EX-H3), the runtime active-set explainer (EX-H4), the reversible
  return-to-PNA-mode control (EX-H5), and the per-dimension strength profile (EX-H8) — and the part that
  makes the gate *mean* something: **cross-process enforcement**. Today `strip_sealed` is a post-projection
  filter, the `private-shareable-on-consent` tier crosses **unconditionally** (no consent is actually
  required), and the MCP servers have no knowledge of any consent the user gave. The gate would be honest
  signage over a door that is always open.

## 2. What this milestone delivers (and why it matters upstream)

The full `EX-CLOUD-LLM` workspace handler **plus** a genuinely projection-bound, consent-gated cloud
surface for the **private overlay** (notes / tags / user-defined custom fields). It realizes:

| Clause / contract | Source | Status today | After this |
| --- | --- | --- | --- |
| EX-H2 consent before raise | PNT `spec/exceptions.md` (normative) | — | workspace gate |
| EX-H3 persistent "not a PNA" signal | normative | — | banner |
| EX-H4 active-set explainer | normative | — | runtime explainer |
| EX-H5 declared reversibility (mode only) | normative | partial (mode-only declared) | return-to-PNA control |
| EX-H8 per-dimension strength profile | normative | — | shown in explainer |
| EX-H7 consent reaches the human / **fail-closed** | normative (best-effort) + **RFC** (enforced at PNA's own surface) | best-effort `instructions` | **enforced** at the workspace surface |
| AC-MCP-A per-call consent for private rows | PNT `spec/PNA_Spec.md` (normative) | partial-conformance | enforced for the overlay |
| AC-MCP-C projection-bound cloud surface | PNT data-floor stub (**proposed**) | not demonstrated | **demonstrated** |
| PR-7 per-field `disclosure_tier`, workspace-only, enforced at the query layer | proposed | tier exists; UI-only | enforced at the query layer |
| EX-H9 strength profile names a **blast-radius** dimension (`enforced`) | proposed | — | the disclosure preview |

**PRM is upstream's intended demonstrator** for the data-floor: AC-MCP-C / PR-7 / EX-H9 are merged into
PNT only as a design-note *stub* and deliberately not yet normative — per PNT `CONTRIBUTING.md` an
obligation-imposing AC lands *with the reference design that demonstrates it*. Building this is what
graduates them. (See PNT `docs/design-notes/2026-06-data-floor-disclosure-tiers.md`.)

**Scope boundary.** This milestone governs the **private overlay** only. Imported **shared contacts**
(names / emails / orgs from `shared.db`) keep their existing v0.1 posture — returned over MCP with the
EX-H7 signal and the standing "local AI recommended" guidance — because shared contacts are a separate
store with a separate (already-documented) posture. The exception's *mode/banner* still reflects "you
connected a cloud AI to your contacts"; the *floor* is what bounds the overlay.

## 3. Posture decisions taken (confirmed with the maintainer)

1. **Enforced, not signal-only.** The `private-shareable-on-consent` projection crosses the MCP surface
   **only when the user has consented in the workspace** → requires on-disk consent state the servers read
   (Scenario B below). Sealed fields never cross, in any mode.
2. **Two-path gate, framed by where the data goes:** *"Local model — data stays on this device"* (no
   exception; INV-1 holds; a recorded mode) vs *"Cloud model — data leaves this device"* (raises
   `EX-CLOUD-LLM`). The real distinction is **same-host process vs. network to another device.** PRM cannot
   detect which; the user declares, and the EX-H7 `instructions` notice nudges a cooperating client to make
   them declare.
3. **Disclosure scope is shown, and re-consented when it grows.** The gate presents the blast radius
   (which fields, how many contacts/values) before consent (EX-H9); if the shareable set later expands,
   consent is re-requested.
4. **Honest, dismissible banners for every grant — each linking to a Settings hub.** Local access raises a
   dismissible informative banner ("Local access to some private data is granted"); cloud access raises the
   persistent "not a PNA right now" exception banner (EX-H3); network-exposed shared contacts (daemon bound
   non-loopback) raise their own banner. Banners can coexist. Dismiss *acknowledges* (never changes the
   grant); every banner links to **Settings → AI access & connections**, where the user sees exactly what
   they granted and can change or revoke it. That Settings hub *is* the EX-H4 active-set explainer + the
   EX-H5 return-to-PNA control, plus a best-effort inventory of what's connected (a reminder to disconnect
   anything no longer needed).

## 4. Architecture — the disclosure state machine

A single durable **disclosure mode** governs the overlay surface:

```
            ┌─────────── consent: "local model, stays on device" ──────────┐
            │                                                               ▼
        ┌───────┐                                                    ┌───────────┐
        │  pna  │  ── consent: "cloud model, leaves this device" ──► │ local-ai  │
        │(default)│ ─────────────────────────────────────────┐      └───────────┘
        └───────┘                                             ▼            │
            ▲                                          ┌───────────────┐   │
            └──────────── return-to-PNA-mode ──────────┤ cloud-exception│◄─┘ (escalate)
                                                       └───────────────┘
```

- **`pna`** (default): MCP overlay surface returns **no** shareable fields (fail-closed) and **no** sealed
  fields (ever). Banner: none. Sidebar: "Kept on this device."
- **`local-ai`**: shareable projection unlocked for MCP reads; **no exception raised** (data stays on the
  host). Banner: a **dismissible informative** banner — "Local access to some private data is granted" —
  linking to Settings; honest that a local model could still relay data elsewhere (only the user can vouch
  for it). Not the red "not a PNA" banner — data hasn't left the device.
- **`cloud-exception`**: shareable projection unlocked; **`EX-CLOUD-LLM` active**. Persistent "not a PNA
  right now" banner (dismiss acknowledges, never clears) linking to Settings; the explainer + strength
  profile + return-to-PNA control live in Settings. Sealed still never crosses.

In every mode the **sealed floor is uniform and structural** — the MCP read path cannot load a sealed
value (it is the floor independent of mode, because even a "local" client might exfiltrate).

### Cross-process state (the crux)

The daemon is the **only writer** of disclosure state (under the existing session-token + Host/Origin +
AC-PRM-C file-lock guards — the PNA's own controlled surface, satisfying the RFC EX-H7 "consent obtained
on a surface the PNA controls"). The **MCP servers read it** on each request.

- **Store:** the `settings` table in `relationships.db` (single source of truth, already migrated, already
  opened read-only by the MCP servers for `identity_map`/`field_resolutions`). Keys:
  - `disclosure_mode` ∈ `{pna, local-ai, cloud-exception}` (default `pna`)
  - `disclosure_consented_at` (ISO) — when the current mode was entered
  - `disclosure_consented_scope` — a digest of the shareable field-id set at consent time (drives the
    "scope grew → re-consent" check)
  - `disclosure_history` — append-only list of `{mode, at}` transitions, for the audit log + explainer
  *(Alternative considered: a `disclosure.json` file in the home — more transparent / trivially resettable,
  but a loose file is writable by any same-UID process with no lock; the settings table keeps writes on the
  guarded daemon path. Decision D1 below.)*
- **Read path:** a pure `core.disclosure.current(home)` helper (read-only, no `cli` import) both the
  daemon and the MCP servers call. The MCP path resolves it per request, so a return-to-PNA in the
  workspace takes effect on the very next tool call.

## 5. Enforcement — harden the floor to the query layer

PR-7 requires enforcement "at the storage/query layer, not UI-only." Today `strip_sealed` projects every
field then drops sealed ones. Replace it on the MCP path with an **audience-bound projection** that never
loads what it can't return:

- `relationships_db.shareable_field_values(db, *, mode)` — selects `field_values JOIN field_definitions`
  **with `WHERE disclosure_tier = 'private-shareable-on-consent'`** *and only when* `mode` unlocks
  disclosure; returns nothing otherwise. Sealed rows are never read.
- `projection.get_contact(home, id, audience="mcp")` — composes the contact from shared records + the
  shareable-only overlay (vs `audience="local"`, the daemon's full view). The MCP `tools.get_contact` /
  `get_provenance` call the `audience="mcp"` form; `strip_sealed` is retired (or kept as a belt-and-braces
  assertion that the mcp projection contains no sealed field — a test, not the mechanism).
- When shareable fields exist but are withheld (mode=`pna`, or scope grew), the contact carries a
  `disclosure: {withheld: N, reason: "consent-required"}` marker so a cooperating client can tell the user
  to open the workspace and consent (best-effort-up, honest).

This makes "a cloud client cannot obtain a sealed field" true **by construction at the query layer**
(AC-MCP-C), and "shareable crosses only with consent" true via the mode gate (enforced EX-H7).

## 6. UX — step by step, per feature

*(Step-level flows; the detailed UI is worked through at implementation time per feature, as agreed.)*

### 6a. The consent gate + disclosure (blast-radius) preview — EX-H2, EX-H9
1. Entry points: a new sidebar item **"AI access"** (or a card in a Settings surface), and the empty-state
   prompt when an MCP read is attempted while in `pna` (surfaced via the `disclosure.withheld` marker the
   next time the user looks).
2. The gate is a modal (the existing `.aboutoverlay` pattern). It states plainly: connecting an AI to your
   private relationship data is a disclosure; PRM cannot tell a cloud model from a local one, so **you**
   declare.
3. **Disclosure preview (blast radius):** "Sharing will make these fields readable: «list of
   shareable-tier fields», across N contacts (M values). These stay sealed and never cross: «sealed
   fields»." A link to *Schema* to change any field's tier first.
4. Two buttons — **"Local model — data stays on this device"** and **"Cloud model — data leaves this
   device."** The cloud button requires a scroll-to-end + explicit checkbox (mirrors fellows' "consent
   recorded before anything is enabled"); the cloud copy names `EX-CLOUD-LLM` and links the explainer.
5. On confirm → `POST /api/disclosure/consent {mode}`; the daemon records mode + `consented_at` +
   `consented_scope`, appends to the audit log, and (for cloud) flips the workspace into not-a-PNA mode.

### 6b. The banner system — EX-H3 (and honest signage for every grant)
A small stack of banners in the shell; more than one can show at once. Each names what's granted, is
**dismissible** (dismiss acknowledges for the session and records `acknowledged_at`; it never changes the
underlying grant — EX-H3), and **links to Settings → AI access & connections** (6c) so the user can see and
change it. Triggers:
1. **Cloud disclosure active** (`disclosure_mode == cloud-exception`) → the persistent **"not a PNA right
   now"** banner naming `EX-CLOUD-LLM`. Sets machine-readable `<body>` markers `data-pna-mode="non-pna"` /
   `data-pna-exceptions="EX-CLOUD-LLM"` (mirrors fellows; lets tests + conformance detect mode). The sidebar
   "Kept on this device" badge flips to a warning state.
2. **Local disclosure active** (`disclosure_mode == local-ai`) → a softer informative banner: "Local access
   to some private data is granted," with the honest note that a local model could still relay data on.
3. **Shared contacts network-exposed** (the daemon is bound non-loopback / `--allow-non-local`) → a banner:
   "Your contacts are reachable on your network." PRM knows this for certain (its own bind address); the
   floor doesn't gate shared reads (D4), so the honest move is to *flag* it and link to Settings.
Banners double as a reminder to revisit Settings and disconnect anything no longer needed.

### 6c. Settings → "AI access & connections" — the hub (EX-H4 explainer + EX-H5 control + inventory)
A new **Settings** view (sidebar item; every banner deep-links here). It is the runtime, list-driven EX-H4
active-set explainer plus the controls and a best-effort connection inventory:
1. **Active access** — the current `disclosure_mode`, when it was granted, and the shareable scope (which
   fields × how many contacts/values). Controls: change the declaration, and **Return to PNA mode** (6d).
2. **Active exceptions** — generated at runtime from the active set (today: `EX-CLOUD-LLM` when
   `cloud-exception`), each with its per-dimension **strength profile** from PNT's EX-CLOUD-LLM table
   (consent-precedes-raise: enforced; non-PNA signal: enforced; reversible: enforced; servers read-only:
   verifiable; provider won't-train: provider-asserted; data-already-sent: none) **plus the EX-H9
   blast-radius row** — *which tiers can cross* = `enforced` (sealed never; only the consented shareable
   projection) — and a link to the upstream registry entry (EX-H4/H8).
3. **Connections (best-effort, honestly bounded).** What PRM can actually show: the daemon's network
   exposure (loopback vs non-loopback); whether PRM's MCP servers are **registered** in Claude Desktop
   (read back from `claude_desktop_config.json`, with `just mcp-uninstall` as the disconnect path); and —
   optionally — an MCP **last-access** timestamp the servers stamp when they serve a read. PRM **cannot**
   enumerate every client it isn't told about (the core "MCP can't identify the consumer" limit); the view
   says so plainly rather than implying a complete registry.

### 6d. Return-to-PNA-mode — EX-H5
1. A button in the explainer and the banner: **"Return to PNA mode."**
2. `POST /api/disclosure/return-to-pna` → sets `disclosure_mode = pna`, clears `consented_at`/`scope`,
   appends to history + audit log. The next MCP call withholds the shareable projection again.
3. Copy is honest about mode-only: "This stops future sharing. It cannot recall data already sent to a
   cloud provider." (EX-H5 MUST-NOT-imply-recall.)

### 6e. Per-field disclosure-tier management — PR-7
1. Already present: the field modal authors `disclosure_tier` (`private-sealed` default /
   `private-shareable-on-consent`) and the Schema list shows a sealed/shareable badge. Keep INV-3 — tiers
   are authored **only here**, never over MCP.
2. Add: a one-line summary at the top of the shareable preview and Schema view ("N fields shareable on
   consent · the rest sealed"), and — if disclosure is currently active — a note that changing a field to
   shareable **expands what an AI can read** and may require re-consent.

### 6f. (Phase 4 — strongest form) per-request disclosure review
The blast-radius preview (6a) bounds *what could* cross at consent time. The most direct answer to "the
LLM asked for more than it needs *in this call*" is to stage each private-overlay read for workspace
approval — generalizing AC-MCP-B's "MCP proposes, the workspace disposes" from comms to reads:
1. An MCP read that would return shareable overlay data returns a **staged disclosure request**
   (`{request_id, summary: fields × contacts}`) instead of the data, and tells the client to have the user
   approve it in the workspace.
2. The workspace shows pending requests ("Claude wants to read «fields» for «N» contacts — Approve once /
   Approve for this session / Deny"); approval releases the data on the client's retry.
3. This needs async-tool / poll protocol design and is the part most worth a focused pass — see Decision D2
   (ship now vs. fast-follow).

## 7. Daemon API additions (`daemon/server.py`)
- `GET /api/disclosure` → `{mode, consented_at, shareable_fields:[…], shareable_counts:{contacts,values},
  scope_grew:bool, history:[…], network_exposed:bool, banners:[…]}` — drives the banner stack, gate
  preview, and Settings hub. `network_exposed` is whether the daemon's own bind host is non-loopback (PRM
  knows it directly).
- `GET /api/connections` → the best-effort inventory for the Settings hub: `{network_exposed, bind_host,
  mcp_registered:{claude_desktop:[server keys]}, mcp_last_access:{…}|null}`. Honestly bounded (see 6c).
- `POST /api/disclosure/consent {mode}` → validate mode ∈ {local-ai, cloud-exception}; record under the
  file-lock; audit. (Reuses the existing session-token + Origin guards.)
- `POST /api/disclosure/return-to-pna` → set `pna`; audit.
- `POST /api/disclosure/ack-banner {banner}` → record this banner's `acknowledged_at` (dismiss for the
  session; never changes the underlying grant).
- *(Phase 4)* `GET /api/disclosure/requests`, `POST /api/disclosure/requests/{id}` (approve/deny).

## 8. Core + MCP changes
- **New `core/disclosure.py`** (pure, no `cli` import; leaf over `relationships_db.settings`): `current(home)`,
  `set_mode(home, mode, *, scope)`, `shareable_scope(home)` (the field-id set + counts for the digest/preview),
  `scope_grew(home)`. Writes go through `core/apply.py` (lock + audit), reads are direct.
- **`core/relationships_db.py`:** `shareable_field_values(db, *, mode)` query-layer selector; settings
  read/write helpers for the disclosure keys.
- **`core/projection.py`:** `get_contact(home, id, audience)`; `list_contacts`/`get_provenance` audience-aware;
  retire `strip_sealed` as the mechanism (keep a sealed-absence assertion for tests).
- **`mcp_servers/tools.py`:** `get_contact`/`get_provenance`/`list_contacts` pass `audience="mcp"` and read
  `core.disclosure.current` to gate the shareable projection; attach the `disclosure.withheld` marker.
- **`mcp_servers/consent.py`:** extend `CLOUD_LLM_NOTICE` to point at the workspace consent gate ("ask the
  user to open PRM and confirm before I return private fields") — the best-effort-up half of enforced EX-H7.
- **`daemon/server.py`:** surface the daemon's own bind host (loopback vs not) for `network_exposed` (it
  already holds `httpd.server_address`); serve the disclosure + connections endpoints.
- **Connections reflection (best-effort):** read `claude_desktop_config.json` (via
  `mcp_servers.install.default_config_path`, read-only) to report whether PRM's servers are registered;
  optionally the MCP servers write a small **last-access marker file** in the home (not the DB — keeps
  `relationships.db` read-only on the MCP side) so Settings can show "last read N min ago."

## 9. Tests
- **`core/disclosure`:** mode transitions; default `pna`; scope-grew detection; history append.
- **Floor (query layer):** `shareable_field_values` returns nothing in `pna`; returns only shareable-tier
  rows in `local-ai`/`cloud-exception`; **never** returns a sealed row in any mode (the AC-MCP-C structural
  test). `projection.get_contact(audience="mcp")` contains no sealed field.
- **MCP gate (SDK-free, via `tools`):** `get_contact` withholds shareable fields until consent; releases
  after; `disclosure.withheld` marker present when withheld. (Mirrors the existing SDK-free `tools` tests.)
- **Daemon API:** consent/return-to-pna/ack endpoints write the right settings, audit, and respect the
  lock; cross-origin + missing-token still refused (existing guards).
- **Banner/mode markers:** a daemon/status-driven test that `data-pna-mode`/`data-pna-exceptions` reflect
  the mode (the conformance-detectable signal).
- **Banners + connections:** `GET /api/disclosure` lists the right banner triggers per mode; `network_exposed`
  is true only for a non-loopback bind; the connections reflection reads the Claude config read-only and
  does not fail when it is absent.
- *(Phase 4)* staged-request lifecycle.

## 10. Docs + attestation (required by CLAUDE.md)
- **`docs/users-guide.md`** — the "Let an AI propose the merges" section + capability table: move the
  cloud-consent line from "⏳ next" to "✅ works"; document the gate, the local/cloud declaration, the
  banner, return-to-PNA, and that sealed fields never reach an AI.
- **`docs/roadmap.md`** — mark the v0.2 `EX-CLOUD-LLM` handler + data-floor as landed; note the upstream
  graduation candidacy.
- **`docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`** — update §Consequences: the workspace
  half (EX-H2–H5/H8) and the enforced floor are now live; EX-H7 moves from best-effort to enforced-at-our-
  surface for the overlay.
- **`docs/Architecture.md`** — the `EX-CLOUD-LLM` exception row → EX-H1–H8 conformant (was partial); add the
  AC-MCP-C / PR-7 / EX-H9 rows citing this design as the demonstrator; AC-MCP-A → conformant for the overlay.
  Regenerate `docs/conformance/` (`just conformance`).
- **Upstream (PNT, separate PR alongside this design):** propose graduating AC-MCP-C / PR-7 / EX-H9 from the
  data-floor stub into the live spec, citing PRM as the demonstrator (per PNT `CONTRIBUTING.md`).

## 11. Sequencing — one milestone, four phases
- **P1 — state + floor (no UI):** `core/disclosure`, settings keys, query-layer `shareable_field_values`,
  audience-bound projection, MCP gating + marker, tests. *(The floor is real even before any UI.)*
- **P2 — workspace handler:** gate + blast-radius preview (EX-H2/H9); the banner stack (cloud-exception /
  local-access / network-exposed, EX-H3); the **Settings "AI access & connections"** hub = runtime explainer
  + strength profile (EX-H4/H8) + return-to-PNA (EX-H5) + best-effort connection inventory; daemon endpoints;
  mode markers.
- **P3 — docs + attestation:** users-guide, roadmap, design note, `Architecture.md` + `just conformance`;
  draft the upstream PNT graduation PR.
- **P4 — per-request disclosure review (6f):** the stronger "understand *this* request" form — gated on
  Decision D2.

## 12. Decisions (resolved 2026-06-24)
- **D1 — state location → `relationships.db` `settings` table.** Lock-guarded writes on the daemon path;
  the store the MCP servers already read.
- **D2 — per-request review (P4) → fast-follow.** P1–P3 land the enforced floor + blast-radius consent and
  demonstrate AC-MCP-C; per-request review (6f) follows as its own pass. The review target is **P1–P3**.
- **D3 — `local-ai` is a recorded mode with its own dismissible banner.** It unlocks the shareable
  projection with **no** exception (data stays on-device) but shows an honest "Local access granted" banner
  linking to Settings — not the red "not a PNA" banner. Banners can coexist; dismiss acknowledges, never
  revokes; each links to Settings to inspect/change.
- **D4 — shared-contact reads are NOT gated** behind the declaration, **but** PRM raises a banner when
  shared contacts become network-reachable (daemon bound non-loopback), linking to Settings. The Settings
  hub shows a best-effort inventory of what's connected so the user can revisit and disconnect — honestly
  bounded by what PRM can actually know.

## 13. Acceptance
- [ ] `core/disclosure` + settings keys; default `pna`; transitions audited.
- [ ] MCP overlay surface returns **no** shareable field until workspace consent; **never** a sealed field
      (query-layer structural test).
- [ ] Gate (local/cloud) with blast-radius preview; consent recorded before anything unlocks.
- [ ] Banner stack: cloud-exception ("not a PNA", `data-pna-mode` markers), local-access, and
      network-exposed banners; each dismissible (ack ≠ revoke) and deep-linking to Settings.
- [ ] Settings "AI access & connections": active access + controls; runtime active-exception explainer with
      the per-dimension strength profile incl. the EX-H9 blast-radius row; best-effort connection inventory
      with stated limits; `network_exposed` reflects the daemon bind host.
- [ ] Return-to-PNA flips the mode; next MCP call re-withholds; copy is honest about mode-only.
- [ ] Scope-grew re-consent path.
- [ ] `pytest` green (SDK-free); SDK-guarded MCP tests skip cleanly from the project venv.
- [ ] Docs updated; `just conformance` regenerates `docs/conformance/`.
- [ ] Manual QA (PR description): wire Claude Desktop, confirm shareable fields are withheld pre-consent and
      flow post-consent, sealed never appears, return-to-PNA re-withholds.
```
