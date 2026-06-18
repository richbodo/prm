# The local-daemon trust surface and the limits of "local = safe" → `INV-1` · `AC-2` (generalize) · the Harden flow

*Design note · daemon / security / local-only · drafted 2026-06-18. Status: **draft** (analysis settled;
the upstream remedy is a live proposal — see "Upstream direction"). Informs **INV-1**; extends
[`mcp-cannot-identify-the-consuming-llm.md`](mcp-cannot-identify-the-consuming-llm.md) from the cloud
consumer to the local one; feeds PNT (`AC-2`, the Harden flow, the distribution axis).*

## The question

PRM's workspace is a `vanilla-js-spa` served by a local Python daemon ([`daemon/server.py`](../../daemon/server.py),
loopback `http.server`) over a `native-sqlite-via-filesystem` store. That shell escapes the constraint
that bit the browser-only reference design: the **File System Access API** (`showDirectoryPicker` /
`showSaveFilePicker` / writable streams) is Chromium-only, so a pure-browser app cannot write back to a
chosen path in Firefox/Safari — only read a picked file and push a download. PRM sidesteps this by
**demoting the browser to a renderer** and moving all filesystem authority into the Python daemon, which
has ordinary OS-level access; the browser pre-flights uploads and triggers downloads, but persistence is
a POST to loopback that Python writes.

That is a real win. But the shell is not free — it **swaps the browser sandbox for the OS process
boundary**. This note records what that costs, separates the costs that are PRM-local from the ones that
are upstream (PNT) architectural facts, and arrives at the finding that frames the rest: in the era the
PNT Preamble already names — *"OS-level AI agents become the way people operate their devices"* —
**"local = safe" no longer holds**, and a PNA must be built for that.

Five findings (A–E), then the synthesis.

---

## Finding A — Browser-sandboxed storage and an MCP/live-mirror surface are coupled; the bridge has a stated cost

The reason PRM is daemon + native SQLite and not a pure-browser app is **multi-process sharing**: an MCP
server (a separate OS process) and a future CardDAV mirror tool must read the *same* store the workspace
writes. A browser sandbox (OPFS / IndexedDB) cannot share its store with sibling OS processes, so it
cannot satisfy `mcp-exposure ≥ shared` or a live mirror without a bridge.

