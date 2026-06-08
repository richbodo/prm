"""shared.db — load and read the raw mirrored-contacts store.

The only writer of the Shared DB (INV-2). Loads canonical contacts into `source_records` +
`field_provenance` + `contacts_fts`, transactionally, with a `PRAGMA user_version` handshake (AC-4),
WAL journaling, and a post-write `quick_check` + zero-row guard (IN-2).

`load()` is given **CanonicalContact-shaped** objects (see `cli.normalize.CanonicalContact`): it reads
`.source`, `.source_uid`, `.stable_key.as_str()`, `.jcard` (a `cli.jcard.JCard`), and `.provenance`.
It does not import `cli`, so the store stays a leaf the ingester depends on — not the other way round.

Re-import is **idempotent**: each record's primary key is `sha1(source ∥ stable_key)`, so importing
the same record again upserts the same row (re-attaching any future identity_map decision keyed on it,
plan §11 M3) rather than duplicating. The full non-destructive re-import-with-orphan-preview flow is a
later milestone (INV-6, plan §11 M5); this is the plain idempotent load.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
_SCHEMA_SQL = (Path(__file__).resolve().parent / "schema" / "shared.sql").read_text(encoding="utf-8")


class SharedDbError(RuntimeError):
    pass


@dataclass
class LoadResult:
    parsed: int            # records handed to load()
    total: int             # rows in source_records afterwards (cumulative across imports)
    by_source: dict        # {source: rows-in-db}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_record_id(source: str, stable_key_str: str) -> str:
    return hashlib.sha1(f"{source}\x1f{stable_key_str}".encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- connections / schema
def connect(db_path, *, read_only: bool = False) -> sqlite3.Connection:
    db_path = Path(db_path)
    if read_only:
        if not db_path.exists():
            raise SharedDbError(f"no shared.db at {db_path}")
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        con.executescript(_SCHEMA_SQL)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
    elif version != SCHEMA_VERSION:
        raise SharedDbError(
            f"shared.db schema version {version} != expected {SCHEMA_VERSION} (incompatible build)"
        )


# --------------------------------------------------------------------------- fts projection
def _fts_fields(jcard) -> tuple[str, str, str, str, str]:
    name = jcard.first_value("fn") or ""
    email_prop = jcard.get("email")
    email = str(email_prop.value) if email_prop and email_prop.value else ""
    org_prop = jcard.get("org")
    if org_prop and isinstance(org_prop.value, list):
        org = " ".join(str(v) for v in org_prop.value if v)
    else:
        org = str(org_prop.value) if org_prop and org_prop.value else ""
    title_prop = jcard.get("title")
    title = str(title_prop.value) if title_prop and title_prop.value else ""
    note_prop = jcard.get("note")
    note = str(note_prop.value) if note_prop and note_prop.value else ""
    return name, str(email), org, title, note


# --------------------------------------------------------------------------- write
def load(db_path, records, *, ingested_at: str | None = None) -> LoadResult:
    """Upsert canonical contacts into shared.db. Transactional; validates before returning."""
    ingested_at = ingested_at or _now_iso()
    records = list(records)
    con = connect(db_path)
    try:
        _ensure_schema(con)
        cur = con.cursor()
        try:
            for r in records:
                srid = source_record_id(r.source, r.stable_key.as_str())
                # Idempotent replace: clear dependents first, then upsert the record.
                cur.execute("DELETE FROM field_provenance WHERE source_record_id = ?", (srid,))
                cur.execute("DELETE FROM contacts_fts WHERE source_record_id = ?", (srid,))
                cur.execute(
                    "INSERT OR REPLACE INTO source_records"
                    "(source_record_id, source, source_uid, stable_key, ingested_at, raw_jcard) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (srid, r.source, r.source_uid, r.stable_key.as_str(), ingested_at, r.jcard.to_json()),
                )
                for p in r.provenance:
                    cur.execute(
                        "INSERT INTO field_provenance(source_record_id, field, value, observed_at) "
                        "VALUES (?, ?, ?, ?)",
                        (srid, p.field, p.value, p.observed_at or ingested_at),
                    )
                name, email, org, title, note = _fts_fields(r.jcard)
                cur.execute(
                    "INSERT INTO contacts_fts(source_record_id, name, email, org, title, note) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (srid, name, email, org, title, note),
                )
            con.commit()
        except Exception:
            con.rollback()
            raise

        ok = con.execute("PRAGMA quick_check").fetchone()[0]
        if ok != "ok":
            raise SharedDbError(f"shared.db quick_check failed: {ok!r}")
        total = con.execute("SELECT count(*) FROM source_records").fetchone()[0]
        if records and total == 0:
            raise SharedDbError("zero-row guard (IN-2): records were loaded but shared.db is empty")
        by_source = dict(con.execute("SELECT source, count(*) FROM source_records GROUP BY source").fetchall())
    finally:
        con.close()
    return LoadResult(parsed=len(records), total=total, by_source=by_source)


# --------------------------------------------------------------------------- read
def stats(db_path) -> dict:
    con = connect(db_path, read_only=True)
    try:
        total = con.execute("SELECT count(*) FROM source_records").fetchone()[0]
        distinct = con.execute("SELECT count(DISTINCT stable_key) FROM source_records").fetchone()[0]
        by_source = dict(con.execute("SELECT source, count(*) FROM source_records GROUP BY source").fetchall())
        last = con.execute("SELECT max(ingested_at) FROM source_records").fetchone()[0]
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    return {
        "schema_version": version,
        "records": total,
        "distinct_identities": distinct,
        "by_source": by_source,
        "last_ingested_at": last,
    }


def search(db_path, query: str, *, limit: int = 20) -> list[tuple]:
    """Prefix-AND FTS over name/email/org/note. Returns (name, email, org, source_record_id) rows."""
    terms = re.findall(r"\w+", query)
    if not terms:
        return []
    match = " ".join(f"{t}*" for t in terms)
    con = connect(db_path, read_only=True)
    try:
        return con.execute(
            "SELECT name, email, org, source_record_id FROM contacts_fts "
            "WHERE contacts_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit),
        ).fetchall()
    finally:
        con.close()


def list_records(db_path, *, limit: int = 50, offset: int = 0) -> dict:
    """Browse page over all records (name-ordered), for the workspace list. Read-only.

    Each row pairs the searchable surface (``contacts_fts``) with its source (``source_records``);
    the join is 1:1 because the loader keeps one fts row per record. Returns the page plus ``total``
    so the UI can paginate.
    """
    con = connect(db_path, read_only=True)
    try:
        total = con.execute("SELECT count(*) FROM source_records").fetchone()[0]
        rows = con.execute(
            "SELECT sr.source_record_id, f.name, f.email, f.org, sr.source "
            "FROM source_records sr JOIN contacts_fts f USING (source_record_id) "
            # Named contacts first (real exports carry many name-less rows), then A–Z case-insensitively.
            "ORDER BY (f.name = ''), f.name COLLATE NOCASE, sr.source_record_id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    finally:
        con.close()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": [
            {"id": rid, "name": name, "email": email, "org": org, "source": source}
            for rid, name, email, org, source in rows
        ],
    }


def get_record(db_path, source_record_id: str) -> dict | None:
    """One record by id: its raw jCard (as stored JSON) + per-field provenance. Read-only.

    Returns ``raw_jcard`` as the on-disk TEXT rather than a parsed object so this stays a leaf that
    never imports ``cli`` — the caller (daemon) owns jCard rendering. ``None`` if no such record.
    """
    con = connect(db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT source, source_uid, stable_key, ingested_at, raw_jcard "
            "FROM source_records WHERE source_record_id = ?",
            (source_record_id,),
        ).fetchone()
        if row is None:
            return None
        source, source_uid, stable_key, ingested_at, raw_jcard = row
        prov = con.execute(
            "SELECT field, value, observed_at FROM field_provenance "
            "WHERE source_record_id = ? ORDER BY field",
            (source_record_id,),
        ).fetchall()
    finally:
        con.close()
    return {
        "id": source_record_id,
        "source": source,
        "source_uid": source_uid,
        "stable_key": stable_key,
        "ingested_at": ingested_at,
        "raw_jcard": raw_jcard,
        "provenance": [{"field": f, "value": v, "observed_at": o} for f, v, o in prov],
    }


def records_for_source(db_path, source: str) -> dict:
    """{source_record_id: raw_jcard} for one source — the comparison basis for re-import (added /
    updated / unchanged / stale). Read-only."""
    con = connect(db_path, read_only=True)
    try:
        return dict(con.execute(
            "SELECT source_record_id, raw_jcard FROM source_records WHERE source = ?", (source,)).fetchall())
    finally:
        con.close()
