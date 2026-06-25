"""core/proposals.py — the merge **changeset**: the unit both flows emit and the apply path consumes.

A changeset is ``{proposal_id, created_by, created_at, rationale, operations:[...]}``. ``created_by`` is
the only semantic difference between the manual author (``"manual:<user>"``) and the AI author
(``"ai:local-dedup"``). Operations: ``merge`` (re-point source records at a surviving contact),
``resolve_field`` (chosen value for a single-valued field — a manual edit is ``rule="user"``), and
``flag_conflict`` (mark a field ``needs_review``). See docs/design-notes/dedupe-design.md and plan §4.

This module is pure data: it constructs and validates changesets; ``core/apply.py`` runs them.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

_VALID_OPS = {"merge", "resolve_field", "flag_conflict", "set_field_value", "clear_field_value",
              "append_field_value"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- op constructors
def merge_op(source_record_ids, into, *, confidence=None, signals=None) -> dict:
    op = {"op": "merge", "source_record_ids": list(source_record_ids), "into": into}
    if confidence is not None:
        op["confidence"] = confidence
    if signals:
        op["signals"] = list(signals)
    return op


def resolve_field_op(contact_id, field, chosen_value, *, chosen_source=None, rule="user") -> dict:
    return {"op": "resolve_field", "contact_id": contact_id, "field": field,
            "chosen_value": chosen_value, "chosen_source": chosen_source, "rule": rule}


def flag_conflict_op(contact_id, field, candidates) -> dict:
    return {"op": "flag_conflict", "contact_id": contact_id, "field": field,
            "candidates": list(candidates), "status": "needs_review"}


# Relationship-overlay value writes (R2). These write the `field_values` table (the user-defined custom
# schema + built-in tags/notes/photo), distinct from `resolve_field` which overrides a *source* field.
def set_field_value_op(contact_id, field_id, value, *, value_json=None,
                       written_by="manual:user", source=None) -> dict:
    return {"op": "set_field_value", "contact_id": contact_id, "field_id": field_id, "value": value,
            "value_json": value_json, "written_by": written_by, "source": source}


def clear_field_value_op(contact_id, field_id, value=None) -> dict:
    # value=None clears the whole field; a value clears just that entry (multi-valued fields).
    return {"op": "clear_field_value", "contact_id": contact_id, "field_id": field_id, "value": value}


def append_field_value_op(contact_id, field_id, value, *, value_json=None,
                          written_by="ai:unknown", source=None) -> dict:
    # Like set_field_value but NON-destructive (R11a / AC-PRM-E): the apply path *appends* a row and never
    # deletes a single-valued field's prior value, so an AI on the append-only tier only ever adds. Idempotent
    # on (contact, field, value) via the value_id hash, so a re-found value is a no-op.
    return {"op": "append_field_value", "contact_id": contact_id, "field_id": field_id, "value": value,
            "value_json": value_json, "written_by": written_by, "source": source}


# --------------------------------------------------------------------------- assembly + validation
def build(operations, *, created_by: str, rationale: str = "") -> dict:
    """Assemble a changeset. The ``proposal_id`` is content-derived (stable for identical operations)."""
    ops = list(operations)
    digest = hashlib.sha1(json.dumps(ops, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return {
        "proposal_id": f"p-{digest}",
        "created_by": created_by,
        "created_at": _now_iso(),
        "rationale": rationale,
        "operations": ops,
    }


def validate(changeset: dict) -> None:
    """Raise ``ValueError`` if the changeset is malformed. Cheap structural checks only."""
    if not isinstance(changeset, dict) or not isinstance(changeset.get("operations"), list):
        raise ValueError("changeset must have an 'operations' list")
    if not changeset["operations"]:
        raise ValueError("changeset has no operations")
    if not changeset.get("created_by"):
        raise ValueError("changeset is missing 'created_by' (manual:<user> | ai:...)")
    for op in changeset["operations"]:
        kind = op.get("op")
        if kind not in _VALID_OPS:
            raise ValueError(f"unknown op: {kind!r}")
        if kind == "merge" and (not op.get("source_record_ids") or not op.get("into")):
            raise ValueError("merge op needs source_record_ids + into")
        if kind in ("resolve_field", "flag_conflict") and (not op.get("contact_id") or not op.get("field")):
            raise ValueError(f"{kind} op needs contact_id + field")
        if kind in ("set_field_value", "clear_field_value", "append_field_value") and (not op.get("contact_id") or not op.get("field_id")):
            raise ValueError(f"{kind} op needs contact_id + field_id")


# --------------------------------------------------------------------------- staging (propose-only)
# The propose-only surface (MCP dedup-ops) *stages* a changeset here as proposals/<id>.json for the
# human to review and apply in the workspace — it never applies (INV-11 / AC-PRM-F).
def store(home, changeset: dict, *, status: str = "pending") -> str:
    """Stage a changeset for review. Idempotent on ``proposal_id``. Returns the id."""
    validate(changeset)
    home.proposals_dir.mkdir(parents=True, exist_ok=True)
    record = {**changeset, "status": status, "stored_at": _now_iso()}
    (home.proposals_dir / f"{changeset['proposal_id']}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return changeset["proposal_id"]


def load(home, proposal_id: str) -> dict | None:
    path = home.proposals_dir / f"{proposal_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_proposals(home, *, status: str | None = None) -> list:
    """Staged proposals (summaries), newest stored last. Filter by ``status`` if given."""
    if not home.proposals_dir.is_dir():
        return []
    out = []
    for p in sorted(home.proposals_dir.glob("*.json")):
        try:
            cs = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if status and cs.get("status") != status:
            continue
        out.append({"proposal_id": cs.get("proposal_id"), "created_by": cs.get("created_by"),
                    "created_at": cs.get("created_at"), "status": cs.get("status"),
                    "rationale": cs.get("rationale", ""), "member_ids": cs.get("member_ids", []),
                    "into": cs.get("into"), "operations": cs.get("operations", [])})
    return out


def set_status(home, proposal_id: str, status: str) -> bool:
    """Mark a staged proposal (e.g. ``applied`` / ``dismissed``) after the human acts on it."""
    cs = load(home, proposal_id)
    if cs is None:
        return False
    cs["status"] = status
    (home.proposals_dir / f"{proposal_id}.json").write_text(
        json.dumps(cs, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
