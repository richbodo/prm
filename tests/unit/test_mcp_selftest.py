#!/usr/bin/env python3
"""'Test MCP servers' (#70 C): the servers' ``--build`` self-check runs **without the SDK** (lazy import),
and the daemon's ``POST /api/mcp/selftest`` spawn-probes the registered servers (responsive + on-disk
build) and reports the **last live read**'s build (the running server's staleness signal).

    python tests/unit/test_mcp_selftest.py
    pytest tests/unit/test_mcp_selftest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, build_label, disclosure, relationships_db  # noqa: E402
from daemon import server  # noqa: E402
from mcp_servers import tools  # noqa: E402

SHARED = str(REPO / "mcp_servers" / "shared_data_ops.py")
DEDUP = str(REPO / "mcp_servers" / "dedup_ops.py")


def test_build_flag_runs_without_sdk():
    # sys.executable is the project venv (no `mcp` SDK) — --build must still print the build via lazy import.
    for script in (SHARED, DEDUP):
        r = subprocess.run([sys.executable, script, "--build"], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == build_label.build_label()


def _home_with_read(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nEND:VCARD\r\n", encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    apply.define_field(home, label="Project", kind="text", disclosure_tier="private-shareable-on-consent")
    apply.set_field_value(home, cid, "project", "PRM")
    apply.set_disclosure_mode(home, disclosure.CLOUD_EXCEPTION)
    tools.get_contact(home, cid)                              # logs a read → the access-log build is stamped
    return home, cid


def _with_fake_config(cfg, fn):
    """Run ``fn`` with the daemon's view of Claude's config monkeypatched to ``cfg`` (restored after)."""
    from mcp_servers import install
    orig_load, orig_path = install.load_config, install.default_config_path
    install.load_config = lambda p: cfg
    install.default_config_path = lambda: Path("/nonexistent/config.json")
    try:
        return fn()
    finally:
        install.load_config, install.default_config_path = orig_load, orig_path


def _selftest(home):
    return json.loads(server.route("POST", "/api/mcp/selftest", {}, home, {})[2])


def test_selftest_spawns_responsive_and_matches():
    with tempfile.TemporaryDirectory() as tmp:
        home, _ = _home_with_read(tmp)
        cfg = {"mcpServers": {"prm-shared-data": {"command": sys.executable,
                                                  "args": [SHARED, "--data-dir", str(home.root)]}}}
        out = _with_fake_config(cfg, lambda: _selftest(home))
        assert out["app_build"] == build_label.build_label()
        assert len(out["servers"]) == 1
        s = out["servers"][0]
        assert s["name"] == "prm-shared-data" and s["responsive"] is True
        assert s["build"] == out["app_build"] and s["matches"] is True
        # the running server's build comes from the access-log read we logged, and matches the app build
        assert out["last_read"] and out["last_read"]["matches"] is True


def test_selftest_no_servers_when_config_empty():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")                  # no reads, no servers
        out = _with_fake_config({"mcpServers": {}}, lambda: _selftest(home))
        assert out["servers"] == [] and out["last_read"] is None and out["app_build"]


def test_selftest_unresponsive_for_bad_command():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        cfg = {"mcpServers": {"prm-shared-data": {"command": "/nonexistent/python", "args": [SHARED]}}}
        out = _with_fake_config(cfg, lambda: _selftest(home))
        s = out["servers"][0]
        assert s["name"] == "prm-shared-data" and s["responsive"] is False and s["build"] is None


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
