"""Google Takeout parser: a Takeout ``.zip`` -> ``ParsedRecord``s.

Takeout is *not* a separate format — it is a zip of ``.vcf`` files under a ``Contacts/`` tree, so
this delegates to the vCard parser. A real export splits contacts by label into per-folder ``.vcf``s
plus an ``All Contacts/All Contacts.vcf`` superset (and a legacy flat ``Contacts/All Contacts.vcf``).
We ingest the **All Contacts** superset to avoid counting the same person once per label folder.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from . import vcard
from .base import ParsedRecord

SOURCE = "google_takeout"


def _all_contacts_member(names: list[str]) -> str | None:
    """Pick the 'All Contacts' vcf (modern foldered, then legacy flat)."""
    vcfs = [n for n in names if n.lower().endswith(".vcf") and "/contacts/" in n.lower()]
    foldered = [n for n in vcfs if n.lower().endswith("/all contacts/all contacts.vcf")]
    if foldered:
        return foldered[0]
    flat = [n for n in vcfs if n.lower().endswith("/contacts/all contacts.vcf")]
    if flat:
        return flat[0]
    return None


def parse(zip_path: str | Path) -> list[ParsedRecord]:
    """Parse the All Contacts superset out of a Takeout zip."""
    with zipfile.ZipFile(zip_path) as zf:
        member = _all_contacts_member(zf.namelist())
        if member is None:
            raise ValueError(f"no Contacts/.../All Contacts.vcf found in {zip_path}")
        raw = zf.read(member)
    return vcard.parse(raw, source=SOURCE)


def parse_vcf_path(vcf_path: str | Path) -> list[ParsedRecord]:
    """Parse an already-unpacked All Contacts.vcf (the bulk fixture lives unpacked)."""
    return vcard.parse(Path(vcf_path).read_bytes(), source=SOURCE)
