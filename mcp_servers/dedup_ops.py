#!/usr/bin/env python3
"""PRM Dedup-Ops MCP server (**PROPOSE-ONLY**) — the AI proposes merges; it cannot apply them.

There is no apply tool on this surface: ``submit_merge_proposal`` *stages* a changeset for the human
to review and apply in the workspace. That absence is the structural guarantee (INV-11 / AC-PRM-F) —
the same "MCP stages, the workspace applies" rule the deterministic flow already follows; the AI is
just another *author* of the same reviewable proposal. Runs over stdio; resolves the PRM home from
``--data-dir`` / ``PRM_HOME``. See ``prompts/dedup.md`` for how the AI should drive it.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.config import resolve_home  # noqa: E402
from core import build_label, relationships_db  # noqa: E402
from mcp_servers import tools  # noqa: E402
from mcp_servers.consent import CLOUD_LLM_NOTICE  # noqa: E402


def build(home) -> "FastMCP":
    from mcp.server.fastmcp import FastMCP   # lazy — so `--build` (self-check) runs without the SDK installed
    # `instructions` carries the EX-H7 best-effort cloud-LLM consent notice to a cooperating client.
    mcp = FastMCP("prm-dedup-ops", instructions=CLOUD_LLM_NOTICE)

    @mcp.tool()
    def find_duplicate_candidates(limit: int = 50) -> dict:
        """Find likely-duplicate clusters to propose merges against (confident → fuzzy)."""
        return tools.find_duplicate_candidates(home, limit)

    @mcp.tool()
    def submit_merge_proposal(member_ids: list[str], into: str,
                              resolutions: list | None = None, rationale: str = "") -> dict:
        """PROPOSE a merge for human review — **staged, never applied**. ``into`` must be one of
        ``member_ids`` (the surviving contact). ``resolutions`` resolve conflicting single-valued
        fields: a list of ``{field, chosen_value, chosen_source}``. Returns the staged proposal id."""
        return tools.submit_merge_proposal(home, member_ids, into, resolutions, rationale)

    @mcp.tool()
    def list_proposals(status: str = "pending") -> dict:
        """List staged merge proposals (default: pending review)."""
        return tools.list_proposals(home, status)

    @mcp.tool()
    def get_proposal(proposal_id: str) -> dict:
        """Get a staged proposal's full changeset."""
        return tools.get_proposal(home, proposal_id)

    return mcp


def main() -> None:
    ap = argparse.ArgumentParser(description="PRM Dedup-Ops MCP server (propose-only).")
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
        print(claude_config.block(claude_config.server_entry("prm-dedup", "dedup_ops.py", home.root)))
        print("# Paste under \"mcpServers\" in claude_desktop_config.json (Settings ▸ Developer ▸ Edit Config), "
              "then fully quit + reopen Claude Desktop.", file=sys.stderr)
        return
    build(home).run()


if __name__ == "__main__":
    main()
