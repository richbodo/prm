# R11 — AI writes: the write tiers + the gathered-data enrichment substrate

*Status: **draft spec** (2026-06-25; expanded from the 2026-06-25 scoping sketch). The AI-write milestone
from [`v0.2-implementation-plan.md`](v0.2-implementation-plan.md) §8 (R11), broadened from "append-only tier"
to the **full write-tier surface + the enrichment seam** that v0.3 delegated gathering builds on.
Rationale / decisions: [`../docs/design-notes/ai-writes-and-gathered-data.md`](../docs/design-notes/ai-writes-and-gathered-data.md)
(read that first — this plan is the *how*, that note is the *why*). Companion to
[`../docs/prm-feature-spec.md`](../docs/prm-feature-spec.md) ("Safe AI writes", INV-10–13) and
[`../docs/roadmap.md`](../docs/roadmap.md) (v0.2 + v0.3). **Not yet implemented** — the MCP surface is read +
propose-only-dedup today.*

## 1. Goal

Let an AI **write the private store** through the MCP surface for the first time, in a way that stays safe at
volume, and **lay the substrate v0.3 delegated gathering needs** — so that gathering is later "point an AI at
it," not a new data model. Two write targets, deliberately different in how much they trust the AI:

- **Overlay (custom-schema) fields** — governed per-field by `ai_write_policy`
  (`review-required` | `append-only` | `free-write`). This is the **AC-PRM-E** demonstrator (the append-only
  additive tier). `review-required` (default) is already subsumed upstream by user-mediation (UM);
  AC-PRM-F (no MCP path commits a review-required write) is the same structural lock.
- **Canonical (contact) fields** — the vCard namespace (`tel`, `email`, `org`, `title`, `adr`, `url`,
  `bday`). The AI **never writes these directly**; it files an additive **observation** the user **promotes**.
  This is the seam the Facebook-name enrichment case (v0.3) runs on.

## 2. Settled decisions (this session)

Pinned in the design note; this plan implements them:

1. **Canonical fields: observations only, you promote.** No MCP tool can make an AI value the default for a
   contact-identity field — promotion is a workspace act. Review-required by construction.
2. **Build the seam now.** The `observations` table + projection-fold + promotion ship in this wave, even
   though the autonomous gatherer is v0.3.
3. **One-deep retention, replace-on-write.** Per `(contact, field)`: current + one previous; the audit log
   holds full history; `shared.db` is the immutable floor. No deep version stacks.
4. **Union for multi-valued.** Gathered `tel`/`email` *add* to the union (attributed), never silently
   replace.

## 3. What already exists vs. what R11 adds

| Piece | Status | R11 work |
| --- | --- | --- |
| `field_definitions.ai_write_policy` (3 tiers, default review-required) + `field_values.written_by`/`source` provenance | ✅ exists (`core/schema/relationships.sql`) | reuse |
| `field_values` multi-row per `(contact_id, field_id)` + `value_id` idempotency | ✅ exists | reuse |
| `field_resolutions` single-valued override (`rule="user"`), the reversible-view lever | ✅ exists | reuse for single-valued promotion |
| Audited apply path: lock → snapshot → txn → audit (`core/apply.py`) | ✅ exists | reuse for promotions; **add** a no-snapshot append/audit path for observation writes |
| **`append` op** — `set_field_value` *replaces* for single-valued kinds; append must NOT delete | ❌ | small — `proposals.append_field_value_op` + a branch in `relationships_db.apply_operations` |
| **`observations` table** (canonical-field enrichment holding pen) | ❌ | small — new table, v2→v3 migration (idempotent ensure-schema) |
| **Projection fold** — accepted observations into the contact; pending → a `suggestions` block; honor a single-valued resolution even with **no** source value | ❌ | medium — `core/projection.py` (`_merge`, `_merge_props`, `get_contact`) |
| **Promotion endpoints** — promote/un-promote an observation (single-valued → `field_resolutions`; multi-valued → status flip) | ❌ | medium — `core/apply.py` + daemon routes + workspace "Gathered" review surface |
| **Writable MCP server** + the two tools + policy dispatch | ❌ | medium — likely `mcp_servers/private_data_ops.py` + `just mcp-install` wiring + `--print-config`/`--build` parity |
| **Limits** — per-value size cap, per-contact obs cap, per-session quota, retention/compaction | ❌ | small–medium |
| Tests + `docs/Architecture.md` AC-PRM-E attestation | ❌ | medium |

