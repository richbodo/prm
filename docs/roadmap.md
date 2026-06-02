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
**owned/identity store** (separate from the raw Shared DB) and survive re-import. Realizes the
**review-required** safe-write tier + snapshot ring + append-only audit log.
*Details: [`../plans/v0.1-implementation-plan.md`](../plans/v0.1-implementation-plan.md).*

## v0.2 — Private overlay, custom schema, AI-write tiers

Grow the owned store into the full **Private DB**: **groups, tags, freeform notes**. On that
foundation, the user-authored **custom relationship schema** — arbitrary typed fields, defined and
edited **only** in the workspace (INV-3). Each field carries an **AI-write policy**
(`review-required` | `append-only` | `free-write`), defaulting to the most protective tier
(INV-10). The MCP private surface gains **write access to field _values_** (never definitions),
exercising the **append-only** tier. Multiple AI proposals are handled as separate queued
changesets reviewed serially (apply-time conflict detection), not as branches to merge — keeping
v0.1's native append-only-log + snapshot-ring substrate (no VCS).

## v0.3 — Delegated gathering: public data (easiest first)

The AI gathers relationship data **without outreach**. A **long-running LLM process** traverses the
contact graph via MCP, looking for publicly-announced activity — GitHub repos, LinkedIn, other
public signals — and runs as a **daily check** over a chosen group. Writes land in the
**append-only** tier (provenance-stamped observations, never overwriting user data), bounded by a
per-run **quota** and recorded in the **audit log** (INV-13) for later review. Plus **AI-assisted
organization**: the user talks to the LLM about organizing contacts; it gathers public info or asks
questions, proposes groupings, and appends notes.

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
- Per-vendor live API connectors (Google People API, MS Graph).
- Federated / peer-to-peer reads between PNAs on different devices.

## Parallel track — contributions back to PNT

This reference design is expected to feed the toolkit. As each lands with its demonstrating code:

- **AC-PRM-B** (multi-source dedup) — graduate from draft via v0.1's dedup + identity store.
- **AC-PRM-E / AC-PRM-F (proposed)** — the tiered safe-AI-write model and the "MCP stages, workspace
  applies" rule for data writes; the toolkit's MCP private surface is read-only today.
- **AC-PRM-C** (single-instance file-lock) — exercised by the native-SQLite storage pick.
- Privacy-reclassification mechanics and per-field provenance findings as they surface.
- SWHID archival + `Architecture.md` attestation once a version is accepted.
