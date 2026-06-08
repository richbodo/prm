#!/usr/bin/env python3
"""Tests for the v0.1 M3d review API: the daemon's candidate + write surface (GET /api/candidates,
POST /api/merge | /api/reject | /api/undo) driven through the pure ``route()`` — the daemon as the
private-data writer (apply path under the file-lock).

    python tests/unit/test_daemon_review_api.py
    pytest tests/unit/test_daemon_review_api.py
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


def _body(resp) -> dict:
    status, ctype, body = resp
    return json.loads(body)


def _dup_home(tmp: str):
    """Two cross-source records sharing an email → two contacts, one confident duplicate."""
    home = resolve_home(Path(tmp) / "h")
    for name, src in (("Robert Smith", "apple_icloud"), ("Bob Smith", "google_takeout")):
        v = Path(tmp) / f"{src}.vcf"
        v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
        ingest_mod.ingest([v], source=src, home=home, dry_run=False)
    return home


def _confident(home):
    clusters = _body(server.route("GET", "/api/candidates", {}, home))["clusters"]
    return next(c for c in clusters if c["tier"] == "confident")


def test_candidates_route():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        assert len(cl["member_ids"]) == 2 and "email_exact" in cl["signals"]


def test_merge_route_applies():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        into = cl["member_ids"][0]
        status, _, body = server.route("POST", "/api/merge", {}, home,
                                       {"member_ids": cl["member_ids"], "into": into})
        assert status == 200 and json.loads(body)["ok"]
        # the two contacts are now one; no confident candidates remain
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 1
        detail = _body(server.route("GET", f"/api/contact/{into}", {}, home))
        assert detail["member_count"] == 2
        assert not [c for c in _body(server.route("GET", "/api/candidates", {}, home))["clusters"] if c["tier"] == "confident"]


def test_merge_route_validates():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        # `into` not among member_ids → 400
        assert server.route("POST", "/api/merge", {}, home, {"member_ids": cl["member_ids"], "into": "nope"})[0] == 400
        assert server.route("POST", "/api/merge", {}, home, {})[0] == 400


def test_reject_route_excludes():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        key = _confident(home)["key"]
        assert _body(server.route("POST", "/api/reject", {}, home, {"key": key}))["ok"]
        assert key not in {c["key"] for c in _body(server.route("GET", "/api/candidates", {}, home))["clusters"]}
        assert server.route("POST", "/api/reject", {}, home, {})[0] == 400      # missing key


def test_undo_route_restores():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        server.route("POST", "/api/merge", {}, home, {"member_ids": cl["member_ids"], "into": cl["member_ids"][0]})
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 1
        assert _body(server.route("POST", "/api/undo", {}, home, {}))["ok"]
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 2   # merge reversed


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
