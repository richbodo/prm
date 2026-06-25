# Plan — External Access visibility: access log + "Current Access Exceptions" UI (+ idea triage)

*Status: **draft for triage** (2026-06-25). Companion to
[`ex-cloud-llm-workspace-handler.md`](ex-cloud-llm-workspace-handler.md) (the EX-CLOUD-LLM handler this
extends), [prm#65](https://github.com/richbodo/prm/issues/65) (server build-label / staleness). Born from
live P4 QA on 2026-06-25, where a sticky approval + a stale-server red herring made it impossible to tell,
from the UI alone, **what access an external system actually had and whether it had been used.***

## 0. Principle & reframe

**Principle:** the user must always know — *in detail* — what access external systems have to their data.
That is the load-bearing goal; the rest is mechanics.

**Reframe (the key idea):** the External Access page should distinguish two layers:
- **Baseline** — the *default* access posture for any external system: the **data-floor** (sealed never
  crosses; shareable crosses only with consent) + the per-database enforcement. This is what's true before
  any grant.
- **Exceptions** — the grants layered on *top* of baseline: the active disclosure **mode** (local-ai /
  cloud-exception) and the per-contact **review approvals**. Every exception is a deviation from local-only.

So the page reorders to **baseline above, exceptions below**, and the "Current access" card becomes an
itemized **"Current Access Exceptions"** list. (This *is* PNT's **EX-H4 active-set explainer**, taken down
to the approval grain — a stronger upstream demonstrator.)

## 1. What PRM can and cannot observe (this drives every decision below)

| Question | Answer | Why |
| --- | --- | --- |
| Did a read **happen** on our surface? | **Yes** | The read is served *by PRM's own MCP server* (`tools.get_contact`). We log at the serve point. |
| What **code** served it? | **Yes** | Stamp `build_label()` on each log line — also closes the staleness gap (#65). |
| Can we enforce **one read then stop**? | **Yes** | Serve → log → auto-revoke, all inside our server. No external help needed. |
| Which **client app** is connected? | **Weakly** | `clientInfo` (self-reported, untrusted) + our server's **parent process** (`getppid` → the client that spawned us). Both name the *client*, not the model. |
| Which **LLM** consumed the output? | **No** | We are the **stdio** server, not a TLS party. The TLS that matters runs *client → provider*, off our box. Even a TLS/mutual-cert scheme would identify the *client app*, not the model. (See [`../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`](../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md).) |
| What happens to data **after** it crosses? | **No** | Off-box and irreversible — the standing EX-H "data already sent = none". |

**Takeaway for the TLS intuition:** identifying the *consumer* cryptographically isn't reachable from PRM's
position — but the practical goals you actually want (know a granted read **occurred**, and **shut the
spigot** after a one-time read) don't require it. They're answerable because **we serve the read ourselves.**
That single fact is what makes Phases 1–2 tractable and pushes "who/what consumed it" into a research issue
(**[#67](https://github.com/richbodo/prm/issues/67)**) — which reframes usefully: we can't ID the *LLM*, but
identifying the *client app* + emitting structured access events gives the user (who owns the host) the
**PRM-side half of egress monitoring** — correlate "data left to client Y" with host network telemetry
(OpenTelemetry / eBPF).

## 2. Phase 1 — Access logging *(implement first — greenlit)*

The write side is already audited (`audit.log.jsonl`: `disclosure_mode` / `disclosure_review` /
`disclosure_approve_read` / `disclosure_deny_read`, under the lock). **The gap is the read side.**

- **`core/access_log.py`** (leaf; no `cli` import) — append-only JSONL at `home/mcp_access.log.jsonl`,
  written by the **MCP server** on every read it serves (same "MCP-server writes a file in the home" pattern
  P4 already uses for staged requests). One line per `get_contact` / `get_provenance`:
  ```json
  {"ts":"…Z","tool":"get_contact","contact_id":"0ae6…","mode":"local-ai",
   "review_on":true,"approved":true,"decision":"released",
   "returned_overlay":["project"],"withheld_overlay":[],"build":"2026-06-25-632318361f"}
  ```
  **Field *names* only — never values** (INV-1). `decision ∈ {released, withheld, sealed-floored, no-overlay}`.
- **Hook point:** `mcp_servers/tools.py` `_review_gate` / `get_contact` — where the decision is known.
- **Readers:** `access_log.recent(home, n)`, `access_log.last_read(home, contact_id)` (pure, read-only).
- **Surface it:** `GET /api/connections` (or `/api/disclosure`) returns a last-read summary (per contact +
  most-recent) so the workspace can show "last AI read … · build …".
- **Build label answers staleness:** because each line carries `build`, "was the server stale?" is now a
  log lookup, not `ps` archaeology — satisfies #65 acceptance (the build label is *visible*).
- **Retention (decided):** rotate/cap the log like the **snapshot ring** — bounded (ISO-stamped segments or
  a max line count), oldest pruned, so it can't grow unbounded.
- **No approvals-shape change needed:** "approved when" is already in `audit.log.jsonl`; "last read when"
  comes from the new access log; the live approval *set* is `disclosure.approvals()`. The exceptions list
  (Phase 2) joins these three.
- **Tests:** a served read appends one line with the right `decision` + `build`; withheld vs released vs
  sealed-floored each produce the expected line; reader returns newest-first.

## 3. Phase 2 — External Access UI *(makes manual testing legible + users aware — the stated goal)*

**3a. Collapsible cards.** Every card becomes expand/contract (native `<details>`/`<summary>` — accessible,
near-zero JS). Recommend defaults: **Current Access Exceptions** and **Pending requests** open; the rest
(two-databases, what's-enforced, data-floor, EX-CLOUD-LLM profile, connections) collapsed with a one-line
summary in the `<summary>` (e.g. data-floor → "2 shareable fields · the rest sealed").

**3b. Reorder — baseline above exceptions:**
1. Your two databases *(context)*
2. What's enforced for each *(baseline)*
3. **The data-floor** *(baseline — the default access floor)* ← moved up
4. **Current Access Exceptions** *(the grants on top)* ← renamed + moved down
5. Pending AI-read requests *(when any)*
6. Connecting a cloud AI — EX-CLOUD-LLM strength profile *(reference; collapsed)*
7. Connections *(now shows last-read from the Phase-1 log)*

**Section dividers (decided):** add **"Baseline"** (cards 1–3) and **"Exceptions"** (cards 4–5) headers to
make the two-layer model explicit; cards 6–7 are a "Reference" tail.

**3c. "Current Access Exceptions" — the itemized list** (renamed from "Current access"). One row per active
exception, so the user sees exactly what's granted:
- **Mode grant** (mode ≠ pna): "Cloud AI — can read N shareable fields across your contacts · granted
  {date} · {review on → *each read needs your approval* | review off → *all shareable fields readable
  without per-read approval*}."
- **Per-contact approvals** (review on): one row each — **{Contact} · {fields} · approved {when}** *(audit
  log)* **· last read {when}** *(access log)* · **[Revoke]** *(§3e)*. Approve five contacts → five rows.
  Collapse to a count with expand when long.
- **Network exposure** (daemon non-loopback): a row — "Your contacts are reachable on your network."
- **Empty state** (pna, no approvals): "No exceptions — local-only. Nothing external has access beyond the
  baseline."
- This card = the **EX-H4 active-set explainer** at approval grain; each exception can link to its strength
  profile.

**3d. Data:** resolve approval ids → names + shareable field names (projection/schema); join `approvals()`
(set) × `audit.log` (approved-when) × `access_log` (last-read). Docs: refresh the users-guide External
Access section.

**3e. Per-item Revoke** *(decision §6.1)*. A small **Revoke** control on each per-contact approval row that
removes *just that contact* from the approvals set — the inverse of `approve_read` — versus the existing
**Return to PNA mode**, which clears *all* approvals and drops the mode. It does **not** change approval
*granularity* (the sticky-vs-one-time question, §4): it only lets the user **undo one grant they can now
see**. Lock + audit like the other disclosure writes (`apply.revoke_read`). Pure visibility-and-control; the
natural companion to *showing* the exceptions.

## 4. Phase 3 — Triage of the bigger ideas *(decide now-vs-later together)*

| Idea | Reachable? | Effort | Recommendation |
| --- | --- | --- | --- |
| **Read-decision logging** (+ build) | Yes (we serve reads) | S | **Now** — Phase 1 |
| **Current Access Exceptions list + collapsible UI** | Yes | M | **Now** — Phase 2 |
| **Detect a granted read was *executed*** | Yes (the log *is* this) | — | **Now** — falls out of Phase 1 |
| **One-time vs sticky approval modes** (keep *both*) | Yes (serve → log → auto-revoke) | M | **Later → [#66](https://github.com/richbodo/prm/issues/66).** Not mid-test; Phase-1 log makes it cheap. Per-grant choice at approve time: *approve once* vs *approve for this session.* |
| **Per-item Revoke control** | Yes | S | **In Phase 2 (§3e)** — pure visibility+control, doesn't change *granularity*; complements the all-clearing "Return to PNA". |
| **Identify the _client_** (not the consumer LLM) → **egress monitoring** | Client: **yes** (parent-process + `clientInfo`); LLM: **no** | — | **Research → [#67](https://github.com/richbodo/prm/issues/67).** Identify the client + emit structured access events → the user (owns the host) correlates with host egress telemetry (OTel / eBPF). The PRM-side half of egress monitoring. |
| **What the consumer does off-box** | **No** | — | **Document** as a standing limit (already EX-H "data already sent = none"). |

## 5. Sequencing vs testing & upstream

1. **Now:** Phase 1 logging — unblocks *diagnosable* manual testing immediately.
2. **Next:** Phase 2 UI — the visibility goal; makes manual P4 testing legible and users fully aware.
3. **After this testing / alongside upstream:** Phase 3 one-time-mode + the identification research issue;
   feed the richer EX-H4 explainer and the #65 build-label upstream to PNT (strengthens the EX-CLOUD-LLM /
   AC-MCP-C demonstrator).

## 6. Decisions (resolved 2026-06-25)

- **Per-item Revoke** → **in Phase 2** (§3e).
- **Section dividers** → **yes** — "Baseline" / "Exceptions" headers (§3b).
- **Access-log retention** → **rotate/cap like the snapshot ring** (§2).
- **One-time vs sticky modes** → **later**, keep both — tracked in **[#66](https://github.com/richbodo/prm/issues/66)**.
- **Client identity / egress** → reframed from "identify the consumer" to "identify the *client* + feed host
  egress monitoring" — tracked in **[#67](https://github.com/richbodo/prm/issues/67)** (research/follow-on).

With these resolved, **Phases 1–2 are ready to build**; the deferred items live in #66 / #67.
```