Everything rides the existing substrate: **one new table, a projection change, promotion endpoints, the
writable MCP server, the caps. No new store, no new deps** (stdlib + the `mcp` SDK).

## 4. The `observations` table (new — relationships.db schema v3)

Additive, attributed, never-auto-canonical. Distinct from `field_values` (the design note explains why
neither existing table fits a canonical-namespace value).

```sql
-- Gathered, not-yet-canonical values for a contact's *canonical* (jCard) fields — the additive layer an
-- AI enrichment run writes into. `field` is a source-namespace property (tel/email/org/title/adr/url/bday),
-- which has no field_definition, so this can't live in field_values. Never auto-canonical: the projection
-- treats an observation as a *suggestion* until the user promotes it. shared.db stays the immutable mirror
-- (INV-2); this is user-owned, in relationships.db. Rationale: docs/design-notes/ai-writes-and-gathered-data.md.
CREATE TABLE IF NOT EXISTS observations (
    obs_id      TEXT PRIMARY KEY,                  -- sha1(contact_id ∥ field ∥ value_norm ∥ source)
    contact_id  TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    field       TEXT NOT NULL,                     -- a canonical jCard property name (tel, email, org, …)
    value       TEXT,
    value_json  TEXT,                              -- structured value (ADR/N) when a scalar won't do
    written_by  TEXT NOT NULL,                     -- 'ai:<session>'
    source      TEXT,                              -- provenance: where the AI found it (e.g. 'web', 'github')
    confidence  REAL,                              -- gatherer's self-reported confidence (advisory only)
    status      TEXT NOT NULL DEFAULT 'observed'
        CHECK (status IN ('observed','accepted','superseded','rejected')),
    written_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_contact ON observations(contact_id);
```

