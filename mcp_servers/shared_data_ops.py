#!/usr/bin/env python3
"""PRM Shared-Data-Ops MCP server (read-only) — canonical contacts + duplicate candidates over MCP.

Read tools return contact PII, so a **cloud** client trips PNT's AC-MCP-A: connecting one is the
moment data leaves the device. **Local AI is recommended** — an MCP server cannot tell which LLM
consumes its output (see docs/design-notes/mcp-cannot-identify-the-consuming-llm.md), so the boundary
is consent + honest signaling, never client identification. Runs over stdio; resolves the PRM home
from ``--data-dir`` / ``PRM_HOME``.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # import core / cli without installing prm

from cli.config import resolve_home  # noqa: E402
from core import build_label, relationships_db  # noqa: E402
from mcp_servers import tools  # noqa: E402
from mcp_servers.consent import CLOUD_LLM_NOTICE  # noqa: E402


def build(home) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP   # lazy — so `--build` (self-check) runs without the SDK installed
    # `instructions` carries the EX-H7 best-effort cloud-LLM consent notice to a cooperating client.
    mcp = FastMCP("prm-shared-data-ops", instructions=CLOUD_LLM_NOTICE)

    @mcp.tool()
    def search_contacts(query: str, limit: int = 20) -> dict:
        """Search contacts by name / email / org / notes; returns canonical (merged) contacts."""
        return tools.search_contacts(home, query, limit)

    @mcp.tool()
    def list_contacts(limit: int = 50, offset: int = 0) -> dict:
        """List canonical contacts, name-ordered (paged)."""
        return tools.list_contacts(home, limit, offset)

    @mcp.tool()
    def get_contact(contact_id: str) -> dict:
        """Get one canonical contact — its merged fields, each with the source it came from."""
        return tools.get_contact(home, contact_id)

    @mcp.tool()
    def get_provenance(contact_id: str) -> dict:
        """Where each of a contact's values came from (which source records contributed)."""
        return tools.get_provenance(home, contact_id)

    @mcp.tool()
    def find_duplicate_candidates(limit: int = 50) -> dict:
        """Find likely-duplicate contact clusters (confident → fuzzy), each with member contacts."""
        return tools.find_duplicate_candidates(home, limit)

    return mcp


def main() -> None:
    ap = argparse.ArgumentParser(description="PRM Shared-Data-Ops MCP server (read-only).")
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
        print(claude_config.block(claude_config.server_entry("prm-shared-data", "shared_data_ops.py", home.root)))
        print("# Paste under \"mcpServers\" in claude_desktop_config.json (Settings ▸ Developer ▸ Edit Config), "
              "then fully quit + reopen Claude Desktop.", file=sys.stderr)
        return
    build(home).run()


if __name__ == "__main__":
    main()
