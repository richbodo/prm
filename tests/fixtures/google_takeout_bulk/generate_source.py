#!/usr/bin/env python3
"""Generate a large, realistic Google Takeout *All Contacts.vcf* for **scale/DB testing**.

This is the bulk counterpart to the small, hand-curated quirk fixtures in
[`../google_takeout/`]. Those exist to unit-test the parser against specific edge cases (~2-5 cards
each). **This** fixture exists to seed ~1000 contacts into the database so forward-looking features
(search at scale, dedup-candidate blocking, the long-running gatherer, perf) have a realistic corpus
to run against. It is one file: ``sources/Takeout/Contacts/All Contacts/All Contacts.vcf``.

The field distributions are **derived from a real 1000-contact Google Takeout export** (validated
2026-06-04; see ``docs/reference/source_validation_status.md``). That real export revealed quirks
the small fixtures don't model, which this corpus reproduces at scale:
  * **vCard 3.0, CRLF, and ZERO ``UID``s** across all 1000 cards (missing-UID is universal in
    Takeout, not occasional) — every contact exercises the email/content-hash fallback (INV-5).
  * **~33% of cards have no ``N``/``FN`` at all** (name-less contacts) — identity must come from
    email, else a content hash; the projection must tolerate a missing display name.
  * **``CATEGORIES``** carries Google label/group membership (~18% of cards).
  * Apple-origin **``itemN.EMAIL`` + ``itemN.X-ABLabel``** grouping dominates email lines, with a
    minority of bare ``EMAIL``.
  * Sparse ``TEL`` (~12%), ``URL`` (~5%), ``NOTE``, ``ADR``, very sparse ``ORG``, a few ``BDAY``
    and ``X-PHONETIC-*`` — matching the real long-tail.
  * A fraction of cards carry a small embedded base64 ``PHOTO``.

Everything is SYNTHETIC and deterministic (a fixed per-index hash, no RNG/timestamps), so the
committed output regenerates byte-identically. Names are historical computing figures; emails use
``example.com``/``example.org`` (RFC 2606); phones use the 555-01xx block. No real export is
committed — real ones live in the repo-root ``ignore-data/`` (gitignored).

Deliberately NOT modeled here (covered by the small fixtures, kept out to stay lean): 75-octet line
folding, external-photo files, the zip container, and deliberate cross-label duplicates. This is a
flat, unpacked ``All Contacts.vcf`` — the superset you actually ingest.

Usage:
    python generate_source.py                 # regenerate at the default count (1000)
    python generate_source.py --count 5000    # bigger DB seed (does not change committed output)
    python generate_source.py --check         # fail if regeneration drifts from committed output
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"
VCF_REL = Path("Takeout/Contacts/All Contacts/All Contacts.vcf")

DEFAULT_COUNT = 1000

# Synthetic pools (historical computing figures). Names may repeat across the corpus (realistic at
# 1000 contacts, and a natural source of fuzzy-duplicate candidates); emails are index-unique.
FIRST = [
    "Ada", "Grace", "Alan", "Katherine", "Edsger", "Barbara", "Donald", "Margaret", "Tim", "Radia",
    "Vint", "Frances", "John", "Adele", "Ken", "Dennis", "Shafi", "Leslie", "Hedy", "André",
    "Niklaus", "Linus", "Guido", "Bjarne", "Brian", "Anita", "Annie", "Hopper", "Karen", "Sophie",
]
LAST = [
    "Lovelace", "Hopper", "Turing", "Johnson", "Dijkstra", "Liskov", "Knuth", "Hamilton",
    "Berners-Lee", "Perlman", "Cerf", "Allen", "McCarthy", "Goldberg", "Thompson", "Ritchie",
    "Goldwasser", "Lamport", "Lamarr", "Truong", "Wirth", "Torvalds", "Wilkes", "Easley",
    "Spärck Jones", "Borg", "Hejlsberg", "Kernighan", "Engelbart", "Wing",
]
CATEGORIES_POOL = [
    "myContacts", "Family", "Ruby Code Club", "business_contacts", "local", "going out peninsula",
    "garage_sale",
]
EMAIL_LABELS = ["_$!<Home>!$_", "_$!<Work>!$_", "INTERNET", "_$!<Other>!$_"]
PHONE_LABELS = ["_$!<Mobile>!$_", "_$!<Home>!$_", "_$!<Work>!$_"]

# 1x1 transparent PNG, base64 — a tiny embedded PHOTO to exercise the path at scale without bloat.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _dice(i: int, salt: int) -> int:
    """Deterministic 0-99 'roll' for index i and an independent stream `salt`."""
    h = (i * 2654435761 + salt * 40503 + 0x9E3779B1) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return h % 100


def _pick(seq, i, salt):
    return seq[_dice(i, salt) % len(seq)]


def _card(i: int) -> str:
    """Build one vCard 3.0 card (CRLF-joined). No UID, ever."""
    named = _dice(i, 1) < 67
    has_email = _dice(i, 2) < 85
    has_phone = _dice(i, 3) < 12
    has_categories = _dice(i, 4) < 18
    has_url = _dice(i, 5) < 5
    has_note = _dice(i, 6) < 3
    has_org = _dice(i, 7) < 2
    has_photo = _dice(i, 8) < 6
    has_bday = _dice(i, 9) < 1
    has_phonetic = _dice(i, 10) < 1

    first = _pick(FIRST, i, 11)
    last = _pick(LAST, i, 12)

    # Guarantee every card has at least one identifier (name, email, or phone) — a card with none is
    # not a real contact. If unnamed and the dice gave neither email nor phone, force an email.
    if not named and not has_email and not has_phone:
        has_email = True

    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    if named:
        lines.append(f"N:{last};{first};;;")
        lines.append(f"FN:{first} {last}")
        if has_phonetic:
            lines.append(f"X-PHONETIC-FIRST-NAME:{first}")
            lines.append(f"X-PHONETIC-LAST-NAME:{last}")
    if has_org:
        lines.append(f"ORG:{last} Labs;")
        lines.append("TITLE:Researcher")
    if has_email:
        label = _pick(EMAIL_LABELS, i, 13)
        addr = f"{first.lower()}.{_ascii_slug(last)}{i}@example.com"
        if _dice(i, 14) < 80:  # dominant: itemN-grouped email with an X-ABLabel
            lines.append(f"item1.EMAIL;type=INTERNET:{addr}")
            lines.append(f"item1.X-ABLabel:{label}")
        else:  # minority: bare EMAIL
            lines.append(f"EMAIL;type=INTERNET;type=pref:{addr}")
    if has_phone:
        num = f"+1-555-{1000 + (i % 9000):04d}"
        lines.append(f"item2.TEL:{num}")
        lines.append(f"item2.X-ABLabel:{_pick(PHONE_LABELS, i, 15)}")
    if has_url:
        lines.append(f"item3.URL:https://example.org/{_ascii_slug(first)}{i}")
        lines.append("item3.X-ABLabel:_$!<HomePage>!$_")
    if has_bday:
        lines.append("BDAY:1970-01-01")
    if has_note:
        lines.append("NOTE:Imported contact.")
    if has_categories:
        cats = sorted({_pick(CATEGORIES_POOL, i, 16), _pick(CATEGORIES_POOL, i, 17)})
        lines.append("CATEGORIES:" + ",".join(cats))
    if has_photo:
        lines.append("PHOTO;ENCODING=b;TYPE=PNG:" + TINY_PNG_B64)
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def _ascii_slug(s: str) -> str:
    """Lowercase ASCII-only slug for emails/urls (drop diacritics & spaces deterministically)."""
    table = {"é": "e", "ä": "a", "ö": "o", "ü": "u", "ß": "ss", " ": "", "-": ""}
    out = []
    for ch in s.lower():
        out.append(table.get(ch, ch if ch.isalnum() else ""))
    return "".join(out)


def render_vcf(count: int) -> bytes:
    buf = io.StringIO()
    for i in range(count):
        buf.write(_card(i))
        buf.write("\r\n")
    return buf.getvalue().encode("utf-8")


def stats(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    lines = text.split("\r\n")
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    n_cards = sum(1 for ln in lines if ln == "BEGIN:VCARD")

    def count_prop(prefix):
        return sum(1 for ln in lines if ln.startswith(prefix))

    return {
        "vcf_file": str(VCF_REL).replace("\\", "/"),
        "version": "3.0",
        "line_ending": "CRLF" if crlf == lf and crlf > 0 else ("LF" if crlf == 0 else "MIXED"),
        "total_cards": n_cards,
        "uid_count": count_prop("UID"),
        "named_cards": count_prop("FN:"),
        "nameless_cards": n_cards - count_prop("FN:"),
        "cards_with_email": sum(
            1 for ln in lines if ln.startswith("item1.EMAIL") or ln.startswith("EMAIL")
        ),
        "grouped_email_lines": count_prop("item1.EMAIL"),
        "bare_email_lines": count_prop("EMAIL;"),
        "x_ablabel_lines": sum(1 for ln in lines if ".X-ABLabel:" in ln),
        "tel_lines": count_prop("item2.TEL"),
        "url_lines": count_prop("item3.URL"),
        "categories_cards": count_prop("CATEGORIES:"),
        "note_cards": count_prop("NOTE:"),
        "org_cards": count_prop("ORG:"),
        "photo_cards": count_prop("PHOTO"),
        "bday_cards": count_prop("BDAY:"),
        "phonetic_cards": count_prop("X-PHONETIC-FIRST-NAME:"),
    }


def generate(count: int = DEFAULT_COUNT) -> dict:
    raw = render_vcf(count)
    if SOURCES.exists():
        shutil.rmtree(SOURCES)
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    vcf_path = SOURCES / VCF_REL
    vcf_path.parent.mkdir(parents=True)
    vcf_path.write_bytes(raw)
    # Minimal Takeout report stub, as the real export ships one.
    (SOURCES / "Takeout" / "archive_browser.html").write_text(
        "<!doctype html><title>Takeout</title>\n", encoding="utf-8"
    )
    EXPECTED.mkdir(parents=True)
    manifest = {
        "description": (
            "Synthetic large Google Takeout 'All Contacts.vcf' for scale/DB testing. Distributions "
            "derived from a real 1000-contact export (validated 2026-06-04). vCard 3.0, CRLF, zero "
            "UIDs; ~33% name-less cards; CATEGORIES, itemN.X-ABLabel email grouping, and a sparse "
            "long-tail of TEL/URL/ORG/BDAY/PHOTO/X-PHONETIC."
        ),
        "default_count": count,
        **stats(raw),
    }
    (EXPECTED / "bulk.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def _dircmp_differs(cmp) -> bool:
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return True
    return any(_dircmp_differs(sub) for sub in cmp.subdirs.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"number of contacts to emit (default {DEFAULT_COUNT})")
    parser.add_argument("--check", action="store_true",
                        help="Regenerate at the default count into a temp area; fail if it differs from committed output.")
    args = parser.parse_args(argv)

    if args.check:
        import filecmp
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for sub in ("sources", "expected"):
                src = HERE / sub
                if src.exists():
                    shutil.copytree(src, tmp_path / sub)
            generate(DEFAULT_COUNT)
            mismatch = [sub for sub in ("sources", "expected")
                        if _dircmp_differs(filecmp.dircmp(tmp_path / sub, HERE / sub))]
            if mismatch:
                print(f"ERROR: committed {', '.join(mismatch)} differ from generated output. "
                      f"Run: python {Path(__file__).name}", file=sys.stderr)
                return 1
        print("OK: committed fixture matches generator output.")
        return 0

    m = generate(args.count)
    print(f"Generated bulk Takeout fixture: {m['total_cards']} cards "
          f"({m['nameless_cards']} nameless, {m['cards_with_email']} with email, "
          f"{m['categories_cards']} with CATEGORIES, {m['uid_count']} UIDs).")
    if args.count != DEFAULT_COUNT:
        print(f"NOTE: emitted {args.count} (committed fixture is {DEFAULT_COUNT}); --check compares the default.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
