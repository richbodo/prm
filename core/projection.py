"""The canonical-contact projection: identity_map ⨝ source_records ⨝ field_resolutions.

Composes the two stores — raw records from ``core.shared_db`` + decisions from ``core.relationships_db`` —
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

import base64
import json

from core import media, relationships_db, schema, shared_db

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


# --------------------------------------------------------------------------- photos / avatar
# A contact's avatar is never a text field — it is served as bytes by the local daemon (see
# docs/design-notes/contact-images.md). We surface only a *presence marker* in the projected contact
# (so base64 never bloats the JSON or crosses the MCP surface), and resolve the actual bytes on demand.
_PHOTO = "photo"


def _photo_prop(members: list) -> list | None:
    """The first source PHOTO property among a contact's records (its raw jCard prop array), or None."""
    for rec in members:
        for prop in rec["props"]:
            if prop[0].lower() == _PHOTO:
                return prop
    return None


def _first_param(params, key: str) -> str:
    v = (params or {}).get(key) if isinstance(params, dict) else None
    v = v[0] if isinstance(v, list) else v
    return str(v).lower() if v else ""


def _photo_value(prop) -> str:
    for v in (prop[3:] if prop else []):
        if isinstance(v, str) and v:
            return v
    return ""


def _source_photo_present(members: list) -> bool:
    """Whether an *inline* (decodable) source PHOTO exists — a `data:` URI or base64 (``ENCODING=b``).
    A remote ``http(s)`` URL or a bare filename ref is **not** present (we never fetch it — INV-1).
    Cheap: inspects the value/params, never decodes (the list/dedup paths run this for every contact)."""
    prop = _photo_prop(members)
    val = _photo_value(prop).strip().lower()
    if not val or val.startswith(("http://", "https://")):
        return False
    if val.startswith("data:"):
        return True
    return _first_param(prop[1] if len(prop) > 1 else {}, "encoding") in ("b", "base64")


def _decode_inline_photo(prop) -> tuple[bytes, str] | None:
    """``(bytes, mime)`` for an inline PHOTO prop (base64 / ``data:`` URI), or None for a URL-only /
    non-inline photo. INV-1: a remote URL is never fetched here."""
    val = _photo_value(prop).strip()
    low = val.lower()
    if not val or low.startswith(("http://", "https://")):
        return None
    if low.startswith("data:"):                                  # data:image/png;base64,XXXX
        head, _, b64 = val.partition(",")
        mime = head[5:].split(";")[0].strip() or None
        data = _b64(b64)
        return (data, mime or media.sniff_mime(data) or "application/octet-stream") if data else None
    params = prop[1] if len(prop) > 1 else {}
    if _first_param(params, "encoding") not in ("b", "base64"):  # bare ref (filename / scheme-less) — not inline
        return None
    data = _b64(val)
    if not data:
        return None
    typ = _first_param(params, "type")
    mime = (f"image/{typ}" if typ else None) or media.sniff_mime(data) or "application/octet-stream"
    return data, ("image/jpeg" if mime == "image/jpg" else mime)


def _b64(s: str) -> bytes:
    try:
        return base64.b64decode(s, validate=False)
    except (ValueError, TypeError):
        return b""


def _photo_marker(members: list, rel_values: list | None) -> dict:
    """Presence marker for the contact JSON: a user-uploaded override (a ``photo`` field value) wins;
    else an imported inline PHOTO; else nothing. Carries no bytes — the daemon serves those."""
    if any(v["field_id"] == _PHOTO and v.get("value") for v in (rel_values or [])):
        return {"present": True, "source": "user"}
    if _source_photo_present(members):
        return {"present": True, "source": "import"}
    return {"present": False, "source": None}


def contact_photo(home, contact_id: str) -> tuple[bytes, str] | None:
    """Resolve a contact's avatar bytes for the **local daemon** to serve: a user-uploaded override
    (a ``photo`` field value → the content-addressed media store) wins; otherwise the imported inline
    PHOTO is decoded from ``shared.db``. Returns ``(bytes, mime)`` or None. INV-1: a URL-only photo is
    never fetched. The MCP surface does NOT expose this — photo bytes stay on the device."""
    override = None
    if home.relationships_db.exists():
        for v in relationships_db.field_values_for(home.relationships_db, contact_id):
            if v["field_id"] == _PHOTO and v.get("value"):
                override = v["value"]
                break
    if override:
        return media.read(home, override)                # None if the blob is missing (prm doctor flags it)
    members = _records_by_contact(home).get(contact_id)
    return _decode_inline_photo(_photo_prop(members)) if members else None


def referenced_image_hashes(home) -> set:
    """Every content hash referenced by an ``image``-kind field value — the live set for media gc /
    verify (``prm doctor``). Imported photos are inline in shared.db and need no blob, so only uploads
    appear here."""
    if not home.relationships_db.exists():
        return set()
    image_fields = {f["field_id"] for f in schema.list_fields(home.relationships_db) if f["kind"] == "image"}
    hashes = set()
    for vals in relationships_db.all_field_values(home.relationships_db).values():
        for v in vals:
            if v["field_id"] in image_fields and v.get("value"):
                hashes.add(v["value"])
    return hashes


