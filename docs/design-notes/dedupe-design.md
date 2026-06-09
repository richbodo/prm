# Contact dedupe — one pipeline, two authors, reversible by construction

*Design note · dedupe & merge · drafted 2026-06-08. Status: accepted — scopes v0.1 **M3** (the
deterministic/manual flow); the AI author is **M4**. Informs **INV-2**, **AC-PRM-B**, and the proposed
**AC-PRM-E/F** safe-write tiers. Build mechanics live in
[`../../plans/v0.1-implementation-plan.md`](../../plans/v0.1-implementation-plan.md) §4 (data model) and
§11 (M3). Survey of prior art that informed it is summarized under Sources.*

## The shape of the problem

Dedupe is two steps: **find** contacts that share enough information to be the same person, and
**decide** whether to merge them. A merge produces a **superset** contact — multi-valued fields (emails,
phones) union; single-valued fields that disagree (a display name) can't be superset-ed and need a
human to **reconcile**. Users reach this two ways: a **deterministic** path (the tool finds candidates,
the user walks each one) and an **AI-assisted** path (the AI works through candidates and says "review
my work"). PRM wants both. The design question is how much they share.

## Decision 1 — One detection core, two proposal authors, one review + apply

The two flows differ in exactly two places: **who authored the proposal**, and **the list-level
affordance** (AI wants batch-triage + a confidence filter; manual wants a "walk me through each cluster"
queue). Everything underneath — the side-by-side diff, the per-field pick-the-winner, the merge preview,
the apply path — is identical (verified across Google "Merge & fix", Apple, Salesforce, HubSpot, Clay).

So PRM builds **one** candidate-detection core, **one** changeset representation, and **one**
review+apply surface; a deterministic UI and an LLM are interchangeable *proposal authors* that emit the
same changeset. This lands on the existing milestone boundary: **M3 is the whole manual flow**
(detect → review → reconcile → apply), with **no AI and no MCP**; **M4 adds the AI author** over MCP,
reusing M3's surface unchanged. It also keeps every AI write behind explicit human approval — the same
boundary the [MCP-trust note](mcp-cannot-identify-the-consuming-llm.md) draws (the AI proposes; the
daemon applies; INV-11 / AC-PRM-F).

## Decision 2 — Immutable raw + projection makes merges reversible

`shared.db` is an immutable mirror (**INV-2**). A merge is therefore *only* a mapping —
`identity_map` (which raw records form one canonical contact) plus per-field overrides
(`field_resolutions`) — and the canonical contact is a **projection**, recomputed, never persisted as
state. This is the best-in-class pattern, not a compromise: the one public system doing exactly it is
Hightouch's Golden Record (raw rows tagged with an identity id → a separately-projected record);
Salesforce and HubSpot instead do **destructive** merge with *no native un-merge* (third-party tools
"undo" only by snapshotting first).

Because raw is never mutated, reversibility is nearly free. **v0.1 uses snapshot-restore** (the existing
pre-apply snapshot ring, AC-9) as Undo — coarse but lossless and already built. *What would change it:*
surgical per-merge un-merge (drop one `identity_map` row + dangling resolutions, recompute) is cheap and
worth adding when concurrent/Private-overlay edits make whole-DB restore too blunt — deferred, not
needed for v0.1's single review-required writer.

## Decision 3 — Union vs reconcile; source-priority survivorship; flag, don't guess

Survivorship is decided **per field**, never per record:

- **UNION** (keep all, dedup by normalized value, carry provenance): `email`, `tel`, `adr`, `url`,
  `impp`, `categories`, `x-social-*`, `related`.
- **RECONCILE** (pick one): `fn`, `n`, `bday`, `photo`, `gender`, `anniversary`; `org`/`title` are
  current-reconcile + history.

Default winner for reconciled fields: **source-priority → most-complete → most-recent** (`observed_at`,
which we already store). The priority is **Apple/Google > LinkedIn > Facebook** — user-curated address
books outrank scraped professional/social graphs (Facebook exports are name-only). A **user override
always beats the rule**, and the priority order is **config, not hardcoded**: it's a policy choice to
*validate against real exports* as they arrive, not an assumption to bake in. When a reconciled field
has ≥2 distinct values and the rule can't break the tie confidently, PRM **flags `needs_review` rather
than guessing** (fail-loudly) — the projection shows the rule's pick marked *unconfirmed* until a human
resolves it.

## Decision 4 — Stdlib candidate detection, tiered, guarded against over-merge

The mature libraries (`dedupe`, `recordlinkage`, `splink`) pull numpy/scipy and often ML/training —
incompatible with PRM's deliberately tiny dependency surface. Detection is **stdlib only**
(`unicodedata`, `difflib`, `re`, `hashlib`):

- **Normalize:** email (lowercase, strip `+tag`; for gmail/googlemail also drop dots), phone (digits,
  last-10 as the match key — reliable without a phone library), name (accent-fold, casefold, a small
  nickname table, plus `first-initial+last` and sorted-token forms).
- **Block** (multi-key, sub-O(n²)): canonical email · phone-last-10 · 5-char surname prefix ·
  sorted name tokens · a phonetic surname key · LinkedIn profile-URL slug for thin records.
- **Score into tiers** with `difflib.SequenceMatcher.ratio()`: **confident** (email exact, or phone +
  name ≥ 0.6), **strong** (surname exact + first-name ≥ 0.85/nickname + a corroborator), **fuzzy**
  (name ≥ 0.85, no corroborator — the Facebook name-only case).
- **Cluster** with union-find over **confident edges only**, a size cap, and a cohesion check, to kill
  the A~B, B~C ⇒ A~C transitive over-merge "black-hole." Strong/fuzzy pairs are *reported*, never
  auto-linked.

This honors **AC-PRM-B**: name+company and name-only matches are fuzzy — surfaced for review, never
auto-merged. Prior art: CardBook ("same email OR phone is enough" → high recall, human reviews),
Thunderbird's Duplicate Contacts Manager (order-independent email/phone sets; excludes household-shared
phones). *What would change it:* at tens of thousands of contacts, or if precision disappoints, revisit
with canopy/phonetic upgrades — but a personal store (the demo is ~1000) sits well within this.

