# Plan — EX-H7 server-side cloud-LLM consent handshake (v0.1 down-payment)

*Status: **implemented** (2026-06-08). Companion to [`../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`](../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md),
[`v0.1-implementation-plan.md`](v0.1-implementation-plan.md) §6, and [`../docs/roadmap.md`](../docs/roadmap.md) (v0.2 `EX-CLOUD-LLM` handler).*

## 1. The gap

PRM's MCP read surface already returns contact **PII** to whatever client connects, and `dedup_ops`
returns it too (cluster members, contact fields). Our own design note already commits to surfacing the
cloud-LLM boundary to the consuming client *at the protocol level*:

> Best-effort consent propagation to the human via the MCP `instructions` handshake (EX-H7) when an
> agent, not the person, is the immediate caller.
> — [`mcp-cannot-identify-the-consuming-llm.md`](../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md):47

But the servers don't do it. Both are built with a bare constructor — `FastMCP("prm-shared-data-ops")`
([`shared_data_ops.py`](../mcp_servers/shared_data_ops.py):24) and `FastMCP("prm-dedup-ops")`
([`dedup_ops.py`](../mcp_servers/dedup_ops.py):24) — with **no `instructions=`**. So PRM's
"local AI recommended" posture lives only in `mcp_servers/README.md` prose; it never travels to the AI
client that's about to send the user's contacts to a cloud model.

The sibling reference design **fellows_local_db** already ships exactly this: a
`CLOUD_LLM_PROPAGATION_NOTICE` passed as `instructions=` on every data-returning server
(`mcp_servers/private_data_ops.py`), pinned by `test_instructions_carry_cloud_llm_propagation_notice`,
and attested in its `Architecture.md` exception table as *"EX-H7 surfaced best-effort via the MCP
`instructions` handshake."*

This is the **server-side half** of the `EX-CLOUD-LLM` handler. It is cheap, has no workspace UI, and is
squarely in v0.1 scope. The **workspace half** (consent gate before connect, persistent "not a PNA"
banner, return-to-PNA-mode control — EX-H2/H3/H4/H5) stays **v0.2**, as the roadmap already says. This
plan ships only what the v0.1 surface owes today and makes a stated-but-unenforced posture actually
reach the client.

## 2. Why `instructions` is the right v0.1 mechanism (and its honest limits)

- **It cannot enforce anything.** Per the design note, an MCP server cannot detect or restrict which LLM
  consumes its output; `instructions` is a *cooperating-client* hint, nothing more. That is precisely
  what EX-H7 asks for: *best-effort* propagation, "hard refuse" being a v0.2+ private-surface concern.
- **It is honest signaling, not detection.** It tells a cloud agent acting on a human's behalf: *get the
  human's explicit consent first; don't treat your own invocation as their consent; prefer a local
  model.* No client identification, consistent with the design note's whole thesis.
- **EX-H8 framing carries over unchanged:** the guarantee is about the *boundary* (consent, signaling),
  never the data once it crosses. The notice claims nothing it can't back.

## 3. Scope

**In:**
1. A single shared notice constant in a **pure** module (no `mcp` SDK import) so the default test suite
   can assert on it without the isolated venv.
2. Wire it as `instructions=` on **both** `shared_data_ops` and `dedup_ops` server builders.
3. Tests: assert the notice content (SDK-free) **and** assert it's actually wired into each built server
   (SDK-guarded, skips cleanly when `mcp` isn't installed — matching how `tests/unit/test_mcp_tools.py`
   stays SDK-free today).
4. Doc updates forced by the change (per CLAUDE.md "keep the docs current").

**Out (explicit non-goals for this change):**
- The **workspace EX-CLOUD-LLM handler** — consent gate, "not a PNA" banner, explainer page,
  return-to-PNA-mode control (EX-H2–H5). Stays **v0.2** (roadmap + design note §Consequences).
- **`.mcpb` one-click packaging.** fellows ships Desktop Extension bundles for its end-user PWA;
  PRM's flavor is `never-distributed-single-user` / build-from-source-and-verify. Adding `.mcpb` now
  bends toward the opaque-binary end PRM deliberately avoids and is gated on the deferred-installer
  question. Recorded here as a **deferred non-goal**, not planned work.
- Per-call consent gating / hard-refuse on the private surface (AC-MCP-A enforcement, AC-MCP-C
  data-floor) — v0.2, and needs the private overlay to exist first.

## 4. Changes, file by file

### 4a. New constant — `mcp_servers/consent.py` (pure, no SDK)
A small module holding the notice so both servers and the default (SDK-free) test import one source of
truth. Pure string + module docstring; no `mcp` import, mirroring the `tools.py` "no SDK import" rule.

Draft text (adapt wording in review — keep it client-directed and honest):

