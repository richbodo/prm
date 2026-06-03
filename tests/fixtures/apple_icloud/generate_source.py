#!/usr/bin/env python3
"""Generate the synthetic Apple iCloud / Contacts.app vCard fixtures.

Single source of truth for the fixtures under ``sources/``. Pure-stdlib and deterministic
(no timestamps, no randomness), so committed output can be regenerated and diffed in review.

Unlike Google Takeout (a zip of vCards), Apple's "Export vCard…" produces a **single ``.vcf``
file** with all cards concatenated — so there is no build/zip step here; the ``.vcf`` *is* the
fixture.

All data is SYNTHETIC. Names are historical computing figures; emails use the reserved
``example.com``/``example.org`` domains (RFC 2606) and phone numbers use the 555-01xx fictional
block. No real export is committed — real ones go in ``tests/fixtures/incoming/`` (gitignored).

Apple quirks this corpus reproduces, observed in a real iCloud export (values redacted):
  * vCard **3.0**, like Google — but **LF line endings** (the sampled export had zero CRLF),
    so the ingester must tolerate bare LF as well as the RFC-required CRLF.
  * **Per-card ``PRODID``** (e.g. ``-//Apple Inc.//macOS 13.3.1//EN``), and crucially it can
    **differ between cards** in one file when contacts came from different devices/OS versions.
  * **Per-card ``REV``** UTC timestamp.
  * **No ``UID`` at all** — like Google, so the stable-id fallback (INV-5) is mandatory; this is
    also what makes cross-source (Google⨯Apple) dedup hinge on normalized email/phone.
  * ``itemN.PROP`` grouping with **``X-ABLabel`` Apple pseudo-labels** like ``_$!<Other>!$_``
    and custom free-text labels (``profile``).
  * **Mixed-case ``TYPE`` params** and the lowercase ``pref`` preferred-marker (``TYPE=pref``),
    plus ``VOICE`` on phones — so TYPE matching must be case-insensitive.
  * Profile ``URL``s grouped under ``X-ABLabel:profile``.
  * ``ADR`` tagged with ``X-ABADR`` (Apple's address country-format label).
  * An Apple-flavored "Ada Lovelace" whose email/phone match the Google ``takeout-duplicates``
    canonical — a deliberate **cross-source dedup** hook (same person, no UID in either source).

Usage:
    python generate_source.py          # regenerate sources/ and expected/
    python generate_source.py --check  # fail if regeneration would change committed output
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"

LF = "\n"  # Apple export in hand used bare LF, not CRLF — encode that on purpose.

# A couple of real Apple PRODID strings (product identifiers, not PII). Varied across cards to
# reproduce a multi-device export.
PRODID_MAC = "-//Apple Inc.//macOS 13.3.1//EN"
PRODID_IOS = "-//Apple Inc.//iOS 14.3//EN"

# Apple pseudo-label encoding for standard labels.
LBL_WORK = "_$!<Work>!$_"
LBL_HOME = "_$!<Home>!$_"
LBL_OTHER = "_$!<Other>!$_"
LBL_MAIN = "_$!<Main>!$_"


def fold(line: str) -> str:
    """Fold at 75 octets (continuations begin with a space). ASCII-only callers (base64/long text)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    pieces = [raw[:75]]
    rest = raw[75:]
    while rest:
        pieces.append(b" " + rest[:74])
        rest = rest[74:]
    return LF.join(p.decode("ascii") for p in pieces)


class AppleContact:
    def __init__(
        self,
        fn,
        n,                 # (family, given, additional, prefix, suffix)
        prodid,
        rev,               # fixed ISO-8601 Z string (deterministic)
        emails=None,       # list of (label, types, address); types is a list incl maybe "pref"
        tels=None,         # list of (label, types, number)
        urls=None,         # list of (label, url)
        adr=None,          # (label, country_format_label, fields_tuple) or None
        org=None,
        note=None,
    ):
        self.fn = fn
        self.n = n
        self.prodid = prodid
        self.rev = rev
        self.emails = emails or []
        self.tels = tels or []
        self.urls = urls or []
        self.adr = adr
        self.org = org
        self.note = note

    @staticmethod
    def _types(params):
        return "".join(";TYPE=" + t for t in params)

    def to_vcard(self) -> str:
        lines = ["BEGIN:VCARD", "VERSION:3.0", "PRODID:" + self.prodid]
        lines.append("N:" + ";".join(self.n))
        lines.append("FN:" + self.fn)
        if self.org:
            lines.append("ORG:" + self.org)

        item = 0
        for label, types, address in self.emails:
            item += 1
            lines.append(f"item{item}.EMAIL{self._types(types)}:" + address)
            lines.append(f"item{item}.X-ABLabel:" + label)
        for label, types, number in self.tels:
            item += 1
            lines.append(f"item{item}.TEL{self._types(types)}:" + number)
            lines.append(f"item{item}.X-ABLabel:" + label)
        for label, url in self.urls:
            item += 1
            lines.append(f"item{item}.URL:" + url)
            lines.append(f"item{item}.X-ABLabel:" + label)
        if self.adr:
            label, country_label, fields = self.adr
            item += 1
            lines.append(f"item{item}.ADR;TYPE=HOME:" + ";".join(fields))
            lines.append(f"item{item}.X-ABADR:" + country_label)
            lines.append(f"item{item}.X-ABLabel:" + label)
        if self.note:
            lines.append(fold("NOTE:" + self.note))
        lines.append("REV:" + self.rev)
        lines.append("END:VCARD")
        return LF.join(lines) + LF