The first reference design tests this precisely. `fellows_local_db` is `opfs-sqlite-wasm` storage **and**
`shared+private+comms` MCP — seemingly a counterexample. It is not: its MCP servers never read OPFS. They
open a **real on-disk SQLite file `mode=ro`**, put there by one of two bridges — (1) a Chromium-only
**File System Access "folder mode"** in which the OPFS worker `sqlite3_serialize`s the DB out to a real
file on every committed mutation (which **dissolves the sandbox** — the file is now readable by any
same-host tool), or (2) a **manual export snapshot**, a stale copy the user hand-places. fellows files
this honestly as `CST-PWA-SANDBOX-SEALED` ("solved-on-chromium; off-folder bounded to shared + manual
export") with `CST-PWA-PRIVATE-SNAPSHOT` still an **open** frontier.

So the claim is **not** "browser storage is incompatible with MCP." It is: *browser storage is compatible
with an MCP / live-mirror surface only across a deliberate bridge with a platform cost* — either the
storage substrate is already a real filesystem file (`native-sqlite-via-filesystem`, which **pays the
cost up front and unconditionally** — PRM's pick), or you add a platform-gated serialize-out
(browser + folder, Chromium-only), or you accept a stale copy. The corollary that drives Finding C:
**the act of crossing that bridge to enable MCP is the same act as exposing the store to every other
local tool.** There is no version where the MCP server can read it but a sibling process cannot.

**Upstream:** PNT documents axes as independent. This coupling — storage substrate × `mcp-exposure` ×
live-mirror ingestion — is unstated. It belongs as an axis-composition note plus a platform-ceiling
constraint (a sandboxed store cannot be shared with sibling processes without a costed bridge).

## Finding B — The loopback daemon is governed by no AC; AC-2's principle should generalize

PNT's `AC-2` is the Never-SaaS enforcement: *"the server, when present, MUST be a delivery channel, not a
service … MUST NOT expose per-user RW endpoints, MUST NOT persist private data, MUST NOT operate
cross-device sync."* But `AC-2` is **flavor-derived** — triggered by `distribution: web-bundle-*` — and
it literally forbids RW endpoints. PRM is `never-distributed-single-user` with a local **RW** daemon, so
`AC-2` **neither fires for PRM nor fits it** (a single-user local workspace legitimately needs RW
endpoints and to persist private data).

Yet `AC-2`'s *intent* — "a server the PNA stands up must not become an ungoverned data surface" — applies
to the loopback daemon directly. The web-bundle delivery server and the native loopback daemon are two
**realizations of one principle**, not two unrelated things. The gap is that the principle is currently
written only in the web-bundle realization.

**Upstream:** generalize `AC-2` into a universal rule — *any same-host-reachable surface a PNA opens over
private data MUST be minimized and bound to the user's own session (loopback-bound, authenticated), so it
discloses nothing to an unauthorized same-host reader* — with flavor-specific realizations (web-bundle
delivery server = existing AC-2; native loopback daemon = new; dissolved-folder file = the fellows case).
PRM is the loopback-daemon demonstrator.

## Finding C — The trust boundary moved to the OS; the surface decomposes into three

Choosing this shell relocates the adversary model from the browser sandbox to the **OS process / user
boundary**. The single phrase "the daemon's trust surface" actually covers **three distinct surfaces**,
each with a different correct home. Conflating them is the error; separating them is the contribution.

**Surface 1 — the app's own UI transport** (browser ↔ daemon), exposing private rows only to its own
workspace session. Today this is **unauthenticated**: [`daemon/server.py`](../../daemon/server.py) does
no `Origin`/`Host`/auth check, and `--host` is overridable and unvalidated (so `--host 0.0.0.0` binds
globally). This is **not an exception** — loopback is local, no private row leaves the device, the app is
talking to itself, and an "exception" that relaxes no named guarantee is, by PNT's `Relaxes:` discipline,
not an exception. But the *unauthenticated* status is a genuine **defect**: it widens reach beyond the OS
file-permission baseline to **any** same-host process — including sandboxed or low-privilege ones that
could *not* read `relationships.db` or `ptrace` the daemon. The fix is intrinsic and checkable:
authenticate the transport to its session (a per-launch token the served page holds), guard `Origin`/
`Host` (DNS-rebinding defense), and pin to loopback. Once authenticated, the surface discloses nothing to
a third party and **`pna-active` stays true** — there is nothing to signal.

**Surface 2 — a surface returning private rows to a consumer the app cannot constrain or identify** (the
MCP private surface; or a deliberate "let other local tools read my contacts" feature). The app cannot
tell a trustworthy local model from a cloud-backed local agent that pipes what it reads to a provider —
the **same epistemic limit** established for the cloud consumer in
[`mcp-cannot-identify-the-consuming-llm.md`](mcp-cannot-identify-the-consuming-llm.md), now extended to
the *local* consumer. "Locally hosted" is no longer a safe proxy for "trustworthy." This is the surface
that warrants the **consent + persistent "not a PNA" signal** handler (the `EX-CLOUD-LLM` shape,
generalized). It triggers on **private-row reachability by an unconstrainable consumer** — *not* on "a
port exists"; a read-only shared surface, or Surface 1 once authenticated, exposes no private rows to an
unconstrained reader and does not trigger it.

**Surface 3 — out-of-band reads the app never mediates** (a same-UID OS-automation agent reads
`relationships.db` directly, `ptrace`s the process, or drives the real workspace UI). The app **cannot**
gate this — authentication cannot stop a same-UID reader of same-UID files, and no banner changes what an
agent driving the UI can see. This is the **Harden flow's** domain (advisory: sandbox the agent, separate
OS user, disk encryption) plus a **platform-ceiling constraint**. It must be honestly *disclosed*, not
falsely *gated* — but it is elevated here from a footnote to a first-class concern because it is the
growth area.

**One consequence to state plainly:** the data-floor / sealing (`AC-MCP-C`, the `disclosure_tier`) bounds
the **cloud MCP surface only**. PRM's own daemon does **not** apply `strip_sealed` —
[`core/projection.py`](../../core/projection.py) returns sealed fields to the local workspace by design
(the user must see their own data) — and Surface 3 bypasses the app entirely. "Sealed" is a *cloud-LLM*
guarantee, never a local-reader guarantee; the spec and our docs must say so, so it is not mis-read.

## Finding D — The shell choice fixes the verifiability class

`daemon + source` lands PRM at the independently-verifiable end of the distribution spectrum (build from
source, run `pytest` + `just conformance` before trusting it — the "home-cooked meal"), but it is heavy
to distribute (needs Python + the daemon). A single-file browser app would be trivially distributable but
harder to verify *and*, per Finding A, MCP-incompatible without the bridge. So `workspace-shell` ×
`storage` **co-determine** where a design lands on the distribution / verifiability spectrum — which
reinforces the open finding that PNT's distribution axis is too coarse
([roadmap](../roadmap.md) parallel track; PNT #39 ↔ prm #8).

## Finding E — Server-side authority makes invariants structural

Putting the canonical writer, the file-lock, the projection/seal, and the enforcement of INV-2 (one
ingest path) and INV-3 (definitions workspace-only) in **Python** means they are enforced *structurally*
— a foreign key, an absent MCP tool, a held lock — not in inspectable, bypassable client JS. A
browser-only shell would have to enforce these in client code, which cannot be a structural lock. "The
authority lives in the local server; the SPA is a thin client" is therefore a *positive* property of this
shell worth carrying upstream as workspace-shell guidance.

---

## Synthesis — design for tomorrow's attack surface

The findings converge on one shift. The classical PNA threat model put the adversary **off-device**
(SaaS, the network, cloud LLMs) and trusted the local host; the defense was "stay local + loopback +
consent-gate the one sanctioned off-device path (`EX-CLOUD-LLM`)." The host is no longer trusted: users
increasingly run **OS-level automation agents** — computer-using agents, desktop MCP agents — that run as
the same OS user with broad filesystem, UI, and localhost access, and that are frequently cloud-backed,
so data they read locally leaves the device anyway. A local-only PNA on such a host is *effectively* not
local-only.

A PNA meets its goals in that world not by pretending it can seal a host it does not own, but by the
three-surface discipline above:

1. **Tighten what the app controls** (Surfaces 1 & 2) into *checkable* obligations — authenticate and
   minimize the app's own surfaces; consent-gate and persistently signal any surface that hands private
   rows to a consumer it cannot identify. Make these ACs + a lint, not prose.
2. **Honestly disclose what it cannot control** (Surface 3) via the Harden flow and a platform-ceiling
   constraint — elevated, not buried.
3. **Never hold the boundary by trying to identify or trust the consumer.** This is the through-line from
   [`mcp-cannot-identify-the-consuming-llm.md`](mcp-cannot-identify-the-consuming-llm.md): you cannot
   identify the cloud LLM, and you equally cannot identify whether a *local* consumer is a cloud-backed
   agent. The boundary is held by consent + honest signaling, never by client identification.

Reserving the "not a PNA" signal for Surface 2's real trigger keeps it **rare and therefore meaningful** —
the predicate-honesty the `pna-active` bit exists to protect. Designing for the surface of *yesterday*
would gate the network hop and wave through the localhost hop; designing for *tomorrow* recognizes they
are the same disclosure arriving by a different route.

## Upstream direction (proposed — under deliberation)

- **A:** an axis-composition note in `spec/axes.md` + a platform-ceiling constraint in `spec/constraints.md`
  (sandboxed store ⇒ no sibling-process sharing without a costed bridge). *Toolkit-fix (clarifies a ceiling).*
- **B:** generalize `AC-2` into a universal "no ungoverned app-opened surface over private data" rule with
  flavor-specific realizations; PRM demonstrates the loopback-daemon one. *New obligation — lands with the
  PRM authentication/loopback-pin code.*
- **C:** clarify that the data-floor (`AC-MCP-C`) bounds the cloud surface only (Surface 1/3 are out of its
  scope); strengthen the Harden-flow countermeasure library + a `CST-*` for same-UID readability (Surface
  3); a lint that flags any app-opened surface returning private rows that is not authenticated /
  minimized / data-floored — "checked, not asserted." Scope any not-a-PNA exception strictly to **Surface
  2** (private-row reachability by an unconstrainable consumer), never to the existence of a port.
- **D:** feed PNT #39 with "the shell choice fixes the verifiability class." *Discussion; axis split stays
  deferred to the installer.*
- **E:** a workspace-shell recommended-approach note (server-side authority ⇒ structural invariant
  enforcement). *Guidance.*

## What would change this

- A durable, cross-platform, sandboxed-but-shareable private store (fellows' open `CST-PWA-PRIVATE-SNAPSHOT`
  frontier) would dissolve Finding A's cost.
- An OS capability that authenticates same-host callers, or per-process data ACLs the app could set, would
  shrink Surface 3 into Surface 1 — making the residue gateable rather than merely disclosable.
- If PNT declines to generalize `AC-2`, Finding B stays a PRM-local hardening with no upstream obligation.

## Sources

- `fellows_local_db` — `reference_designs/fellows_local_db/Architecture.md` (`CST-PWA-SANDBOX-SEALED`,
  `CST-PWA-PRIVATE-SNAPSHOT`); its `mcp_servers/private_data_ops.py` (`mode=ro` over an on-disk file) and
  folder-mode serialize-out (`app/static/vendor/sqlite-worker.js`).
- PNT spec — `spec/PNA_Spec.md` (Goals; the "OS-level AI agents" Preamble; AC-MCP-A; the `pna-active`
  interop bit), `spec/axes.md` (`AC-2`; MCP-exposure picks), `spec/exceptions.md` (`EX-CLOUD-LLM`; the
  handler contract; the Harden flow / environmental-threat taxonomy), `spec/constraints.md`.
- PRM — [`daemon/server.py`](../../daemon/server.py) (loopback bind; no Origin/auth check; `--host`
  override), [`core/projection.py`](../../core/projection.py) (`strip_sealed` is MCP-only; the daemon sees
  everything), [`mcp-cannot-identify-the-consuming-llm.md`](mcp-cannot-identify-the-consuming-llm.md),
  [`../roadmap.md`](../roadmap.md) (parallel track; distribution semantics).
