"""The canonical-contact projection: identity_map ⨝ source_records ⨝ field_resolutions.

Composes the two stores — raw records from ``core.shared_db`` + decisions from ``core.private_db`` —
into the merged contact a user sees. **Pure read; never writes.** Multi-valued properties *union*;
single-valued ones *reconcile* to one chosen value (an explicit ``field_resolution`` if present, else
survivorship: source priority → most-recent). Rationale + field classification:
``docs/design-notes/dedupe-design.md``.

With the 1:1 identity baseline (pre-merge) every contact has exactly one source record, so the output
matches the raw per-record view; once merges (M3c) point several records at one ``contact_id`` this
unions/reconciles them automatically. Parses the stored jCard array form directly (``["vcard", [...]]``)
so ``core`` stays a leaf that does not import ``cli``.
"""

from __future__ import annotations

import json

from core import private_db, shared_db

# Multi-valued → keep all distinct values. Single-valued → reconcile to one. Anything not listed here
# defaults to UNION, so an unrecognized property never silently drops data.
RECONCILE_FIELDS = {"fn", "n", "bday", "photo", "gender", "anniversary", "org", "title", "nickname", "kind"}
_HIDDEN = {"version", "x-ablabel"}   # structural / vendor-label noise; not user-facing contact data


def _flatten(values) -> str:
    """A jCard property's value element(s) → a display/compare string. Structured values (N/ADR) are
    arrays; multi-element values join with ' · '."""
    parts = []
    for v in values:
        if isinstance(v, list):
            joined = ", ".join(str(x) for x in v if x not in (None, ""))
            if joined:
                parts.append(joined)
        elif v not in (None, ""):
            parts.append(str(v))
    return " · ".join(parts)


def _props(raw_jcard: str) -> list:
    data = json.loads(raw_jcard)
    return data[1] if isinstance(data, list) and len(data) == 2 and data[0] == "vcard" else []


def _records_by_contact(home) -> dict:
    """{contact_id: [ {source, ingested_at, props}, … ]}, grouped via the identity_map (a record with
    no mapping defaults to its own contact, so the projection is robust if private.db is absent)."""
    con = shared_db.connect(home.shared_db, read_only=True)
    try:
        rows = con.execute(
            "SELECT source_record_id, source, ingested_at, raw_jcard FROM source_records"
        ).fetchall()
    finally:
        con.close()
    imap = private_db.identity_map(home.private_db) if home.private_db.exists() else {}
    groups: dict[str, list] = {}
    for srid, source, ingested_at, raw in rows:
        cid = imap.get(srid, srid)
        groups.setdefault(cid, []).append({"source": source, "ingested_at": ingested_at, "props": _props(raw)})
    return groups


def _merge(cid: str, members: list, rank: dict, resolutions: dict) -> dict:
    """Merge a contact's source records into one canonical contact."""
    by_name: dict[str, list] = {}
    sources: list[str] = []
    for rec in members:
        if rec["source"] not in sources:
            sources.append(rec["source"])
        for prop in rec["props"]:
            name = prop[0].lower()
            if name in _HIDDEN:
                continue
            value = _flatten(prop[3:] if len(prop) > 3 else [])
            if not value:
                continue
            by_name.setdefault(name, []).append({"value": value, "source": rec["source"], "at": rec["ingested_at"] or ""})

    fields = []
    for name, items in by_name.items():
        if name in RECONCILE_FIELDS:
            res = resolutions.get((cid, name))
            if res and res[0] is not None:
                chosen = {"value": res[0], "source": res[1] or "user"}
            else:
                best_rank = min(rank.get(i["source"], 999) for i in items)
                chosen = max((i for i in items if rank.get(i["source"], 999) == best_rank), key=lambda i: i["at"])
            fields.append({"name": name, "kind": "single",
                           "values": [{"value": chosen["value"], "source": chosen["source"]}]})
        else:
            seen: dict[str, dict] = {}
            for i in items:
                seen.setdefault(i["value"], {"value": i["value"], "source": i["source"]})
            fields.append({"name": name, "kind": "union", "values": list(seen.values())})

    def pick(field_name):
        f = next((x for x in fields if x["name"] == field_name), None)
        return f["values"][0]["value"] if f and f["values"] else ""

    return {
        "id": cid,
        "fn": pick("fn"),
        "email": pick("email"),
        "org": pick("org"),
        "sources": sources,
        "source": sources[0] if sources else "",
        "member_count": len(members),
        "fields": fields,
    }


def _all(home) -> list:
    groups = _records_by_contact(home)
    rank = {s: i for i, s in enumerate(private_db.source_priority(home.private_db) if home.private_db.exists()
                                       else private_db.DEFAULT_SOURCE_PRIORITY)}
    resolutions = private_db.field_resolutions(home.private_db) if home.private_db.exists() else {}
    return [_merge(cid, members, rank, resolutions) for cid, members in groups.items()]


