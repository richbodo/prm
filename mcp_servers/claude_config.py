"""Emit the Claude Desktop ``mcpServers`` JSON for a PRM MCP server.

Turns "hand-write the JSON config with the right absolute paths" into a copy-paste: each server's
``--print-config`` flag calls ``block()`` and prints a ready-to-paste snippet. The paths are derived
here, where they're known — the MCP-venv Python (what Claude Desktop launches), the server script, and
the resolved PRM home.

Pure module: **no ``mcp`` SDK import** (mirrors ``tools.py``), so it's unit-testable from the project
venv without the isolated ``mcp_servers/.venv``.
"""

from __future__ import annotations

import json
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
# What Claude Desktop runs — the isolated SDK venv created by `just mcp-install-deps`.
VENV_PYTHON = MCP_DIR / ".venv" / "bin" / "python"


def server_entry(server_key: str, script_name: str, data_dir) -> dict:
    """One ``mcpServers`` entry (absolute command + args) for a single server."""
    return {
        server_key: {
            "command": str(VENV_PYTHON),
            "args": [str(MCP_DIR / script_name), "--data-dir", str(Path(data_dir).resolve())],
        }
    }


def block(*entries: dict) -> str:
    """A paste-ready ``{"mcpServers": {...}}`` block merging one or more ``server_entry`` dicts."""
    merged: dict = {}
    for e in entries:
        merged.update(e)
    return json.dumps({"mcpServers": merged}, indent=2)
