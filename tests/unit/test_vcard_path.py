#!/usr/bin/env python3
"""Unit tests for the vCard ingestor path: parse -> normalize -> jCard + provenance + stable id.

Runs against the committed, real-validated fixtures (Apple iCloud, Google Takeout quirks + bulk).
Stdlib + the package under test; needs the vCard library (vobjectx or vobject). Runs either way:

    python tests/unit/test_vcard_path.py     # plain script
    pytest tests/unit/test_vcard_path.py      # discovered as test_* functions
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import normalize  # noqa: E402
from cli.jcard import JCard  # noqa: E402
from cli.parsers import detect_format, takeout  # noqa: E402
from cli.parsers import vcard as vcard_parser  # noqa: E402
from cli.parsers.base import SourceFormat  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"
QUIRKS = (FIX / "google_takeout" / "sources" / "takeout-google-quirks"
          / "Takeout" / "Contacts" / "All Contacts" / "All Contacts.vcf")
BASIC_TREE = FIX / "google_takeout" / "sources" / "takeout-basic"
BULK_VCF = (FIX / "google_takeout_bulk" / "sources"
            / "Takeout" / "Contacts" / "All Contacts" / "All Contacts.vcf")


def _load(path: Path, source: str):
    return normalize.normalize_all(vcard_parser.parse(path.read_bytes(), source))


def _by_email(contacts, addr):
    return next(c for c in contacts if c.jcard.first_value("email") == addr)


# --------------------------------------------------------------------------- Apple iCloud
def test_apple_stable_id_falls_back_to_email():
    """Apple exports carry no UID, so identity must fall back to the normalized email (INV-5)."""
    contacts = _load(APPLE, "apple_icloud")
    ada = _by_email(contacts, "ada.lovelace@example.com")
    assert ada.source_uid is None
    assert ada.stable_key.kind == "email"
    assert ada.stable_key.value == "ada.lovelace@example.com"


def test_apple_maps_structured_name_and_provenance():
    contacts = _load(APPLE, "apple_icloud")
    ada = _by_email(contacts, "ada.lovelace@example.com")
    n = ada.jcard.get("n")
    assert n is not None and n.value[:2] == ["Lovelace", "Ada"]   # [family, given, ...]
    assert ada.display_name == "Ada Lovelace"
    # Every provenance row is tagged with the source; REV becomes observed_at.
    assert ada.provenance and all(p.source == "apple_icloud" for p in ada.provenance)
    assert any(p.observed_at for p in ada.provenance), "REV should populate observed_at"


def test_apple_grouped_email_keeps_group_param():
    contacts = _load(APPLE, "apple_icloud")
    ada = _by_email(contacts, "ada.lovelace@example.com")
    email = ada.jcard.get("email")
    assert email.group is not None and email.group.startswith("item")


# --------------------------------------------------------------------------- name-less card
def test_nameless_card_identifies_by_email_and_keeps_categories():
    """The ~33% real-world case: no N/FN. Identity = email; CATEGORIES parsed as multi-value."""
    contacts = _load(QUIRKS, "google_takeout")
    noname = _by_email(contacts, "unknown.contact@example.com")
    assert noname.display_name is None              # no FN
    assert noname.jcard.get("n") is None            # no N
    assert noname.stable_key.kind == "email"
    cats = noname.jcard.get("categories")
    assert cats is not None and cats.values == ["myContacts", "Ruby Code Club"]


def test_quirks_have_no_uids():
    contacts = _load(QUIRKS, "google_takeout")
    assert all(c.source_uid is None for c in contacts)
    assert all(c.stable_key.kind in ("email", "hash", "url") for c in contacts)


# --------------------------------------------------------------------------- Takeout zip
def test_takeout_zip_selects_all_contacts_and_parses():
    """detect_format + takeout.parse pull the All Contacts superset out of a real-shaped zip."""
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "takeout.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in BASIC_TREE.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(BASIC_TREE).as_posix())
        assert detect_format(zip_path) is SourceFormat.GOOGLE_TAKEOUT
        records = takeout.parse(zip_path)
    assert len(records) == 3 and all(r.source == "google_takeout" for r in records)


# --------------------------------------------------------------------------- scale (bulk)
def test_bulk_scale_parses_a_thousand_with_stable_ids():
    contacts = normalize.normalize_all(takeout.parse_vcf_path(BULK_VCF))
    assert len(contacts) == 1000
    assert all(c.source_uid is None for c in contacts)                 # 0 UIDs (validated real fact)
    assert all(c.stable_key for c in contacts)
    nameless = sum(1 for c in contacts if c.display_name is None)
    assert 200 <= nameless <= 450                                      # ~33% name-less
    by_email = sum(1 for c in contacts if c.stable_key.kind == "email")
    assert by_email >= 800                                             # most identify by email


# --------------------------------------------------------------------------- jCard round-trip
def test_jcard_json_roundtrips():
    contacts = _load(APPLE, "apple_icloud")
    jc = contacts[0].jcard
    again = JCard.from_json(jc.to_json())
    assert again.to_jcard() == jc.to_jcard()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((t.__name__, exc))
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
