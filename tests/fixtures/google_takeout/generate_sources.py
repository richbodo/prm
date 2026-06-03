#!/usr/bin/env python3
"""Generate the synthetic Google Takeout fixture trees.

This script is the single source of truth for the unpacked Takeout fixtures under
``sources/``. It is pure-stdlib and deterministic: running it twice produces byte-identical
output (no timestamps, no randomness), so the committed ``sources/`` tree and ``expected/``
manifests can be regenerated and diffed in review.

All data here is SYNTHETIC. Names are historical computing figures; emails use the reserved
``example.com``/``example.org`` domains (RFC 2606) and phone numbers use the 555-01xx fictional
block (NANP). No real contact data is ever committed to this repo — drop real exports in the
repo-root ``ignore-data/`` (gitignored) instead.

What the corpus deliberately exercises (the real-world quirks the ingester must survive):
  * vCard **3.0** — the version Google Takeout actually emits.
  * CRLF line endings and 75-octet line folding (continuation lines begin with a space).
  * Google's ``itemN.PROP`` / ``itemN.X-ABLabel`` property-grouping convention.
  * Contacts with **no UID** — the common case for Takeout, which forces the
    email/content-hash stable-id fallback (INV-5).
  * A **name-less card** (no ``N``/``FN`` at all) — ~33% of a real export; identity must fall back
    to the email and the projection must tolerate a missing display name.
  * ``CATEGORIES`` — Google's label/group membership, present even in ``All Contacts.vcf``.
  * Unicode names and apostrophes.
  * Photos embedded as base64 (``PHOTO;ENCODING=b``) and, separately, referenced as external
    image files named after the contact (a documented heuristic; see README).
  * The same person appearing across ``All Contacts`` / ``My Contacts`` / label folders.
  * Near-duplicate records across labels (case-different email, extra phone, name variant) for
    dedup testing.
  * The legacy flat ``Contacts/All Contacts.vcf`` layout alongside the modern foldered one.

Usage:
    python generate_sources.py          # regenerate sources/ and expected/
    python generate_sources.py --check  # fail if regeneration would change committed output
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"

CRLF = "\r\n"

ARCHIVE_BROWSER_HTML = (
    "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
    "<title>Google Takeout (synthetic fixture)</title></head>"
    "<body><h1>Your Takeout archive</h1>"
    "<p>SYNTHETIC FIXTURE — not a real export. See the fixture README.</p>"
    "</body></html>"
)


# --------------------------------------------------------------------------------------------
# Tiny deterministic PNG (solid colour). Used for embedded and external photo fixtures so the
# image-handling paths see real, decodable bytes without pulling in Pillow or bloating git.
# --------------------------------------------------------------------------------------------
def make_png(width: int = 8, height: int = 8, rgb: tuple[int, int, int] = (0x4A, 0x90, 0xD9)) -> bytes:
    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# --------------------------------------------------------------------------------------------
# vCard 3.0 emitter
# --------------------------------------------------------------------------------------------
def fold(line: str) -> str:
    """Fold a single logical line at 75 octets per RFC 6350 §3.2 (continuations start with SPACE).

    Only called on ASCII content (base64 blobs, long English notes) so byte-splitting never
    severs a multibyte character. Unicode property lines are kept short by construction.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    pieces = [raw[:75]]
    rest = raw[75:]
    while rest:
        pieces.append(b" " + rest[:74])
        rest = rest[74:]
    return CRLF.join(p.decode("ascii") for p in pieces)


