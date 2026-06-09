# Bulk-approve merges — the decisions, and where PRM differs

*Design note · dedupe & merge · drafted 2026-06-09. Status: accepted — scopes v0.1 **M3f**
(bulk-approve). Builds on [`dedupe-design.md`](dedupe-design.md) (the base one-pipeline / two-author /
reversible model — Decisions 1–5) and does not restate it. Informs **INV-2**, **INV-11**, **INV-12**,
**AC-PRM-B**, **AC-PRM-F**. Build mechanics:
[`../../plans/v0.1-implementation-plan.md`](../../plans/v0.1-implementation-plan.md) §11 M3f.*

## The question this note answers

Bulk-approve lets a user merge a whole *group* of duplicates at once (all `confident`, all AI-proposed,
…) instead of one at a time. The merge surface is the most sensitive in the app — it changes the
canonical shape of a person's network — so the reviewing question is blunt: **did batching merges weaken
data safety?**

The answer this note defends is **no**: bulk batches the *approval*, never the *trust*. Every property
that made one-at-a-time safe is still structurally intact, and each new affordance is paired with a
compensating guard, so the surface area for accidental harm did not grow. The friction that one-at-a-time
provided (Clay's finding: deliberate friction prevents accidental merges) was **relocated to where the
risk is**, not removed.

## The decisions

### A. Hard constraints respected (pre-existing; non-negotiable)

- **INV-2 — immutable raw; merges write only `private.db`.** A bulk merge of N contacts touches
  `identity_map` + `field_resolutions` only; `shared.db` is never mutated, whatever N is. A merge is a
  mapping in a projection layer, not a destructive edit.
- **INV-11 / AC-PRM-F — the AI proposes, the workspace disposes.** No bulk-apply was added to the MCP
  surface. The AI still only *stages* proposals; a human applies — now possibly in bulk, but always by an
  explicit workspace action. The AI gained no apply power.
- **INV-12 / AC-9 — reversible + audited.** The batch takes one pre-apply snapshot and writes one audit
  entry, exactly as a single merge does.
- **AC-PRM-B — name-only / name+company never auto-merge, never silently transitively merge.** Honored
  twice: name-based tiers are opt-in (never default-selected), and overlapping name-based pairs are
  *skipped*, never chained (see D and E).
- **Decision 3 (flag, don't guess).** A disagreeing single-valued field is never silently auto-picked in
  bulk; the user must resolve it inline before that item can be merged.
- **Decision 5 (review-required tier; nothing auto-applies).** The crucial reading: *review-required*
  means *a human applies by explicit action* — it never meant *one record at a time*. Bulk is still an
  explicit human apply; the **spot-check pass is the review**. Bulk lives inside Decision 5; it doesn't
  breach it.

### B. Interpretations — where a constraint left latitude and we chose

- **Enabling bulk-approve at all.** Decision 5 explicitly parked this for "the M4 AI author, where a batch
  of machine proposals needs triage," so this *fulfils* a deferred plan rather than overriding one. The
  judgment: **replace** one-at-a-time friction with three equivalent properties (group-level confidence;
  spot-check-with-exclude; atomic single-undo) rather than dropping it.
- **Conflict handling = resolve inline** — chosen over *exclude-conflicted-from-bulk* and over *auto-apply
  the survivorship default*. We rejected auto-apply precisely because it would bend "flag, don't guess";
  a conflicted set joins the batch only once the user has picked.
- **Tier scope = confident + AI pre-checked; name-based opt-in behind a confirm.** The bulk-surface
  reading of AC-PRM-B: the riskiest matches demand the most deliberate action and are visually quarantined.
- **One combined changeset** — chosen over looping the single-merge apply N times. This is the spine: the
  substrate's `operations` list run in one transaction. It is what makes the batch atomic and the undo
  singular. The rejected alternative would have produced N snapshots and a *partially-applied* batch on
  failure.
- **Survivor (`into`) = source-priority → most-complete → most-recent**, computed server-side as a
  *recommendation* (not user-editable in v1) — Decision 3's survivorship order applied to the bulk case,
  where the user isn't eyeballing each survivor.
- **Overlap = skip-and-report, greedily** — chosen over *refuse-the-whole-batch* and over *auto-union the
  chain* (see E).
- **Undo = reuse the coarse snapshot-restore** — chosen over building surgical per-merge un-merge;
  lossless and already built (Decision 2 / AC-9).

### C. Genuine data-safety choices

- **Atomic, all-or-nothing batch.** Any op fails → the transaction rolls back *and* the snapshot is
  restored; no half-applied batch is ever observable.
- **One Undo reverses the whole batch.** One snapshot means there is no "I undid one of 139 and I'm now
  stuck mid-batch" state.
- **No silent transitive merge** (see E) — the core safety property of the overlap handling.
- **Conflicts can't be committed unresolved.** The readiness gate (`ready = selected − excluded −
  unresolved`) makes "Merge N ready" structurally unable to apply an unresolved conflict.
- **The exclude valve.** A bad apple spotted while flipping can be pulled before commit — the direct
  replacement for per-item friction.
- **MCP unchanged** — bulk is entirely human-side; the AI's powers are identical to before.

### D. Perception-of-safety choices (making the genuine safety legible)

- The visual split of **"safe to bulk-merge"** vs **"spot-check each"**, with per-group `safe` /
  `caution` / `N need a pick` pills.
- The **opt-in confirm** for higher-risk groups — friction exactly where the risk is.
- The **dot-strip** colour-coding (ready / needs-a-pick / excluded), so the batch's state is visible at a
  glance.
- Reassurances: **"↺ Reversible — part of one batch"** on every card; **"One Undo reverses all N
  merges"** at the end.
- **"Your imported source records were never changed"** in the summary — surfacing INV-2 in plain words.
- The **skip report** ("N that overlapped another merge were skipped — they'll reappear next time") —
  nothing is silently dropped.

### E. The overlap decision, in full (the subtle one)

Confident clusters are made **disjoint** by union-find, so they can't share a contact. But the opt-in
name-based tiers (`strong`/`fuzzy`) are surfaced as **standalone pairs** that routinely *do* share a
contact — three similar records give pairs (A,B), (B,C), (A,C). A large fuzzy selection therefore chains.

Three options were on the table:

1. **Refuse the whole batch on any overlap** (the first cut). Honest, but it dead-ends a 139-merge batch
   because two items chain — a poor experience exactly when bulk is most useful.
2. **Auto-union the chain** (A,B,C → one contact). **Rejected** — that is a silent transitive merge of
   name-only matches, which AC-PRM-B forbids.
3. **Skip-and-report, greedily** (chosen). Accept disjoint items; an item that partially overlaps an
   already-accepted one is **skipped**, the batch still commits what it can, and the skipped pairs
   resurface next pass (now against the merged contact) for an explicit decision.

Option 3 is a small but real model choice: PRM **converges over passes** rather than resolving the whole
transitive graph in one shot, trading one-shot completeness for *never an accidental transitive merge*.

## Where PRM's merge model differs from other systems

(Prior-art survey lives in [`dedupe-design.md` § Sources](dedupe-design.md); this is the contrast that
the bulk/safety decisions sharpen.)

- **Bulk merge is non-destructive and atomically reversible.** Mainstream contact tools — Google
  "Merge & fix", Apple Contacts, Salesforce, HubSpot — bulk-merge *destructively*: the losing records are
  gone and there is no native un-merge, let alone a single undo for a whole batch. PRM's bulk merge is a
  mapping over an immutable raw store, applied as one transaction with one snapshot, so a single Undo
  reverses the entire batch.
- **It batches the approval but keeps the review.** Most systems offer a binary — auto-merge confident
  duplicates with no human in the loop, *or* force tedious one-at-a-time. PRM does neither: you batch the
  *approval* while the spot-check pass (inline conflict resolution + exclude) preserves the *review*.
- **Conflicts are never silently resolved, even in bulk.** MDM survivorship tables (Salesforce / HubSpot
  / Profisee) auto-pick a winner per rule. PRM flags a disagreeing single-valued field and requires a
  human pick before that item can merge — flag-don't-guess survives into the batch.
- **No silent transitive merge under bulk.** The transitive-closure "black hole" is a known dedup failure
  mode; naive systems union aggressively. PRM unions only *confident* edges (guarded by size + cohesion),
  never name-only ones, and at bulk time skips-and-resurfaces overlapping pairs instead of chaining them.
- **One substrate, two authors.** Tools that add "AI dedup" usually bolt it on as a separate, often
  destructive path. In PRM the deterministic UI and the LLM emit the *same* changeset; bulk-approve is
  simply the human applying many at once, and the AI never gains apply power (INV-11).

## Known seams (honest limitations — fail loudly, not silently)

- **Undo doesn't revert a constituent proposal's file status.** After a bulk undo, an AI proposal that was
  in the batch stays marked `applied` (its status is a JSON file outside `private.db`, which
  snapshot-restore can't touch), so its cluster reappears as a plain detected candidate. The *data* is
  correct — the merge is reversed; only the "this was an AI proposal" framing is lost. This is **identical
  to the existing single `apply-proposal` + undo behaviour** — pre-existing, just more visible at batch
  scale.
- **Proposal status bookkeeping is non-fatal.** Marking proposals `applied` happens *after* the atomic
  commit; a failure there is reported as `status_unmarked`, not a failed merge. (A JSON-file write can't
  be made atomic with the SQLite transaction without a redesign.)
- **Undo is coarse (whole-DB snapshot-restore).** Surgical per-merge un-merge is deferred.
- **Spot-check prefetches all previews on entry** — fine for the confident/AI sets; a several-hundred-item
  fuzzy opt-in fetches that many previews up front (local-only, but can feel slow).
- **Default-to-Bulk landing** — a UX call that changes what existing users see first; trivially reversible.

## What would revisit these

- **Surgical un-merge** — once v0.2's private overlay makes whole-DB snapshot-restore too blunt (it would
  also fix the proposal-status-on-undo seam).
- **Auto-unioning overlapping fuzzy chains** — only if real-export validation shows users want it; today
  it is deliberately rejected as a silent transitive merge.
- **A user-editable survivor** — if the source-priority recommendation proves wrong often.
