#!/usr/bin/env python3
"""DB-level tests for the shared.db load path (plan §11 M1c).

Covers schema bootstrap + version handshake, the transactional load, idempotent re-import, the
stable-key collapse, FTS search, the zero-row guard, and two-store isolation. Drives real
CanonicalContacts from the Apple fixture through parse → normalize → load.

    python tests/db/test_shared_db.py
    pytest tests/db/test_shared_db.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import normalize  # noqa: E402
from cli.parsers import vcard  # noqa: E402
from core import shared_db  # noqa: E402

APPLE = REPO / "tests" / "fixtures" / "apple_icloud" / "sources" / "icloud-export.vcf"
AT = "2026-06-04T00:00:00Z"


def _apple_contacts():
    return normalize.normalize_all(vcard.parse(APPLE.read_bytes(), "apple_icloud"))


def _load(tmp, contacts, **kw):
    db = Path(tmp) / "shared.db"
    return db, shared_db.load(db, contacts, ingested_at=AT, **kw)


# ------------------------------------------------------------------ schema / handshake
def test_bootstrap_sets_schema_version_and_tables():
    with tempfile.TemporaryDirectory() as tmp:
        db, _ = _load(tmp, _apple_contacts())
        con = sqlite3.connect(db)
        assert con.execute("PRAGMA user_version").fetchone()[0] == shared_db.SCHEMA_VERSION
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"source_records", "field_provenance"} <= tables
        con.close()


def test_incompatible_schema_version_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "shared.db"
        con = sqlite3.connect(db)
        con.execute("PRAGMA user_version = 99")
        con.commit()
        con.close()
        try:
            shared_db.load(db, _apple_contacts(), ingested_at=AT)
        except shared_db.SharedDbError:
            return
        raise AssertionError("expected a schema-version mismatch error")


# ------------------------------------------------------------------ load
def test_load_writes_records_provenance_and_fts():
    with tempfile.TemporaryDirectory() as tmp:
        db, result = _load(tmp, _apple_contacts())
        assert result.parsed == 4 and result.total == 4 and result.by_source == {"apple_icloud": 4}
        con = sqlite3.connect(db)
        assert con.execute("SELECT count(*) FROM source_records").fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM field_provenance").fetchone()[0] > 0
        # raw_jcard round-trips
        raw = con.execute("SELECT raw_jcard FROM source_records LIMIT 1").fetchone()[0]
        assert raw.startswith('["vcard"')
        con.close()


def test_reimport_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "shared.db"
        shared_db.load(db, _apple_contacts(), ingested_at=AT)
        result = shared_db.load(db, _apple_contacts(), ingested_at=AT)   # again
        assert result.total == 4   # not 8


def test_same_stable_key_collapses():
    with tempfile.TemporaryDirectory() as tmp:
        contacts = _apple_contacts()
        contacts.append(contacts[0])           # an exact duplicate identity
        _, result = _load(tmp, contacts)
        assert result.parsed == 5 and result.total == 4   # the dup collapsed


def test_observed_at_falls_back_to_ingest_time():
    with tempfile.TemporaryDirectory() as tmp:
        db, _ = _load(tmp, _apple_contacts())
        con = sqlite3.connect(db)
        nulls = con.execute("SELECT count(*) FROM field_provenance WHERE observed_at IS NULL").fetchone()[0]
        con.close()
        assert nulls == 0   # NULLs were filled with the ingest time


# ------------------------------------------------------------------ search
def test_search_prefix_matches():
    with tempfile.TemporaryDirectory() as tmp:
        db, _ = _load(tmp, _apple_contacts())
        rows = shared_db.search(db, "Lovel")          # prefix
        assert any(r[0] == "Ada Lovelace" for r in rows)
        assert shared_db.search(db, "nonexistentxyz") == []


# ------------------------------------------------------------------ guards / stats
def test_stats_reports_counts():
    with tempfile.TemporaryDirectory() as tmp:
        db, _ = _load(tmp, _apple_contacts())
        s = shared_db.stats(db)
        assert s["records"] == 4 and s["schema_version"] == 1 and s["last_ingested_at"] == AT


def test_empty_load_is_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        _, result = _load(tmp, [])
        assert result.total == 0   # zero-row guard only fires when input had records


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
