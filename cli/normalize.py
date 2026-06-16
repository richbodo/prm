"""Normalize: source-native ``ParsedRecord`` -> canonical ``CanonicalContact``.

This is where source fields become **one canonical representation** (jCard, RFC 7095), every value
gets **per-field provenance** (INV-7), and the record is assigned a **stable id** (INV-5). It is the
single place that knows how a vCard property maps to a jCard property; parsers stay dumb, the loader
(a later milestone) stays generic.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import media

from .identity import StableKey, stable_key
from .jcard import JCard
from .parsers.base import ParsedRecord, RawField
from .provenance import FieldProvenance, value_to_text

# vCard-format artifacts that are not contact data. REV is consumed as the provenance timestamp.
_SKIP = {"VERSION", "PRODID", "REV"}

# Non-default jCard value types (everything else is "text").
_TYPE = {
    "url": "uri",
    "photo": "uri",
    "bday": "date-and-or-time",
    "anniversary": "date-and-or-time",
}


@dataclass
class CanonicalContact:
    """A contact in canonical form, ready to load into shared.db."""

    source: str
    source_uid: str | None          # the source's own UID if any (re-import re-attach key)
    jcard: JCard
    provenance: list[FieldProvenance]
    stable_key: StableKey
    raw_text: str = ""
    photo_bytes: bytes | None = None   # image bytes to write to the media store at persist (the jcard
    photo_mime: str | None = None      # carries a `prm-media:<hash>` ref; bytes never enter the DB)

    @property
    def display_name(self) -> str | None:
        return self.jcard.first_value("fn")


def _jcard_params(f: RawField) -> dict:
    """vCard params -> jCard params object: lowercase names, lowercase values, group preserved."""
    params: dict = {}
    for k, vals in f.params.items():
        low = [v.lower() for v in vals]
        params[k.lower()] = low[0] if len(low) == 1 else low
    if f.group:
        params["group"] = f.group
    return params


def to_canonical(record: ParsedRecord) -> CanonicalContact:
    rev = record.get("REV")
    observed_at = str(rev.value) if rev and rev.value else None
    uid_field = record.get("UID")
    source_uid = str(uid_field.value) if uid_field and uid_field.value else None

    jc = JCard()
    provenance: list[FieldProvenance] = []
    for f in record.fields:
        if f.name in _SKIP:
            continue
        name = f.name.lower()
        params = _jcard_params(f)
        vtype = _TYPE.get(name, "text")

        if name == "categories" and isinstance(f.value, list):
            jc.add(name, params, vtype, *f.value)          # multi-valued: several value elements
        else:
            jc.add(name, params, vtype, f.value)           # scalar, or one structured array (n/adr/org)

        provenance.append(
            FieldProvenance(field=name, value=value_to_text(f.value), source=record.source, observed_at=observed_at)
        )

    # A sidecar photo (e.g. matched from a Google Takeout image) becomes a `prm-media:<hash>` reference in
    # the jCard (lean — the bytes go to the media store at persist), superseding any source PHOTO above.
    if record.photo:
        h = media.content_hash(record.photo)
        subtype = (record.photo_mime or "image/jpeg").split("/")[-1]
        jc.add("photo", {"type": subtype}, "uri", media.make_ref(h))   # stable_key ignores photo (identity.py)
        provenance.append(FieldProvenance(field="photo", value=media.make_ref(h),
                                          source=record.source, observed_at=observed_at))

    return CanonicalContact(
        source=record.source,
        source_uid=source_uid,
        jcard=jc,
        provenance=provenance,
        stable_key=stable_key(jc, record.source),
        raw_text=record.raw_text,
        photo_bytes=record.photo,
        photo_mime=record.photo_mime,
    )


def normalize_all(records: list[ParsedRecord]) -> list[CanonicalContact]:
    return [to_canonical(r) for r in records]
