#!/usr/bin/env python3
"""private.db version handshake (AC-4, private half): a private store whose `PRAGMA user_version` does
not match the build's `SCHEMA_VERSION` **refuses to mutate** — every sanctioned writer goes through
`_ensure_schema`, which raises rather than writing against an incompatible store. Mirrors the shared-side
`test_incompatible_schema_version_is_rejected`.

    python tests/db/test_private_db_version.py
    pytest tests/db/test_private_db_version.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core import private_db  # noqa: E402


def _stamp_wrong_version(db: Path) -> None:
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version = 99")                  # an incompatible build wrote this store
    con.commit()
    con.close()


def test_private_mismatch_refuses_seed():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "private.db"
        _stamp_wrong_version(db)
        try:
            private_db.seed_identities(db, ["a", "b"])       # the ingester's private write
        except private_db.PrivateDbError:
            return
        raise AssertionError("expected PrivateDbError for a private.db schema-version mismatch")


def test_private_mismatch_refuses_apply():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "private.db"
        _stamp_wrong_version(db)
        try:
            private_db.apply_operations(db, [{"op": "merge", "source_record_ids": ["x"], "into": "y"}])
        except private_db.PrivateDbError:
            return
        raise AssertionError("expected PrivateDbError on the daemon apply path too")


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
