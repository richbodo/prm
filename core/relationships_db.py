"""relationships.db — the private store (identity_map, field_resolutions, decisions, settings).

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

SCHEMA_VERSION = 2
_MIGRATABLE_FROM = {0, 1}   # 0 = a fresh store; 1 = a v0.1 store (pre relationship-schema) → bring up to v2
_SCHEMA_SQL = (Path(__file__).resolve().parent / "schema" / "relationships.sql").read_text(encoding="utf-8")

# Default reconcile order: user-curated address books (Apple/Google) outrank scraped professional /
# social graphs (LinkedIn/Facebook). Stored in `settings` at init so it is config, not hardcoded —
# validate against real exports (docs/design-notes/dedupe-design.md).
DEFAULT_SOURCE_PRIORITY = ["apple_icloud", "google_takeout", "google_csv", "vcard", "linkedin", "facebook"]


class RelationshipsDbError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- v0.1 → v0.2 migration
_LEGACY_NAME = "private.db"   # the v0.1 store name; renamed to relationships.db on first v0.2 run


def migrate_legacy(new_path, legacy_path=None) -> bool:
    """One-time rename of the v0.1 ``private.db`` store to ``relationships.db`` (with its ``-wal`` /
    ``-shm`` sidecars, so an uncheckpointed WAL is preserved and replayed against the renamed file).

    Idempotent and safe: a no-op if the new store already exists or no legacy store is present. The
    schema is unchanged by the rename (still v1), so the renamed file opens directly. Returns True iff
    a rename happened. Pure path I/O — keeps this module a leaf (it never imports ``cli``)."""
    new_path = Path(new_path)
    legacy_path = Path(legacy_path) if legacy_path is not None else new_path.with_name(_LEGACY_NAME)
    if new_path.exists() or not legacy_path.exists():
        return False
    for suffix in ("", "-wal", "-shm"):
        src = legacy_path.with_name(legacy_path.name + suffix)
        if src.exists():
            src.rename(new_path.with_name(new_path.name + suffix))
    return True


# --------------------------------------------------------------------------- connections / schema
def connect(db_path, *, read_only: bool = False) -> sqlite3.Connection:
    db_path = Path(db_path)
    migrate_legacy(db_path)                      # defense: never create a fresh store beside a legacy one
    if read_only:
        if not db_path.exists():
            raise RelationshipsDbError(f"no relationships.db at {db_path}")
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version in _MIGRATABLE_FROM:
        # Idempotent: every CREATE is IF NOT EXISTS and every built-in seed is INSERT OR IGNORE, so a
        # fresh store (v0) is built whole and a v0.1 store (v1) gets only the v2 delta — the new
        # field_definitions / field_values tables + built-in fields — leaving its v1 data untouched.
        # That is the v1→v2 migration; no destructive ALTERs.
        con.executescript(_SCHEMA_SQL)
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('source_priority', ?)",
            (json.dumps(DEFAULT_SOURCE_PRIORITY),),
        )
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
    elif version != SCHEMA_VERSION:
        raise RelationshipsDbError(
            f"relationships.db schema version {version} != expected {SCHEMA_VERSION} (incompatible build)"
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


# --------------------------------------------------------------------------- write (apply)
def apply_operations(db_path, operations) -> list:
    """Run a changeset's operations in **one transaction** (the daemon calls this under the file-lock,
    after snapshotting). Returns the applied ops for the audit log; rolls back on any error.

    - ``merge``: re-point the given ``source_record_ids`` at ``into``; orphaned contacts (now with no
      records) are deleted, cascading their dangling ``field_resolutions``.
    - ``resolve_field``: upsert the chosen value (a manual edit is just ``rule="user"``).
    - ``flag_conflict``: record the field ``needs_review`` (default value = the first candidate).
    """
    con = connect(db_path)
    try:
        _ensure_schema(con)
        cur = con.cursor()
        applied = []
        try:
            for op in operations:
                kind = op.get("op")
                if kind == "merge":
                    into = op["into"]
                    cur.execute("INSERT OR IGNORE INTO contacts(contact_id, created_at) VALUES (?, ?)", (into, _now_iso()))
                    for srid in op["source_record_ids"]:
                        cur.execute("UPDATE identity_map SET contact_id = ? WHERE source_record_id = ?", (into, srid))
                    cur.execute("DELETE FROM contacts WHERE contact_id NOT IN (SELECT contact_id FROM identity_map)")
                elif kind == "resolve_field":
                    cur.execute(
                        "INSERT OR REPLACE INTO field_resolutions"
                        "(contact_id, field, chosen_value, chosen_source, rule, status, resolved_by, resolved_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (op["contact_id"], op["field"], op.get("chosen_value"), op.get("chosen_source"),
                         op.get("rule", "user"), op.get("status", "applied"), op.get("resolved_by"), _now_iso()),
                    )
                elif kind == "flag_conflict":
                    default = (op.get("candidates") or [{}])[0]
                    cur.execute(
                        "INSERT OR REPLACE INTO field_resolutions"
                        "(contact_id, field, chosen_value, chosen_source, rule, status, resolved_at) "
                        "VALUES (?, ?, ?, ?, 'needs_review', 'needs_review', ?)",
                        (op["contact_id"], op["field"], default.get("value"), default.get("source"), _now_iso()),
                    )
                else:
                    raise RelationshipsDbError(f"unknown changeset op: {kind!r}")
                applied.append(op)
            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()
    return applied


def record_decision(db_path, pair_key: str, decision: str) -> None:
    """Persist a dedup decision ('not_duplicate' | 'merged') keyed on the stable cluster key."""
    con = connect(db_path)
    try:
        _ensure_schema(con)
        con.execute(
            "INSERT OR REPLACE INTO dedup_decisions(pair_key, decision, decided_at) VALUES (?, ?, ?)",
            (pair_key, decision, _now_iso()),
        )
        con.commit()
    finally:
        con.close()


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


def rejected_pairs(db_path) -> set:
    """Cluster/pair keys the user marked 'not a duplicate' — excluded from re-detection so a dismissed
    candidate never resurfaces (survives re-import, keyed on the stable cluster key)."""
    if not Path(db_path).exists():
        return set()
    con = connect(db_path, read_only=True)
    try:
        return {k for (k,) in con.execute(
            "SELECT pair_key FROM dedup_decisions WHERE decision = 'not_duplicate'")}
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
