# PRM MCP servers

Three stdio MCP servers that let an AI assistant read your contacts, **propose** merges (it can never
apply them), and write **values** into your relationship fields under a per-field policy. Reviewing and
approving merges — and any `review-required` write — stays in the PRM workspace.

| Server | Tools | Posture |
| --- | --- | --- |
| `shared_data_ops.py` | `search_contacts`, `list_contacts`, `get_contact`, `get_provenance`, `find_duplicate_candidates` | **read-only** |
| `dedup_ops.py` | `find_duplicate_candidates`, `submit_merge_proposal`, `list_proposals`, `get_proposal` | **propose-only** (no apply tool — INV-11 / AC-PRM-F) |
| `private_data_ops.py` | `write_field_value` | **write-values-only**, policy-gated (R11a / AC-PRM-E): `review-required` **stages** a proposal · `append-only` **appends** · `free-write` **sets**. No field-definition tool (INV-3/4); bounded by a per-session quota + size cap. |

## Install into Claude Desktop — one command

```bash
just mcp-install
```

Sets up the SDK venv (if needed), then **safely registers all three servers** in Claude Desktop's
`claude_desktop_config.json`. It is **non-destructive by construction**: it **backs the file up** first,
**merges** PRM's entries under `mcpServers` (every other server *and* Claude Desktop's own
`preferences` / `coworkUserFilesPath` are left untouched), writes atomically, and is **idempotent** —
re-run it anytime (e.g. if Claude Desktop resets `mcpServers` during an update) to restore them. It
shows a preview and asks before writing.

Then **fully quit Claude Desktop (⌘Q — closing the window isn't enough) and reopen it**; Settings ▸
Developer ▸ *Local MCP servers* should list `prm-shared-data`, `prm-dedup`, and `prm-private-data`. Point
the assistant at [`prompts/dedup.md`](prompts/dedup.md).

```bash
just mcp-install --data-dir DIR   # bind a non-default PRM home (else $PRM_HOME, else ./prm-data/)
just mcp-install --print          # preview the change; write nothing
just mcp-uninstall                # remove PRM's servers (backs up; leaves other servers alone)
```

### Prefer to paste it yourself?

Run `just mcp-install-deps` first, then print either server's ready-to-paste entry (absolute paths
filled in) and add it via Claude Desktop → Settings ▸ Developer ▸ **Edit Config**:

```bash
just mcp-shared-data-ops --print-config     # read-only server's entry
just mcp-dedup-ops --print-config           # propose-only server's entry
just mcp-private-data-ops --print-config    # write-values-only server's entry
```

```jsonc
{
  "mcpServers": {
    "prm-shared-data": {
      "command": "/Users/you/src/prm/mcp_servers/.venv/bin/python",
      "args": ["/Users/you/src/prm/mcp_servers/shared_data_ops.py", "--data-dir", "/Users/you/src/prm/prm-data"]
    }
  }
}
```

(To add both by hand, merge the two `prm-*` entries into one `mcpServers` object.) Fully quit + reopen
Claude Desktop afterward.

## Run standalone (test harness)

```bash
just mcp-shared-data-ops     # read-only server  (stdio)
just mcp-dedup-ops           # propose-only server (stdio)
just mcp-private-data-ops    # write-values-only server (stdio)
```

By themselves these block on stdin waiting for JSON-RPC frames — useful for a protocol test harness,
not interactive use. The servers import `core/` + `cli.config` directly from the repo (stdlib-only), so
the SDK venv (`mcp_servers/.venv`, created by `just mcp-install-deps`) holds only the `mcp` SDK —
`core`'s deliberately tiny dependency surface stays clean.

**Pointing a server at your data.** Each server reads your **PRM home** — the data dir holding
`shared.db` / `relationships.db` / `proposals/`. `just mcp-install` and `--print-config` bind it for you; the
standalone commands above accept `--data-dir DIR`, or set `$PRM_HOME` (resolution order:
`--data-dir` → `$PRM_HOME` → `./prm-data/`; see the user's guide
[§ Where your data lives](../docs/users-guide.md#where-your-data-lives)).

## Driving the dedup loop

Once the servers are registered, hand the assistant the dedup instructions in
[`prompts/dedup.md`](prompts/dedup.md) — paste its contents (or attach the file) into your MCP client
session; it is **not** auto-loaded. That prompt tells the AI how to pull duplicate candidates, ask you
clarifying questions, build a **conservative** merge changeset, and `submit_merge_proposal` — after which
the proposal appears in the workspace **Duplicates** tab tagged 🤖 for you to approve or reject. The AI
only proposes; you apply.

## ⚠️ Cloud vs local AI

The read tools return contact **PII**. The moment a **cloud** client (e.g. Claude Desktop backed by a
cloud model) can read it, your data leaves the device — PNT's `AC-MCP-A` / `EX-CLOUD-LLM`. **A local
AI is recommended.** An MCP server *cannot* detect or restrict which LLM consumes its output (see
[`../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`](../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md)),
so this boundary is held by your consent and honest signaling — never by trying to identify the client.

All three servers carry that signal at the protocol level: each is built with an MCP `instructions` handshake
([`consent.py`](consent.py), the EX-H7 best-effort clause) telling a *cooperating* cloud client to get
your explicit consent before reading and to prefer a local model. (Writing is *ingress*, not egress — the
write server appends a value but never reads contact data back out, so it sits outside the disclosure gate.) It's honest signaling, not a gate — a
non-cooperating client can ignore it. The **full** cloud-AI consent handler is now **shipped**: the
workspace **AI access** tab gates whether the private overlay crosses at all (declare *local* or *cloud*),
bounds *which* fields can (the per-field **data-floor** — sealed fields never cross, even with consent),
shows a persistent "not a PNA" banner while a cloud grant is active, and offers one-click return to
local-only mode. See [`../docs/users-guide.md`](../docs/users-guide.md) ("Let an AI propose the merges")
and the [`../docs/Architecture.md`](../docs/Architecture.md) exception attestation.

## Access log — what an AI read, and when

Every contact read the servers serve (`get_contact` / `get_provenance`) appends one line to
`mcp_access.log.jsonl` in your PRM home: the contact, the disclosure mode, the per-request-review decision
(`released` / `withheld` / `no-shareable`), the overlay field **names** returned vs withheld (**never
values** — INV-1), and the **build label** of the server that served it — so a server still running stale
code after an update (a real gotcha: ⌘Q + reopen Claude Desktop after pulling) is immediately visible.
A leaf write (`core/access_log.py`), bounded like the snapshot ring. Read it with **`just access-log`**
(or `just access-log 100`) — handy when manually verifying what a connected AI actually pulled.

## Self-check — `--build` and the "Test MCP servers" button

Each server accepts **`--build`**: it prints its build label and exits, **without loading the MCP SDK** (the
SDK import is lazy), so it works from any Python. The workspace's **External Access → Connections → "Test MCP
servers"** button uses it: the daemon spawns each registered server with `--build` to confirm it's
**responsive** and report its **on-disk** build, and pairs that with the **last live read**'s build from the
access log — the build the server your AI client is *actually* running stamped. (A fresh spawn always
reflects current on-disk code, so the running-server staleness signal is the last-read build, not the spawn.)
