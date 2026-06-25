#!/usr/bin/env python3
"""Tests for the Claude Desktop installer (mcp_servers/install.py).

Pure stdlib — no `mcp` SDK, and **never touches the real ~/Library config**: every test uses a
tmp path. Pins the load-bearing safety properties: preserve other keys + other servers, back up before
write, atomic round-trip, idempotency, and clean uninstall.

    python tests/unit/test_mcp_install.py
    pytest tests/unit/test_mcp_install.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from mcp_servers import install  # noqa: E402


class _Home:
    def __init__(self, root): self.root = Path(root)


def _entries(tmp):
    return install.desired_entries(_Home(Path(tmp) / "prm-data"))


# ------------------------------------------------------------------ load_config
def test_load_missing_and_empty_are_empty_dict():
    with tempfile.TemporaryDirectory() as tmp:
        assert install.load_config(Path(tmp) / "nope.json") == {}
        empty = Path(tmp) / "empty.json"
        empty.write_text("   \n", encoding="utf-8")
        assert install.load_config(empty) == {}


def test_load_invalid_json_refuses_not_clobbers():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.json"
        bad.write_text('{"mcpServers": {,}}', encoding="utf-8")   # trailing/empty comma → invalid
        try:
            install.load_config(bad)
        except install.ConfigError:
            pass
        else:
            raise AssertionError("invalid JSON must raise ConfigError, never be silently overwritten")


# ------------------------------------------------------------------ merge preserves everything (the whole point)
def test_apply_preserves_other_keys_and_servers():
    with tempfile.TemporaryDirectory() as tmp:
        # A realistic Claude config: app-owned keys + a third-party server.
        config = {
            "coworkUserFilesPath": "/Users/x/Claude",
            "preferences": {"remoteToolsDeviceName": "hub"},
            "mcpServers": {"some-other": {"command": "node", "args": ["x.js"]}},
        }
        out = install.apply_entries(config, _entries(tmp))
        # Untouched app keys + foreign server survive verbatim.
        assert out["coworkUserFilesPath"] == "/Users/x/Claude"
        assert out["preferences"] == {"remoteToolsDeviceName": "hub"}
        assert out["mcpServers"]["some-other"] == {"command": "node", "args": ["x.js"]}
        # PRM's two servers are added.
        assert set(install.PRM_KEYS) <= set(out["mcpServers"])
        # Input dict not mutated in place.
        assert "prm-shared-data" not in config["mcpServers"]


def test_apply_into_empty_or_missing_mcpservers():
    with tempfile.TemporaryDirectory() as tmp:
        for config in ({}, {"mcpServers": {}}, {"preferences": {}}):
            out = install.apply_entries(config, _entries(tmp))
            assert set(install.PRM_KEYS) <= set(out["mcpServers"])


# ------------------------------------------------------------------ plan / idempotency
def test_plan_reports_add_then_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        entries = _entries(tmp)
        assert {s for _, s in install.plan({}, entries)} == {"add"}
        applied = install.apply_entries({}, entries)
        assert {s for _, s in install.plan(applied, entries)} == {"unchanged"}   # idempotent


def test_plan_reports_update_when_paths_differ():
    with tempfile.TemporaryDirectory() as tmp:
        old = install.apply_entries({}, install.desired_entries(_Home("/old/home")))
        states = dict(install.plan(old, _entries(tmp)))
        assert states["prm-shared-data"] == "update"


# ------------------------------------------------------------------ backup + atomic write round-trip
def test_backup_then_atomic_write_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude_desktop_config.json"
        original = {"preferences": {"a": 1}, "mcpServers": {}}
        path.write_text(json.dumps(original), encoding="utf-8")

        bak = install.backup(path)
        assert bak and bak.exists() and json.loads(bak.read_text()) == original

        install.write_atomic(path, install.apply_entries(original, _entries(tmp)))
        reloaded = install.load_config(path)
        assert reloaded["preferences"] == {"a": 1}                       # preserved
        assert set(install.PRM_KEYS) <= set(reloaded["mcpServers"])      # installed
        assert not path.with_name(path.name + ".prm-tmp").exists()       # temp cleaned up by rename


def test_backup_of_missing_file_is_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert install.backup(Path(tmp) / "nope.json") is None


# ------------------------------------------------------------------ uninstall
def test_remove_keeps_other_servers():
    with tempfile.TemporaryDirectory() as tmp:
        config = install.apply_entries({"mcpServers": {"keepme": {"command": "x"}}}, _entries(tmp))
        new, removed = install.remove_entries(config, install.PRM_KEYS)
        assert set(removed) == set(install.PRM_KEYS)
        assert new["mcpServers"] == {"keepme": {"command": "x"}}


def test_default_config_path_points_at_claude_config():
    assert install.default_config_path().name == "claude_desktop_config.json"


def test_all_three_servers_are_registered():
    # The write-values server (R11a) is wired into the same registry as install + the daemon selftest.
    assert "prm-private-data" in install.PRM_KEYS
    with tempfile.TemporaryDirectory() as tmp:
        entries = _entries(tmp)
        assert {"prm-shared-data", "prm-dedup", "prm-private-data"} <= set(entries)
        assert entries["prm-private-data"]["args"][0].endswith("/mcp_servers/private_data_ops.py")


# ------------------------------------------------------------------ end-to-end via main(), against a temp config
def test_main_non_interactive_installs_and_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude_desktop_config.json"
        path.write_text(json.dumps({"preferences": {"keep": True}}), encoding="utf-8")
        dd = str(Path(tmp) / "prm-data")
        rc = install.main(["--config", str(path), "--data-dir", dd, "--non-interactive"])
        assert rc == 0
        cfg = json.loads(path.read_text())
        assert cfg["preferences"] == {"keep": True}                      # app keys preserved
        assert set(install.PRM_KEYS) <= set(cfg["mcpServers"])           # installed
        assert list(path.parent.glob("*.bak.prm-*"))                     # a backup was made
        # Second run changes nothing (idempotent) and exits 0.
        assert install.main(["--config", str(path), "--data-dir", dd, "--non-interactive"]) == 0


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
