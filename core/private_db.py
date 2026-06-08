"""private.db — the private store (identity_map, field_resolutions, decisions, settings).

A leaf, like ``shared_db``: it does **not** import ``cli`` or ``shared_db``. Two sanctioned writers,
serialized by the AC-PRM-C file-lock — the ingester seeds the 1:1 identity baseline at import
(``seed_identities``), and the daemon applies approved merges (M3c). The canonical contact a user sees
is a projection over this store + shared.db (see ``core/projection.py``). Rationale:
``docs/design-notes/dedupe-design.md``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
_SCHEMA_SQL = (Path(__file__).resolve().parent / "schema" / "private.sql").read_text(encoding="utf-8")

# Default reconcile order: user-curated address books (Apple/Google) outrank scraped professional /
# social graphs (LinkedIn/Facebook). Stored in `settings` at init so it is config, not hardcoded —
# validate against real exports (docs/design-notes/dedupe-design.md).
DEFAULT_SOURCE_PRIORITY = ["apple_icloud", "google_takeout", "google_csv", "vcard", "linkedin", "facebook"]


class PrivateDbError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- connections / schema
def connect(db_path, *, read_only: bool = False) -> sqlite3.Connection:
    db_path = Path(db_path)
    if read_only:
        if not db_path.exists():
            raise PrivateDbError(f"no private.db at {db_path}")
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        con.executescript(_SCHEMA_SQL)
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('source_priority', ?)",
            (json.dumps(DEFAULT_SOURCE_PRIORITY),),
        )
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
    elif version != SCHEMA_VERSION:
        raise PrivateDbError(
            f"private.db schema version {version} != expected {SCHEMA_VERSION} (incompatible build)"
        )


def ensure(db_path) -> None:
    """Create the store + schema if absent (idempotent)."""
    con = connect(db_path)
    try:
        _ensure_schema(con)
    finally:
        con.close()


# --------------------------------------------------------------------------- write (seed)
def seed_identities(db_path, source_record_ids, *, created_at: str | None = None) -> int:
    """1:1 baseline: every *new* source record becomes its own canonical contact. Idempotent —
    records already in the identity_map are skipped, so prior merge decisions survive re-import.
    Returns the number of newly-seeded records."""
    created_at = created_at or _now_iso()
    con = connect(db_path)
    try:
        _ensure_schema(con)
        cur = con.cursor()
        seeded = 0
        try:
            for srid in source_record_ids:
                if cur.execute("SELECT 1 FROM identity_map WHERE source_record_id = ?", (srid,)).fetchone():
                    continue
                cur.execute("INSERT OR IGNORE INTO contacts(contact_id, created_at) VALUES (?, ?)", (srid, created_at))
                cur.execute("INSERT INTO identity_map(source_record_id, contact_id) VALUES (?, ?)", (srid, srid))
                seeded += 1
            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()
    return seeded


# --------------------------------------------------------------------------- read
def identity_map(db_path) -> dict:
    """{source_record_id: contact_id} for every mapped record."""
    con = connect(db_path, read_only=True)
    try:
        return dict(con.execute("SELECT source_record_id, contact_id FROM identity_map").fetchall())
    finally:
        con.close()


def field_resolutions(db_path) -> dict:
    """{(contact_id, field): (chosen_value, chosen_source)} for applied resolutions."""
    con = connect(db_path, read_only=True)
    try:
        rows = con.execute(
            "SELECT contact_id, field, chosen_value, chosen_source FROM field_resolutions"
        ).fetchall()
        return {(c, f): (v, s) for c, f, v, s in rows}
    finally:
        con.close()


def source_priority(db_path) -> list:
    """The reconcile order from settings (falls back to the default if unset)."""
    con = connect(db_path, read_only=True)
    try:
        row = con.execute("SELECT value FROM settings WHERE key = 'source_priority'").fetchone()
    finally:
        con.close()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            pass
    return list(DEFAULT_SOURCE_PRIORITY)


def stats(db_path) -> dict:
    con = connect(db_path, read_only=True)
    try:
        contacts = con.execute("SELECT count(*) FROM contacts").fetchone()[0]
        mapped = con.execute("SELECT count(*) FROM identity_map").fetchone()[0]
        merged = con.execute(
            "SELECT count(*) FROM (SELECT contact_id FROM identity_map GROUP BY contact_id HAVING count(*) > 1)"
        ).fetchone()[0]
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    return {"schema_version": version, "contacts": contacts, "mapped_records": mapped, "merged_contacts": merged}