def _sort_key(c):
    name = c.get("fn") or ""
    return (name == "", name.casefold(), c["id"])   # named first, then A–Z, stable


# --------------------------------------------------------------------------- public read API
def all_contacts(home) -> list:
    """Every canonical contact, fully merged and name-ordered (named first). ``list_contacts`` is a
    page of this; duplicate detection (``core.candidates``) consumes the whole set."""
    return sorted(_all(home), key=_sort_key)


def list_contacts(home, *, limit: int = 50, offset: int = 0) -> dict:
    """A name-ordered page of canonical contacts (named first). Each carries its contributing
    ``sources`` and ``member_count`` so the UI can show multi-source merges."""
    contacts = all_contacts(home)
    page = contacts[offset:offset + limit]
    return {
        "total": len(contacts),
        "limit": limit,
        "offset": offset,
        "records": [
            {"id": c["id"], "name": c["fn"], "email": c["email"], "org": c["org"],
             "source": c["source"], "sources": c["sources"], "member_count": c["member_count"]}
            for c in page
        ],
    }


def _rank(home) -> dict:
    """{source: priority-index} — lower is more authoritative (Apple/Google before LinkedIn/Facebook)."""
    order = private_db.source_priority(home.private_db) if home.private_db.exists() else private_db.DEFAULT_SOURCE_PRIORITY
    return {s: i for i, s in enumerate(order)}


def _preview_fields(members: list, rank: dict) -> tuple[list, list]:
    """Classify a set of source records into merge-preview ``fields`` + the list of conflicting field
    names. UNION fields keep all distinct values; a RECONCILE field with >1 distinct value becomes a
    ``conflict`` carrying a survivorship ``suggested`` default (source priority → most-recent) but
    pre-selecting nothing. Pure — no DB access. Shared by ``preview_merge`` and ``clusters_meta``."""
    by_name: dict[str, list] = {}
    for rec in members:
        for prop in rec["props"]:
            name = prop[0].lower()
            if name in _HIDDEN:
                continue
            value = _flatten(prop[3:] if len(prop) > 3 else [])
            if value:
                by_name.setdefault(name, []).append({"value": value, "source": rec["source"], "at": rec["ingested_at"] or ""})

    fields, conflicts = [], []
    for name, items in by_name.items():
        distinct = {}
        for i in items:
            distinct.setdefault(i["value"], i)
        opts = list(distinct.values())
        if name in RECONCILE_FIELDS and len(opts) > 1:
            best_rank = min(rank.get(o["source"], 999) for o in opts)
            default = max((o for o in opts if rank.get(o["source"], 999) == best_rank), key=lambda o: o["at"])
            fields.append({"name": name, "kind": "conflict",
                           "options": [{"value": o["value"], "source": o["source"]} for o in opts],
                           "suggested": {"value": default["value"], "source": default["source"]}})
            conflicts.append(name)
        elif name in RECONCILE_FIELDS:
            fields.append({"name": name, "kind": "single", "values": [{"value": opts[0]["value"], "source": opts[0]["source"]}]})
        else:
            fields.append({"name": name, "kind": "union", "values": [{"value": o["value"], "source": o["source"]} for o in opts]})
    return fields, conflicts


def _survivor(contact_ids: list, groups: dict, rank: dict) -> str:
    """Recommended surviving contact for a merge. Tie-break (dedupe-design Decision 3): source priority →
    most-complete (more distinct non-empty props) → most-recent → id. Stable multi-pass sort."""
    if not contact_ids:
        return ""
    def fields_of(cid):
        return len({prop[0].lower() for rec in groups.get(cid, []) for prop in rec["props"]
                    if prop[0].lower() not in _HIDDEN and _flatten(prop[3:] if len(prop) > 3 else [])})
    def rank_of(cid):
        return min((rank.get(r["source"], 999) for r in groups.get(cid, [])), default=999)
    def latest_of(cid):
        return max((r["ingested_at"] or "" for r in groups.get(cid, [])), default="")
    ordered = sorted(contact_ids)                                          # id asc (final tiebreak)
    ordered = sorted(ordered, key=latest_of, reverse=True)                 # then most-recent
    ordered = sorted(ordered, key=lambda c: (rank_of(c), -fields_of(c)))   # then priority, most-complete
    return ordered[0]


def preview_merge(home, contact_ids: list) -> dict:
    """Preview merging several canonical contacts into one — which fields **union**, which stay
    **single**, and which **conflict** (a single-valued field with >1 distinct value the reviewer must
    pick). Read-only; drives the Duplicates review surface (M3d). For each conflict it suggests the
    survivorship default (source priority → most-recent) but pre-selects nothing."""
    groups = _records_by_contact(home)
    rank = _rank(home)
    members = [rec for cid in contact_ids for rec in groups.get(cid, [])]
    fields, conflicts = _preview_fields(members, rank)
    member_list = []
    for cid in contact_ids:
        recs = groups.get(cid, [])
        name = ""
        for rec in recs:
            for prop in rec["props"]:
                if prop[0].lower() == "fn":
                    name = _flatten(prop[3:] if len(prop) > 3 else [])
                    break
            if name:
                break
        member_list.append({"id": cid, "name": name, "source": recs[0]["source"] if recs else ""})
    return {"member_ids": list(contact_ids), "members": member_list, "fields": fields, "conflicts": conflicts}