class Contact:
    """A synthetic contact. ``emails``/``tels`` may be grouped Google-style via ``itemN``.

    ``fn`` and ``n`` may be ``None`` to model a **name-less** card (no ``FN``/``N`` emitted) — the
    common Takeout case where a contact is identified only by email/phone.
    """

    def __init__(
        self,
        fn,                      # display name, or None for a name-less card
        n,                       # (family, given, additional, prefix, suffix), or None
        uid=None,
        emails=None,             # list of (label, address); label drives itemN grouping if grouped=True
        tels=None,               # list of (type, number)
        org=None,
        title=None,
        bday=None,
        note=None,
        categories=None,         # list of label/group names -> CATEGORIES:a,b
        extras=None,             # list of raw vCard lines (already property:value)
        photo_png=None,          # bytes -> embedded as PHOTO;ENCODING=b
        grouped=False,           # use itemN.EMAIL + itemN.X-ABLabel grouping
    ):
        self.fn = fn
        self.n = n
        self.uid = uid
        self.emails = emails or []
        self.tels = tels or []
        self.org = org
        self.title = title
        self.bday = bday
        self.note = note
        self.categories = categories or []
        self.extras = extras or []
        self.photo_png = photo_png
        self.grouped = grouped

    def to_vcard(self) -> str:
        lines = ["BEGIN:VCARD", "VERSION:3.0"]
        if self.n is not None:
            lines.append("N:" + ";".join(self.n))
        if self.fn is not None:
            lines.append("FN:" + self.fn)
        if self.org:
            lines.append("ORG:" + self.org)
        if self.title:
            lines.append("TITLE:" + self.title)

        if self.grouped:
            item = 0
            for label, address in self.emails:
                item += 1
                lines.append(f"item{item}.EMAIL;TYPE=INTERNET:" + address)
                lines.append(f"item{item}.X-ABLabel:" + label)
            for label, number in self.tels:
                item += 1
                lines.append(f"item{item}.TEL:" + number)
                lines.append(f"item{item}.X-ABLabel:" + label)
        else:
            for label, address in self.emails:
                lines.append(f"EMAIL;TYPE=INTERNET,{label}:" + address)
            for typ, number in self.tels:
                lines.append(f"TEL;TYPE={typ}:" + number)

        if self.bday:
            lines.append("BDAY:" + self.bday)
        if self.categories:
            lines.append("CATEGORIES:" + ",".join(self.categories))
        for extra in self.extras:
            lines.append(extra)
        if self.note:
            lines.append(fold("NOTE:" + self.note))
        if self.uid:
            lines.append("UID:" + self.uid)
        if self.photo_png is not None:
            b64 = base64.b64encode(self.photo_png).decode("ascii")
            lines.append(fold("PHOTO;ENCODING=b;TYPE=PNG:" + b64))
        lines.append("END:VCARD")
        return CRLF.join(lines) + CRLF


def vcf(contacts) -> str:
    return "".join(c.to_vcard() for c in contacts)


# --------------------------------------------------------------------------------------------
# Synthetic contact builders
# --------------------------------------------------------------------------------------------
def ada(uid="urn:uuid:ada00000-0000-4000-8000-000000000001", grouped=False, photo=False):
    return Contact(
        fn="Ada Lovelace",
        n=("Lovelace", "Ada", "", "", ""),
        uid=uid,
        emails=[("Work", "ada.lovelace@example.com")],
        tels=[("CELL", "+1-555-0101")],
        org="Analytical Engines",
        title="Mathematician",
        grouped=grouped,
        photo_png=make_png(rgb=(0xC0, 0x39, 0x2B)) if photo else None,
    )


def grace():
    return Contact(
        fn="Grace Hopper",
        n=("Hopper", "Grace", "Brewster", "RADM", ""),
        uid="urn:uuid:9c001000-0000-4000-8000-000000000002",
        emails=[("Work", "grace.hopper@example.org")],
        tels=[("WORK", "+1-555-0102")],
        org="US Navy",
    )


def alan():
    return Contact(
        fn="Alan Turing",
        n=("Turing", "Alan", "Mathison", "", ""),
        uid="urn:uuid:a1a17000-0000-4000-8000-000000000003",
        emails=[("Home", "alan.turing@example.com")],
        tels=[("HOME", "+1-555-0103")],
    )


def jose():
    # Unicode name; grouped emails; no UID; phonetic + bday extras; long folded note.
    return Contact(
        fn="José Ñoño",
        n=("Ñoño", "José", "", "", ""),
        uid=None,
        emails=[("Work", "jose.nono@example.com"), ("Personal", "jose@example.org")],
        tels=[("Mobile", "+1-555-0110")],
        org="Café Müller",
        bday="1985-07-21",
        extras=["X-PHONETIC-FIRST-NAME:Ho-SAY", "X-PHONETIC-LAST-NAME:NYO-nyo"],
        note=(
            "Met at the 2019 distributed-systems workshop in Lisbon; introduced by a mutual "
            "colleague. Prefers email over phone. This note is intentionally long so it exceeds "
            "the 75-octet line limit and forces the parser to reassemble a folded NOTE property "
            "across several continuation lines."
        ),
        grouped=True,
    )


def tanaka():
    # Unicode CJK name; structured N; no UID.
    return Contact(
        fn="田中 太郎",
        n=("田中", "太郎", "", "", ""),
        uid=None,
        emails=[("Work", "taro.tanaka@example.com")],
        tels=[("CELL", "+81-3-5555-0111")],
        org="株式会社サンプル",
        grouped=True,
    )


def mary(uid=None):
    # Apostrophe in name; grouped emails; appears in multiple folders.
    return Contact(
        fn="Mary O'Brien",
        n=("O'Brien", "Mary", "", "", ""),
        uid=uid,
        emails=[("Work", "mary.obrien@example.com")],
        tels=[("WORK", "+1-555-0112")],
        org="O'Brien & Co.",
        title="Director",
        grouped=True,
    )


