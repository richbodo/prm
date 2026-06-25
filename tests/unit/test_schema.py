#!/usr/bin/env python3
"""Tests for the v0.2 R1 relationship-schema substrate: the seeded built-in fields, the field-definition
CRUD (`core/schema.py`), the builtin/user class rules (INV-3), the value→definition foreign-key lock
(INV-3/4: a value for an undefined field cannot be written), and the v1→v2 migration.

    python tests/unit/test_schema.py
    pytest tests/unit/test_schema.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core import relationships_db, schema  # noqa: E402


def _store(tmp: str) -> Path:
    db = Path(tmp) / "relationships.db"
    relationships_db.ensure(db)                 # current schema + built-in fields seeded
    relationships_db.seed_identities(db, ["c1"])  # a real contact, for the value FK
    return db


# ------------------------------------------------------------------ seeded built-ins
def test_builtins_seeded():
    with tempfile.TemporaryDirectory() as tmp:
        db = _store(tmp)
        by_id = {f["field_id"]: f for f in schema.list_fields(db)}
        assert set(by_id) >= {"photo", "tags", "notes"}
        for fid, kind in (("photo", "image"), ("tags", "multi_select"), ("notes", "long_text")):
            f = by_id[fid]
            assert f["kind"] == kind
            assert f["class"] == "builtin"
            assert f["ai_write_policy"] == "review-required"   # INV-10 most-protective default
            assert f["disclosure_tier"] == "private-sealed"    # PR-7 most-protective default
        assert by_id["tags"]["config"]["options"] == []        # managed vocab starts empty (filled in R4)
        # photo (avatar) sorts before tags before notes
        order = [f["field_id"] for f in schema.list_fields(db) if f["class"] == "builtin"]
        assert order == ["photo", "tags", "notes"]


# ------------------------------------------------------------------ define / get user fields
def test_define_and_get_user_field():
    with tempfile.TemporaryDirectory() as tmp:
        db = _store(tmp)
        fid = schema.define_field(db, label="How We Met", kind="date")
        assert fid == "how_we_met"
        f = schema.get_field(db, fid)
        assert f and f["class"] == "user" and f["kind"] == "date"
        assert f["disclosure_tier"] == "private-sealed"        # default sealed even for user fields
        assert any(x["field_id"] == fid for x in schema.list_fields(db))


def test_define_validation_rejects_bad_input():
    with tempfile.TemporaryDirectory() as tmp:
        db = _store(tmp)
        for kw in ({"label": "X", "kind": "rainbow"},               # unknown kind
                   {"label": "X", "kind": "text", "field_id": "9bad"},  # bad slug
                   {"label": "X", "kind": "text", "ai_write_policy": "yolo"}):  # unknown policy
            try:
                schema.define_field(db, **kw)
            except schema.SchemaError:
                continue
            raise AssertionError(f"expected SchemaError for {kw}")
        schema.define_field(db, label="Dup", kind="text", field_id="dup")
        try:
            schema.define_field(db, label="Dup2", kind="text", field_id="dup")
            raise AssertionError("expected SchemaError on duplicate field_id")
        except schema.SchemaError:
            pass


def test_select_config_is_normalized():
    with tempfile.TemporaryDirectory() as tmp:
        db = _store(tmp)
        fid = schema.define_field(db, label="Vibe", kind="single_select",
                                  config={"options": ["warm", {"value": "cool", "description": "distant"}]})
        opts = schema.get_field(db, fid)["config"]["options"]
        assert opts[0] == {"value": "warm", "label": "warm", "description": ""}
        assert opts[1] == {"value": "cool", "label": "cool", "description": "distant"}


# ------------------------------------------------------------------ update / class rules
def test_update_mutable_attrs_and_immutability():
    with tempfile.TemporaryDirectory() as tmp:
        db = _store(tmp)
        fid = schema.define_field(db, label="Project", kind="text")
        updated = schema.update_field(db, fid, label="Current project",
                                      disclosure_tier="private-shareable-on-consent")
        assert updated["label"] == "Current project"
        assert updated["disclosure_tier"] == "private-shareable-on-consent"
        for bad in ({"kind": "number"}, {"class": "builtin"}, {"bogus": 1}):
            try:
                schema.update_field(db, fid, **bad)
                raise AssertionError(f"expected SchemaError changing {bad}")
            except schema.SchemaError:
                pass
        # a builtin's *mutable* attrs are editable (e.g. the tag vocabulary)
        schema.update_field(db, "tags", config={"options": [{"value": "family", "description": "kin"}]})
        assert schema.get_field(db, "tags")["config"]["options"][0]["value"] == "family"


def test_remove_user_field_but_not_builtin():
    with tempfile.TemporaryDirectory() as tmp:
        db = _store(tmp)
        fid = schema.define_field(db, label="Temp", kind="text")
        schema.remove_field(db, fid)
        assert schema.get_field(db, fid) is None
        for builtin in ("tags", "notes", "photo"):
            try:
                schema.remove_field(db, builtin)
                raise AssertionError(f"expected SchemaError removing builtin {builtin}")
            except schema.SchemaError:
                pass


# ------------------------------------------------------------------ the INV-3/4 value→definition FK lock
def test_field_value_foreign_key_lock():
    with tempfile.TemporaryDirectory() as tmp:
        db = _store(tmp)
        con = relationships_db.connect(db)                   # foreign_keys=ON for writes
        try:
            # a value for a DEFINED field on a REAL contact is accepted
            con.execute(
                "INSERT INTO field_values(value_id, contact_id, field_id, value, written_by, written_at) "
                "VALUES ('v1', 'c1', 'notes', 'hello', 'manual:user', '2026-06-16T00:00:00Z')")
            con.commit()
            # a value for an UNDEFINED field is refused (INV-3/4 structural lock)
            for cid, fid in (("c1", "no_such_field"), ("no_such_contact", "notes")):
                try:
                    con.execute(
                        "INSERT INTO field_values(value_id, contact_id, field_id, value, written_by, written_at) "
                        "VALUES ('vx', ?, ?, 'x', 'manual:user', '2026-06-16T00:00:00Z')", (cid, fid))
                    con.commit()
                    raise AssertionError(f"expected FK violation for ({cid}, {fid})")
                except sqlite3.IntegrityError:
                    con.rollback()
        finally:
            con.close()


# ------------------------------------------------------------------ migration up to the current schema
def test_v1_store_is_migrated_to_current():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "relationships.db"
        relationships_db.ensure(db)
        # roll the store back to a v0.1 shape: drop every post-v1 table, stamp user_version = 1
        con = sqlite3.connect(db)
        con.executescript("DROP TABLE observations; DROP TABLE field_values; DROP TABLE field_definitions; "
                           "PRAGMA user_version = 1;")
        con.commit()
        con.close()

        relationships_db.ensure(db)                          # ensure() must migrate v1 → current
        con = sqlite3.connect(db)
        try:
            assert con.execute("PRAGMA user_version").fetchone()[0] == relationships_db.SCHEMA_VERSION
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"field_definitions", "field_values", "observations"} <= tables
        finally:
            con.close()
        assert {f["field_id"] for f in schema.list_fields(db)} >= {"photo", "tags", "notes"}


def test_v2_store_gains_observations():
    # A v0.2 store (relationship schema present, no observations table) is migrated to v3 in place — the
    # R11b delta, applied idempotently with no destructive ALTERs.
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "relationships.db"
        relationships_db.ensure(db)
        con = sqlite3.connect(db)
        con.executescript("DROP TABLE observations; PRAGMA user_version = 2;")    # roll back to a v2 shape
        con.commit()
        con.close()

        relationships_db.ensure(db)                          # ensure() adds the observations table → v3
        con = sqlite3.connect(db)
        try:
            assert con.execute("PRAGMA user_version").fetchone()[0] == relationships_db.SCHEMA_VERSION
            assert "observations" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()


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
