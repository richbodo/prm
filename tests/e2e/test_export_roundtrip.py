#!/usr/bin/env python3
"""End-to-end export round-trip: import -> ``prm export`` (vCard 3.0) -> re-parse is equivalent.

Two guarantees:

  1. **Round-trip equivalence** — importing a real-shaped corpus, exporting it, and re-parsing the
     emitted ``.vcf`` through the same ingest pipeline yields the same set of canonical contacts
     (names + the full union of emails). Exercises the emitter against the parser end to end.
  2. **Merge is reflected in the export** — once two source records are merged into one canonical
     contact, the export emits **one** card that **unions** the members' distinct values (here, two
     phone numbers), not two cards.

    python tests/e2e/test_export_roundtrip.py
    pytest tests/e2e/test_export_roundtrip.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from cli.normalize import normalize_all  # noqa: E402
from cli.parsers import vcard as vcard_parser  # noqa: E402
from cli.vcard_writer import write_vcards  # noqa: E402
from core import apply, candidates, projection  # noqa: E402

APPLE = REPO / "tests" / "fixtures" / "apple_icloud" / "sources" / "icloud-export.vcf"


def _emails_from_projection(contacts: list) -> set:
    return {v["value"] for c in contacts for f in c["fields"] if f["name"] == "email" for v in f["values"]}


def _emails_from_parsed(canon: list) -> set:
    return {str(p.value) for c in canon for p in c.jcard.get_all("email") if p.value}


def _phones_from_parsed_card(canon, name: str) -> set:
    rec = next(c for c in canon if c.display_name == name)
    return {str(p.value) for p in rec.jcard.get_all("tel") if p.value}


def test_export_roundtrip_equivalence():
    """import the Apple fixture -> export -> re-parse: same contact count, names, and email set."""
    assert APPLE.exists(), "run tests/fixtures/apple_icloud/generate_source.py first"
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        ingest_mod.ingest([APPLE], source="apple_icloud", home=home, dry_run=False)

        contacts = projection.all_contacts(home)
        text = write_vcards(projection.export_jcards(home))

        canon = normalize_all(vcard_parser.parse(text, "roundtrip"))
        assert len(canon) == len(contacts)                       # one card out per canonical contact
        proj_names = {c["fn"] for c in contacts if c["fn"]}
        assert {c.display_name for c in canon if c.display_name} == proj_names
        assert _emails_from_parsed(canon) == _emails_from_projection(contacts)


def test_export_unions_merged_members():
    """A merged contact exports as ONE card unioning both members' phone numbers."""
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        for i, (src, tel) in enumerate([("apple_icloud", "111"), ("google_takeout", "222")]):
            v = Path(tmp) / f"r{i}.vcf"
            v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\n"
                         f"EMAIL:ada@x.org\r\nTEL:{tel}\r\nEND:VCARD\r\n", encoding="utf-8")
            ingest_mod.ingest([v], source=src, home=home, dry_run=False)

        assert len(projection.all_contacts(home)) == 2           # two source records, not yet merged

        cluster = candidates.find_duplicate_candidates(home)[0]  # confident: same exact email
        assert cluster["tier"] == "confident"
        cs = apply.build_merge_changeset(home, cluster["member_ids"], cluster["member_ids"][0])
        apply.apply_changeset(home, cs)

        cards = projection.export_jcards(home)
        assert len(cards) == 1                                   # merged → a single exported card

        canon = normalize_all(vcard_parser.parse(write_vcards(cards), "roundtrip"))
        assert len(canon) == 1
        assert _phones_from_parsed_card(canon, "Ada Lovelace") == {"111", "222"}


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append(t.__name__)
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
