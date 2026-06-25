# AI writes and gathered data — the additive overlay and the write tiers

*Design note · relationship store & AI writes · drafted 2026-06-25. Status: accepted — scopes the v0.2
**R11** AI-write substrate (the three write tiers) and the **gathered-data enrichment** seam that v0.3
delegated gathering builds on. Extends [`contact-edit-override-model.md`](contact-edit-override-model.md)
from *user* edits to *AI-gathered* enrichment, and reuses [`dedupe-design.md`](dedupe-design.md) Decision 2
(immutable-raw + projection ⇒ reversible). Build mechanics:
[`../../plans/r11-append-only-tier.md`](../../plans/r11-append-only-tier.md). Informs **INV-2/4/7/10–13**,
the proposed **AC-PRM-E**, and the v0.3 roadmap item (delegated gathering).*

## The shape of the problem

Two threads converge here. **R11** asks: now that we want an AI to write the private store at all (today
the MCP surface is read + propose-only-dedup), how do we let it write *safely* — the per-field
`ai_write_policy` tiers (`review-required` | `append-only` | `free-write`) already on `field_definitions`?
And the **v0.3 delegated-gathering** use case asks the harder question the project has carried since the
start: an AI runs out across the contacts and *brings data back* — a phone for a Facebook contact that was
only ever a name, an employer for a LinkedIn-URL-only record, "what they're working on" for everyone. Where
does gathered data **land**, given that `shared.db` is the immutable mirror of what the SaaS exports gave us
(INV-2), and how does the user keep it — accept the good, reverse the wrong — without us hoarding versions?

The Facebook case is the one that matters most: for many people the bulk of their network is trapped behind
names-only Facebook exports, so the feature that fills those records back out is how they **reclaim the root
of their personal network**. It is also the feature most likely to write a *wrong* value at volume (an AI at
the wrong temperature, a bad source, a confident hallucination), so its safety model has to be load-bearing,
not advisory.

## The decision

**An AI never writes a canonical contact field directly. It writes an *additive, attributed observation*,
and the user *promotes* it into the view — using the same reversible-projection levers dedup already uses.**
Direct AI writes are confined to the user's *overlay* (custom schema fields), governed per-field by the
write tier. Everything lands in `relationships.db`; `shared.db` stays the pristine mirror (INV-2).

| What the AI does | Where it lands | How it becomes visible | How it's reversed |
| --- | --- | --- | --- |
| Gather a **canonical** field (tel/email/org/title — the vCard namespace) | a new `observations` row in `relationships.db`, status `observed`, stamped `ai:<session>` + source | **only** when the user **promotes** it | un-promote → the projection falls back; the source value (if any) is still in `shared.db` |
| Write an **overlay** field marked `review-required` (default) | a staged proposal (the existing propose/dispose substrate) | the user approves it in the workspace | reject the proposal; snapshot Undo |
| Write an **overlay** field marked `append-only` | an appended `field_values` row (non-destructive), stamped `ai:<session>` | immediately, as an *attributed* value; the human value wins on display | revert-by-session / drop the AI rows |
| Write an **overlay** field marked `free-write` | a direct `field_values` set, **size-capped** | immediately | overwrite again / Undo |

The canonical-field rule is the important one: it makes "the human is the actuator" (UM-1/2/3) **structural**
for contact-identity data. The MCP tool that an AI uses to file an observation *can only file* — there is no
promote tool on the surface, exactly as the dedup surface has no apply tool (INV-11). So no bug, prompt, or
temperature swing lets an AI silently change a contact's phone number; the worst it can do is queue a
suggestion you ignore.

## The pattern this reuses — the reversible view

