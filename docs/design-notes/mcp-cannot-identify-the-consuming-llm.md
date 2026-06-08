# MCP cannot identify the consuming LLM → `EX-CLOUD-LLM`

*Design note · LLMs & MCP · drafted 2026-06-04. Status: accepted. Informs **INV-1** and the v0.2
`EX-CLOUD-LLM` handler (see [`../roadmap.md`](../roadmap.md)).*

## The question

PRM is local-only (**INV-1**): contact and user-authored data stay on the device. But the roadmap
connects PRM's MCP servers to a desktop AI client — and the obvious target is **Claude Desktop**,
backed by a cloud model. If the user points a *cloud* LLM at PRM's MCP surface, their data leaves the
device. Can PRM **detect** that — distinguish a cloud LLM from a local one — and restrict or block it,
so local-only is *enforced* rather than merely promised?

## Finding: no. Not at the MCP layer, and the reason is structural.

1. **The server is the data *source*; the host is the *sink*.** An MCP server returns a tool result to
   the host (Claude Desktop). What the host then does with it — feed a cloud model, a local model, log
   it, forward it — is entirely outside the server's view or control. You cannot govern data after
   handing it over (the same wall DRM hits).
2. **`clientInfo` is self-reported and identifies the *app*, not the LLM.** The MCP `initialize`
   handshake carries `clientInfo {name, version}` — the client application's own label (e.g.
   `"claude-desktop"`), trivially spoofable. There is no field for *which model* consumes the output,
   no model identity, no owner.
3. **MCP's auth runs client→server — the wrong direction.** The 2025-06-18 spec makes MCP servers
   OAuth 2.1 *Resource Servers* and adds RFC 8707 Resource Indicators. All of that lets the *server*
   authenticate the *client* (and stops token replay across servers). There is no reverse mechanism —
   no TLS client certificate, no remote attestation — by which "the LLM" proves it is local or
   discloses its owner. And even if one existed, point 1 still defeats it.

So "restrict cloud LLMs technically" is a dead end. The distinction PRM cares about — *does this data
leave the device?* — is invisible at the protocol boundary.

## Decision: handle it honestly with a consented, reversible exception — not detection.

PRM adopts PNT's **`EX-CLOUD-LLM`** exception
([`spec/exceptions.md`](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/exceptions.md)),
the mechanism the `fellows_local_db` reference design pioneered for the same problem with its ~500
users. Connecting a cloud MCP client doesn't *silently violate* local-only; it **raises a named
exception** the app catches and handles:

- **Consent before the raise** (EX-H2): first connect is blocked behind an agreement that names the
  exception and links its explainer.
- **Persistent "not a PNA right now" signal** while active (EX-H3): the workspace shows it until the
  user returns to PNA mode; dismissing *acknowledges* but does not clear it.
- **Runtime active-set explainer** (EX-H4) and **reversible return-to-PNA-mode** (EX-H5) — *mode
  only*: it stops future sharing but **cannot recall data already sent**.
- **Best-effort consent propagation** to the human via the MCP `instructions` handshake (EX-H7) when
  an agent, not the person, is the immediate caller. **Live in v0.1:** both servers construct
  `FastMCP(..., instructions=CLOUD_LLM_NOTICE)` (`mcp_servers/consent.py`), so a cooperating cloud
  client is told to get the user's consent first and prefer a local model. It is honest signaling, not
  a gate — a non-cooperating client can ignore it.

The honest framing (EX-H8 strength profile): *once data crosses to a third party, PRM can guarantee
nothing about the data itself; every real guarantee is about the **boundary** — consent, signaling,
reversibility — and **local recoverability**.* PRM states this per-dimension, not as a single
"secure / not secure" claim: "the provider won't train on this" is `provider-asserted`; "data already
sent" is `none`.

## Consequences

- **INV-1 becomes precise:** it holds **in PNA mode**; `EX-CLOUD-LLM` is the one sanctioned exit, not
  a leak.
- The handler splits across two homes. The **server-side** half — EX-H7's `instructions` handshake — is
  **done in v0.1** (both servers, `mcp_servers/consent.py`), because the v0.1 *read* surface already
  returns contact PII to a connected cloud client. The **workspace** half (consent gate before connect,
  persistent "not a PNA" banner, return-to-PNA-mode control — EX-H2–H5) is UI work owed by the time a
  cloud client can reach the **private** MCP surface — **v0.2**. v0.1's standing stance remains "local AI
  recommended" (see the implementation plan §6).
- Building it lets PRM **demonstrate `EX-CLOUD-LLM` for PNT**, which today cites only
  `fellows_local_db`.

## What would change this

- A future MCP revision carrying *verifiable* client/LLM attestation (cryptographic, not
  self-reported) would let a server *log or gate on* a claimed identity — but consequence #1 (the host
  controls the data after handoff) means even that could not *enforce* local-only. Consent + signaling
  would remain the real boundary.
- A genuinely **local** model reading the MCP surface raises **no** exception — INV-1 holds — which is
  why "local AI recommended" is the standing guidance, not a workaround.

## Sources

- PNT [`spec/exceptions.md`](https://github.com/richbodo/personal_network_toolkit/blob/main/spec/exceptions.md)
  — `EX-CLOUD-LLM`, handler clauses EX-H1–H8, strength profiles.
- `fellows_local_db`: `docs/use_with_claude_desktop.md` (the shipped consent flow), `docs/never-saas.md`,
  `docs/local_vs_saas_risk.md`.
- [MCP specification](https://modelcontextprotocol.io/specification/2025-11-25);
  [MCP authorization update, 2025-06-18](https://auth0.com/blog/mcp-specs-update-all-about-auth/).
