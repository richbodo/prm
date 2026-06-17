# PRM — Project Roadmap

*Status: draft (2026-06-02). The PRM is the second PNT reference design and the first to realize the
draft Personal Relationship Manager use case. Companion to
[`prm-feature-spec.md`](prm-feature-spec.md) and [`../plans/v0.1-implementation-plan.md`](../plans/v0.1-implementation-plan.md).*

Versions are scoped smallest-honest-slice first. Each builds on the prior store and the MCP seam.

## v0.1 — Ingest, view, AI-assisted dedup

The minimum viable PRM. A **CLI ingester** (file import: vCard, vendor CSV, Google Takeout — no
CardDAV) writes a read-only **Shared DB** with stable contact IDs and per-field provenance. A
**local web workspace** is for **search and view**. An **MCP server** exposes read tools, and an
**AI deduplicates** via a propose → review → apply flow: the AI stages a merge **changeset**, the
user approves it in the workspace "like merging a PR." Merge decisions live in a minimal
**private store** (separate from the raw Shared DB) and survive re-import. Realizes the
**review-required** safe-write tier + snapshot ring + append-only audit log. Runs **zero-config**:
all stores live under one **PRM home** (default `./prm-data/`, so demo mode never touches anything
outside the repo), and `prm init --demo` seeds a realistic ~1000-contact home from the synthetic
fixtures. Import **infers the source, previews it, and asks before committing** (with a
`--non-interactive` best-effort mode), over a pure `ingest()` reused by the MCP surface.
*Details: [`../plans/v0.1-implementation-plan.md`](../plans/v0.1-implementation-plan.md).*

## v0.2 — Private overlay, custom schema, AI-write tiers

Grow the v0.1 private store into the full **Private DB**: **groups, tags, freeform notes**. On that
foundation, the user-authored **custom relationship schema** — arbitrary typed fields, defined and
edited **only** in the workspace (INV-3). Each field carries an **AI-write policy**
(`review-required` | `append-only` | `free-write`), defaulting to the most protective tier
(INV-10). The MCP private surface gains **write access to field _values_** (never definitions),
exercising the **append-only** tier. Multiple AI proposals are handled as separate queued
changesets reviewed serially (apply-time conflict detection), not as branches to merge — keeping
v0.1's native append-only-log + snapshot-ring substrate (no VCS).

Also in v0.2: a **config file** (records the active `data_dir`) + **platform data dirs** (XDG /
macOS / Windows) for installed (non-repo) use — **landed** as the `just install` wizard + `prm config`
(per-user data dir, recorded so updates never touch data) and a native **desktop window** (`prm app` /
`just app`, pywebview; see [`../plans/desktop-app-and-install.md`](../plans/desktop-app-and-install.md)).
Still ahead: **source discovery** — a `discover_sources()` library that scans `~/Downloads` and a
configured ingestion dir for likely contact exports (signature-matched, opt-in, never a background poll)
and offers a pick-list. Surfaced via `prm scan` / `prm import` (no path) and **reused by the MCP
ingestion surface**, so CLI and AI share one discovery path.

**Cloud-AI honesty — the `EX-CLOUD-LLM` handler.** The private MCP surface is the canonical trigger for
PNT's **`EX-CLOUD-LLM`** exception: the moment a cloud client (e.g. Claude Desktop) can read private
rows, data leaves the device. PRM **cannot detect or block a cloud LLM** at the MCP layer (`clientInfo`
is self-reported; MCP auth runs client→server), so v0.2 holds the boundary with a *handler*, not a
filter — a consent gate before first connect, a persistent "not a PNA right now" signal in the
workspace, a runtime active-set explainer, and a reversible return-to-PNA-mode control (mode only; it
cannot recall data already sent). Full rationale:
[`design-notes/mcp-cannot-identify-the-consuming-llm.md`](design-notes/mcp-cannot-identify-the-consuming-llm.md).
*(The v0.1 **read** surface already returns contact PII to a connected cloud client — same posture,
"local AI recommended"; see plan §6.)*

