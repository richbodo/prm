"""Google Takeout parser: a Takeout ``.zip`` -> ``ParsedRecord``s.

Takeout is *not* a separate format — it is a zip of ``.vcf`` files under a ``Contacts/`` tree, so
this delegates to the vCard parser. A real export splits contacts by label into per-folder ``.vcf``s
plus an ``All Contacts/All Contacts.vcf`` superset (and a legacy flat ``Contacts/All Contacts.vcf``).

By default we ingest the **All Contacts** superset only — the user's current, curated address book.
``all_folders=True`` (the ``prm import --all-folders`` "fuller import") *also* reads every label folder,
pulling in archival contacts (old imports, device backups) that aren't in All Contacts. Same-person
duplicates collapse automatically on the stable id (email-keyed records share a ``source_record_id``);
the remaining near-dups surface in the normal dedup review. The default path *reports* the label folders
it skipped (so the option is discoverable — no silent gap).
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
# (`Contacts/<label>/<FN>.jpg`), in the label folders. We resolve a photo to a contact two ways, both
# unambiguous-only (a name/email pointing at two different images is dropped, never auto-guessed —
# AC-PRM-B): a **global FN index** (filename → image), and — when reading the label folders — an
# **email bridge** (a folder card's FN matches its sidecar → that card's email → the image), which
# reattaches archival photos to current contacts even when the display name has since drifted. Verified
# against a real 1,101-photo export (email recovers ~4× more than FN alone).
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp")
_COLLISION = re.compile(r"\(\d+\)$")           # Google's duplicate-name suffix, e.g. "Mark Lipscombe(3)"
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
         ".heic": "image/heic", ".webp": "image/webp"}


def _norm_name(s: str) -> str:
    """The match key for a card FN or an image file stem: drop a trailing `(N)`, collapse whitespace,
    casefold."""
    s = _COLLISION.sub("", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip().casefold()


def _emails(rec: ParsedRecord) -> set:
    out = set()
    for f in rec.get_all("EMAIL"):
        v = f.value
        v = v if isinstance(v, str) else " ".join(v) if v else ""
        v = v.strip().lower()
        if "@" in v:
            out.add(v)
    return out


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


def label_folders(names: list[str]) -> list[str]:
    """Label-folder names (other than 'All Contacts') that hold a vcf — the contacts the default import
    skips. Used for the discoverability heads-up."""
    allc = _all_contacts_member(names)
    out = {Path(n).parent.name for n in names
           if n.lower().endswith(".vcf") and n != allc and "/contacts/" in n.lower()}
    return sorted(out)


def _unambiguous(by_key: dict) -> dict:
    """Collapse ``{key: {hash: (bytes, mime)}}`` to ``{key: (bytes, mime)}``, keeping only keys that map
    to exactly one distinct image."""
    return {k: next(iter(v.values())) for k, v in by_key.items() if len(v) == 1}


def _global_photo_index(zf: zipfile.ZipFile, names: list[str]) -> dict:
    """``{normalized FN: (bytes, mime)}`` over every image file, unambiguous-only (FN-direct match)."""
    by_name: dict[str, dict] = {}
    for n in names:
        suffix = Path(n).suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            continue
        key = _norm_name(Path(n).stem)
        data = zf.read(n) if key else b""
        if data:
            by_name.setdefault(key, {})[media.content_hash(data)] = (data, _MIME.get(suffix, "image/jpeg"))
    return _unambiguous(by_name)


def _email_photo_index(zf: zipfile.ZipFile, names: list[str]) -> dict:
    """``{normalized email: (bytes, mime)}`` — built by matching each label folder's sidecar images to
    that folder's vcf cards by FN-in-folder, then keying on those cards' emails (unambiguous-only). The
    strong bridge that reattaches an archival photo to a current contact via a shared email."""
    photos_by_folder: dict[str, dict] = {}
    for n in names:
        suffix = Path(n).suffix.lower()
        if suffix in _IMAGE_SUFFIXES and (key := _norm_name(Path(n).stem)):
            photos_by_folder.setdefault(str(Path(n).parent), {})[key] = (zf.read(n), _MIME.get(suffix, "image/jpeg"))
    by_email: dict[str, dict] = {}
    for n in names:
        if not n.lower().endswith(".vcf"):
            continue
        folder_photos = photos_by_folder.get(str(Path(n).parent))
        if not folder_photos:
            continue
        for rec in vcard.parse(zf.read(n), source=SOURCE):
            fn = rec.get("FN")
            match = folder_photos.get(_norm_name(str(fn.value))) if fn and fn.value else None
            if match:
                for e in _emails(rec):
                    by_email.setdefault(e, {})[media.content_hash(match[0])] = match
    return _unambiguous(by_email)


def _attach_photos(zf: zipfile.ZipFile, names: list[str], records: list[ParsedRecord], *,
                   email_index: dict | None = None) -> None:
    """Attach a sidecar photo to each card — by **email** first (the strong bridge, when available), else
    the **global FN** index. The matched photo supersedes any hosted ``PHOTO:https://…`` URL (INV-1)."""
    fn_index = _global_photo_index(zf, names)
    email_index = email_index or {}
    if not fn_index and not email_index:
        return
    for rec in records:
        fn = rec.get("FN")
        key = _norm_name(str(fn.value)) if fn and fn.value else ""
        match = next((email_index[e] for e in _emails(rec) if e in email_index), None) or fn_index.get(key)
        if match:
            rec.photo, rec.photo_mime = match
            rec.fields = [f for f in rec.fields if f.name != "PHOTO"]   # the real bytes win over a URL


def _all_vcf_members(names: list[str], allc: str | None) -> list[str]:
    """Every Contacts vcf, with All Contacts **last** so it wins the stable-id upsert for any contact it
    shares with a label folder (the current, curated card is preferred over an archival copy)."""
    vcfs = [n for n in names if n.lower().endswith(".vcf") and "/contacts/" in n.lower()]
    others = sorted(n for n in vcfs if n != allc)
    return others + ([allc] if allc else [])


def parse(zip_path: str | Path, *, all_folders: bool = False) -> list[ParsedRecord]:
    """Parse a Takeout zip → records, attaching each contact's sidecar photo. By default reads only the
    All Contacts superset; ``all_folders`` also reads every label folder (the fuller import)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        allc = _all_contacts_member(names)
        members = _all_vcf_members(names, allc) if all_folders else ([allc] if allc else [])
        if not members:
            raise ValueError(f"no Contacts/.../All Contacts.vcf found in {zip_path}")
        records: list[ParsedRecord] = []
        for m in members:
            records += vcard.parse(zf.read(m), source=SOURCE)
        email_index = _email_photo_index(zf, names) if all_folders else None
        _attach_photos(zf, names, records, email_index=email_index)
    return records


def skipped_label_folders(zip_path: str | Path) -> list[str]:
    """The label folders a *default* import skips — for the discoverability heads-up. Returns [] if the
    path isn't a readable Takeout zip."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return label_folders(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return []


def parse_vcf_path(vcf_path: str | Path) -> list[ParsedRecord]:
    """Parse an already-unpacked All Contacts.vcf (the bulk fixture lives unpacked)."""
    return vcard.parse(Path(vcf_path).read_bytes(), source=SOURCE)