`status`: **observed** (pending — a suggestion) → **accepted** (promoted into the view) → **superseded** (the
one-deep previous, kept for a single revert) / **rejected** (dismissed; won't resurface). The `obs_id` hash
makes a re-found identical value idempotent (a no-op append).

## 5. Projection fold (`core/projection.py`)

Three concrete changes; all read-only, no new writers:

- **Fold accepted observations into the canonical field.** In `_merge` / `_merge_props`, after gathering
  source props into `by_name`, add `status='accepted'` observation rows for the contact: a **union** field
  gains them as additional values (carrying `source='ai:<session>'` provenance, deduped by normalized value);
  a **reconcile** field treats an accepted observation like a candidate the resolution can point at.
- **Honor a single-valued resolution / accepted observation even with no source value.** Today `_merge`
  iterates only source-present fields, so a `tel` on a Facebook contact (no source `tel`) never surfaces.
  The fold must union the set of fields from *source ∪ accepted-observations ∪ resolutions*, so a field that
  exists **only** in the overlay appears.
- **Surface pending observations as `suggestions`.** A separate block on the local (`audience="local"`)
  contact — `{field, value, source, confidence, obs_id}` — that drives the workspace "Gathered" review
  surface. Pending observations are **never** folded into the canonical field, and (like all overlay data)
  never cross the MCP `audience="mcp"` projection.

## 6. Promotion + reversibility (the one-deep model)

Promotion is a **workspace** act (there is no MCP promote tool), through the audited apply path:

- **Single-valued** (org/title/bday): promote = write a `field_resolutions` row (`rule="user"`,
  `chosen_value = obs.value`) — the existing override (`apply.resolve_field`). Flip the obs row to
  `accepted`; flip any prior `accepted` obs for that `(contact, field)` to `superseded` and **delete an
  older `superseded`** (one-deep). Reverse = drop the resolution → survivorship falls back (the superseded
  obs, else the source value in `shared.db`).
- **Multi-valued** (tel/email/adr): promote = flip the obs row to `accepted`; the projection unions it in.
  Reverse = flip back to `rejected`. No "previous" is stored — union values coexist; the source and other
  values remain. (The audit log records each flip.)

Reuses `apply.py`'s lock → snapshot → txn → audit for promotions (rare, deliberate). **Observation writes**
(frequent, from the AI) take a lighter **append + audit, no snapshot** path, so a gathering run can't thrash
the ×20 snapshot ring (the R11 worry the design note resolves).

## 7. The writable MCP server (the structural locks)

A new stdio server (likely `mcp_servers/private_data_ops.py`) exposing **two** write tools, each bounded so
the surface "exposes no tool that exceeds a class's tier" (INV-11 — the same lock as propose-only dedup):

- **`observe_contact_field(contact_id, field, value, source, confidence)`** — canonical-field enrichment.
  *Always* files an `observations` row (status `observed`). It **cannot** promote — no such tool exists — so
  an AI can never make a contact-identity value the default. This is decision #1, structural.
- **`write_field_value(contact_id, field_id, value)`** — overlay (custom) fields, **dispatching by the
  field's `ai_write_policy`**:

  | Field policy | Tool behavior |
  | --- | --- |
  | `review-required` (default) | **stages a proposal** (no direct write) → human approves in the workspace |
  | `append-only` | **appends** a provenance-stamped `field_values` row (quota- + size-checked) |
  | `free-write` | **sets** directly (size-checked) |

  Because the one tool never directly commits a review-required write, the structural guarantee holds.

The dedup surface stays propose-only and unchanged. INV-3 holds: **no definition tool** exists, so an AI
still cannot create or alter a field — it only writes values into the user's existing shapes.

## 8. Limits (enforced at the write, not documented)

- **per-value byte cap** (e.g. 8 KB) — `observe_contact_field` and `write_field_value` refuse an oversize
  value; keeps a free-write scratchpad from bloating the store.
- **per-contact observation cap** (e.g. 64 rows) — refuse / compact past it.
- **per-session write quota** (count, e.g. 50) — a `settings` cap + a per-`ai:<session>` counter; refuses
  past the cap (INV-13). "A run" = a session for v0.2 (no autonomous gatherer yet).
- **retention/compaction** — `obs_id` idempotency collapses identical re-finds; one-deep `superseded` per
  single-valued field; cap `observed`/`rejected`; the audit log is the full history of record.
- **`prm doctor`** — report observation counts + oversize/over-cap flags, so a misbehaving gatherer is
  visible.

## 9. Data-floor orthogonality

`ai_write_policy` (writes **in**) and `disclosure_tier` (reads **out**) are independent. Writing is ingress,
so it is **not** gated by the EX-CLOUD-LLM disclosure mode — an AI may `observe`/append to a `private-sealed`
field it can't read back. Overlay AI values inherit the field's `disclosure_tier` (sealed by default);
**observations are canonical-namespace and, like all overlay data, are never emitted on the `audience="mcp"`
projection** — so gathered data can't leak back out through the same surface that filed it. Both defaults are
the most-protective tier.

## 10. Build order (PR-sized)

- **R11a — the append op + overlay write tiers (the AC-PRM-E demonstrator).** `proposals.append_field_value_op`
  + the non-destructive append branch in `relationships_db.apply_operations`; the `write_field_value` tool
  with policy dispatch (append for append-only, **stage** for review-required, set for free-write); the
  per-session quota + size cap. Tests prove *review-required can't be written directly* and *append-only
  appends non-destructively*. **This alone graduates AC-PRM-E.** *(≈2 PRs — the core append op + dispatch +
  quota/size cap; then the writable MCP server + `just mcp-install` wiring + `--print-config`/`--build` parity.)*
- **R11b — the observation substrate (the gathering seam).** The `observations` table (v2→v3 migration);
  the `observe_contact_field` tool; the projection fold (accepted → canonical, pending → `suggestions`,
  overlay-only fields surface). No promotion UI yet — observations land and are visible as suggestions.
  *(≈2 PRs — table + v3 migration + `observe_contact_field`; then the projection fold + `suggestions` surface.)*
- **R11c — promotion + reversibility.** Promote/un-promote endpoints (single-valued → `field_resolutions`;
  multi-valued → status flip) with one-deep retention; the workspace **"Gathered" review surface**
  (Accept / Reject / Skip per suggestion, attributed). A `frontend-design` pass, as Duplicates had.
  *(≈2 PRs — promote/un-promote endpoints + one-deep retention; then the "Gathered" review UI + the design pass.)*
- **R11d — free-write UI + revert-by-session + doctor.** Free-write field opt-in surface; a
  "revert AI writes (this session)" control; `prm doctor` observation/oversize reporting.
  *(≈1–2 PRs — free-write opt-in + revert-by-session; doctor reporting may ride along.)*
- **R11e — attestation.** `docs/Architecture.md` AC-PRM-E row + INV-10/12/13; regenerate
  `docs/conformance/`; the dynamic egress probe (no observation/AI value crosses the MCP floor). *(≈1 PR.)*

R11a is the smallest honest slice that graduates AC-PRM-E and can ship alone; R11b–c are the gathering seam
the user asked to "build now"; R11d–e are usability + attestation.

**Totals: ≈8–9 PRs** for the full plan, in three natural waves — **≈2** (R11a, graduates AC-PRM-E on its own)
→ **≈6** (R11a–c, the gathering seam) → **≈8–9** (R11a–e, usable + attested). The two estimate swing points
are the `frontend-design` pass (R11c) and the MCP-server wiring (R11a); a couple of small pieces (the quota,
doctor reporting) may ride along rather than stand alone, which is what makes it a range.

## 11. Tests (when built)

- `write_field_value` on a **review-required** field stages a proposal and **does not** write (the structural
  INV-11 test); on **append-only** appends + stamps `ai:<session>` non-destructively (a manual value
  survives); on **free-write** sets directly.
- `observe_contact_field` files an observation and **cannot** make it canonical (no promote tool); a promoted
  observation appears in the projection; un-promote reverts it; the **source value re-appears** when a
  single-valued promotion is dropped.
- **Union** holds: a gathered `tel` *adds* to an existing phone (both present, attributed), never replaces.
- **One-deep retention**: a second promotion supersedes the first and keeps exactly one previous; an older
  superseded is gone; the audit log still has all three events.
- **Limits**: the per-session quota refuses past the cap (INV-13); an oversize value is refused; a
  `private-sealed` field still seals an AI-written value (the floor holds on the read side); observations
  never appear on the `audience="mcp"` projection.
- A deferred capability gets a **red/skipped test with an explicit marker**, never a silent gap.

## 12. Open build-time items (honest deferrals)

- **Single-valued vs. union promotion mechanics** are settled in shape (above); the exact `superseded`
  garbage-collection and the union "revert = which row" detail are pinned during R11c against a real test.
- **Net-new contacts** an AI might surface (a person *not* in the roster) route through `local_records`
  (deferred CRUD-Create), **not** observations — observations enrich an existing `contact_id`. Out of scope
  here; noted so the seam isn't mistaken for contact creation.
- **Autonomous / always-on gatherer** is v0.3; v0.2's quota is a per-session safety bound, and
  revert-by-session (R11d) is the lighter path the daily loop will need. Flagged, not built.
- **Fold-in vs. fast-follow with the data-floor wave**: R11a (AC-PRM-E) can graduate *with* the data-floor
  in one wave or fast-follow it — it is net-new write surface, so splitting is lower-risk; PNT's roadmap
  already allows AC-PRM-E to land in **v0.2.x**.