def _records_by_contact(home) -> dict:
    """{contact_id: [ {source, ingested_at, props}, … ]}, grouped via the identity_map (a record with
    no mapping defaults to its own contact, so the projection is robust if relationships.db is absent)."""
    con = shared_db.connect(home.shared_db, read_only=True)
    try:
        rows = con.execute(
            "SELECT source_record_id, source, ingested_at, raw_jcard FROM source_records"
        ).fetchall()
    finally:
        con.close()
    imap = relationships_db.identity_map(home.relationships_db) if home.relationships_db.exists() else {}
    groups: dict[str, list] = {}
    for srid, source, ingested_at, raw in rows:
        cid = imap.get(srid, srid)
        groups.setdefault(cid, []).append({"source": source, "ingested_at": ingested_at, "props": _props(raw)})
    return groups


def _relationship_fields(rel_values: list) -> list:
    """Build the relationship-overlay fields (custom schema + built-in tags/notes/photo) for a contact
    from its ``field_values`` rows. Multi-valued kinds gather all values; each field carries its
    ``disclosure_tier`` (the read-side data-floor key) and ``custom: True`` so the UI groups them apart
    and the MCP seal (``strip_sealed``) can drop the sealed ones."""
    by_field: dict[str, dict] = {}
    for v in rel_values:
        f = by_field.get(v["field_id"])
        if f is None:
            f = {"name": v["field_id"], "label": v["label"], "kind": v["kind"], "custom": True,
                 "disclosure_tier": v["disclosure_tier"], "values": []}
            by_field[v["field_id"]] = f
        if v["value"] not in (None, ""):
            f["values"].append({"value": v["value"]})
    return list(by_field.values())


def _merge(cid: str, members: list, rank: dict, resolutions: dict, rel_values: list | None = None) -> dict:
    """Merge a contact's source records into one canonical contact, then append its relationship-overlay
    fields (``rel_values`` — the field_values rows; empty/None pre-R2 or when none are set)."""
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

    fields.extend(_relationship_fields(rel_values or []))   # custom schema + tags/notes/photo
    photo = _photo_marker(members, rel_values)
    fields = [f for f in fields if f["name"] != _PHOTO]      # the avatar is served as bytes, never a text row

    return {
        "id": cid,
        "fn": pick("fn"),
        "email": pick("email"),
        "org": pick("org"),
        "sources": sources,
        "source": sources[0] if sources else "",
        "member_count": len(members),
        "photo": photo,
        "fields": fields,
    }


def strip_sealed(contact: dict | None) -> dict | None:
    """The read-side data-floor (PR-7 / AC-MCP-C): drop every ``private-sealed`` relationship field from
    a projected contact. Source (shared-tier) fields carry no ``disclosure_tier`` and always pass.
    Returns a shallow copy; ``None`` passes through. The MCP read surface applies this; the daemon
    (the local workspace) does not."""
    if not contact:
        return contact
    kept = [f for f in contact.get("fields", []) if f.get("disclosure_tier") != "private-sealed"]
    return {**contact, "fields": kept}


def _all(home) -> list:
    groups = _records_by_contact(home)
    rank = {s: i for i, s in enumerate(relationships_db.source_priority(home.relationships_db) if home.relationships_db.exists()
                                       else relationships_db.DEFAULT_SOURCE_PRIORITY)}
    resolutions = relationships_db.field_resolutions(home.relationships_db) if home.relationships_db.exists() else {}
    rel = relationships_db.all_field_values(home.relationships_db) if home.relationships_db.exists() else {}
    return [_merge(cid, members, rank, resolutions, rel.get(cid, [])) for cid, members in groups.items()]


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
             "source": c["source"], "sources": c["sources"], "member_count": c["member_count"],
             "has_photo": c["photo"]["present"]}
            for c in page
        ],
    }


def _rank(home) -> dict:
    """{source: priority-index} — lower is more authoritative (Apple/Google before LinkedIn/Facebook)."""
    order = relationships_db.source_priority(home.relationships_db) if home.relationships_db.exists() else relationships_db.DEFAULT_SOURCE_PRIORITY
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
    resolutions = relationships_db.field_resolutions(home.relationships_db) if home.relationships_db.exists() else {}
    rows = []
    for cid, members in groups.items():
        props = _merge_props(cid, members, rank, resolutions)
        fn = next((_flatten(p[3:] if len(p) > 3 else []) for p in props if p[0].lower() == "fn"), "")
        rows.append((fn == "", fn.casefold(), cid, props))       # named first, A–Z, stable by id
    rows.sort(key=lambda r: r[:3])
    return [["vcard", props] for *_, props in rows]


def get_contact(home, contact_id: str) -> dict | None:
    """One merged canonical contact (or ``None``). Computes only that contact's group, with its
    relationship-overlay fields. NOT sealed — the daemon (local workspace) sees everything; the MCP
    surface applies ``strip_sealed``."""
    groups = _records_by_contact(home)
    members = groups.get(contact_id)
    if not members:
        return None
    rank = {s: i for i, s in enumerate(relationships_db.source_priority(home.relationships_db) if home.relationships_db.exists()
                                       else relationships_db.DEFAULT_SOURCE_PRIORITY)}
    resolutions = relationships_db.field_resolutions(home.relationships_db) if home.relationships_db.exists() else {}
    rel = relationships_db.field_values_for(home.relationships_db, contact_id) if home.relationships_db.exists() else []
    return _merge(contact_id, members, rank, resolutions, rel)
