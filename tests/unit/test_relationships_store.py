#!/usr/bin/env python3
"""Tests for the v0.1 M3a private store + projection: schema bootstrap, 1:1 identity seeding
(idempotent, re-import-safe), two-store separation, and the canonical-contact projection
(grouping + union/reconcile survivorship).

Runs as a plain script or under pytest.

    python tests/unit/test_relationships_store.py
    pytest tests/unit/test_relationships_store.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import relationships_db, projection, shared_db  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"


def _imported_home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    report = ingest_mod.ingest([APPLE], home=home, dry_run=False)
    assert report.persisted and report.stored == 4
    return home


# ------------------------------------------------------------------ schema + seeding
def test_schema_bootstrap_and_settings():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "relationships.db"
        relationships_db.ensure(db)
        assert db.exists()
        con = relationships_db.connect(db, read_only=True)
        try:
            assert con.execute("PRAGMA user_version").fetchone()[0] == relationships_db.SCHEMA_VERSION
        finally:
            con.close()
        # source_priority is seeded as config, Apple/Google ahead of LinkedIn/Facebook
        prio = relationships_db.source_priority(db)
        assert prio.index("apple_icloud") < prio.index("linkedin") < prio.index("facebook")


# ------------------------------------------------------------------ v0.1 → v0.2 migration (R0)
def test_legacy_private_db_is_migrated_with_data():
    """A v0.1 home whose private store is still named `private.db` is renamed to `relationships.db`
    on first v0.2 access, preserving the identity decisions; the migration is idempotent."""
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        home.create()
        relationships_db.seed_identities(home.legacy_private_db, ["a", "b"])   # write under the OLD name
        assert home.legacy_private_db.exists() and not home.relationships_db.exists()

        assert relationships_db.migrate_legacy(home.relationships_db, home.legacy_private_db) is True
        assert home.relationships_db.exists() and not home.legacy_private_db.exists()
        assert relationships_db.identity_map(home.relationships_db) == {"a": "a", "b": "b"}  # data preserved

        # idempotent + safe: nothing to do once migrated (and never when no legacy store exists)
        assert relationships_db.migrate_legacy(home.relationships_db, home.legacy_private_db) is False


def test_legacy_migration_moves_wal_sidecars_and_is_noop_when_absent():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        home.create()
        # no legacy store at all → no-op
        assert relationships_db.migrate_legacy(home.relationships_db, home.legacy_private_db) is False
        # a legacy store plus its WAL sidecars → all three move under the new name (WAL preserved)
        legacy = home.legacy_private_db
        legacy.write_bytes(b"db")
        (legacy.parent / (legacy.name + "-wal")).write_bytes(b"wal")
        (legacy.parent / (legacy.name + "-shm")).write_bytes(b"shm")
        assert relationships_db.migrate_legacy(home.relationships_db, home.legacy_private_db) is True
        new = home.relationships_db
        assert new.read_bytes() == b"db" and not legacy.exists()
        assert (new.parent / (new.name + "-wal")).read_bytes() == b"wal"
        assert (new.parent / (new.name + "-shm")).read_bytes() == b"shm"


def test_seed_1to1_and_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "relationships.db"
        assert relationships_db.seed_identities(db, ["a", "b", "c"]) == 3
        imap = relationships_db.identity_map(db)
        assert imap == {"a": "a", "b": "b", "c": "c"}          # 1:1 baseline
        # re-seeding existing ids is a no-op; only the genuinely new one is added…
        assert relationships_db.seed_identities(db, ["a", "b", "c", "d"]) == 1
        # …and an existing *merge* decision is preserved across a re-seed.
        con = relationships_db.connect(db)
        con.execute("UPDATE identity_map SET contact_id='a' WHERE source_record_id='b'")  # b merged into a
        con.commit(); con.close()
        assert relationships_db.seed_identities(db, ["a", "b"]) == 0
        assert relationships_db.identity_map(db)["b"] == "a"          # merge survived


def test_import_seeds_private_and_keeps_shared_intact():
    with tempfile.TemporaryDirectory() as tmp:
        home = _imported_home(tmp)
        # import seeded a 1:1 baseline: one contact per source record, none merged yet
        st = relationships_db.stats(home.relationships_db)
        assert st["contacts"] == 4 and st["mapped_records"] == 4 and st["merged_contacts"] == 0
        # the private write did not touch the raw store (INV-2): shared.db still holds 4 records
        assert shared_db.stats(home.shared_db)["records"] == 4
        # every shared record is mapped
        assert set(relationships_db.identity_map(home.relationships_db)) == \
            {r["id"] for r in shared_db.list_records(home.shared_db, limit=10)["records"]}


# ------------------------------------------------------------------ projection
def test_projection_baseline_one_per_record():
    with tempfile.TemporaryDirectory() as tmp:
        home = _imported_home(tmp)
        page = projection.list_contacts(home, limit=10)
        assert page["total"] == 4
        assert all(c["member_count"] == 1 for c in page["records"])       # 1:1 until merged
        names = [c["name"] for c in page["records"] if c["name"]]
        assert names == sorted(names, key=str.casefold)                   # named-first, A–Z


def test_projection_merges_records():
    with tempfile.TemporaryDirectory() as tmp:
        home = _imported_home(tmp)
        ids = list(relationships_db.identity_map(home.relationships_db))
        a, b = ids[0], ids[1]
        con = relationships_db.connect(home.relationships_db)                          # merge b into a
        con.execute("UPDATE identity_map SET contact_id=? WHERE source_record_id=?", (a, b))
        con.commit(); con.close()

        assert projection.list_contacts(home, limit=10)["total"] == 3      # 4 → 3 after the merge
        merged = projection.get_contact(home, a)
        assert merged["member_count"] == 2
        emails = next((f for f in merged["fields"] if f["name"] == "email"), None)
        assert emails and emails["kind"] == "union" and len(emails["values"]) >= 2   # both kept
        assert projection.get_contact(home, b) is None                     # b is no longer its own contact


# ------------------------------------------------------------------ merge semantics (unit)
def test_merge_unions_multivalued():
    members = [
        {"source": "apple_icloud", "ingested_at": "2020", "props": [["email", {}, "text", "a@x.com"]]},
        {"source": "google_takeout", "ingested_at": "2021", "props": [["email", {}, "text", "b@y.com"], ["email", {}, "text", "a@x.com"]]},
    ]
    merged = projection._merge("c1", members, {"apple_icloud": 0, "google_takeout": 1}, {})
    email = next(f for f in merged["fields"] if f["name"] == "email")
    assert email["kind"] == "union" and {v["value"] for v in email["values"]} == {"a@x.com", "b@y.com"}


def test_merge_reconcile_prefers_source_priority():
    # fn conflicts; apple (rank 0) wins over linkedin (rank 4) even though linkedin's row is newer.
    members = [
        {"source": "linkedin", "ingested_at": "2026-01-01", "props": [["fn", {}, "text", "Bob L"]]},
        {"source": "apple_icloud", "ingested_at": "2020-01-01", "props": [["fn", {}, "text", "Robert L"]]},
    ]
    merged = projection._merge("c1", members, {"apple_icloud": 0, "linkedin": 4}, {})
    fn = next(f for f in merged["fields"] if f["name"] == "fn")
    assert fn["kind"] == "single" and fn["values"][0]["value"] == "Robert L"


def test_merge_resolution_overrides_survivorship():
    members = [{"source": "apple_icloud", "ingested_at": "2020", "props": [["fn", {}, "text", "Robert L"]]}]
    merged = projection._merge("c1", members, {"apple_icloud": 0}, {("c1", "fn"): ("Bob (chosen)", "user")})
    fn = next(f for f in merged["fields"] if f["name"] == "fn")
    assert fn["values"][0]["value"] == "Bob (chosen)"


def test_preview_merge_marks_conflicts():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        for name, src in (("Robert Smith", "apple_icloud"), ("Bob Smith", "google_takeout")):
            v = Path(tmp) / f"{src}.vcf"
            v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
            ingest_mod.ingest([v], source=src, home=home, dry_run=False)
        ids = list(relationships_db.identity_map(home.relationships_db))
        pv = projection.preview_merge(home, ids)
        fn = next(f for f in pv["fields"] if f["name"] == "fn")
        assert fn["kind"] == "conflict" and len(fn["options"]) == 2 and "fn" in pv["conflicts"]
        email = next(f for f in pv["fields"] if f["name"] == "email")
        assert email["kind"] == "union" and len(email["values"]) == 1   # same email → one value, no conflict


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