PRM already has the mechanism the user reached for ("make the AI value the default; if it's wrong, reverse it
by changing the view"). It is the dedup/override substrate:

- `shared.db` is immutable raw (INV-2). The canonical contact is a **projection**
  (`core/projection.py`), recomputed from raw + a thin layer of *decisions* in `relationships.db`.
- A **single-valued** field (org, title, bday) is reconciled to one value; an explicit
  `field_resolutions` row (`rule="user"`) overrides survivorship — and it can hold a value **no source
  has**. Delete the row → the view falls back. That is *already* "promote a value as the default, reverse
  by deleting one row," reversible and lossless. Contact edit mode
  ([`contact-edit-override-model.md`](contact-edit-override-model.md)) is its first non-dedup customer.
- A **multi-valued** field (tel, email, adr) is a **union** — values accumulate, deduped by normalized
  value, each carrying provenance.

So gathered data does not need a new reversibility mechanism. It needs **one new projection input** — the
observation — and the existing levers to promote it: a `field_resolutions` override for single-valued
fields, a union-add for multi-valued ones.

## Where gathered data lands — the additive observation layer

A gathered canonical value can't reuse the two existing tables:

- It can't be a **`field_values`** row — those hang off a `field_definition` by foreign key (the INV-3/4
  lock), and canonical jCard properties (`tel`, `email`) are *not* user-defined fields. Forcing a fake
  "tel" definition would conflate the user's custom-schema namespace with the contact namespace.
- It can't be a **`field_resolutions`** row alone — that is single-valued (one row per contact+field) and
  only surfaces a field that *already has a source value* (the projection iterates source-present fields).
  A Facebook contact has no `tel` at all, and phones are multi-valued.

