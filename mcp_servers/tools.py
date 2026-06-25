"""Pure tool bodies for the PRM MCP servers — **no ``mcp`` SDK import**, so they are unit-testable and
keep the SDK wiring (``shared_data_ops`` / ``dedup_ops``) thin.

Read tools project the canonical contact (``core.projection``). The dedup tool is **PROPOSE-ONLY**: it
stages a changeset (``core.proposals``) for the human to review and apply in the workspace, and never
applies it. There is no apply tool on this surface — that's the structural INV-11 / AC-PRM-F guarantee.
``core.apply.build_merge_changeset`` is used only to *build* the changeset (a read); the apply engine
is never invoked here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core import access_log, apply, build_label, candidates, disclosure, relationships_db, projection, proposals, shared_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _custom_names(contact: dict) -> list:
    """The overlay (custom / shareable-tier) field names present in a projected MCP contact."""
    return [f["name"] for f in contact.get("fields", []) if f.get("custom")]


def _log_read(home, tool: str, contact_id: str, *, returned: list, withheld: list) -> None:
    """Record one served MCP read in the access log — best-effort, **never breaks the read**. Field *names*
    only, never values (INV-1). ``decision`` summarizes the per-request-review outcome; ``build`` stamps the
    serving code so a stale server is detectable (prm#65). See ``plans/external-access-visibility.md``."""
    decision = "withheld" if withheld else ("released" if returned else "no-shareable")
    access_log.append(home, {
        "ts": _now_iso(),
        "tool": tool,
        "contact_id": contact_id,
        "mode": disclosure.mode(home),
        "review_on": disclosure.review_required(home),
        "approved": disclosure.is_approved(home, contact_id),
        "decision": decision,
        "returned_overlay": sorted(returned),
        "withheld_overlay": sorted(withheld),
        "build": build_label.build_label(),
    })


# --------------------------------------------------------------------------- read (shared-data-ops)
def search_contacts(home, query: str, limit: int = 20) -> dict:
    if not home.shared_db.exists():
        return {"query": query, "results": []}
    imap = relationships_db.identity_map(home.relationships_db) if home.relationships_db.exists() else {}
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


# The MCP read surface uses the cloud-facing projection (`audience="mcp"`): the query-layer data-floor
# (AC-MCP-C / PR-7) — `private-sealed` fields are never even selected, and `private-shareable-on-consent`
# fields cross ONLY when the workspace-recorded disclosure mode consents (`core.disclosure`; EX-CLOUD-LLM /
# enforced EX-H7). When **per-request review** (P4) is on, a contact's shareable fields are withheld until
# the user approves *that contact* in the workspace — the read STAGES a request and is marked `review:pending`
# (the "MCP stages, the workspace disposes" rule, generalized to reads). A `disclosure` marker reports what
# was withheld so a cooperating client can ask the user to act. The daemon reads the full `audience="local"`
# projection directly.
def _review_gate(home, contact_id: str, contact: dict) -> dict:
    """Apply per-request review (P4) to an already-floored MCP contact. No-op when review is off. Otherwise:
    an approved contact is marked ``review:approved``; an unapproved one has its shareable fields withheld,
    a request staged for the workspace, and is marked ``review:pending``."""
    if not disclosure.review_required(home):
        return contact
    shareable = [f for f in contact.get("fields", []) if f.get("custom")]
    if not shareable:
        return contact
    marker = dict(contact.get("disclosure") or {})
    if disclosure.is_approved(home, contact_id):
        marker["review"] = "approved"
    else:
        disclosure.stage_request(home, contact_id, contact.get("fn", ""), [f["name"] for f in shareable])
        contact["fields"] = [f for f in contact.get("fields", []) if not f.get("custom")]
        marker["review"] = "pending"
        marker["review_withheld"] = len(shareable)
    contact["disclosure"] = marker
    return contact


def get_contact(home, contact_id: str) -> dict:
    c = projection.get_contact(home, contact_id, audience="mcp")
    if not c:
        return {"error": "no such contact", "id": contact_id}
    pre = _custom_names(c)
    c = _review_gate(home, contact_id, c)
    post = _custom_names(c)
    _log_read(home, "get_contact", contact_id, returned=post, withheld=[f for f in pre if f not in post])
    return c


def get_provenance(home, contact_id: str) -> dict:
    c = projection.get_contact(home, contact_id, audience="mcp")
    if not c:
        return {"error": "no such contact", "id": contact_id}
    pre = _custom_names(c)
    c = _review_gate(home, contact_id, c)
    post = _custom_names(c)
    _log_read(home, "get_provenance", contact_id, returned=post, withheld=[f for f in pre if f not in post])
    out = {"id": contact_id, "sources": c["sources"], "member_count": c["member_count"],
           "fields": [{"name": f["name"], "kind": f["kind"], "values": f["values"]} for f in c["fields"]]}
    if c.get("disclosure"):
        out["disclosure"] = c["disclosure"]
    return out


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


# --------------------------------------------------------------------------- write values (private-data-ops)
def write_field_value(home, contact_id: str, field_id: str, value, *, written_by: str,
                      value_json=None, source=None) -> dict:
    """Write one relationship-field VALUE through the policy dispatch (R11a / AC-PRM-E). The field's
    ``ai_write_policy`` decides: ``review-required`` (default) **stages a proposal** the human approves in
    the workspace — never a direct write (INV-11), the same "MCP stages, the workspace applies" rule as the
    dedup surface; ``append-only`` **appends** a provenance-stamped row (non-destructive — a human value is
    never overwritten); ``free-write`` **sets**. Field *definitions* are never written here (INV-3/4: no
    definition tool + the FK lock). A policy refusal (unknown field / size cap / per-session quota) returns
    an ``error`` dict rather than raising, so a cooperating client can recover. Returns the stage/apply
    result (``core.apply.write_field_value``)."""
    try:
        return apply.write_field_value(home, contact_id, field_id, value, value_json=value_json,
                                       written_by=written_by, source=source)
    except apply.WritePolicyError as exc:
        return {"error": str(exc), "contact_id": contact_id, "field_id": field_id}


def observe_contact_field(home, contact_id: str, field: str, value, *, written_by: str,
                          source=None, confidence=None) -> dict:
    """File an AI **observation** for a CANONICAL contact field (R11b) — additive, attributed, and NEVER
    canonical. It lands as a *suggestion* the user promotes in the workspace; there is **no promote tool**
    on this surface, so an AI can't make a contact-identity value the default (the structural lock that
    keeps "the human is the actuator" true for contact data). Bounded by the per-session quota + size cap.
    A refusal (non-enrichable field / size cap / quota) returns an ``error`` dict rather than raising.
    Returns ``core.apply.observe_field``'s result."""
    try:
        return apply.observe_field(home, contact_id, field, value, written_by=written_by,
                                   source=source, confidence=confidence)
    except apply.WritePolicyError as exc:
        return {"error": str(exc), "contact_id": contact_id, "field": field}
