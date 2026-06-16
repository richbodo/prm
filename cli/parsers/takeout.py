"""Google Takeout parser: a Takeout ``.zip`` -> ``ParsedRecord``s.

Takeout is *not* a separate format — it is a zip of ``.vcf`` files under a ``Contacts/`` tree, so
this delegates to the vCard parser. A real export splits contacts by label into per-folder ``.vcf``s
plus an ``All Contacts/All Contacts.vcf`` superset (and a legacy flat ``Contacts/All Contacts.vcf``).
We ingest the **All Contacts** superset to avoid counting the same person once per label folder.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from core import media

from . import vcard
from .base import ParsedRecord

SOURCE = "google_takeout"

# Google Takeout ships a contact's photo as a sidecar image named verbatim after the card's FN
# (`Contacts/<label>/<FN>.jpg`), scattered across the label folders while the cards we ingest come from
# the `All Contacts` superset. So we index the images **globally by FN** and attach one only when a name
# maps to a single distinct image — an exact, unambiguous match (verified against a real 1,101-photo
# export). Anything ambiguous is left for the guided matcher (R7c), never auto-guessed (AC-PRM-B ethos).
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp")
_COLLISION = re.compile(r"\(\d+\)$")           # Google's duplicate-name suffix, e.g. "Mark Lipscombe(3)"
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
         ".heic": "image/heic", ".webp": "image/webp"}


def _norm_name(s: str) -> str:
    """The match key for a card FN or an image file stem: drop a trailing `(N)`, collapse whitespace,
    casefold."""
    s = _COLLISION.sub("", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip().casefold()


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


def _photo_index(zf: zipfile.ZipFile, names: list[str]) -> dict:
    """``{normalized FN: (bytes, mime)}`` for every image whose name maps **unambiguously** to one
    distinct photo. A name pointing at two different images (two people sharing it) is dropped."""
    by_name: dict[str, dict[str, tuple[bytes, str]]] = {}
    for n in names:
        suffix = Path(n).suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            continue
        key = _norm_name(Path(n).stem)
        if not key:
            continue
        data = zf.read(n)
        if not data:
            continue
        by_name.setdefault(key, {})[media.content_hash(data)] = (data, _MIME.get(suffix, "image/jpeg"))
    # keep only names that resolve to exactly one distinct image (content hash)
    return {name: next(iter(variants.values())) for name, variants in by_name.items() if len(variants) == 1}


def _attach_photos(zf: zipfile.ZipFile, names: list[str], records: list[ParsedRecord]) -> None:
    """Attach each sidecar photo to the card whose FN matches it (exact, unambiguous). The matched photo
    supersedes any hosted ``PHOTO:https://…`` URL the card carries (we never fetch those — INV-1)."""
    index = _photo_index(zf, names)
    if not index:
        return
    for rec in records:
        fn = rec.get("FN")
        match = index.get(_norm_name(str(fn.value))) if fn and fn.value else None
        if match:
            rec.photo, rec.photo_mime = match
            rec.fields = [f for f in rec.fields if f.name != "PHOTO"]   # the real bytes win over a URL


def parse(zip_path: str | Path) -> list[ParsedRecord]:
    """Parse the All Contacts superset out of a Takeout zip, attaching each contact's sidecar photo."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        member = _all_contacts_member(names)
        if member is None:
            raise ValueError(f"no Contacts/.../All Contacts.vcf found in {zip_path}")
        records = vcard.parse(zf.read(member), source=SOURCE)
        _attach_photos(zf, names, records)
    return records


def parse_vcf_path(vcf_path: str | Path) -> list[ParsedRecord]:
    """Parse an already-unpacked All Contacts.vcf (the bulk fixture lives unpacked)."""
    return vcard.parse(Path(vcf_path).read_bytes(), source=SOURCE)
