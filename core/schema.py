"""core/schema.py — the user-authored relationship schema (field *definitions*).

INV-3: field definitions are authored **only** here — the workspace/daemon write path — never over MCP
(the MCP surface exposes no definition tool; that absence is the structural lock). Two classes of field:

- **builtin** (`photo` / `tags` / `notes`) — seeded by the schema (``relationships.sql``), **non-removable**
  without a code change; their *kind* and *class* are fixed, but mutable attributes (label, config, policy,
  disclosure tier, position) can still be edited (e.g. the tag-management modal edits ``tags`` options).
- **user** — fully CRUD-able.

Field *values* reference definitions by foreign key (INV-3/4: a value for an undefined field cannot be
written; enforced with ``PRAGMA foreign_keys=ON``, set by ``relationships_db.connect``). The value-write
API + projection compose land in R2; this module is the definition layer R2 builds on. A leaf over
``relationships_db`` (it does not import ``cli``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core import relationships_db

FIELD_KINDS = {"text", "long_text", "number", "date", "boolean",
               "single_select", "multi_select", "url", "image"}
_SELECT_KINDS = {"single_select", "multi_select"}
AI_WRITE_POLICIES = {"review-required", "append-only", "free-write"}
DISCLOSURE_TIERS = {"private-sealed", "private-shareable-on-consent"}
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FIELD_COLS = ("field_id", "label", "kind", "config", "class", "ai_write_policy",
               "disclosure_tier", "required", "position", "created_at", "updated_at")


class SchemaError(ValueError):
    """A schema authoring rule was violated (bad kind, locked builtin, duplicate key, …)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (label or "").strip().lower()).strip("_")
    if s and s[0].isdigit():
        s = "f_" + s
    return s[:64]


def _row_to_field(row) -> dict:
    d = dict(zip(_FIELD_COLS, row))
    d["required"] = bool(d["required"])
    try:
        d["config"] = json.loads(d["config"]) if d["config"] else {}
    except (ValueError, TypeError):
        d["config"] = {}
    return d