REV1 = "2021-03-22T06:43:28Z"
REV2 = "2022-11-09T18:02:11Z"


def contacts():
    return [
        # Cross-source dedup hook: matches the Google takeout-duplicates canonical Ada
        # (ada.lovelace@example.com / +1-555-0101), Apple-flavored, no UID.
        AppleContact(
            fn="Ada Lovelace",
            n=("Lovelace", "Ada", "", "", ""),
            prodid=PRODID_MAC,
            rev=REV1,
            emails=[(LBL_WORK, ["INTERNET", "pref"], "ada.lovelace@example.com")],
            tels=[(LBL_HOME, ["CELL", "VOICE"], "+1-555-0101")],
            urls=[("profile", "https://example.com/~ada")],
            org="Analytical Engines",
        ),
        # Mixed-device export (iOS PRODID), pseudo-label _$!<Other>!$_, multiple emails, no phone.
        AppleContact(
            fn="Grace Hopper",
            n=("Hopper", "Grace", "Brewster", "RADM", ""),
            prodid=PRODID_IOS,
            rev=REV2,
            emails=[
                (LBL_WORK, ["INTERNET", "pref"], "grace.hopper@example.org"),
                (LBL_OTHER, ["INTERNET"], "ghopper@example.com"),
            ],
        ),
        # Phone-only contact (common in the real export — many cards had no email),
        # lowercase pref + VOICE, custom free-text label.
        AppleContact(
            fn="Katherine Johnson",
            n=("Johnson", "Katherine", "", "", ""),
            prodid=PRODID_MAC,
            rev=REV1,
            tels=[
                (LBL_MAIN, ["CELL", "VOICE", "pref"], "+1-555-0120"),
                ("on-site", ["WORK", "VOICE"], "+1-555-0121"),
            ],
        ),
        # Address with X-ABADR, ORG/NOTE, unicode in name.
        AppleContact(
            fn="José Ñoño",
            n=("Ñoño", "José", "", "", ""),
            prodid=PRODID_IOS,
            rev=REV2,
            emails=[(LBL_HOME, ["INTERNET", "pref"], "jose.nono@example.com")],
            adr=(LBL_HOME, "us", ("", "", "1 Engine Way", "Lovelace City", "CA", "94000", "USA")),
            note="Imported from an old iPhone; profile URL is the canonical one.",
        ),
    ]


def expected(cards) -> dict:
    fns = sorted(c.fn for c in cards)
    prodids = sorted({c.prodid for c in cards})
    return {
        "description": (
            "Synthetic Apple iCloud single-file vCard export (LF line endings, vCard 3.0, "
            "per-card PRODID/REV, no UID, itemN+X-ABLabel pseudo-labels, TYPE=pref/VOICE). "
            "'Ada Lovelace' matches the Google takeout-duplicates canonical for cross-source dedup."
        ),
        "vcf_file": "icloud-export.vcf",
        "line_ending": "LF",
        "contact_count": len(cards),
        "with_uid": 0,
        "distinct_prodids": prodids,
        "with_xablabel": sum(1 for c in cards if (c.emails or c.tels or c.urls or c.adr)),
        "fns": fns,
    }


def generate(cards) -> None:
    if SOURCES.exists():
        shutil.rmtree(SOURCES)
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    SOURCES.mkdir(parents=True)
    EXPECTED.mkdir(parents=True)

    vcf = "".join(c.to_vcard() for c in cards)
    # Write bytes to guarantee LF survives (no platform newline translation).
    (SOURCES / "icloud-export.vcf").write_bytes(vcf.encode("utf-8"))
    (EXPECTED / "icloud-export.json").write_text(
        json.dumps(expected(cards), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _dircmp_differs(cmp) -> bool:
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return True
    return any(_dircmp_differs(sub) for sub in cmp.subdirs.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Regenerate into a temp area and fail if it differs from committed output.")
    args = parser.parse_args(argv)
    cards = contacts()

    if args.check:
        import filecmp
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for sub in ("sources", "expected"):
                src = HERE / sub
                if src.exists():
                    shutil.copytree(src, tmp_path / sub)
            generate(cards)
            mismatch = [sub for sub in ("sources", "expected")
                        if _dircmp_differs(filecmp.dircmp(tmp_path / sub, HERE / sub))]
            if mismatch:
                print(f"ERROR: committed {', '.join(mismatch)} differ from generated output. "
                      f"Run: python {Path(__file__).name}", file=sys.stderr)
                return 1
        print("OK: committed fixtures match generator output.")
        return 0

    generate(cards)
    print(f"Generated Apple iCloud fixture ({len(cards)} contacts) into {SOURCES.name}/icloud-export.vcf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