```python
"""Cloud-LLM consent propagation notice (EX-H7) — surfaced to MCP clients via `instructions`.

Honest signaling, not enforcement: an MCP server cannot tell which LLM consumes its output
(docs/design-notes/mcp-cannot-identify-the-consuming-llm.md), so this is a best-effort hint a
cooperating client can relay to the human. The full EX-CLOUD-LLM handler (workspace consent gate,
"not a PNA" banner, return-to-PNA-mode) is v0.2 (docs/roadmap.md)."""

CLOUD_LLM_NOTICE = (
    "PRM is a local-only Personal Network Application: by default the user's contact data never "
    "leaves their device. These tools return that PRIVATE contact data (names, emails, phones, "
    "and the user's merge decisions). Sending it to a cloud-hosted model takes PRM out of local "
    "(PNA) mode under the EX-CLOUD-LLM exception. If you are a cloud AI acting on a person's "
    "behalf, you MUST ensure that person knows their contacts are crossing to a cloud provider and "
    "has personally consented — do NOT treat your own invocation as their consent. Prefer a local "
    "model. You can only PROPOSE merges; the human reviews and applies every change in the PRM "
    "workspace."
)
```

(The last sentence is accurate for both servers — `shared_data_ops` is read-only and `dedup_ops` is
propose-only — so one shared constant is correct.)

### 4b. Wire it in — `mcp_servers/shared_data_ops.py` and `mcp_servers/dedup_ops.py`
In each `build(home)`:
```python
from mcp_servers.consent import CLOUD_LLM_NOTICE  # top-of-file import
...
mcp = FastMCP("prm-shared-data-ops", instructions=CLOUD_LLM_NOTICE)
```
One line each; no other behavior changes.

### 4c. Tests — `tests/unit/test_mcp_consent.py` (new)
- **SDK-free** (runs in the normal suite): assert `CLOUD_LLM_NOTICE` is non-empty and contains the
  load-bearing phrases — `"EX-CLOUD-LLM"`, `"local"`, `"consent"` — so the honest signal can't be
  silently gutted.
- **SDK-guarded** (`mcp = pytest.importorskip("mcp")`): call `shared_data_ops.build(home)` and
  `dedup_ops.build(home)`, assert each server's `instructions` *is* `CLOUD_LLM_NOTICE`. This is the
  no-bypass proof that the constant is actually attached, not just defined — and it skips cleanly when
  run from the project `.venv` (no SDK), exactly like the existing MCP tests.

This satisfies the repo's "fail loudly / tests pin behavior" convention: if someone removes the
`instructions=` wiring, the guarded test fails; if the SDK is absent, it skips rather than masquerading
as passed.

## 5. Documentation updates (required by CLAUDE.md)

- **`docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`** — refine the EX-H7 bullet and the
  §Consequences note: EX-H7 best-effort `instructions` propagation is now **live in v0.1** on both
  servers; the *workspace* handler (EX-H2–H5) remains v0.2. (Today the note reads as if the whole
  handler, EX-H7 included, is v0.2.)
- **`mcp_servers/README.md`** — under "⚠️ Cloud vs local AI", add one line that both servers carry the
  `instructions` handshake telling a cooperating cloud client to get the user's consent first, with the
  honest caveat that it's best-effort and unenforceable.
- **`docs/users-guide.md`** — *likely no change*: the handshake is surfaced to the AI client, not a
  user-visible CLI/flow change, so the capability table and "Let an AI propose the merges" section stay
  accurate. Re-check at implementation time; touch only if a user-noticeable behavior actually changed.
- **Not now, but flag for M6:** when `docs/Architecture.md` is written (plan §9 / M6), the
  `EX-CLOUD-LLM` exception-attestation row should record EX-H7 as *conformant (best-effort via
  `instructions`)* and EX-H2–H5 as *deferred to v0.2*, with this test as the verification — mirroring
  fellows' attestation table. Out of scope for this change; noted so M6 picks it up.

## 6. Acceptance

- [x] `mcp_servers/consent.py` exists; pure (no `mcp` import); `CLOUD_LLM_NOTICE` defined.
- [x] Both servers construct `FastMCP(..., instructions=CLOUD_LLM_NOTICE)`.
- [x] `tests/unit/test_mcp_consent.py`: SDK-free content test passes in the project `.venv`; SDK-guarded
      wiring test passes in `mcp_servers/.venv` and skips in the project `.venv`.
- [x] `pytest` green from the project venv (88 passed, 1 skipped — the guarded test skips, not fails).
- [x] Design note + `mcp_servers/README.md` updated; users-guide re-checked (no change — the handshake
      is client-facing, not a user-visible CLI/flow change).
- [ ] Manual QA in PR description: register a server with Claude Desktop, confirm the `instructions`
      appear in the client's server card / `mcp*.log` handshake frame.

## 7. Why this is worth doing now

It closes the gap between what PRM's design note *promises* and what its servers *do*, with ~15 lines of
code + a test, and it gives PNT a **second** demonstration of EX-H7's server-side mechanism alongside
fellows — directly serving PRM's role as the `EX-CLOUD-LLM` reference demonstrator (roadmap, Parallel
track). The expensive, user-facing half stays honestly deferred to v0.2.
