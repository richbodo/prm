#!/usr/bin/env python3
"""End-to-end export round-trip: import -> ``prm export`` (vCard 3.0) -> re-parse is equivalent.

Two guarantees:

  1. **Round-trip equivalence** — importing a real-shaped corpus, exporting it, and re-parsing the
     emitted ``.vcf`` through the same ingest pipeline yields the same set of canonical contacts
     (names + the full union of emails). Exercises the emitter against the parser end to end.
  2. **Merge is reflected in the export** — once two source records are merged into one canonical
     contact, the export emits **one** card that **unions** the members' distinct values (here, two
     phone numbers), not two cards.

And, for the lossless **raw backup** (``--raw``):

  3. **Raw backup round-trips bit-for-bit** — dumping every raw source record and re-importing the
     dump into a *fresh* home reconstructs the Shared DB exactly (same ``source_record_id``, source,
     stable key, jCard, and provenance), through the real ingest pipeline (format detection included).
  4. **Raw preserves what merged collapses** — with a merge applied, the merged vCard export emits one
     card while the raw backup still dumps both underlying records (it backs up the *raw* store).

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
from core import apply, candidates, projection, shared_db  # noqa: E402

APPLE = REPO / "tests" / "fixtures" / "apple_icloud" / "sources" / "icloud-export.vcf"


def _write_vcf(path: Path, source: str, body: str, *, home) -> None:
    path.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\n{body}\r\nEND:VCARD\r\n", encoding="utf-8")
    ingest_mod.ingest([path], source=source, home=home, dry_run=False)


def _shared_signature(home) -> set:
    """The Shared DB reduced to its lossless content: per record, the id, source, stable key, jCard,
    and provenance (field/value/observed_at). ``ingested_at`` is intentionally excluded — a restore is
    a re-ingestion, so it reflects restore time, while everything identity-bearing must be exact."""
    return {
        (r["id"], r["source"], r["stable_key"], r["raw_jcard"],
         tuple((p["field"], p["value"], p["observed_at"]) for p in r["provenance"]))
        for r in shared_db.all_records(home.shared_db)
    }


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


def test_raw_backup_roundtrip_is_lossless():
    """Dump every raw record -> re-import the backup into a fresh home -> the Shared DB is identical."""
    with tempfile.TemporaryDirectory() as tmp:
        h1 = resolve_home(Path(tmp) / "h1")
        _write_vcf(Path(tmp) / "a.vcf", "apple_icloud",
                   "FN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nTEL:111", home=h1)
        _write_vcf(Path(tmp) / "b.vcf", "google_takeout",
                   "FN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nTEL:222", home=h1)
        _write_vcf(Path(tmp) / "c.vcf", "linkedin", "FN:Sam Lee", home=h1)   # thin, content-hash id

        # A private merge decision must NOT change the raw backup (it dumps the raw store, INV-2).
        cluster = candidates.find_duplicate_candidates(h1)[0]
        apply.apply_changeset(h1, apply.build_merge_changeset(h1, cluster["member_ids"], cluster["member_ids"][0]))

        from cli import backup
        backup_path = Path(tmp) / "backup.json"
        backup_path.write_text(backup.dump(shared_db.all_records(h1.shared_db)), encoding="utf-8")

        h2 = resolve_home(Path(tmp) / "h2")
        report = ingest_mod.ingest([backup_path], home=h2, dry_run=False)   # detection -> PRM_BACKUP -> restore
        assert report.persisted and report.total == 3
        assert _shared_signature(h2) == _shared_signature(h1)               # bit-for-bit identical


def test_raw_preserves_what_merged_collapses():
    """After a merge: the merged vCard export is one card; the raw backup still holds both records."""
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        _write_vcf(Path(tmp) / "a.vcf", "apple_icloud", "FN:Ada Lovelace\r\nEMAIL:ada@x.org", home=home)
        _write_vcf(Path(tmp) / "b.vcf", "google_takeout", "FN:Ada Lovelace\r\nEMAIL:ada@x.org", home=home)

        cluster = candidates.find_duplicate_candidates(home)[0]
        apply.apply_changeset(home, apply.build_merge_changeset(home, cluster["member_ids"], cluster["member_ids"][0]))

        assert len(projection.export_jcards(home)) == 1        # merged view: one card
        assert len(shared_db.all_records(home.shared_db)) == 2  # raw backup: both records preserved


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
