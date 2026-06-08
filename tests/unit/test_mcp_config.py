#!/usr/bin/env python3
"""Tests for the Claude Desktop config emitter (mcp_servers/claude_config.py).

Pure — no ``mcp`` SDK needed (mirrors the helper), so it runs in the project venv. Pins that the
printed snippet is valid JSON with the absolute paths Claude Desktop needs.

    python tests/unit/test_mcp_config.py
    pytest tests/unit/test_mcp_config.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from mcp_servers import claude_config  # noqa: E402


def test_single_server_block_is_valid_paste_ready_json():
    out = claude_config.block(
        claude_config.server_entry("prm-shared-data", "shared_data_ops.py", "prm-data"))
    doc = json.loads(out)                                   # valid JSON
    entry = doc["mcpServers"]["prm-shared-data"]
    # command is the isolated MCP-venv python (absolute), inside mcp_servers/
    assert entry["command"].endswith("/.venv/bin/python")
    assert "mcp_servers" in entry["command"]
    # args: absolute script path, then --data-dir <absolute home>
    script, flag, data_dir = entry["args"]
    assert script.endswith("/mcp_servers/shared_data_ops.py")
    assert flag == "--data-dir"
    assert Path(data_dir).is_absolute()                    # resolved, even if it doesn't exist yet


def test_combined_block_merges_both_servers():
    out = claude_config.block(
        claude_config.server_entry("prm-shared-data", "shared_data_ops.py", "prm-data"),
        claude_config.server_entry("prm-dedup", "dedup_ops.py", "prm-data"))
    servers = json.loads(out)["mcpServers"]
    assert set(servers) == {"prm-shared-data", "prm-dedup"}
    assert servers["prm-dedup"]["args"][0].endswith("/mcp_servers/dedup_ops.py")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append(t.__name__)
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
