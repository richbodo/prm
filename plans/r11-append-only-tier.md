# R11 — the append-only AI-write tier (AC-PRM-E demonstrator)

*Status: **draft for scoping** (2026-06-25). The Phase-B milestone from
[`v0.2-implementation-plan.md`](v0.2-implementation-plan.md) §8 (R11), pulled into its own sketch so we can
decide whether to fold **AC-PRM-E** into the data-floor graduation wave or fast-follow it. Companion to
[`../docs/prm-feature-spec.md`](../docs/prm-feature-spec.md) ("Safe AI writes", INV-10–13) and
[`../docs/roadmap.md`](../docs/roadmap.md) (v0.2 + parallel track). Not yet implemented.*

## 1. Goal

Let an AI **write values into the private store** through the MCP surface, governed by the per-field
`ai_write_policy` that already exists (`review-required` | `append-only` | `free-write`, default
review-required). This is the first time an AI can write the store at all — today the MCP surface is
**read + propose-only-dedup**. R11 demonstrates PNT's **AC-PRM-E** (tiered safe-AI-write) with the
**append-only** tier; **AC-PRM-F** (no MCP path commits a review-required write directly) is the same
structural lock, already subsumed upstream by user-mediation (UM).

## 2. What "append-only" means operationally

A field marked `append-only` accepts a **direct** AI write (no human-in-the-moment review), but only by
**appending a new, provenance-stamped row** — never overwriting or deleting:

- **Non-destructive** — a human-authored value is never touched; the AI only adds.
- **Human wins on display** — the manual value is primary; AI rows render as attributed observations
  ("AI-observed: X · session · date").
- **Provenance-stamped** — `written_by = 'ai:<session>'`, `source`, `written_at` (already columns on
  `field_values`).
- **Bounded** — a per-run/session **quota** caps a runaway loop (INV-13).
- **Reversible + audited** — every AI write hits the audit log; revertable (INV-12).

For contrast: **review-required** (default) → the AI may *not* write directly; it **stages a proposal** you
approve in the workspace (the existing propose/dispose substrate). **free-write** → direct overwrite,
opt-in, low-stakes.

## 3. The structural lock (what makes it safe + demonstrates AC-PRM-E/F)

One MCP tool — `write_field_value(contact_id, field_id, value)` — that **dispatches by the field's policy**:

| Field policy | Tool behavior |
| --- | --- |
| `review-required` (default) | **stages a proposal** (no direct write) → human approves in the workspace |
| `append-only` | **appends** a provenance-stamped row (quota-checked) |
| `free-write` | **sets** directly |

Because the single tool *never* directly commits a review-required write, the surface "exposes no tool that
exceeds a class's tier" (INV-11) — the structural guarantee, identical in spirit to the propose-only dedup
lock (`mcp_servers/dedup_ops.py` has no apply tool).

## 4. What already exists vs. what R11 adds

| Piece | Status | R11 work |
| --- | --- | --- |
| `field_definitions.ai_write_policy` + `field_values.written_by` provenance | ✅ exists | — |
| `field_values` multi-row per `(contact_id, field_id)` + `value_id` idempotency (dup append is a no-op) | ✅ exists | — |
| Audited apply path (lock → snapshot → txn → audit, `core/apply.py`) | ✅ exists | reuse |
| **`append` op** — today `set_field_value` *replaces* for a single-valued kind; append must NOT delete | ❌ | small — add to `core/relationships_db.py:apply_operations` + a `proposals.append_field_value_op` |
| **MCP `write_field_value` tool** + policy dispatch | ❌ | medium — new tool; likely a small `mcp_servers/private_data_ops.py` server + `just mcp-install` wiring + `--print-config`/`--build` parity |
| **Per-session quota** (settings cap + per-`ai:<session>` counter) | ❌ | small–medium |
| **Projection: human-wins + attribution** (`core/projection.py:_relationship_fields`) | ❌ | small–medium |
| **Workspace: show AI-attributed values + "revert AI writes"** | ❌ | medium (UI) |
| Tests + `docs/Architecture.md` AC-PRM-E attestation | ❌ | medium |

Everything rides the existing substrate — **no new store, no new deps** (stdlib + the `mcp` SDK only).

## 5. Two scope tiers (the fold-in decision)

**Minimal demonstrator — enough to graduate AC-PRM-E (~3 focused PRs):**
the `append` op + the dispatching `write_field_value` tool (append for append-only, **refuse/stage** for
review-required, set for free-write) + provenance + audit + a per-session quota + tests proving
*review-required can't be written directly* and *append-only appends non-destructively*. Legitimately
demonstrates "per-class policy · most-protective default · reversible + audited · structural no-bypass."

**Full R11 — usable, not just demonstrable (~+2–3 PRs):** projection human-wins/attribution display; the
workspace "revert AI writes" control; the lighter **append-log + revert-by-session** path (so the v0.3
long-running gatherer doesn't thrash the ×20 snapshot ring); free-write UI.

## 6. Data-floor interaction (orthogonality)

`ai_write_policy` (writes **in**) and `disclosure_tier` (reads **out**) are **orthogonal**. Writing is
**ingress**, so it is *not* gated by the EX-CLOUD-LLM disclosure mode — an AI may append to a `private-sealed`
field it can't read back. AI-written values inherit the field's `disclosure_tier` (sealed by default), so a
new AI observation is sealed from the moment it exists. Both defaults are the most-protective tier.

## 7. Open decisions to pin scope

1. **Quota — define "a run."** Per-`ai:<session>` counter with a configurable cap (e.g. 50). No autonomous
   gatherer exists in v0.2 (that's v0.3), so the quota is just a safety bound on the tool. *Recommend:
   per-session cap in `settings`; trivial.*
2. **Reversibility model.** Reuse the snapshot-per-write apply path (simple; heavy at volume) vs. build the
   lighter **append + revert-by-session** now. *Recommend: reuse the apply path for v0.2; note
   revert-by-session as a v0.3 optimization for the gatherer.*
3. **Free-write in scope?** AC-PRM-E is really the *append-only* additive tier. *Recommend: ship
   append-only + review-required-stages in the demonstrator; free-write as a one-line dispatch branch + a
   test, no special UI.*
4. **Display/UI now or later?** AC-PRM-E is about *governance* (policy/audit/reversibility), not display.
   *Recommend: the demonstrator skips the fancy UI; AI values are attributed in the existing field
   rendering; the "revert AI writes" control is full-R11.*

## 8. Recommendation (fold-in vs. fast-follow)

- The **minimal demonstrator (~3 PRs)** is the smallest honest slice that lets AC-PRM-E graduate *with* the
  data-floor in one wave.
- But it is **net-new write surface** — the first AI-write path into the store — a real trust-surface step,
  versus the data-floor / EX-H7 / AC-PRM-H which are **done and tested**.
- So unless AC-PRM-E must ride the *same* graduation PR, **splitting is lower-risk**: graduate the three
  ready items now, build R11 (even just the minimal demonstrator), and graduate AC-PRM-E in a fast-follow.
  PNT's roadmap already allows AC-PRM-E to "await PRM" and land in **v0.2.x**.

## 9. Tests (when built)

`append_field_value` is non-destructive (a manual value survives an AI append); `write_field_value` on a
`review-required` field stages a proposal and **does not** write (the structural INV-11 test); append-only
appends + stamps `ai:<session>`; the per-session quota refuses past the cap (INV-13); a sealed-tier field
still seals an AI-written value (the floor holds on the read side); human-wins display; revert removes the
AI rows. A deferred capability gets a red/skipped test with an explicit marker, never a silent gap.