def noname():
    # Real Takeout case (~33% of cards): no N/FN at all. Identified only by a grouped email, and a
    # member of two groups via CATEGORIES. Forces identity onto the email (no name to hash) and the
    # projection to render without a display name.
    return Contact(
        fn=None,
        n=None,
        uid=None,
        emails=[("Work", "unknown.contact@example.com")],
        categories=["myContacts", "Ruby Code Club"],
        grouped=True,
    )


def katherine():
    return Contact(
        fn="Katherine Johnson",
        n=("Johnson", "Katherine", "", "", ""),
        uid="urn:uuid:cafe0000-0000-4000-8000-000000000020",
        emails=[("Work", "katherine.johnson@example.org")],
        tels=[("WORK", "+1-555-0120")],
        org="NASA",
        photo_png=make_png(rgb=(0x2E, 0x7D, 0x32)),
    )


def edsger():
    return Contact(
        fn="Edsger Dijkstra",
        n=("Dijkstra", "Edsger", "Wybe", "", ""),
        uid="urn:uuid:cafe0000-0000-4000-8000-000000000021",
        emails=[("Work", "edsger.dijkstra@example.com")],
        tels=[("HOME", "+1-555-0121")],
    )


def shannon():
    return Contact(
        fn="Claude Shannon",
        n=("Shannon", "Claude", "Elwood", "", ""),
        uid="urn:uuid:1eaf0000-0000-4000-8000-000000000030",
        emails=[("Work", "claude.shannon@example.com")],
        tels=[("WORK", "+1-555-0130")],
        org="Bell Labs",
    )


def vonneumann():
    return Contact(
        fn="John von Neumann",
        n=("von Neumann", "John", "", "", ""),
        uid="urn:uuid:1eaf0000-0000-4000-8000-000000000031",
        emails=[("Work", "john.vonneumann@example.org")],
        tels=[("HOME", "+1-555-0131")],
    )


# Dedup fixture: one canonical Ada + two near-duplicates with shared signals, no UID.
def ada_dup_caps():
    return Contact(
        fn="Ada Lovelace",
        n=("Lovelace", "Ada", "", "", ""),
        uid=None,
        emails=[("Work", "ADA.LOVELACE@EXAMPLE.COM")],   # case-different vs canonical
        tels=[("CELL", "+1-555-0101"), ("WORK", "+1-555-9999")],  # shared phone + extra
        org="Analytical Engines",
        grouped=True,
    )


def ada_dup_variant():
    return Contact(
        fn="Ada B. Lovelace",                            # name variant
        n=("Lovelace", "Ada", "Byron", "", ""),
        uid=None,
        emails=[("Personal", "ada@analyticalengines.example")],
        tels=[("CELL", "+1-555-0101")],                  # shared phone -> dedup signal
        grouped=True,
    )


# --------------------------------------------------------------------------------------------
# Fixture definitions: a fixture is a set of relative file paths -> file content.
# .vcf paths map to a list of Contacts; image paths map to PNG bytes; html is the archive stub.
# --------------------------------------------------------------------------------------------
def build_fixture_specs():
    specs = {}

    # 1. Modern layout, clean records, UIDs present, no photos.
    specs["takeout-basic"] = {
        "description": "Modern foldered layout; 3 clean vCard 3.0 records with UIDs; no photos.",
        "vcards": {
            "Takeout/Contacts/All Contacts/All Contacts.vcf": [ada(), grace(), alan()],
        },
        "images": {},
    }

    # 2. Google quirks: itemN grouping, folded note, unicode, apostrophe, NO UID, multi-folder overlap.
    specs["takeout-google-quirks"] = {
        "description": (
            "Google's itemN.X-ABLabel grouping, folded NOTE, unicode (José Ñoño / 田中太郎), "
            "apostrophe names, no UID (forces stable-id fallback), a name-less email-only card with "
            "CATEGORIES group membership, and the same person across "
            "All Contacts / My Contacts / a label folder."
        ),
        "vcards": {
            "Takeout/Contacts/All Contacts/All Contacts.vcf": [jose(), tanaka(), mary(), noname()],
            "Takeout/Contacts/My Contacts/My Contacts.vcf": [mary()],
            "Takeout/Contacts/Starred in Android/Starred in Android.vcf": [jose()],
        },
        "images": {},
    }

    # 3. Embedded base64 photo (the modern Google norm), mixed with a photo-less record.
    specs["takeout-photos"] = {
        "description": "Embedded PHOTO;ENCODING=b base64 image (modern norm), mixed with a no-photo record.",
        "vcards": {
            "Takeout/Contacts/All Contacts/All Contacts.vcf": [katherine(), edsger()],
        },
        "images": {},
    }

    # 4. External image files named after the contact (legacy heuristic; see README).
    specs["takeout-photos-external"] = {
        "description": (
            "Photo referenced as an external image file named after the contact display name "
            "(legacy/heuristic matching; embedded base64 is the modern norm)."
        ),
        "vcards": {
            "Takeout/Contacts/All Contacts/All Contacts.vcf": [ada(photo=False)],
        },
        "images": {
            "Takeout/Contacts/All Contacts/Ada Lovelace.png": make_png(rgb=(0x8E, 0x24, 0xAA)),
        },
    }

    # 5. Duplicates across labels for dedup testing (shared email/phone, case + name variants).
    specs["takeout-duplicates"] = {
        "description": (
            "Near-duplicate records across labels: canonical Ada (with UID) plus a caps-email "
            "variant and a middle-name variant, all sharing phone/email signals; no UID on dupes."
        ),
        "vcards": {
            "Takeout/Contacts/All Contacts/All Contacts.vcf": [ada()],
            "Takeout/Contacts/Work/Work.vcf": [ada_dup_caps(), ada_dup_variant()],
        },
        "images": {},
    }

    # 6. Legacy flat layout: Contacts/All Contacts.vcf directly, no per-label subfolder.
    specs["takeout-legacy-flat"] = {
        "description": "Older flat layout: Contacts/All Contacts.vcf with no per-label subfolder.",
        "vcards": {
            "Takeout/Contacts/All Contacts.vcf": [shannon(), vonneumann()],
        },
        "images": {},
    }

    return specs