**Data-floor — bound *what* the cloud can read (PNT `AC-MCP-C` demonstrator).** The consent handler
governs *whether* a cloud client reads private rows; it does not bound *which* rows. v0.2 adds the
read-side mirror of its AI-write tiers (INV-10): every Private-DB / custom field carries a **disclosure
tier**, default **sealed** (changeable only in the workspace), and the cloud-facing MCP surface is
**projection-bound** — structurally unable to return a sealed field, even with consent. So the most
sensitive notes are never reachable by a cloud client like Claude Desktop, and a defeated consent gate
leaks only the user-curated shareable projection, not the whole store. This realizes PNT's proposed
**`AC-MCP-C` / `PR-7` / `EX-H9`** data-floor ([PNT PR #34](https://github.com/richbodo/personal_network_toolkit/pull/34)),
which lands *with* this design — candidate **AC-PRM-G**.

## v0.3 — Delegated gathering: public data (easiest first)

The AI gathers relationship data **without outreach**. A **long-running LLM process** traverses the
contact graph via MCP, looking for publicly-announced activity — GitHub repos, LinkedIn, other
public signals — and runs as a **daily check** over a chosen group. Writes land in the
**append-only** tier (provenance-stamped observations, never overwriting user data), bounded by a
per-run **quota** and recorded in the **audit log** (INV-13) for later review. Plus **AI-assisted
organization**: the user talks to the LLM about organizing contacts; it gathers public info or asks
questions, proposes groupings, and appends notes. A natural first target is the **friend-reconciliation
checklist** — the origin-tagged roster of dataless contacts (Facebook names, LinkedIn-URL-only) that
v0.1 ingests and v0.2 lets the user annotate; here the AI researches and triages them down to the
people the user actually knows. See the use-case note in [`prm-feature-spec.md`](prm-feature-spec.md).

## v0.4 — Delegated gathering: outreach (querier side)

The harder gathering path. **Reachability resolution** per contact (an automatable transport, else
flagged for manual handling); outreach **composed from a template and sent only after explicit
user approval** (batchable; never auto-send, INV-8). Replies re-enter through the F1 ingestion pipe:
the AI extracts structured values from prose into custom fields, marked **self-reported via
`<transport>` on `<date>`**. Transport adapters: email first, then Signal (`signal-cli`).

## v0.5+ — Live mirror & breadth

**CardDAV live mirror** (iCloud/Fastmail/Nextcloud) via an external sync tool in one-way mirror
mode — the app reads mirrored files, never implements sync. Broader importers; richer conflict
resolution; full concurrent-AI merge story over git.

## Deferred / separate designs

- **The responder app** — the AI that receives, validates, and drafts replies to inbound PNA
  queries — is a **separate future reference design** (skeptical-by-construction, draft-never-send,
  local-AI-required). v0.4 builds only the querier half.
- **Full contact CRUD.** Field-level **Update** arrives *with* v0.1 dedupe — a `resolve_field` override
  is also a manual field edit (see [`design-notes/dedupe-design.md`](design-notes/dedupe-design.md)).
  **Create** (user-authored contacts → a `local_records` store) and **Delete** (a projection-filtered
  tombstone) are a follow-on on that same substrate; Create lands with v0.2's private overlay.
- Per-vendor live API connectors (Google People API, MS Graph).
- Federated / peer-to-peer reads between PNAs on different devices.
- **An installer** (the normal path for non-developers; writes the chosen `data_dir` into the config
  file). Deferred to project maturity — and note it bends the `never-distributed-single-user` flavor,
  so it waits on the distribution-semantics question below.

## Parallel track — contributions back to PNT

This reference design is expected to feed the toolkit. As each lands with its demonstrating code:

- **AC-PRM-B** (multi-source dedup) — graduate from draft via v0.1's dedup + identity store.
- **AC-PRM-E / AC-PRM-F (proposed)** — the tiered safe-AI-write model and the "MCP stages, workspace
  applies" rule for data writes; the toolkit's MCP private surface is read-only today.
- **`EX-CLOUD-LLM`** — building the v0.2 consent-gate + "not a PNA" signal + return-to-PNA-mode
  handler gives PNT a **second** reference demonstration of the exception (the spec currently cites
  only `fellows_local_db`). See [`design-notes/mcp-cannot-identify-the-consuming-llm.md`](design-notes/mcp-cannot-identify-the-consuming-llm.md).
- **`AC-MCP-C` / `PR-7` / `EX-H9` data-floor (candidate `AC-PRM-G`)** — the per-field disclosure tier
  + projection-bound cloud surface that bounds *what* a cloud LLM can read (the read-side mirror of the
  v0.2 AI-write tiers). PRM v0.2 is the proposed demonstrator for PNT's data-floor proposal
  ([PNT PR #34](https://github.com/richbodo/personal_network_toolkit/pull/34)); it complements the
  `EX-CLOUD-LLM` handler above.
- **AC-PRM-C** (single-instance file-lock) — exercised by the native-SQLite storage pick.
- Privacy-reclassification mechanics and per-field provenance findings as they surface.
- **Distribution semantics (open finding).** PNT's `never-distributed-single-user` flavor is too
  coarse. "Distribution" is a spectrum with different *verifiability* properties: (1) point a user at
  a **GitHub repo** — they build it and can run the **PNT conformance tests** to confirm it's a
  compliant PNA *before trusting it*; (2) a **built binary in a repo** (source nearby); (3) a
  **binary from a website that doesn't host source** (opaque, hardest to verify). Orthogonal: shipping
  **code only** vs. **code + sensitive data** (cf. fellows). The safety property that matters is
  *independent verifiability* — the build-from-source-and-run-conformance-tests path (this PRM today:
  a "home-cooked meal" a friend builds and verifies) is qualitatively different from an opaque binary.
  This likely warrants splitting the distribution axis in PNT. **Proposed upstream as a discussion RFC
  — [personal_network_toolkit#39](https://github.com/richbodo/personal_network_toolkit/issues/39)
  (tracks PRM [#8](https://github.com/richbodo/prm/issues/8)); not yet a spec change** (per
  CONTRIBUTING, an obligation-changing axis split lands with its demonstrating design — PRM is the
  candidate demonstrator, gated on the deferred installer).
- SWHID archival + `Architecture.md` attestation once a version is accepted.
