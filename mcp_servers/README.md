# PRM MCP servers

Two stdio MCP servers that let an AI assistant read your contacts and **propose** merges — it can
never apply them. Reviewing and approving merges stays in the PRM workspace.

| Server | Tools | Posture |
| --- | --- | --- |
| `shared_data_ops.py` | `search_contacts`, `list_contacts`, `get_contact`, `get_provenance`, `find_duplicate_candidates` | **read-only** |
| `dedup_ops.py` | `find_duplicate_candidates`, `submit_merge_proposal`, `list_proposals`, `get_proposal` | **propose-only** (no apply tool — INV-11 / AC-PRM-F) |

## Install

```bash
just mcp-install-deps        # creates mcp_servers/.venv and installs the mcp SDK
```

The servers import `core/` + `cli.config` directly from the repo (stdlib-only), so the venv holds only
the SDK — `core`'s deliberately tiny dependency surface stays clean.

## Run

```bash
just mcp-shared-data-ops     # read-only server  (stdio)
just mcp-dedup-ops           # propose-only server (stdio)
```

By themselves these just block on stdin waiting for JSON-RPC frames — useful for a test harness, not
interactive use. For real use, register them with an MCP client (e.g. Claude Desktop):

```jsonc
{
  "mcpServers": {
    "prm-shared-data": {
      "command": "/ABSOLUTE/PATH/prm/mcp_servers/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/prm/mcp_servers/shared_data_ops.py", "--data-dir", "/ABSOLUTE/PATH/prm/prm-data"]
    },
    "prm-dedup": {
      "command": "/ABSOLUTE/PATH/prm/mcp_servers/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/prm/mcp_servers/dedup_ops.py", "--data-dir", "/ABSOLUTE/PATH/prm/prm-data"]
    }
  }
}
```

(Or set `PRM_HOME` instead of `--data-dir`.) Then point the assistant at `prompts/dedup.md`.

## ⚠️ Cloud vs local AI

The read tools return contact **PII**. The moment a **cloud** client (e.g. Claude Desktop backed by a
cloud model) can read it, your data leaves the device — PNT's `AC-MCP-A` / `EX-CLOUD-LLM`. **A local
AI is recommended.** An MCP server *cannot* detect or restrict which LLM consumes its output (see
[`../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md`](../docs/design-notes/mcp-cannot-identify-the-consuming-llm.md)),
so this boundary is held by your consent and honest signaling — never by trying to identify the client.

Both servers carry that signal at the protocol level: each is built with an MCP `instructions` handshake
([`consent.py`](consent.py), the EX-H7 best-effort clause) telling a *cooperating* cloud client to get
your explicit consent before reading and to prefer a local model. It's honest signaling, not a gate — a
non-cooperating client can ignore it. The **full** cloud-AI consent handler (a workspace consent gate,
a persistent "not a PNA" banner, a return-to-PNA-mode control) is a v0.2 milestone (see
[`../docs/roadmap.md`](../docs/roadmap.md)).
