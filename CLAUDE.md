# CLAUDE.md

Personal Relationship Manager (PRM) — a **local-only** Personal Network Application (PNA): it mirrors
a person's contacts from everywhere they're scattered (Google, Apple, LinkedIn, Facebook, loose
`.vcf`/`.csv`) into one local store they own, lets them keep private relationship data on top, and
lets an AI read and help maintain that data on their behalf. **Contact data never leaves the device.**
PRM is a **reference design** for the [Personal Network Toolkit](https://github.com/richbodo/personal_network_toolkit)
(PNT, the sibling spec repo); this is an **application**, not a spec.

**Read in this order:** [`README.md`](README.md) →
[`docs/users-guide.md`](docs/users-guide.md) (install + how-to; the user-facing source of truth) →
[`docs/prm-feature-spec.md`](docs/prm-feature-spec.md) (what the product is) →
[`docs/roadmap.md`](docs/roadmap.md) (version-by-version scope) →
[`plans/v0.1-implementation-plan.md`](plans/v0.1-implementation-plan.md) (how v0.1 is built).
Install with `pip install -e ".[dev]"` and run `pytest` before pushing.

## Sibling repositories (cross-repo work)

The PNA Toolkit spec repo and the other reference design are separate Git repos checked out as **siblings of this one**, one directory above the repo root:

- `../personal_network_toolkit` — the PNA Toolkit (PNT): the universal spec, contracts, and lints this design attests conformance to. Its `origin/main` is the source of truth for upstream work.
- `../fellows_local_db` — the first reference design (Directory Archive), for cross-design comparison.

A change here can have cross-repo implications for PNT (a conformance or contract change); when in doubt, read PNT's `spec/`. This sibling layout is a stable convention of the working environment (it could change, but holds for now). It lives in CLAUDE.md — not in agent memory — because memory is keyed to the working directory, so a worktree at a different path starts with a fresh memory dir; a committed file is the only channel that reaches every worktree and every concurrent agent.

## Keep the docs current

[`docs/users-guide.md`](docs/users-guide.md) is the user-facing source of truth, and it carefully
marks **what works today vs. what's coming**. Any change that alters a behavior a user would notice —
a new parser/source, a CLI flag, what `--dry-run` does, the demo flow, or moving a capability from
"⏳ next" to "✅ works" — **MUST update `docs/users-guide.md` (and its capability table) in the same
change.** The status markers are a promise to the reader; keep them honest. This rule exists because
docs like these drift behind the code the moment a change skips them.

## Documentation map — one source of truth per fact

Restating a fact in a second doc creates a drift surface. Each doc *owns* a category and the others
*link* to it rather than re-explaining:

- **`docs/prm-feature-spec.md`** — what the product is and which PNT use case / contracts it realizes.
  Owns *the product definition*.
- **`docs/roadmap.md`** — version-by-version scope and what's deferred. Owns *what's coming, and when*.
- **`plans/v0.1-implementation-plan.md`** — how the current version is built (architecture, invariants,
  module seams). Owns *implementation detail*.
- **`docs/design-notes/`** — design *rationale*: why a decision was made, the constraint that forced it,
  alternatives rejected (e.g. the LLM/MCP trust boundary and `EX-CLOUD-LLM`). Owns *the reasoning behind
  decisions* — distinct from the roadmap, which owns *what's coming*.
- **`docs/users-guide.md`** — task-ordered how-to for users, plus the "what works today" status table.
  Owns *what a user does, step by step*. Links to the spec / roadmap for definitions; does not restate them.
- **`docs/reference/`** — third-party *source* format references (not PRM's own schemas), and
  `source_validation_status.md`. Owns *external format facts*.
- **`README.md`** — one-paragraph orientation + the doc index. Links out; owns nothing else.

When you add a fact, put it in the doc that owns its category and link from the others.

## Conventions

- **Python 3.10+.** Runtime deps are kept tiny and deliberate — today just `vobjectx` (the vCard lexer;
  the older `vobject` is an accepted drop-in fallback). Don't add a runtime dependency without a reason
  recorded in `pyproject.toml` and the implementation plan. `pytest` is dev-only (`.[dev]`).
- **Local-only is the core invariant — INV-1.** In **PNA mode**, contact and user-authored data stay on
  the device; PRM is *Never-SaaS* (any server is a delivery channel, not a service). The one sanctioned
  way data leaves the device is the named **`EX-CLOUD-LLM`** exception — connecting a cloud MCP client
  like Claude Desktop — and only when handled to PNT's exception contract: explicit consent *before* the
  raise, a persistent "not a PNA right now" signal while active, and a reversible return-to-PNA-mode path
  (mode only — it cannot recall data already sent). See PNT
  [`spec/exceptions.md`](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/exceptions.md)
  (`EX-CLOUD-LLM`, handler clauses EX-H1–H8). **An MCP server cannot detect or restrict which LLM consumes
  its output** — `clientInfo` is self-reported and app-level, and MCP's OAuth runs client→server — so the
  boundary is held by consent + honest signaling, *never* by trying to identify the client. Any *other*
  off-box path (telemetry, cloud sync, a new network call) is a design change to flag explicitly, not a feature.
- **The ingestor is the only writer of the raw Shared DB (INV-2).** Read/search paths read it; they don't
  mutate it.
- **Tests pin behavior** — run `pytest` after changes; put manual test/QA steps in the **PR description**.
- **Default posture: orient without moving; branch only to work.** Reading or priming never needs a
  branch change — don't `git checkout main` / `git pull` just to get oriented (in a multi-worktree
  setup `main` is often checked out elsewhere, so the checkout fails or strands uncommitted work).
  Create a worktree (next bullet) when you start *actual work*, and run `git worktree list` first to
  spot a sibling already on a related branch.
- **Multiple agents on one host → one git worktree each.** Give every concurrent Claude Code / agent its
  own worktree so a `git checkout` in one can't yank the branch (or uncommitted work) out from under
  another: `git worktree add ../prm-wt-<branch> -b <branch>`, then `just setup` in it (fresh `.venv`), or
  symlink `.venv` / `prm-data/` in to share them. Worktrees isolate the filesystem, **not** the workspace
  port **8770** — edits and non-server tests run in parallel, but `just serve` and any server-based run
  must be **serialized** across worktrees (`just doctor` flags 8770 in use; `just port` *frees* it, which
  kills whatever holds it — including a sibling's server — so coordinate). If you share `prm-data/`,
  don't `just clean-data` / re-ingest while a sibling is serving. `git worktree remove ../prm-wt-<branch>`
  when done.

## Fail loudly — no silent gaps

- **Convert an absent guarantee into a red test, never a silent pass.** A parser that can't yet handle a
  field should have a test that pins the current behavior, not a quiet skip that reads as "handled."
- **Deferrals are honest** — an explicit status marker (the users-guide "⏳ next", a roadmap "deferred"
  note, a documented `--dry-run`), never a bare `TODO` that *claims* a capability the code doesn't deliver.
- **Validate sources against real exports.** [`docs/reference/source_validation_status.md`](docs/reference/source_validation_status.md)
  tracks which export formats were validated against a real-world export versus built from documentation
  alone. When you add or change a parser, update that status — don't let a doc-only fixture masquerade as
  validated.
