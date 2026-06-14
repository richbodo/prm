# Editable contacts without a writable Shared DB — the override model

*Design note · contact CRUD / edit mode · drafted 2026-06-15. Status: accepted — scopes v0.2 contact
**edit mode** (the user-facing CRUD surface) and the `relationships.db` rename. Decision record:
[`../../brainstorms/2026-06-15-prm-v0.2-relationships-schema.md`](../../brainstorms/2026-06-15-prm-v0.2-relationships-schema.md);
build mechanics: [`../../plans/v0.2-implementation-plan.md`](../../plans/v0.2-implementation-plan.md).
Extends the "Relationship to CRUD" section of [`dedupe-design.md`](dedupe-design.md) from dedupe
reconciliation to a first-class edit feature.*

## The decision

**The user can fully edit contacts, but no edit is ever written to `shared.db`.** Every create, update,
and delete lands in the private store (`relationships.db`) and the canonical contact the user sees stays
a **projection** over the immutable mirror plus those private overlays:

| Operation | Mechanism (in `relationships.db`) |
| --- | --- |
| **Update** a field | a `field_resolutions` override (`rule="user"`); the projection layers it over the source value |
| **Create** a contact | a `local_records` row; the projection unions it with the mirrored set |
| **Delete** a contact | a `tombstones` row; the projection filters it out (the source record stays) |
| **Annotate** (tags / notes / custom fields / images) | `field_values` keyed by `contact_id` |

From the user's point of view this is indistinguishable from "editing the contact." The layering is
invisible; the edit shows immediately, tagged as theirs.

## The constraint that forced it — INV-2 / AC-1

PRM keeps mirrored contacts and private data in **two separate stores** (PNT **AC-1**), and the Shared DB
is **read-only at runtime — only the ingestion path writes it** (**INV-2**). That invariant is
load-bearing, not ceremony:

- **Re-import / re-mirror stays safe.** Because `shared.db` is a clean mirror, the user can always re-pull
  it from source; their edits live elsewhere and **re-attach by stable id** (INV-5/6). A re-mirror can
  never clobber hand-authored data.
- **Edits are reversible and audited for free.** Writes to the private store go through the existing
  apply path — file-lock → snapshot ring → one transaction → append-only audit log. Undo is snapshot
  restore.
- **Provenance stays honest.** The source value is preserved; the user's override is layered with its own
  `rule="user"` provenance, so "where did this value come from" still has a truthful answer.

So a contact "edit mode" that wrote into `shared.db` would not be a small convenience — it would dissolve
the two-store split the whole safety model rests on.

## The alternative we rejected — a writable Shared DB

The naive shape of "edit mode" is: open the contact, change a field, save it back to `shared.db`. It was
explicitly considered and rejected:

- **It violates INV-2 / AC-1** and would require dropping a normative invariant and **re-attesting** the
  design upstream (the Shared DB is the AC-1 read-only mirror; `fellows_local_db` and PRM both attest to
  it).
- **It re-creates the sync problem it was meant to avoid.** Once edits live in the mirror, the next
  re-mirror either overwrites them or forces a three-way merge — exactly the "all kinds of syncing
  problems down the road" the feature was worried about. The override model has **no** such problem:
  `shared.db` is disposable and re-pullable; edits are durable and separate.
- **It loses reversibility and clean provenance.** A destructive in-place edit has nothing to layer over
  and nothing to restore to without bolting on a separate snapshot of the mirror.

The override model delivers the same UX while *keeping* every property the writable approach gives up.
This is the same pattern dedupe already uses (a merge is just `identity_map` + `field_resolutions` over
the immutable raw), so edit mode is not new substrate — it is the dedupe substrate exposed as a feature.

## Why one private store, named `relationships.db`

v0.2 renames `private.db` → **`relationships.db`** and keeps **one** private store holding both the
dedup/identity decisions and the new relationship overlay (custom schema, values, tags, notes, image
refs). One file means the structural locks are real, in-file foreign keys — `field_values → field_definitions`
(a value for an undefined field cannot be written, INV-3/4) and `… → contacts(contact_id)`. The
trade-off accepted: relationship values share the store the projection and MCP already read, so sealing
them is the job of the **disclosure-tier data-floor** (default `private-sealed` + a projection-bound MCP
surface), not of store-absence. For attestation, `relationships.db` maps to the PNT spec's conceptual
"Private DB."

## What would change this decision

- A shift to **auto-applied AI write tiers** that bypass review does not change it — those still write the
  private store, never the mirror.
- A **distribution/installer change that introduces a non-local or server-authoritative contact store**
  would reopen the two-store question (and much else); revisit then.
- Real-world use showing the projection is too slow to compute at the user's contact volume would motivate
  **materializing** the projection (a cache), but the source of truth would still be immutable-raw +
  private overlays — the override model would stand; only its evaluation would change.
