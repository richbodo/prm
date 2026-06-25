#!/usr/bin/env python3
"""PRM Private-Data-Ops MCP server (**WRITE-VALUES-ONLY**) — an AI writes field *values*, never definitions.

The one write tool, ``write_field_value``, is governed by the field's ``ai_write_policy`` (R11a / AC-PRM-E):

- ``review-required`` (the default) **stages a proposal** the human approves in the workspace — there is no
  direct write (INV-11), the same "MCP stages, the workspace applies" rule the dedup surface follows;
- ``append-only`` **appends** a provenance-stamped row (non-destructive — a human value is never overwritten);
- ``free-write`` **sets** directly.

There is **no field-definition tool** on this surface (INV-3/4 — the schema is authored only in the
workspace; the absence is the structural lock), and a direct AI write is bounded by a per-session quota
(INV-13) + a per-value size cap. Writing is *ingress*, so it is not gated by the EX-CLOUD-LLM disclosure mode
(an AI may append to a sealed field it can't read back); AI-written values inherit the field's sealed-by-
default disclosure tier. Runs over stdio; resolves the PRM home from ``--data-dir`` / ``PRM_HOME``.
"""

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.config import resolve_home  # noqa: E402
from core import build_label, relationships_db  # noqa: E402
from mcp_servers import tools  # noqa: E402
from mcp_servers.consent import CLOUD_LLM_NOTICE  # noqa: E402


def build(home) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP   # lazy — so `--build` (self-check) runs without the SDK installed
    # One write session per server process: the per-session quota (INV-13) bounds a runaway loop within a
    # single connection; a reconnect starts a fresh budget. Stamped on every value this connection writes.
    session = f"ai:mcp-{uuid.uuid4().hex[:8]}"
    # `instructions` carries the EX-H7 best-effort cloud-LLM consent notice to a cooperating client.
    mcp = FastMCP("prm-private-data-ops", instructions=CLOUD_LLM_NOTICE)

    @mcp.tool()
    def write_field_value(contact_id: str, field_id: str, value: str, source: str = "") -> dict:
        """Write one relationship-field VALUE — **governed by the field's AI-write policy**.
        ``review-required`` (default) STAGES a proposal for the user to approve in the workspace (never a
        direct write); ``append-only`` APPENDS a provenance-stamped value (non-destructive — a human value is
        never overwritten); ``free-write`` SETS it. You can write *values* only — field definitions are
        authored solely in the workspace, so use an existing ``field_id``. A refusal (unknown field / size
        cap / per-session quota) returns an ``error``. Returns the stage/apply result."""
        return tools.write_field_value(home, contact_id, field_id, value,
                                       written_by=session, source=source or None)

    @mcp.tool()
    def observe_contact_field(contact_id: str, field: str, value: str, source: str = "",
                              confidence: float = 0.0) -> dict:
        """File an OBSERVATION for a canonical contact field (tel / email / org / title / adr / url / bday /
        nickname / impp / role) — additive, attributed, and NEVER canonical. It lands as a *suggestion* the
        user promotes in the workspace; there is no way to make it the default from here. Use this to fill in
        a contact's missing reachability/identity fields — e.g. a phone for a name-only Facebook contact. A
        refusal (non-enrichable field / size cap / per-session quota) returns an ``error``."""
        return tools.observe_contact_field(home, contact_id, field, value, written_by=session,
                                           source=source or None, confidence=confidence or None)

    return mcp


def main() -> None:
    ap = argparse.ArgumentParser(description="PRM Private-Data-Ops MCP server (write field values; policy-gated).")
    ap.add_argument("--data-dir", help="PRM home (else $PRM_HOME, else ./prm-data/)")
    ap.add_argument("--print-config", action="store_true",
                    help="print the Claude Desktop JSON for this server and exit (don't run it)")
    ap.add_argument("--build", action="store_true",
                    help="print this server's build label and exit (the daemon's 'Test MCP servers' self-check)")
    args = ap.parse_args()
    if args.build:                                           # self-check: no SDK, no home, no side effects
        print(build_label.build_label())
        return
    home = resolve_home(args.data_dir)
    relationships_db.migrate(home.relationships_db, home.legacy_private_db)   # v0.1→v0.2 rename + schema upgrade
    if args.print_config:
        from mcp_servers import claude_config
        print(claude_config.block(claude_config.server_entry("prm-private-data", "private_data_ops.py", home.root)))
        print("# Paste under \"mcpServers\" in claude_desktop_config.json (Settings ▸ Developer ▸ Edit Config), "
              "then fully quit + reopen Claude Desktop.", file=sys.stderr)
        return
    build(home).run()


if __name__ == "__main__":
    main()
