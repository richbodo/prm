#!/usr/bin/env python3
"""Tests for the EX-H7 cloud-LLM consent handshake on the MCP servers.

Two layers, so the load-bearing signal can't be silently gutted:

- **SDK-free** (runs in the project ``.venv``): the notice exists and still carries its load-bearing
  phrases. ``mcp_servers.consent`` imports no SDK, so this collects everywhere.
- **SDK-guarded** (``pytest.importorskip("mcp")``): the notice is actually wired into each built server
  as ``instructions`` — the no-bypass proof. Skips cleanly from the project ``.venv`` (no ``mcp`` SDK),
  exactly like ``tests/unit/test_mcp_tools.py`` stays SDK-free.

    python tests/unit/test_mcp_consent.py
    pytest tests/unit/test_mcp_consent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from mcp_servers.consent import CLOUD_LLM_NOTICE  # noqa: E402


# ------------------------------------------------------------------ SDK-free: the notice is honest
def test_notice_carries_load_bearing_phrases():
    assert CLOUD_LLM_NOTICE.strip()
    # The exception is named, and the boundary it signals (local-only, consent) is stated.
    assert "EX-CLOUD-LLM" in CLOUD_LLM_NOTICE
    for phrase in ("local", "consent"):
        assert phrase in CLOUD_LLM_NOTICE.lower(), f"notice lost the {phrase!r} signal"


# ------------------------------------------------------------------ SDK-guarded: it's actually wired in
def test_servers_wire_the_notice_into_instructions():
    # Skip hinges only on the SDK being present (mcp_servers/.venv), never on pytest — that venv has the
    # SDK but no pytest, so the standalone runner must still exercise the assertion there.
    try:
        import mcp  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("mcp SDK not installed (run from mcp_servers/.venv)")

    from cli.config import resolve_home
    from mcp_servers import dedup_ops, shared_data_ops

    home = resolve_home(REPO / "prm-data")  # build() doesn't read the store; no home needs to exist
    for build in (shared_data_ops.build, dedup_ops.build):
        mcp = build(home)
        assert mcp.instructions == CLOUD_LLM_NOTICE


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = skipped = 0
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "Skipped":   # pytest.importorskip outside the pytest runner
                skipped += 1
                print(f"[SKIP] {t.__name__}: {exc}")
                continue
            failures += 1
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures - skipped}/{len(tests)} passed, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
