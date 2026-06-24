#!/usr/bin/env python3
"""Daemon endpoints for the EX-CLOUD-LLM workspace handler (P2): /api/disclosure (state + banner triggers),
/api/connections (best-effort inventory), and the consent / return-to-pna writes. Drives the pure router
(server.route) directly — no socket bind.

    python tests/unit/test_daemon_disclosure.py
    pytest tests/unit/test_daemon_disclosure.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from daemon import server  # noqa: E402


def _home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home


def _get(home, path, **kw):
    status, _, body = server.route("GET", path, {}, home, **kw)
    return status, json.loads(body)


def _post(home, path, body):
    status, _, raw = server.route("POST", path, {}, home, body)
    return status, json.loads(raw)


def test_default_state_then_consent_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        s, st = _get(home, "/api/disclosure")
        assert s == 200 and st["mode"] == "pna" and st["banners"] == [] and st["network_exposed"] is False

        s, st = _post(home, "/api/disclosure/consent", {"mode": "cloud-exception"})
        assert s == 200 and st["mode"] == "cloud-exception" and "cloud-exception" in st["banners"]
        assert st["consented_at"]

        s, st = _post(home, "/api/disclosure/return-to-pna", {})
        assert s == 200 and st["mode"] == "pna" and st["banners"] == []


def test_local_ai_banner_and_bad_mode_refused():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        _, st = _post(home, "/api/disclosure/consent", {"mode": "local-ai"})
        assert st["mode"] == "local-ai" and st["banners"] == ["local-ai"]   # informative, not the cloud banner
        s, err = _post(home, "/api/disclosure/consent", {"mode": "bogus"})
        assert s == 400 and "error" in err


def test_network_exposed_raises_its_own_banner():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        _, st = _get(home, "/api/disclosure", network_exposed=True)
        assert st["network_exposed"] is True and "network-exposed" in st["banners"]


def test_connections_inventory_shape():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        s, conn = _get(home, "/api/connections")
        assert s == 200 and conn["network_exposed"] is False
        assert isinstance(conn["mcp_registered"]["claude_desktop"], list)   # honestly bounded, never raises


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