# --------------------------------------------------------------------------------------------
# Expected-results manifest (verifiable with stdlib only by the test, independent of the parser)
# --------------------------------------------------------------------------------------------
def expected_for(spec) -> dict:
    vcf_files = sorted(spec["vcards"].keys())
    contact_count = 0
    with_embedded_photo = 0
    with_uid = 0
    without_name = 0
    with_categories = 0
    fns = []
    for path in vcf_files:
        for c in spec["vcards"][path]:
            contact_count += 1
            if c.fn:
                fns.append(c.fn)
            else:
                without_name += 1
            if c.uid:
                with_uid += 1
            if c.photo_png is not None:
                with_embedded_photo += 1
            if c.categories:
                with_categories += 1
    return {
        "description": spec["description"],
        "vcf_files": vcf_files,
        "external_images": sorted(spec["images"].keys()),
        "contact_count": contact_count,
        "with_uid": with_uid,
        "without_uid": contact_count - with_uid,
        "without_name": without_name,
        "with_categories": with_categories,
        "with_embedded_photo": with_embedded_photo,
        "fns": sorted(fns),
    }


# --------------------------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------------------------
def generate(specs) -> None:
    if SOURCES.exists():
        shutil.rmtree(SOURCES)
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    SOURCES.mkdir(parents=True)
    EXPECTED.mkdir(parents=True)

    for name, spec in sorted(specs.items()):
        root = SOURCES / name
        # archive_browser.html lives at the Takeout root, like a real export.
        (root / "Takeout").mkdir(parents=True)
        (root / "Takeout" / "archive_browser.html").write_text(ARCHIVE_BROWSER_HTML, encoding="utf-8")

        for rel, contacts in spec["vcards"].items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # vCard uses CRLF; write bytes to avoid the platform newline translation.
            dest.write_bytes(vcf(contacts).encode("utf-8"))

        for rel, data in spec["images"].items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        (EXPECTED / f"{name}.json").write_text(
            json.dumps(expected_for(spec), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate into a temp area and fail if it differs from the committed output.",
    )
    args = parser.parse_args(argv)

    specs = build_fixture_specs()

    if args.check:
        import filecmp
        import tempfile

        # Snapshot current, regenerate, compare, restore.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for sub in ("sources", "expected"):
                src = HERE / sub
                if src.exists():
                    shutil.copytree(src, tmp_path / sub)
            generate(specs)
            mismatch = []
            for sub in ("sources", "expected"):
                old, new = tmp_path / sub, HERE / sub
                cmp = filecmp.dircmp(old, new)
                if _dircmp_differs(cmp):
                    mismatch.append(sub)
            if mismatch:
                print(f"ERROR: committed {', '.join(mismatch)} differ from generated output. "
                      f"Run: python {Path(__file__).name}", file=sys.stderr)
                return 1
        print("OK: committed fixtures match generator output.")
        return 0

    generate(specs)
    n = len(specs)
    print(f"Generated {n} Google Takeout fixtures into {SOURCES.relative_to(HERE.parent.parent.parent)}/")
    return 0


def _dircmp_differs(cmp) -> bool:
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return True
    return any(_dircmp_differs(sub) for sub in cmp.subdirs.values())


if __name__ == "__main__":
    raise SystemExit(main())
