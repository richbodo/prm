"""core/apply.py — the apply engine: turn an approved changeset into a committed merge.

This is the daemon's path to the private store and the *only* writer of applied merge decisions. Every
apply takes the single-instance file-lock (AC-PRM-C), **snapshots relationships.db first** (the v0.1 Undo
point), runs the changeset in one transaction, and appends to the append-only audit log (INV-12). A
dismissal records a ``dedup_decision`` (so the pair never resurfaces); ``undo`` restores the most
recent snapshot. Rationale: docs/design-notes/dedupe-design.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core import audit, relationships_db, proposals, schema, snapshots
from core.lock import file_lock


def build_merge_changeset(home, contact_ids, into, *, resolutions=None,
                          created_by="manual:user", rationale="") -> dict:
    """Construct a changeset that merges ``contact_ids`` into the surviving contact ``into`` and applies
    the reviewer's field choices. Expands each (losing) contact to its source records via the
    identity_map, so the merge survives prior merges. ``resolutions`` is a list of
    ``{field, chosen_value, chosen_source?, rule?}``."""
    members = set(contact_ids) | {into}
    imap = relationships_db.identity_map(home.relationships_db) if home.relationships_db.exists() else {}
    srids = [srid for srid, cid in imap.items() if cid in members and cid != into]
    ops = []
    if srids:
        ops.append(proposals.merge_op(srids, into))
    for r in (resolutions or []):
        ops.append(proposals.resolve_field_op(into, r["field"], r.get("chosen_value"),
                                               chosen_source=r.get("chosen_source"), rule=r.get("rule", "user")))
    cs = proposals.build(ops, created_by=created_by, rationale=rationale)
    cs["member_ids"] = sorted(members)   # the canonical contacts being merged (for the review UI)
    cs["into"] = into
    return cs


def apply_changeset(home, changeset: dict) -> dict:
    """Validate → lock → snapshot → run ops (one transaction) → audit. Returns the apply record."""
    proposals.validate(changeset)
    home.create()
    with file_lock(home.lock_file):
        snap = snapshots.snapshot(home)                      # pre-apply Undo point
        try:
            applied = relationships_db.apply_operations(home.relationships_db, changeset["operations"])
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


def set_field_value(home, contact_id, field_id, value, *, value_json=None,
                    written_by="manual:user", source="manual") -> dict:
    """Set a relationship-overlay value (the custom schema + built-in tags/notes/photo) through the
    audited apply path — lock → snapshot → one transaction → audit, so it is reversible by Undo. A
    single-valued field is replaced; a multi-valued field (tags) gains the value."""
    op = proposals.set_field_value_op(contact_id, field_id, value, value_json=value_json,
                                      written_by=written_by, source=source)
    return apply_changeset(home, proposals.build([op], created_by=written_by, rationale="set field value"))


def clear_field_value(home, contact_id, field_id, value=None, *, written_by="manual:user") -> dict:
    """Clear a relationship value (the whole field, or one entry of a multi-valued field). Audited +
    reversible, same path as ``set_field_value``."""
    op = proposals.clear_field_value_op(contact_id, field_id, value)
    return apply_changeset(home, proposals.build([op], created_by=written_by, rationale="clear field value"))


def resolve_field(home, contact_id, field, value, *, chosen_source=None, written_by="manual:user") -> dict:
    """Override a single-valued (reconcile) *source* field on one contact — a manual edit (``rule="user"``,
    so it beats survivorship and survives re-import). Writes ``field_resolutions``; the projection layers
    it over the immutable source value (the override model — INV-2 stays intact). Audited + reversible."""
    op = proposals.resolve_field_op(contact_id, field, value, chosen_source=chosen_source, rule="user")
    return apply_changeset(home, proposals.build([op], created_by=written_by, rationale="edit field"))


def update_field(home, field_id, **changes) -> dict:
    """Workspace-only schema-definition edit (INV-3), serialized by the AC-PRM-C file-lock so two daemon
    threads can't race. Used by the tag-management modal (editing the ``tags`` vocabulary) and the R6
    schema builder. No snapshot/audit yet — non-destructive definition edits aren't in the Undo ring;
    destructive field removal (R6) will snapshot."""
    home.create()
    with file_lock(home.lock_file):
        return schema.update_field(home.relationships_db, field_id, **changes)


def define_field(home, **kw) -> dict:
    """Create a user-authored field definition (INV-3 — workspace-only), serialized by the file-lock.
    ``kw`` is forwarded to ``schema.define_field`` (label, kind, config, ai_write_policy, disclosure_tier,
    required, …). Returns the new field. Non-destructive, so no snapshot."""
    home.create()
    with file_lock(home.lock_file):
        fid = schema.define_field(home.relationships_db, **kw)
        return schema.get_field(home.relationships_db, fid)


def remove_field(home, field_id) -> dict:
    """Remove a **user** field — destructive (its FK cascades the field's values), so **snapshot first**
    (the Undo point), under the file-lock, and audit it. Refuses a built-in before snapshotting. Reversible
    by Undo, like a merge."""
    field = schema.get_field(home.relationships_db, field_id)
    if field is None:
        raise schema.SchemaError(f"no such field: {field_id!r}")
    if field["class"] == "builtin":
        raise schema.SchemaError(f"{field_id!r} is a built-in field and cannot be removed")
    home.create()
    with file_lock(home.lock_file):
        snap = snapshots.snapshot(home)                      # pre-remove Undo point (values cascade)
        try:
            schema.remove_field(home.relationships_db, field_id)
        except Exception:
            snapshots.restore(home, snap)
            raise
        audit.append(home, {"kind": "remove_field", "field_id": field_id, "snapshot": snap.name})
        return {"ok": True, "snapshot": str(snap)}


def edit_contact(home, contact_id, *, resolutions=None, values=None, clears=None, multi=None,
                 written_by="manual:user") -> dict:
    """Apply ALL of a contact's edits as **one** changeset — one lock, one snapshot, one audit entry, one
    Undo. This is what the workspace edit-mode save calls: it is atomic (the whole edit reverts together)
    and avoids the concurrent-write lock contention that firing each edit as its own request would cause.

    - ``resolutions``: ``[{field, value}]``     → single-valued source-field overrides (``rule="user"``)
    - ``values``: ``[{field_id, value}]``        → single-valued relationship/custom field values (set)
    - ``clears``: ``[field_id]``                 → relationship/custom fields to clear
    - ``multi``: ``[{field_id, values:[...]}]``  → multi-valued fields (tags) — **replace the whole set**
      (clear, then re-add each), atomically within this one changeset
    """
    ops = [proposals.resolve_field_op(contact_id, r["field"], r.get("value"), rule="user")
           for r in (resolutions or [])]
    ops += [proposals.set_field_value_op(contact_id, v["field_id"], v.get("value"),
                                         written_by=written_by, source="manual") for v in (values or [])]
    ops += [proposals.clear_field_value_op(contact_id, fid) for fid in (clears or [])]
    for m in (multi or []):
        ops.append(proposals.clear_field_value_op(contact_id, m["field_id"]))
        ops += [proposals.set_field_value_op(contact_id, m["field_id"], v, written_by=written_by, source="manual")
                for v in (m.get("values") or [])]
    if not ops:
        return {"ok": True, "applied": []}
    return apply_changeset(home, proposals.build(ops, created_by=written_by, rationale="edit contact"))


def _item_member_ids(home, item) -> list:
    """The canonical member contact-ids an item will merge — for batch collision detection. A proposal's
    members come from its stored changeset; a candidate carries them directly."""
    if item.get("kind") == "proposal":
        cs = proposals.load(home, item.get("proposal_id"))
        return list(cs.get("member_ids") or []) if cs else []
    return list(item.get("member_ids") or [])


def apply_batch(home, items, *, created_by="manual:workspace-batch", rationale="") -> dict:
    """Apply many duplicate-merges as **one** combined changeset — one snapshot, one transaction, one audit
    entry, one Undo. ``items`` is a list of:

      - ``{"kind":"candidate", "member_ids":[...], "into":<id among member_ids>, "resolutions":[{field,...}]}``
      - ``{"kind":"proposal", "proposal_id":"p-...", "resolutions":[...optional inline overrides...]}``

    A contact may not be merged two ways in one batch: identical member-sets are deduped (a proposal
    beats its detected candidate), and an item that *partially* overlaps an already-accepted one is
    **skipped and reported** (in ``skipped``) rather than chain-merged — the name-based tiers surface as
    standalone pairs that can share a contact, so this keeps the batch moving without a silent transitive
    merge (AC-PRM-B); a skipped pair resurfaces next pass. Constituent proposals are marked ``applied``
    after the commit (non-fatal — reported as ``status_unmarked``)."""
    items = list(items or [])
    if not items:
        raise ValueError("merge-batch: no items to apply")

    # 1) Resolve to canonical member-sets; dedup identical clusters (a proposal beats its detected
    #    candidate); skip an item that partially overlaps an already-accepted one (fuzzy/strong pairs
    #    can share a contact — a contact can't be merged two ways at once). Skips are reported, not fatal.
    resolved, seen_sets, claimed, skipped = [], {}, {}, []
    for it in items:
        mids = _item_member_ids(home, it)
        if not mids:
            continue
        key = frozenset(mids)
        if key in seen_sets:                                   # exact same cluster twice — keep the proposal
            j = seen_sets[key]
            if it.get("kind") == "proposal" and resolved[j].get("kind") != "proposal":
                resolved[j] = it
            continue
        if any(claimed.get(cid, key) != key for cid in mids):  # partial overlap → skip, resurfaces next pass
            skipped.append(mids)
            continue
        seen_sets[key] = len(resolved)
        for cid in mids:
            claimed[cid] = key
        resolved.append(it)

    # 2) Build the combined op list (all reads happen here, before the single apply).
    all_ops, proposal_ids = [], []
    for it in resolved:
        if it.get("kind") == "proposal":
            cs = proposals.load(home, it.get("proposal_id"))
            if cs is None:
                raise ValueError(f"merge-batch: no such proposal {it.get('proposal_id')!r}")
            ops = list(cs.get("operations") or [])
            target = next((op["into"] for op in ops if op.get("op") == "merge"), cs.get("into"))
            for r in (it.get("resolutions") or []):            # appended → INSERT OR REPLACE makes them win
                ops.append(proposals.resolve_field_op(target, r["field"], r.get("chosen_value"),
                                                      chosen_source=r.get("chosen_source"), rule=r.get("rule", "user")))
            all_ops.extend(ops)
            proposal_ids.append(cs["proposal_id"])
        else:
            cs = build_merge_changeset(home, it["member_ids"], it["into"], resolutions=it.get("resolutions") or [])
            all_ops.extend(cs["operations"])

    if not any(op.get("op") == "merge" for op in all_ops):
        raise ValueError("merge-batch: nothing to merge (members may already be merged)")

    # 3) One changeset, applied once → one snapshot / one audit entry / one Undo.
    combined = proposals.build(all_ops, created_by=created_by, rationale=rationale or "bulk approve")
    combined["batch"] = True
    result = apply_changeset(home, combined)

    # 4) Mark constituent proposals applied (non-fatal — the merge already committed atomically).
    unmarked = []
    for pid in proposal_ids:
        try:
            proposals.set_status(home, pid, "applied")
        except Exception:
            unmarked.append(pid)

    return {"ok": True, "merged": sum(1 for op in all_ops if op.get("op") == "merge"),
            "skipped": skipped, "snapshot": result["snapshot"], "applied": result["applied"],
            "proposal_id": combined["proposal_id"], "status_unmarked": unmarked}


def reject_cluster(home, cluster_key: str, *, by: str = "manual:user") -> dict:
    """Record a 'not a duplicate' dismissal (survives re-import; the pair won't resurface)."""
    home.create()
    with file_lock(home.lock_file):
        relationships_db.record_decision(home.relationships_db, cluster_key, "not_duplicate")
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


def set_disclosure_mode(home, mode, *, by="manual:workspace") -> dict:
    """Record the cloud-disclosure mode for the ``EX-CLOUD-LLM`` handler / AC-MCP-C floor — the only writer
    of that state, serialized by the AC-PRM-C file-lock and audited, so the MCP servers (separate processes)
    read a value the workspace set. Entering a disclosing mode (``local-ai`` / ``cloud-exception``) stamps
    the consented shareable-field scope (for the scope-grew re-consent check); ``pna`` clears it. A settings
    change, not a data mutation — no snapshot; reversibility is just another mode write (``return_to_pna``).
    Rationale: plans/ex-cloud-llm-workspace-handler.md."""
    from core import disclosure
    if mode not in disclosure.MODES:
        raise ValueError(f"unknown disclosure mode: {mode!r}")
    home.create()
    with file_lock(home.lock_file):
        db = home.relationships_db
        relationships_db.ensure(db)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        disclosing = disclosure.discloses(mode)
        scope = disclosure.shareable_field_ids(home) if disclosing else []
        try:
            hist = json.loads(relationships_db.get_setting(db, disclosure.HISTORY_KEY) or "[]")
        except (ValueError, TypeError):
            hist = []
        hist = (hist + [{"mode": mode, "at": now}])[-disclosure.HISTORY_MAX:]
        relationships_db.set_setting(db, disclosure.MODE_KEY, mode)
        relationships_db.set_setting(db, disclosure.AT_KEY, now if disclosing else "")
        relationships_db.set_setting(db, disclosure.SCOPE_KEY, json.dumps(scope))
        relationships_db.set_setting(db, disclosure.HISTORY_KEY, json.dumps(hist))
        return audit.append(home, {"kind": "disclosure_mode", "mode": mode, "by": by, "scope": scope})


def return_to_pna(home, *, by="manual:workspace") -> dict:
    """EX-H5 reversible return-to-PNA-mode — **mode only**: it stops future sharing but cannot recall data
    already sent to a cloud provider."""
    from core import disclosure
    return set_disclosure_mode(home, disclosure.PNA, by=by)
