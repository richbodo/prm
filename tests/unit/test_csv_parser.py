#!/usr/bin/env python3
"""Tests for the vendor-CSV parser (LinkedIn + Google CSV) through to load + search.

Validated against the synthetic fixtures, whose shapes were verified against real exports
(see docs/reference/source_validation_status.md). Runs as a script or under pytest.
"""

from __future__ import annotations

import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import normalize  # noqa: E402
from cli.parsers import csv as csvp  # noqa: E402
from core import shared_db  # noqa: E402

FIX = REPO / "tests" / "fixtures"
LINKEDIN = FIX / "linkedin" / "sources" / "Connections.csv"
GOOGLE_CSV = FIX / "google_csv" / "sources" / "contacts.csv"


def _load(path, source):
    return normalize.normalize_all(csvp.parse(path.read_bytes(), source))


def _by_name(contacts, name):
    return next(c for c in contacts if c.display_name == name)


# --------------------------------------------------------------------------- LinkedIn
def test_linkedin_identity_tiers():
    """The three real LinkedIn tiers: opted-in email, profile URL, and the URL-less deactivated row."""
    contacts = _load(LINKEDIN, "linkedin")
    kinds = Counter(c.stable_key.kind for c in contacts)
    assert kinds["email"] >= 1 and kinds["url"] >= 1 and kinds["hash"] >= 1

    grace = _by_name(contacts, "Grace Hopper")          # the rare opted-in email
    assert grace.stable_key.kind == "email"
    ada = _by_name(contacts, "Ada Lovelace")            # no email -> profile URL is the key
    assert ada.stable_key.kind == "url" and "linkedin.com/in/adalovelace" in ada.stable_key.value


def test_linkedin_deactivated_row_uses_content_hash_with_date():
    contacts = _load(LINKEDIN, "linkedin")
    # The name-less + URL-less row: identified only by company + connected-on.
    dead = next(c for c in contacts
                if c.display_name is None and c.jcard.get("url") is None and c.jcard.get("email") is None)
    assert dead.stable_key.kind == "hash"
    assert dead.jcard.get("org").value == ["Defunct Robotics"]   # ORG is a structured (list) value
    assert dead.jcard.first_value("x-connected-on")        # the date is carried (and folded into the hash)


def test_linkedin_preamble_is_skipped():
    # Parsing succeeds despite the 3-line Notes preamble (header found by content).
    assert len(_load(LINKEDIN, "linkedin")) >= 20


# --------------------------------------------------------------------------- Google CSV
def test_google_csv_mapping():
    contacts = _load(GOOGLE_CSV, "google_csv")
    assert all(c.source_uid is None for c in contacts)              # Google CSV has no id column
    hedy = _by_name(contacts, "Hedy Lamarr")                       # no email -> content hash
    assert hedy.stable_key.kind == "hash"
    grace = _by_name(contacts, "Grace Hopper")                     # numbered multi-email
    assert len(grace.jcard.get_all("email")) == 2
    cats = grace.jcard.get("categories")
    assert cats and "* myContacts" in cats.values                  # ' ::: ' group membership


def test_unrecognized_csv_raises():
    try:
        csvp.parse("foo,bar\n1,2\n", "x")
    except ValueError:
        return
    raise AssertionError("expected a ValueError for an unrecognized CSV header")


# --------------------------------------------------------------------------- end to end
def test_linkedin_loads_and_title_is_searchable():
    """Load LinkedIn into shared.db and confirm position/title is searchable (the FTS fix)."""
    contacts = _load(LINKEDIN, "linkedin")
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "shared.db"
        result = shared_db.load(db, contacts, ingested_at="2026-06-04T00:00:00Z")
        assert result.total >= 20 and result.by_source == {"linkedin": result.total}
        # "Rear Admiral" is Grace's Position -> TITLE; searchable now that title is indexed.
        hits = shared_db.search(db, "admiral")
        assert any(name == "Grace Hopper" for name, *_ in hits)


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
