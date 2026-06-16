"""Shared parser types and format detection.

``RawField`` / ``ParsedRecord`` are the boundary between a specific parsing library (vobject today)
and the rest of the ingestor. A parser emits these; nothing downstream imports the parsing library.
"""

from __future__ import annotations

import enum
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SourceFormat(enum.Enum):
    VCARD = "vcard"             # a .vcf file (one or many cards)
    GOOGLE_TAKEOUT = "takeout"  # a Takeout .zip (vCards under Contacts/)
    VENDOR_CSV = "csv"          # a vendor CSV (LinkedIn, Google CSV), incl. a LinkedIn export zip
    FACEBOOK_JSON = "facebook"  # a Facebook DYI friends .json, or an export .zip containing one
    PRM_BACKUP = "prm_backup"   # PRM's own lossless raw-records backup .json (re-importable)
    UNKNOWN = "unknown"


@dataclass
class RawField:
    """One source-native property line, normalized to plain Python.

    ``value`` is a ``str`` for simple properties, or a ``list[str]`` for structured/multi values
    (``N``, ``ADR``, ``ORG``, ``CATEGORIES``). ``group`` is the vCard group prefix (``item1``) that
    binds, e.g., an ``X-ABLabel`` to the property it labels; ``None`` when ungrouped.
    """

    name: str                       # UPPERCASE source property name, e.g. "EMAIL", "X-ABLABEL"
    value: Any                      # str | list[str]
    params: dict[str, list[str]] = field(default_factory=dict)
    group: str | None = None


@dataclass
class ParsedRecord:
    """One source record as a flat list of fields, plus where it came from."""

    source: str                     # source identifier, e.g. "google_takeout"
    fields: list[RawField]
    raw_text: str = ""              # the original serialized record (for shared.db / debugging)
    photo: bytes | None = None      # decoded image bytes matched from a sidecar file (e.g. Google Takeout)
    photo_mime: str | None = None   # its mime, if known from the file extension

    def get(self, name: str) -> RawField | None:
        name = name.upper()
        return next((f for f in self.fields if f.name == name), None)

    def get_all(self, name: str) -> list[RawField]:
        name = name.upper()
        return [f for f in self.fields if f.name == name]


def detect_format(path: str | Path) -> SourceFormat:
    """Best-effort format detection by extension + light content sniffing."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".vcf":
        return SourceFormat.VCARD
    if suffix == ".csv":
        return SourceFormat.VENDOR_CSV
    if suffix == ".json":
        head = p.read_bytes()[:4096]
        if b'"prm_backup"' in head:       # PRM's own raw-records backup
            return SourceFormat.PRM_BACKUP
        if b'"friends' in head:          # "friends" or "friends_v2"
            return SourceFormat.FACEBOOK_JSON
        return SourceFormat.UNKNOWN
    if suffix == ".zip" and zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
        if any("/Contacts/" in n and n.lower().endswith(".vcf") for n in names):
            return SourceFormat.GOOGLE_TAKEOUT
        if any(n.endswith("Connections.csv") for n in names):
            return SourceFormat.VENDOR_CSV  # LinkedIn export
        if any("friend" in n.lower() and n.lower().endswith(".json") for n in names):
            return SourceFormat.FACEBOOK_JSON
    return SourceFormat.UNKNOWN