## Decision 5 — v0.1 ergonomics: neutral, one-at-a-time, nothing auto-applies

v0.1 is the **review-required tier only** — even a confident match is *proposed*, never auto-applied.
The default flow is **one-at-a-time** (Clay's finding: deliberate friction prevents accidental merges),
candidates presented **neutrally** (no pre-checking). Rejections persist (a `dedup_decisions` record
keyed on stable source ids) so a dismissed pair doesn't resurface on re-detect or re-import.

**Bulk approve (shipped).** Alongside one-at-a-time, the workspace offers a **bulk** mode that lands the
capability this note anticipated for the M4 AI author — "a batch of machine proposals actually needs
triage." It *replaces* the per-item friction with three equivalent safety properties rather than
removing it: (1) you select **logical groups** whose trust is already known — the tiers
(`confident`/`strong`/`fuzzy`/`review`) plus the AI-proposed group — with **confident + AI pre-selected
and the name-based tiers opt-in** behind an explicit confirm; (2) a **spot-check** pass where you flip
through the selection, **resolve any single-valued conflict inline** (so "flag, don't guess" still
holds — Decision 3) and **exclude** anything wrong before committing; (3) the batch is applied as **one
combined changeset** (the substrate's `operations` list, run in a single transaction with one pre-apply
snapshot), so it is **atomic** and a **single Undo reverses the whole batch**. The combined changeset is
the sanctioned author for the multi-op shape the substrate always allowed but the single-merge surface
never exposed. Overlapping clusters are refused (the workspace only presents disjoint clusters); a
proposal and its detected candidate are deduped on the stable cluster key.

## Relationship to CRUD

Editing a contact field is *the same operation* as dedupe reconciliation: both write a `resolve_field`
override to `private.db`, which the projection blends over the source value. So M3's substrate — the
projection, `field_resolutions`, and the reversible audited apply path — **is** field-level **Update**.
PRM therefore builds **dedupe first**: it's the harder constraint (it forces immutable-raw + per-field
provenance + reversible, snapshotted, audited writes), and Update rides along nearly free. **Create**
(user-authored contacts → a `local_records` store) and **Delete** (a projection-filtered tombstone) are
a follow-on on the same substrate; Create aligns with v0.2's private overlay. See
[`../roadmap.md`](../roadmap.md).

## Sources

Survey by three research passes (2026-06-08), cited from source/docs where possible:

- **Model / survivorship / reversibility** — Hightouch Golden Record (immutable-raw + projected record);
  Salesforce & HubSpot destructive-merge-without-unmerge; Profisee / Data Ladder on attribute-level
  survivorship; event-sourcing compensating-events for undo.
- **Algorithm** — `dedupe` predicate-blocking + connected-component clustering; `recordlinkage`
  (Block / SortedNeighbourhood); CardBook and Thunderbird Duplicate Contacts Manager matching rules;
  the transitive-closure over-merge failure mode; Gmail address canonicalization.
- **Review UX** — Google "Merge & fix", Apple Contacts, Fastmail, Salesforce/HubSpot field-level
  survivorship tables, Clay's one-at-a-time friction; GitHub PR-review conventions for the diff styling.

*What would supersede this note:* a shift away from review-required writes (auto-apply tiers), a
distribution/installer change that adds a non-local store, or real-export validation that overturns the
source-priority order or the stdlib detection's precision.
