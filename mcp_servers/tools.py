"""Pure tool bodies for the PRM MCP servers — **no ``mcp`` SDK import**, so they are unit-testable and
keep the SDK wiring (``shared_data_ops`` / ``dedup_ops``) thin.

Read tools project the canonical contact (``core.projection``). The dedup tool is **PROPOSE-ONLY**: it
stages a changeset (``core.proposals``) for the human to review and apply in the workspace, and never
applies it. There is no apply tool on this surface — that's the structural INV-11 / AC-PRM-F guarantee.
``core.apply.build_merge_changeset`` is used only to *build* the changeset (a read); the apply engine
is never invoked here.
"""

from __future__ import annotations

from core import apply, candidates, private_db, projection, proposals, shared_db


# --------------------------------------------------------------------------- read (shared-data-ops)
def search_contacts(home, query: str, limit: int = 20) -> dict:
    if not home.shared_db.exists():
        return {"query": query, "results": []}
    imap = private_db.identity_map(home.private_db) if home.private_db.exists() else {}
    seen, results = set(), []
    for name, email, org, srid in shared_db.search(home.shared_db, query, limit=limit):
        cid = imap.get(srid, srid)
        if cid in seen:
            continue
        seen.add(cid)
        results.append({"id": cid, "name": name, "email": email, "org": org})
    return {"query": query, "results": results}


def list_contacts(home, limit: int = 50, offset: int = 0) -> dict:
    return projection.list_contacts(home, limit=limit, offset=offset)


def get_contact(home, contact_id: str) -> dict:
    return projection.get_contact(home, contact_id) or {"error": "no such contact", "id": contact_id}


def get_provenance(home, contact_id: str) -> dict:
    c = projection.get_contact(home, contact_id)
    if not c:
        return {"error": "no such contact", "id": contact_id}
    return {"id": contact_id, "sources": c["sources"], "member_count": c["member_count"],
            "fields": [{"name": f["name"], "kind": f["kind"], "values": f["values"]} for f in c["fields"]]}


def find_duplicate_candidates(home, limit: int = 50) -> dict:
    clusters = candidates.find_duplicate_candidates(home)
    return {"total": len(clusters), "clusters": clusters[:limit] if limit else clusters}


# --------------------------------------------------------------------------- propose-only (dedup-ops)
def submit_merge_proposal(home, member_ids: list, into: str, resolutions=None, rationale: str = "") -> dict:
    """PROPOSE a merge for the human to review — **stage only, never apply** (INV-11). Returns the
    staged proposal id; the human approves it in the workspace Duplicates tab."""
    if not member_ids or into not in member_ids:
        return {"error": "`into` must be one of member_ids", "member_ids": member_ids, "into": into}
    changeset = apply.build_merge_changeset(home, member_ids, into, resolutions=resolutions or [],
                                            created_by="ai:local-dedup", rationale=rationale)
    pid = proposals.store(home, changeset, status="pending")
    return {"ok": True, "proposal_id": pid, "status": "pending",
            "note": "Staged for review — open the workspace Duplicates tab to approve or reject."}


def list_proposals(home, status: str = "pending") -> dict:
    return {"proposals": proposals.list_proposals(home, status=status or None)}


def get_proposal(home, proposal_id: str) -> dict:
    return proposals.load(home, proposal_id) or {"error": "no such proposal", "proposal_id": proposal_id}
