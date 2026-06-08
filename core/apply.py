"""core/apply.py — the apply engine: turn an approved changeset into a committed merge.

This is the daemon's path to the private store and the *only* writer of applied merge decisions. Every
apply takes the single-instance file-lock (AC-PRM-C), **snapshots private.db first** (the v0.1 Undo
point), runs the changeset in one transaction, and appends to the append-only audit log (INV-12). A
dismissal records a ``dedup_decision`` (so the pair never resurfaces); ``undo`` restores the most
recent snapshot. Rationale: docs/design-notes/dedupe-design.md.
"""

from __future__ import annotations

from core import audit, private_db, proposals, snapshots
from core.lock import file_lock


def build_merge_changeset(home, contact_ids, into, *, resolutions=None,
                          created_by="manual:user", rationale="") -> dict:
    """Construct a changeset that merges ``contact_ids`` into the surviving contact ``into`` and applies
    the reviewer's field choices. Expands each (losing) contact to its source records via the
    identity_map, so the merge survives prior merges. ``resolutions`` is a list of
    ``{field, chosen_value, chosen_source?, rule?}``."""
    members = set(contact_ids) | {into}
    imap = private_db.identity_map(home.private_db) if home.private_db.exists() else {}
    srids = [srid for srid, cid in imap.items() if cid in members and cid != into]
    ops = []
    if srids:
        ops.append(proposals.merge_op(srids, into))
    for r in (resolutions or []):
        ops.append(proposals.resolve_field_op(into, r["field"], r.get("chosen_value"),
                                               chosen_source=r.get("chosen_source"), rule=r.get("rule", "user")))
    return proposals.build(ops, created_by=created_by, rationale=rationale)


def apply_changeset(home, changeset: dict) -> dict:
    """Validate → lock → snapshot → run ops (one transaction) → audit. Returns the apply record."""
    proposals.validate(changeset)
    home.create()
    with file_lock(home.lock_file):
        snap = snapshots.snapshot(home)                      # pre-apply Undo point
        try:
            applied = private_db.apply_operations(home.private_db, changeset["operations"])
        except Exception:
            snapshots.restore(home, snap)                    # any failure → roll back to the snapshot
            raise
        entry = audit.append(home, {
            "kind": "apply",
            "proposal_id": changeset.get("proposal_id"),
            "created_by": changeset.get("created_by"),
            "rationale": changeset.get("rationale", ""),
            "operations": applied,
            "snapshot": snap.name,
        })
    return {"proposal_id": changeset.get("proposal_id"), "snapshot": str(snap), "applied": applied, "audit": entry}


def reject_cluster(home, cluster_key: str, *, by: str = "manual:user") -> dict:
    """Record a 'not a duplicate' dismissal (survives re-import; the pair won't resurface)."""
    home.create()
    with file_lock(home.lock_file):
        private_db.record_decision(home.private_db, cluster_key, "not_duplicate")
        return audit.append(home, {"kind": "reject", "cluster_key": cluster_key, "by": by})


def undo(home) -> str | None:
    """Restore the most recent snapshot (v0.1 Undo). Returns the snapshot path, or ``None`` if empty."""
    with file_lock(home.lock_file):
        snaps = snapshots.list_snapshots(home)
        if not snaps:
            return None
        latest = snaps[-1]
        snapshots.restore(home, latest)
        audit.append(home, {"kind": "undo", "restored": latest.name})
        return str(latest)