def clusters_meta(home, clusters: list) -> list:
    """Annotate detected duplicate clusters with bulk-merge metadata: per cluster, the conflicting
    single-valued ``conflicts`` (fields the reviewer must pick) and a recommended survivor ``into``.
    Loads the record map + source priority **once** for the whole batch (cheaper than per-cluster
    ``preview_merge``). Read-only; drives the bulk-approve group cards + spot-check defaults."""
    groups = _records_by_contact(home)
    rank = _rank(home)
    out = []
    for cl in clusters:
        ids = cl.get("member_ids", [])
        members = [rec for cid in ids for rec in groups.get(cid, [])]
        _, conflicts = _preview_fields(members, rank)
        out.append({"key": cl.get("key"), "conflicts": conflicts, "into": _survivor(ids, groups, rank)})
    return out


def _clean_prop(prop: list) -> list:
    """A fresh property array with the export-irrelevant ``group`` param stripped (the ``itemN``
    binding for ``x-ablabel`` labels, which the projection already treats as noise); ``type``/``pref``
    and the structured value are kept verbatim."""
    name, params, vtype, *values = prop
    return [name, {k: v for k, v in (params or {}).items() if k != "group"}, vtype, *values]


def _merge_props(cid: str, members: list, rank: dict, resolutions: dict) -> list:
    """The merged property set for one canonical contact, **preserving jCard structure** — the
    structure-keeping inverse of ``_merge``'s flattened view. UNION names keep every distinct value;
    a RECONCILE name collapses to one (an explicit ``field_resolution`` wins, else survivorship:
    source priority -> most-recent), exactly as the projection a user sees."""
    by_name: dict[str, list] = {}
    for rec in members:
        for prop in rec["props"]:
            name = prop[0].lower()
            if name in _HIDDEN or not _flatten(prop[3:] if len(prop) > 3 else []):
                continue
            by_name.setdefault(name, []).append({"prop": prop, "source": rec["source"], "at": rec["ingested_at"] or ""})

    out = []
    for name, items in by_name.items():
        if name in RECONCILE_FIELDS:
            res = resolutions.get((cid, name))
            if res and res[0] is not None:
                # Keep the structured property whose flattened value matches the chosen one; if the
                # choice was a manual edit with no matching source value, emit it as a plain text prop.
                match = next((i["prop"] for i in items
                              if _flatten(i["prop"][3:] if len(i["prop"]) > 3 else []) == res[0]), None)
                out.append(_clean_prop(match) if match is not None else [name, {}, "text", res[0]])
            else:
                best_rank = min(rank.get(i["source"], 999) for i in items)
                chosen = max((i for i in items if rank.get(i["source"], 999) == best_rank), key=lambda i: i["at"])
                out.append(_clean_prop(chosen["prop"]))
        else:
            seen: dict[str, list] = {}
            for i in items:
                seen.setdefault(_flatten(i["prop"][3:] if len(i["prop"]) > 3 else []), i["prop"])
            out.extend(_clean_prop(p) for p in seen.values())
    return out


def export_jcards(home) -> list:
    """Every canonical contact as an RFC 7095 jCard array (``["vcard", [props]]``), name-ordered
    (named first). The merged, structure-preserving basis for export — the inverse of ingest, consumed
    by ``cli.vcard_writer``. Pure read; never writes."""
    groups = _records_by_contact(home)
    rank = _rank(home)
    resolutions = private_db.field_resolutions(home.private_db) if home.private_db.exists() else {}
    rows = []
    for cid, members in groups.items():
        props = _merge_props(cid, members, rank, resolutions)
        fn = next((_flatten(p[3:] if len(p) > 3 else []) for p in props if p[0].lower() == "fn"), "")
        rows.append((fn == "", fn.casefold(), cid, props))       # named first, A–Z, stable by id
    rows.sort(key=lambda r: r[:3])
    return [["vcard", props] for *_, props in rows]


def get_contact(home, contact_id: str) -> dict | None:
    """One merged canonical contact (or ``None``). Computes only that contact's group."""
    groups = _records_by_contact(home)
    members = groups.get(contact_id)
    if not members:
        return None
    rank = {s: i for i, s in enumerate(private_db.source_priority(home.private_db) if home.private_db.exists()
                                       else private_db.DEFAULT_SOURCE_PRIORITY)}
    resolutions = private_db.field_resolutions(home.private_db) if home.private_db.exists() else {}
    return _merge(contact_id, members, rank, resolutions)
