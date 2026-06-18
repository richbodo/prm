# Architecture (PRM)

This document is PRM's **specialization-and-conformance layer**: it declares which version of the PNA Spec
this repo conforms to, names the axis picks PRM has made, and attests — per Acceptance Criterion — how the
code realizes each commitment and which test proves it. It is PRM's **Security Target** (the PNA Spec plays
the Protection Profile role); the PNA Toolkit requires every accepted reference design to ship one.

Universal PNA architecture — the goals, the two-store ownership split, the version-handshake contract, the
universal ACs — lives in the [PNA Spec](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md)
upstream. This file does not restate it; it links to it.

---

## Spec conformance

**Toolkit-Version:** [0.1 (draft)](https://github.com/richbodo/personal_network_toolkit/blob/main/VERSION)
— the PNT version (spec + contracts + skill + lint + templates) this design was built and validated against.
**Use case:** Personal Relationship Manager (the second PNT reference design; the first to exercise the
multi-source dedup contract).

### Flavor — PRM's six axis picks

| Axis | Pick | Why |
|---|---|---|
| [Distribution](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#distribution) | `never-distributed-single-user` | Built from source by the user themselves; no server, no auth, no service worker. The **independently-verifiable** end of the distribution spectrum — a friend builds it and runs this conformance flow before trusting it. |
| [Storage substrate](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#storage-substrate) | `native-sqlite-via-filesystem` | Native `sqlite3` over OS files, WAL + advisory file-lock. Triggers **AC-PRM-C**. |
| [Ingestion shape](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#ingestion-shape) | `multi-source-merge-with-dedup` | vCard / Google Takeout / LinkedIn / Google CSV / Facebook merged with stable IDs + per-field provenance. Triggers **AC-PRM-B**. |
| [Workspace shell](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#workspace-shell) | `vanilla-js-spa` | `workspace/` — hand-written JS SPA, no framework/bundler, served by a stdlib `http.server` daemon — **loopback-bound, with a per-process session token + Host/Origin guard** so other local programs can't read contacts through it ([`design-notes/local-daemon-trust-surface.md`](design-notes/local-daemon-trust-surface.md), "Surface 1"). |
| [Comms transport set](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#comms-transport-set) | `none` | v0.1 has no outreach surface (→ AC-16/18/19/MCP-B not-applicable). |
| [MCP-exposure](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#mcp-exposure) | `shared-only` | Two stdio MCP servers: read-only `prm-shared-data` + a **propose-only** `prm-dedup` (stages changesets to `relationships.db`; never commits). |

This section is PRM's **AC attestation table** — the Security-Target role from the toolkit's
[`ARCHITECTURE_TEMPLATE.md`](https://github.com/richbodo/personal_network_toolkit/blob/main/reference_designs/templates/ARCHITECTURE_TEMPLATE.md).
Every applicable AC carries a **Realization** (how the code satisfies it), a **Verification** (the test that
proves it), and a **Status** (`conformant` / `partial-conformance` / `not-applicable`).

> **Evidence rule (enforced by `tests/conformance/test_attestation_has_evidence.py`).** A `conformant` row's
> Verification must name a resolvable test ref (`path/to/test.py::name`) that is not `xfail`/`skip`, or a
> declared review kind (`human-review`, `code inspection`, `by construction`, `by bounding`). A bare `*.md`
> pointer is not evidence. **Negative invariants** ("X must NOT happen") cite a **negative test**. `partial` /
> `not-applicable` rows are exempt from resolution but must carry that honest status.

### Universal ACs

| AC | Realization | Verification | Status |
|---|---|---|---|
| [AC-1 (two-store ownership split)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-1) | Two SQLite files: `shared.db` (raw mirrored contacts, **read-only at runtime** — opened `mode=ro` by readers; only the ingester writes it, INV-2) + `relationships.db` (read-write, user-owned: identity_map / field_resolutions / decisions). `core/shared_db.py`, `core/relationships_db.py`; dedup writes **only** `relationships.db` (INV-2). | `tests/unit/test_relationships_store.py::test_import_seeds_private_and_keeps_shared_intact`; `tests/db/test_shared_db.py` | conformant |
| [AC-4 (versioned cross-boundary handshake)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-4) | `PRAGMA user_version` handshake in both stores (`_ensure_schema`); a mismatch **refuses mutations** (`SharedDbError`/`RelationshipsDbError`) while **reads continue** (read paths open `mode=ro`, no version-check). The daemon (`daemon/server.py:route`) maps the refusal to a clean 409, never a 500/traceback. | `tests/db/test_shared_db.py::test_incompatible_schema_version_is_rejected`; `tests/db/test_relationships_db_version.py::test_private_mismatch_refuses_seed`, `::test_private_mismatch_refuses_apply`; `tests/unit/test_daemon_diag.py::test_schema_mismatch_refuses_write_reads_continue` | conformant |
| [AC-6 (always-reachable diagnostic escape)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-6) | `prm doctor --unlock` force-clears a stale home lock from any state (refuses when the lock is held *live*, so it never creates a double-writer; `core/diag.py:lock_state` + `cli/prm_import.py:cmd_doctor`); the workspace `?diag` overlay and reset are reachable regardless of app state. | `tests/unit/test_doctor.py::test_unlock_clears_stale_lock`, `::test_unlock_refuses_when_held_live` | conformant |
| [AC-7 (self-service field-debug substrate)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-7) | Build label (AC-15); `GET /api/diag` + `?diag` overlay = a sanitized (PII-free) state dump; `prm doctor`; a redacting local error log (`core/diag.py:capture_error`, emails stripped); the SPA boot watchdog; the AC-6 escape. No error leaves the device (never-SaaS → no error HTTP sink). | `tests/unit/test_diag.py::test_state_dump_is_sanitized_metadata`, `::test_error_log_redacts_and_tails`; `tests/unit/test_daemon_diag.py::test_diag_route_sanitized`; `tests/unit/test_doctor.py::test_doctor_dump_runs` | conformant |
| [AC-9 (auto-backup of private data)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-9) | `core/snapshots.py` — a pre-apply snapshot ring of `relationships.db` via sqlite's online-backup API, `KEEP=20` ISO-rotated. PRM is invoked per-command (no long-running boot), so the cadence is **per-mutation** (every applied change leaves a recoverable point) rather than per-boot — stronger for the mutation case. This *is* v0.1 Undo. | `tests/unit/test_apply.py::test_snapshot_and_restore`, `::test_apply_merge_then_undo` | conformant |
| [AC-10 (opt-in non-destructive re-imports)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-10) | `cli/ingest.py:reimport` — added / updated / unchanged / **stale** diff with an orphan/merge-impact preview before commit; stale records are **kept** (flagged), merge decisions re-attached by stable id (INV-6); snapshot + audit before apply. | `tests/unit/test_reimport.py::test_reimport_added_updated_stale`, `::test_reimport_apply_keeps_stale_and_snapshots`, `::test_reimport_flags_merges_with_stale` | conformant |
| [AC-11 (concurrent-access detection)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-11) | `core/lock.py:file_lock` — a single-instance advisory `flock` guarding the canonical writers; a second holder is **refused** with a specific message naming the held lock (`LockError`), never a silent concurrent write. | `tests/unit/test_lock.py::test_second_holder_refused_with_message` | conformant |
| [AC-15 (build label tied to source revision)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-15) | `core/build_label.py` — `<YYYY-MM-DD>-<short-sha>[-dirty]` derived from the git checkout at **run/serve** time (PRM has no build step; stamp-file + packaged-version fallbacks for archived source); surfaced in `prm status`, `/api/status`, `/api/diag`, and the workspace footer. | `tests/unit/test_build_label.py::test_running_label_is_git_date_sha`, `::test_archived_source_uses_stamp_file`, `::test_no_git_no_stamp_is_non_empty_fallback` | conformant |
| [AC-16 (user-driven transport selection)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-16) | Not applicable — `comms-transport-set: none` (PRM v0.1 has no outreach surface). | — | not-applicable |
| [AC-17 (sourced provenance)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-17) | `field_provenance` (per-field source + timestamp, `core/schema/shared.sql`) + per-record `source` inferred and confirmed at import (`cli/ingest.py`); every Shared-store record traces to a user-configured external source. | `tests/db/test_shared_db.py::test_load_writes_records_provenance_and_fts` | conformant |
| [AC-18 (transports cannot read message contents)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-18) | Not applicable — `comms-transport-set: none`. | — | not-applicable |
| [AC-19 (user-visible payload before send)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-19) | Not applicable — `comms-transport-set: none`. | — | not-applicable |
| [AC-PRM-A (LLM calls over user data are transports)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-prm-a) | A **local model is recommended/default** for anything touching contact data; cloud use is signaled, not enforced. An MCP server **cannot identify the consuming LLM** (`clientInfo` is self-reported; MCP auth runs client→server — see [`docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`](design-notes/mcp-cannot-identify-the-consuming-llm.md)), so the boundary is **consent + honest signaling** (the EX-CLOUD-LLM `instructions` notice), and per-call prompt visibility lives in the cloud client's own UI. | `tests/unit/test_mcp_consent.py`; `code inspection` (the read tools return PII; the boundary is documented, not gated) | partial-conformance (consent + signaling, not enforcement — an MCP server cannot gate which LLM consumes its output) |
| [AC-PRM-D (re-ingestion is user-initiated)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-prm-d) | Import/re-import are explicit `prm import` / `prm reimport` (or MCP) actions; boot never background-polls (no poller exists — `by construction`). | `tests/unit/test_reimport.py`; `by construction` (no scheduler/poller in the codebase) | conformant |
| [AC-MCP-A (cloud AI clients require consent for Private DB)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-mcp-a) | The MCP surface is **`shared-only`** (read-only contacts) + a **propose-only** dedup surface that stages changesets but exposes **no private-read tool** (INV-11). The read tools still return contact PII, so cloud clients hit the same posture as fellows; the consent handshake is best-effort server-side (EX-H7), not per-call gating (an MCP server can't identify the LLM). | `tests/unit/test_mcp_tools.py::test_submit_is_propose_only`; `tests/unit/test_mcp_consent.py` | partial-conformance (consent + signaling; not per-call server-side gating) |
| [AC-MCP-B (MCP Communications stages; workspace launches)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/PNA_Spec.md#ac-mcp-b) | Not applicable — no Communications MCP surface (`comms: none`). | — | not-applicable |

### Flavor-derived ACs triggered by PRM's picks

| AC | Triggered by | Realization | Verification | Status |
|---|---|---|---|---|
| [AC-PRM-B (multi-source dedup contract)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#ac-prm-b) | `ingestion:multi-source-merge-with-dedup` | A **stable id survives merge and re-import** (`cli/identity.py` fallback chain → `core/relationships_db.py:identity_map`, re-attached by `source_uid`); conflicts are **surfaced, never silently resolved** (`core/candidates.py` tiers + `core/projection.py:preview_merge` flag-don't-guess; name-only/name+company are propose-only, never auto-merged); **per-field provenance** recorded (`field_provenance`). | `tests/unit/test_candidates.py::test_detect_clusters_and_tiers`, `::test_transitive_overmerge_guard`; `tests/unit/test_relationships_store.py::test_preview_merge_marks_conflicts`; `tests/unit/test_reimport.py::test_reimport_preserves_merges`; `tests/e2e/test_dedup_flow.py::test_bulk_flow_scenario` | conformant |
| [AC-PRM-C (single-instance file-lock)](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#ac-prm-c) | `storage:native-sqlite-via-filesystem` | `core/lock.py:file_lock` — advisory `flock` per PRM home; a second process **refuses cleanly with a specific message** naming the held lock, rather than racing the canonical writer. | `tests/unit/test_lock.py::test_second_holder_refused_with_message`, `::test_release_allows_reacquire` | conformant |

Picks PRM did not take on the other axes carry their own flavor-derived ACs in
[axes.md](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md); none fire here
(`never-distributed-single-user`, `vanilla-js-spa`, `comms:none`, and `shared-only` trigger no axis-specific ACs).

### Exception attestation (non-PNA mode)

PRM raises one PNA **Exception** — `EX-CLOUD-LLM` — when the user connects a cloud-hosted MCP client (e.g.
Claude Desktop on a hosted model) that can read contact data. PRM **cannot detect or block** the cloud LLM at
the MCP layer, so the boundary is held by consent + honest signaling, never by trying to identify the client.

| EX | Relaxes | Handled? | Realization | Verification | Status |
|---|---|---|---|---|---|
| [EX-CLOUD-LLM](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/exceptions.md#ex-cloud-llm) | PNA-DEFINITION (local-only), AC-MCP-A; stresses Goal 1 | partial; reversible (mode only) | **Server-side (shipped):** a best-effort consent-to-human propagation notice carried on **both** MCP servers via the MCP `instructions` handshake (`mcp_servers/consent.py:CLOUD_LLM_NOTICE` → `shared_data_ops.py` + `dedup_ops.py`) — **EX-H7**; stable `EX-*` id (**EX-H1**); recommended solution named (**EX-H6**). **Deferred to v0.2 (workspace overlay):** the pre-raise consent gate (**EX-H2**), the persistent "not a PNA right now" signal (**EX-H3**), the runtime active-set explainer (**EX-H4**), the reversible return-to-PNA-mode control (**EX-H5**), and the per-dimension strength profile (**EX-H8**). | `tests/unit/test_mcp_consent.py` (the `instructions` carry the cloud-LLM notice) | partial-conformance — EX-H1/H6/H7 conformant (server-side, shipped); EX-H2–H5/H8 deferred to v0.2 ([prm#36](https://github.com/richbodo/prm/issues/36)) |

### Constraint attestation

A **constraint** (`CST-*`) is a platform ceiling inherited by an axis pick. PRM's `native-sqlite-via-filesystem`
storage **inherits no PWA constraints** (the `CST-PWA-*` family attaches to `opfs-sqlite-wasm` + `web-bundle`,
which PRM does not pick). The single-instance write ceiling native SQLite imposes is captured as **AC-PRM-C**
(an AC, not a CST). PRM therefore declares **no constraint attestation rows** for Toolkit-Version 0.1.

### User-mediation attestation

A standing invariant beneath the AC-MCP / AC-PRM families: **the human is the actuator; the workspace is the
locus of ground truth.** PRM is the **mutation-side** demonstrator (fellows demonstrates the egress side) — its
propose→review→apply loop: the *proposer* (an AI over MCP, or the detector) only **stages** a changeset; the
*principal* **disposes** in the workspace.

| UM | Property | Realization | Verification | Status |
|---|---|---|---|---|
| UM-1 | **No bypass** | No MCP/AI path performs a review-required write; `mcp_servers/dedup_ops.py` exposes **no apply tool** — only `submit_merge_proposal` (stages JSON). The daemon under the file-lock is the sole applier (`core/apply.py`). (INV-11) | `tests/unit/test_mcp_tools.py::test_submit_is_propose_only` (the private store is unchanged after a proposal) | conformant |
| UM-2 | **Separation** | The proposer carries no actuation capability: a proposal is inert `proposals/<id>.json`; only an explicit workspace `POST /api/merge`/`apply-proposal` applies it (`core/apply.py:apply_changeset`). | `tests/unit/test_mcp_tools.py::test_proposal_then_human_applies`; `tests/e2e/test_dedup_flow.py::test_bulk_flow_scenario` | conformant |
| UM-3 | **Legibility** | The workspace renders each changeset as a deterministic, human-readable PR-style diff (per-field, with provenance); values are HTML-escaped (`workspace/app.js:esc`). The bounded claim: separation/legibility/attribution, **not** comprehension. | `tests/e2e/test_dedup_flow.py::test_bulk_flow_scenario` (the resolved value the human picks is what the projection shows) | conformant |

---

## Contributions to the spec

This submission rides four spec changes, each demonstrated by working code in this repo:

- **Distribution-axis verifiability split** ([toolkit#39](https://github.com/richbodo/personal_network_toolkit/issues/39) ⇄ [prm#8](https://github.com/richbodo/prm/issues/8)): `never-distributed-single-user` collapses *build-from-verifiable-source* with *opaque-binary* delivery; they differ in **independent verifiability**. PRM is the build-from-source demonstrator (a friend clones, builds, and runs this conformance flow before trusting it). — demonstrated by this whole repo + `just conformance`.
- **Formalize AC-PRM-E / AC-PRM-F** (the tiered safe-AI-write model — review-required / append-only / free-write; no MCP path commits a review-required write directly): promote from *proposed* to spec. — demonstrated by the propose-only MCP surface (`mcp_servers/dedup_ops.py`) + workspace apply (`core/apply.py`); v0.1 ships the **review-required** tier (append-only/free-write are v0.2).
- **User-mediation list (UM-1/2/3), mutation side**: PRM's propose→review→apply is the mutation-side demonstration the egress-focused fellows MVD lacked. — demonstrated by the UM attestation above.
- **Generalize AC-2 — "no ungoverned app-opened surface over private data"** (candidate **AC-PRM-H**): [AC-2](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/axes.md#ac-2) ("the server is a delivery channel, not a service") is flavor-derived for `web-bundle-*` distribution and forbids RW endpoints, so it neither fires for nor fits `never-distributed-single-user` + a local **RW** daemon. PRM demonstrates the **loopback-daemon realization** of AC-2's intent: a per-process session token + Host/Origin guard + loopback-pin (`daemon/server.py`), so the workspace is the app's own transport, not a data tap other local programs can read. Pairs with two clarifications it surfaces — the data-floor (AC-MCP-C) bounds the **cloud** surface *only* (the local daemon and on-disk store are out of its scope), and same-user out-of-band reads ("Surface 3", e.g. an OS-automation agent) belong to the **Harden flow** + a `CST-*` ceiling, not an app conformance gate. Full analysis: [`design-notes/local-daemon-trust-surface.md`](design-notes/local-daemon-trust-surface.md). — demonstrated by `daemon/server.py` + `tests/unit/test_daemon_security.py`, with a static "checked, not asserted" gate (`scripts/loopback_surface_lint.py`, run by `just conformance`; a candidate upstream PNT lint).

## Reproducibility notes

- **Dependencies:** Python ≥ 3.10; runtime: `vobjectx` (vCard lexer) + the official `mcp` SDK; dev: `pytest`. Everything else is stdlib (`sqlite3` + FTS5, `http.server`, `csv`, `zipfile`, `json`, `hashlib`).
- **Build commands:** `pip install -e ".[dev]"` (or `just setup`); `just test`; `just conformance` (lint + suite); `just evaluate-report` regenerates `docs/conformance/`.
- **External services required:** none — local-only by design (INV-1); the daemon binds `127.0.0.1` and authenticates its own workspace session (a per-process token + Host/Origin guard), so other local programs can't read contacts through it.
- **Known dependency-rot risks:** `vobjectx` is the maintained fork of the dormant `vobject` (the older `vobject` is an accepted drop-in fallback); the `mcp` SDK is young and may move. FTS5 must be compiled into the target `sqlite3` (checked by `just doctor`).