So canonical enrichment gets its own small table, **`observations`** (mechanics in the plan): additive,
append-only, attributed (`written_by='ai:<session>'`, `source`, `confidence`, `written_at`, `status`),
targeting a canonical jCard property. It is a **holding pen**, never the contact itself. The projection
folds *accepted* observations into the contact (union for multi-valued, an override for single-valued) and
surfaces *pending* ones to a workspace review surface as suggestions. This keeps the two-store meaning
honest: **`shared.db` = what the SaaS exports gave me (the provenance root I'm reclaiming); `relationships.db`
= everything I and my AI add and decide on top — all separable, all reversible.** Gathered data is
definitionally "on top," so it belongs in the private store, never the mirror — and so the AI never has to
write `shared.db`, and INV-2 holds with no relaxation.

This is also where **schema evolution** stays the user's: a gathered fact that fits no existing field sits as
an observation (or in a free-write scratchpad). The AI cannot invent a field for it (INV-3, structural); the
user creates the field and promotes the value into it. The AI fills shapes; only the user makes new shapes.

## The write tiers, mapped onto this

The three tiers on `field_definitions.ai_write_policy` are not three unrelated features — they are the *degree
of human mediation* for an **overlay** field, with the most-protective tier the default (INV-10):

- **`review-required` (default).** The AI may not write directly; `write_field_value` on such a field
  **stages a proposal** the user approves. Same propose/dispose loop as dedup. This is also, by
  construction, the only way an AI value reaches a **canonical** field — via an observation the user
  promotes — so contact-identity data is review-required whether or not a field is involved.
- **`append-only` (the AC-PRM-E demonstrator).** A direct write that may only **append** a
  provenance-stamped `field_values` row — never overwrite or delete. The human value wins on display; AI
  rows render as attributed observations. Bounded by a per-session quota (INV-13), reversible + audited
  (INV-12).
- **`free-write`.** A direct set into a low-stakes field the user has explicitly opted in — the "junk
  scratchpad" a gatherer can dump into. The only safety it needs is a **size cap** so it can't bloat the
  store; mistakes are tolerable by definition.

`ai_write_policy` (writes **in**) and `disclosure_tier` (reads **out**) stay orthogonal: an AI may append to
a `private-sealed` field it can't read back, and every AI-written value inherits the field's tier (sealed by
default), so a new observation is sealed from the moment it exists. Both defaults are the most-protective
tier.

## One-deep retention — bounded by design

The additive instinct ("keep the old copies") is right, but unbounded history is not the goal — a daily
gatherer that finds a different number every day is explicitly **out of scope**. The constraint, which the
implementation must honor:

**Per `(contact, field)`, keep the *current* promoted value and at most *one* previous. A new promotion
replaces — demoting current → previous and discarding the older previous.** This buys exactly one move:
*"this AI phone is wrong — give me back what was there before."* Three fallback layers, deepest always free:

1. the **current** promoted value (in the view),
2. **one previous** promoted value (the only explicit history the live store keeps),
3. the **source** value in `shared.db` — immutable, always retrievable, never counted against the budget.
   If the original mirror had a correct value that a promotion later shadowed, dropping the promotion brings
   it straight back.

Full forensic history is *not* lost — it lives in the **append-only audit log** (`audit.log.jsonl`), which
records every AI write and promotion. We simply don't carry that history in the working store, where it would
bloat the projection. (On-demand gathering also keeps this cheap: observation writes go to the
append/audit path — *no* per-write snapshot, avoiding the ×20 snapshot-ring thrash — while a *promotion*, the
rarer deliberate act, goes through the normal snapshot+apply.)

## Union vs. reconcile on promotion

Promotion is shaped by the field, matching how contacts really work:

- **Multi-valued (tel, email, adr): union — gathered values *add*, never silently replace.** A real person
  has a mobile *and* a work line; a gathered phone joins the set (attributed), and "revert" is just removing
  the wrong one — the source and other values remain. There is no "previous version" to keep because nothing
  was overwritten. For a Facebook contact with *zero* source phones, a single gathered phone behaves exactly
  like replace-and-revert, because there is nothing to union against — which is the case the user actually
  cares about, and it falls out for free.
- **Single-valued (org, title, bday): reconcile — a `field_resolutions` override.** Here "replace the
  previous version" is literal, and the one-deep retention applies: the prior promoted value is kept once for
  a single revert; the source value is always underneath.

## The limits that make AI writes safe (real, not advisory)

Assume the AI is occasionally wrong, buggy, or adversarially prompted; the store's integrity must not depend
on it being right. Every limit is enforced at the write, never merely documented:

- **per-value byte cap** — an oversize write is *refused*, so a free-write scratchpad can't balloon the DB;
- **per-contact observation cap** — one contact can't accumulate unbounded gathered rows;
- **per-session write quota** (count) — the INV-13 runaway-loop bound;
- **retention/compaction** — `value_id`/`obs_id` idempotency collapses identical re-finds; one-deep
  superseded per single-valued field; the audit log carries the full trail;
- **no canonical write without a human promote** — structural (no promote tool on the MCP surface);
- **`prm doctor`** surfaces observation counts and oversize flags, so a misbehaving gatherer is *visible*.

## Alternatives rejected

- **Gathered data as a synthetic source written into `shared.db`** (the "it's just another source" framing,
  cf. the feature spec's F5 "replies feed back through the F1 pipe"). Tempting — it reuses the projection's
  union/reconcile wholesale — but it makes an AI a *runtime writer of the mirror*, dissolving INV-2 and the
  two-store meaning (the mirror is supposed to be "what an external system gave me," and a gathered value is
  not). It also tangles AI data with real imports, so a clean re-mirror or a "drop everything the AI added"
  can no longer be a simple separation. The observation layer keeps the AI on the user-owned side of the
  split and INV-2 intact. *(F5's narrower claim — gathered values writing into the **custom** fields — is
  preserved: that is exactly the overlay append-only path.)*
- **Gathered canonical values in `field_values`.** Rejected: it needs a fake field definition per canonical
  property, conflating the user's schema namespace with the contact namespace (above).
- **Full immutable version history in the live store.** Rejected per the one-deep constraint: the audit log
  is the history of record; the working store stays lean.
- **Letting the AI promote / set canonical fields directly under append-only or free-write.** Rejected for
  contact-identity data: the user's own example has an explicit acceptance step, and "the human is the
  actuator" is the property worth keeping structural. Direct AI writes stay on the opt-in overlay.

## What would change this decision

- **A net-new-contact discovery feature** (the AI surfaces a person *not* in the roster, vs. enriching an
  existing thin contact) would route through `local_records` (the deferred CRUD-Create store), not the
  observation layer — observations enrich an existing `contact_id`; they don't mint people.
- **A genuinely autonomous, always-on gatherer** (vs. today's on-demand assumption) would make the snapshot
  cadence and the retention/compaction policy load-bearing rather than comfortable — revisit the
  append-path and the caps then (the v0.3 concern the plan flags).
- **Real use showing the projection is too slow** once observations fold in at volume would motivate
  *materializing* the projection (a cache) — but the source of truth stays immutable-raw + overlays; only
  evaluation changes, exactly as for the override model.
- A shift away from **review-required as the canonical-field default** — auto-accepting gathered
  contact-identity data — would supersede the core decision here and warrants its own note.