def _normalize_config(kind: str, config) -> str:
    """Validate + canonicalize a field's config to a JSON string. Light, per-kind:
    select kinds carry an ``options`` list of ``{value,label,description}``; image carries ``multi``."""
    config = dict(config or {})
    if kind in _SELECT_KINDS:
        opts = config.get("options", [])
        if not isinstance(opts, list):
            raise SchemaError("select field config.options must be a list")
        norm = []
        for o in opts:
            if isinstance(o, str):
                o = {"value": o, "label": o}
            if not isinstance(o, dict) or not o.get("value"):
                raise SchemaError("each select option needs a 'value'")
            norm.append({"value": str(o["value"]), "label": str(o.get("label", o["value"])),
                         "description": str(o.get("description", ""))})
        config["options"] = norm
    elif kind == "image":
        config["multi"] = bool(config.get("multi", False))
    return json.dumps(config, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- read
def list_fields(db_path) -> list[dict]:
    """Every field definition, ordered by position then label (built-ins first by their low positions)."""
    if not Path(db_path).exists():
        return []
    con = relationships_db.connect(db_path, read_only=True)
    try:
        rows = con.execute(
            f"SELECT {', '.join(_FIELD_COLS)} FROM field_definitions ORDER BY position, label COLLATE NOCASE"
        ).fetchall()
    finally:
        con.close()
    return [_row_to_field(r) for r in rows]


def get_field(db_path, field_id: str) -> dict | None:
    if not Path(db_path).exists():
        return None
    con = relationships_db.connect(db_path, read_only=True)
    try:
        row = con.execute(
            f"SELECT {', '.join(_FIELD_COLS)} FROM field_definitions WHERE field_id = ?", (field_id,)
        ).fetchone()
    finally:
        con.close()
    return _row_to_field(row) if row else None


# --------------------------------------------------------------------------- write (workspace-only, INV-3)
def define_field(db_path, *, label: str, kind: str, config=None,
                 ai_write_policy: str = "review-required", disclosure_tier: str = "private-sealed",
                 required: bool = False, position: int | None = None, field_id: str | None = None) -> str:
    """Create a **user** field definition. Validates the kind/policy/tier/config and the slug, and
    refuses a duplicate field_id. Returns the field_id. (Builtins are seeded by the schema, not here.)"""
    if kind not in FIELD_KINDS:
        raise SchemaError(f"unknown field kind: {kind!r}")
    if ai_write_policy not in AI_WRITE_POLICIES:
        raise SchemaError(f"unknown ai_write_policy: {ai_write_policy!r}")
    if disclosure_tier not in DISCLOSURE_TIERS:
        raise SchemaError(f"unknown disclosure_tier: {disclosure_tier!r}")
    fid = field_id or _slugify(label)
    if not _SLUG_RE.match(fid):
        raise SchemaError(f"invalid field_id {fid!r} (need lowercase letters/digits/underscore, ≤64)")
    cfg = _normalize_config(kind, config)

    relationships_db.ensure(db_path)
    con = relationships_db.connect(db_path)
    try:
        if con.execute("SELECT 1 FROM field_definitions WHERE field_id = ?", (fid,)).fetchone():
            raise SchemaError(f"field {fid!r} already exists")
        if position is None:
            position = (con.execute("SELECT COALESCE(MAX(position), 0) FROM field_definitions").fetchone()[0]) + 10
        now = _now_iso()
        con.execute(
            "INSERT INTO field_definitions(field_id, label, kind, config, class, ai_write_policy, "
            "disclosure_tier, required, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, ?, ?)",
            (fid, label, kind, cfg, ai_write_policy, disclosure_tier, int(bool(required)), position, now, now),
        )
        con.commit()
    finally:
        con.close()
    return fid


_MUTABLE = ("label", "config", "ai_write_policy", "disclosure_tier", "required", "position")


def update_field(db_path, field_id: str, **changes) -> dict:
    """Edit a field's **mutable** attributes (label / config / ai_write_policy / disclosure_tier /
    required / position). `kind` and `class` are immutable — for any field, builtin or user. Raises if
    the field is missing or an unknown/immutable attribute is passed."""
    bad = set(changes) - set(_MUTABLE)
    if bad:
        raise SchemaError(f"cannot change {sorted(bad)} (immutable or unknown)")
    current = get_field(db_path, field_id)
    if current is None:
        raise SchemaError(f"no such field: {field_id!r}")
    if not changes:                               # nothing to update — avoid building empty SET SQL
        return current
    if "ai_write_policy" in changes and changes["ai_write_policy"] not in AI_WRITE_POLICIES:
        raise SchemaError(f"unknown ai_write_policy: {changes['ai_write_policy']!r}")
    if "disclosure_tier" in changes and changes["disclosure_tier"] not in DISCLOSURE_TIERS:
        raise SchemaError(f"unknown disclosure_tier: {changes['disclosure_tier']!r}")
    if "config" in changes:
        changes["config"] = _normalize_config(current["kind"], changes["config"])
    if "required" in changes:
        changes["required"] = int(bool(changes["required"]))

    sets = ", ".join(f"{k} = ?" for k in changes) + ", updated_at = ?"
    params = list(changes.values()) + [_now_iso(), field_id]
    con = relationships_db.connect(db_path)
    try:
        con.execute(f"UPDATE field_definitions SET {sets} WHERE field_id = ?", params)
        con.commit()
    finally:
        con.close()
    return get_field(db_path, field_id)


def remove_field(db_path, field_id: str) -> None:
    """Delete a **user** field (cascading its values via FK). Refuses a builtin — those are removable
    only by a code change (the seeded relationship surface a user relies on)."""
    field = get_field(db_path, field_id)
    if field is None:
        raise SchemaError(f"no such field: {field_id!r}")
    if field["class"] == "builtin":
        raise SchemaError(f"{field_id!r} is a built-in field and cannot be removed")
    con = relationships_db.connect(db_path)
    try:
        con.execute("DELETE FROM field_definitions WHERE field_id = ?", (field_id,))
        con.commit()
    finally:
        con.close()
