# Design notes

The *why* behind PRM's decisions — the reasoning, constraints, and rejected alternatives that the
[feature spec](../prm-feature-spec.md) (*what the product is*) and the [roadmap](../roadmap.md)
(*what's coming, and when*) deliberately don't carry. A note records a decision, the constraint that
forced it, the alternatives considered, and **what would change the decision**. Notes are
append-mostly: when a decision is revisited, supersede the note (a dated update or a successor link)
rather than silently rewriting history.

This is the doc category that *owns design rationale* — see [`../../CLAUDE.md`](../../CLAUDE.md),
"Documentation map".

## LLMs & MCP

How PRM reasons about large language models and the Model Context Protocol — the trust boundary
around cloud AI, what the protocol can and can't enforce, and how PRM stays an honest PNA across it.

- [**MCP cannot identify the consuming LLM → `EX-CLOUD-LLM`**](mcp-cannot-identify-the-consuming-llm.md)
  — an MCP server can't tell whether a cloud or local model is reading its output, so PRM handles
  cloud AI with a *consented, reversible exception*, not detection or blocking.

## Security & the local boundary

How PRM's shell choice (a loopback daemon + native SQLite, not a browser-only app) reshapes the threat
model — what the shell costs, which costs are PRM-local vs upstream PNT facts, and why "local = safe" no
longer holds once OS-level automation agents share the host.

- [**The local-daemon trust surface and the limits of "local = safe"**](local-daemon-trust-surface.md)
  — the daemon shell trades the browser sandbox for the OS process boundary; the surface decomposes into
  three (the app's own UI transport, an unconstrainable private-row consumer, and out-of-band reads), each
  with a different home (intrinsic AC / consented exception / Harden flow). Captures five findings (A–E)
  and the upstream direction (generalize `AC-2`; scope the not-a-PNA signal to private-row reachability).

## Dedupe & merge

How PRM finds and merges duplicate contacts — the deterministic + AI flows, the reversible merge model,
and the stdlib detection algorithm.

- [**Contact dedupe — one pipeline, two authors, reversible by construction**](dedupe-design.md)
  — one detection core + one review/apply, with a deterministic UI (M3) and an LLM (M4) as
  interchangeable proposal authors; immutable-raw + projection makes merges reversible; field-level
  Update rides the same substrate.
- [**Bulk-approve merges — the decisions, and where PRM differs**](bulk-merge-decisions.md)
  — why batching merges (M3f) doesn't weaken data safety: the constraints respected vs interpreted, the
  genuine-vs-perceived-safety split, the skip-don't-chain overlap rule, and the contrast with destructive
  bulk-merge in mainstream contact tools.

## Relationship store & contact editing

How PRM lets the user edit contacts and author private relationship data without breaking the read-only
mirror — the seam where v0.2's overlay attaches to v0.1's immutable contacts, and how an AI may add to it
safely.

- [**Editable contacts without a writable Shared DB — the override model**](contact-edit-override-model.md)
  — why v0.2 contact edit mode (Update/Create/Delete) writes the private store and leaves `shared.db`
  immutable (INV-2 / AC-1): re-mirror stays safe, edits are reversible + audited, provenance stays
  honest; the writable-Shared-DB alternative and why it was rejected; and the one-store `relationships.db`
  rename.
- [**AI writes and gathered data — the additive overlay and the write tiers**](ai-writes-and-gathered-data.md)
  — how an AI may write the private store safely: the three write tiers (`review-required` /
  `append-only` / `free-write`) for overlay fields, and — for delegated gathering (v0.3) — an additive
  **observation** layer the AI files and the user **promotes** into the view, so a canonical contact field
  is never AI-written directly. Reuses the dedup/override reversible projection; one-deep retention; the
  rejected "gathered-as-a-synthetic-source" alternative.
