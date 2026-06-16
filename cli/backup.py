"""PRM raw backup: a lossless, re-importable JSON dump of the **raw Shared DB records**.

The companion to the merged vCard export (``cli/vcard_writer.py``). Where that emits the *de-duplicated*
contacts for portability to other apps — and therefore can't round-trip back into PRM — this dumps
**every raw source record exactly as stored**: its ``source`` label, the stored ``stable_key``, the
canonical jCard, and per-field provenance. Re-importing the dump reconstructs the same
``source_record_id`` for every record (``sha1(source ∥ stable_key)``), so it **re-attaches** rather
than creating fresh records (a true store round-trip — INV-5).

Scope: this backs up the **mirrored contacts** (the raw Shared DB, INV-2). Private merge decisions live
in ``relationships.db`` and are a separate concern — when restored into a home whose ``relationships.db`` still
references these record ids, those decisions continue to apply.

    {"prm_backup": 1, "exported_at": "…Z", "records": [
        {"source": "...", "source_uid": null, "stable_key": "email:…",
         "ingested_at": "…Z", "jcard": ["vcard", [...]], "provenance": [{field, value, observed_at}]}
    ]}
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from core import media

from .identity import StableKey
from .jcard import JCard
from .normalize import CanonicalContact
from .provenance import FieldProvenance

BACKUP_KEY = "prm_backup"
BACKUP_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _photo_ref(jcard: list) -> str | None:
    """The media content hash a record's jCard references via its ``prm-media:`` PHOTO, or None."""
    for prop in (jcard[1] if isinstance(jcard, list) and len(jcard) == 2 else []):
        if prop and prop[0].lower() == "photo":
            return media.parse_ref((prop[3] if len(prop) > 3 else "") or "")
    return None


def dump(records: list[dict], *, home=None, exported_at: str | None = None) -> str:
    """Serialize raw Shared DB records (``core.shared_db.all_records``) to a backup document. ``raw_jcard``
    (stored TEXT) is embedded as its parsed jCard array so the backup is human-readable JSON throughout.
    When ``home`` is given, the **media blobs** that the records reference (``prm-media:<hash>`` photos)
    are bundled as base64 under ``"media"`` — so the backup is a lossless round-trip for imported photos."""
    out_records = [
        {
            "source": r["source"],
            "source_uid": r["source_uid"],
            "stable_key": r["stable_key"],
            "ingested_at": r["ingested_at"],
            "jcard": json.loads(r["raw_jcard"]),
            "provenance": list(r.get("provenance") or []),
        }
        for r in records
    ]
    doc = {BACKUP_KEY: BACKUP_VERSION, "exported_at": exported_at or _now_iso(), "records": out_records}
    if home is not None:
        blobs: dict[str, str] = {}
        for rec in out_records:
            h = _photo_ref(rec["jcard"])
            if h and h not in blobs and (got := media.read(home, h)):
                blobs[h] = base64.b64encode(got[0]).decode("ascii")
        if blobs:
            doc["media"] = blobs
    return json.dumps(doc, ensure_ascii=False, indent=2)


def load(data) -> list[CanonicalContact]:
    """Restore a backup document to ``CanonicalContact``s, **bypassing normalization** so the stored
    ``stable_key`` and provenance are preserved verbatim (re-deriving them could drift — e.g. a fragile
    content hash). Accepts bytes/str/dict. Raises ``ValueError`` if it isn't a PRM backup."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict) or BACKUP_KEY not in data:
        raise ValueError("not a PRM backup (missing 'prm_backup' key)")
    version = data.get(BACKUP_KEY)
    if version != BACKUP_VERSION:
        raise ValueError(f"unsupported PRM backup version {version!r} (expected {BACKUP_VERSION})")

    blobs = data.get("media") or {}                          # {content_hash: base64} — bundled photo bytes
    contacts: list[CanonicalContact] = []
    for rec in data.get("records", []):
        source = rec["source"]
        jcard = JCard.from_jcard(rec["jcard"])
        provenance = [
            FieldProvenance(field=p["field"], value=p["value"], source=source, observed_at=p.get("observed_at"))
            for p in rec.get("provenance", [])
        ]
        # restore a referenced media blob (the jcard keeps the prm-media ref; _persist writes the bytes)
        photo_bytes = None
        h = _photo_ref(rec["jcard"])
        if h and h in blobs:
            try:
                photo_bytes = base64.b64decode(blobs[h])
            except (ValueError, TypeError):
                photo_bytes = None
        contacts.append(CanonicalContact(
            source=source,
            source_uid=rec.get("source_uid"),
            jcard=jcard,
            provenance=provenance,
            stable_key=StableKey.from_str(rec["stable_key"]),
            photo_bytes=photo_bytes,
        ))
    return contacts
