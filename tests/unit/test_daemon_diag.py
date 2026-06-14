#!/usr/bin/env python3
"""Daemon AC-7 (`GET /api/diag`, sanitized) + AC-4 (a schema-version mismatch refuses **mutations**
cleanly with a 409 — never a 500/raw traceback — while **reads continue**), driven through `route()`.

    python tests/unit/test_daemon_diag.py
    pytest tests/unit/test_daemon_diag.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from daemon import server  # noqa: E402


def _body(resp) -> dict:
    return json.loads(resp[2])


def _home(tmp):
    home = resolve_home(Path(tmp) / "h")
    for i, (name, email) in enumerate([("Ada Lovelace", "ada@example.org"), ("Alan Turing", "alan@example.uk")]):
        v = Path(tmp) / f"{i}.vcf"
        v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:{email}\r\nEND:VCARD\r\n", encoding="utf-8")
        ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home


def test_diag_route_sanitized():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        status, _, b = server.route("GET", "/api/diag", {}, home)
        d = json.loads(b)
        assert status == 200 and d["build_label"] and "shared" in d["stores"]
        assert "ada@example.org" not in b.decode() and "Ada Lovelace" not in b.decode()


def test_schema_mismatch_refuses_write_reads_continue():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        con = sqlite3.connect(home.relationships_db)               # an incompatible build wrote relationships.db
        con.execute("PRAGMA user_version = 99"); con.commit(); con.close()

        contacts = _body(server.route("GET", "/api/contacts", {}, home))
        assert contacts["total"] >= 2                        # reads continue (no version-check on the read path)

        ids = [r["id"] for r in contacts["records"][:2]]
        status, _, b = server.route("POST", "/api/merge", {}, home, {"member_ids": ids, "into": ids[0]})
        assert status == 409 and status != 500               # mutation refused cleanly, not a 500/traceback
        assert "mutations refused" in _body((status, _, b))["error"]


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
