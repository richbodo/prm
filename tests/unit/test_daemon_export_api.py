#!/usr/bin/env python3
"""Tests for the workspace export surface (D2): GET /api/export downloads the merged vCard or the
lossless raw backup. The raw backup is round-tripped back through the import surface (D1) to prove the
two halves compose. Drives the pure ``route()`` — no socket.

    python tests/unit/test_daemon_export_api.py
    pytest tests/unit/test_daemon_export_api.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import shared_db  # noqa: E402
from daemon import server  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"


def _seeded_home(tmp):
    home = resolve_home(Path(tmp) / "h")
    report = ingest_mod.ingest([APPLE], home=home, dry_run=False)
    assert report.persisted and report.stored == 4
    return home


def _get(home, query):
    resp = server.route("GET", "/api/export", query, home)
    headers = resp[3] if len(resp) > 3 else {}
    return resp[0], resp[1], resp[2], headers


def _post(home, path, body):
    status, ctype, body_bytes = server.route("POST", path, {}, home, body)[:3]
    return status, json.loads(body_bytes)


def test_export_vcard_downloads_with_filename():
    with tempfile.TemporaryDirectory() as tmp:
        home = _seeded_home(tmp)
        status, ctype, body, headers = _get(home, {"format": ["vcard"]})
        assert status == 200 and ctype.startswith("text/vcard")
        assert b"BEGIN:VCARD" in body and body.count(b"BEGIN:VCARD") == 4
        assert ".vcf" in headers.get("Content-Disposition", "")


def test_export_raw_roundtrips_through_import():
    with tempfile.TemporaryDirectory() as tmp:
        home = _seeded_home(tmp)
        status, ctype, body, headers = _get(home, {"raw": ["1"]})
        assert status == 200 and ctype.startswith("application/json")
        assert ".json" in headers.get("Content-Disposition", "")
        assert "prm_backup" in body.decode("utf-8")            # detect_format keys on this marker

        # round-trip: stage + commit the backup bytes into a fresh home, expect identical stable keys
        home2 = resolve_home(Path(tmp) / "h2")
        b64 = base64.b64encode(body).decode("ascii")
        _, out = _post(home2, "/api/import/stage", {"relpath": "backup.json", "data_base64": b64})
        _, cm = _post(home2, "/api/import/commit", {"token": out["token"]})
        assert cm["report"]["stored"] == 4

        orig = {r["stable_key"] for r in shared_db.all_records(home.shared_db)}
        roundtripped = {r["stable_key"] for r in shared_db.all_records(home2.shared_db)}
        assert orig == roundtripped


def test_export_without_shared_db_is_409():
    with tempfile.TemporaryDirectory() as tmp:
        empty = resolve_home(Path(tmp) / "h")
        assert _get(empty, {"format": ["vcard"]})[0] == 409


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
