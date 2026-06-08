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

_VALID_OPS = {"merge", "resolve_field", "flag_conflict"}


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
