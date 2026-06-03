#!/usr/bin/env python3
"""Validate the bulk Google Takeout fixture against its manifest (stdlib only).

Runs before the ingester exists. Re-parses the committed ``All Contacts.vcf`` and checks the
recorded distribution, then asserts the defining real-world quirks the bulk corpus exists to carry
at scale: **zero UIDs**, a substantial **name-less** fraction, and **CATEGORIES** present.

    python test_fixtures.py        # plain script
    pytest test_fixtures.py        # discovered as test_* functions
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"
VCF = SOURCES / "Takeout" / "Contacts" / "All Contacts" / "All Contacts.vcf"

# Import the generator's stats() so the test and generator agree on how facts are counted.
sys.path.insert(0, str(HERE))
from generate_source import stats  # noqa: E402


def _manifest() -> dict:
    return json.loads((EXPECTED / "bulk.json").read_text(encoding="utf-8"))


def check() -> list[str]:
    exp = _manifest()
    act = stats(VCF.read_bytes())
    return [f"{k}: expected {exp[k]!r}, got {act[k]!r}" for k in act if exp.get(k) != act[k]]


def test_fixture_matches_manifest():
    assert VCF.exists(), "run generate_source.py first"
    problems = check()
    assert not problems, "bulk fixture mismatches:\n" + "\n".join(problems)


def test_no_uids_at_all():
    """Missing-UID is universal in Takeout — the whole corpus must exercise the fallback (INV-5)."""
    assert _manifest()["uid_count"] == 0


def test_has_substantial_nameless_fraction():
    """Name-less cards are a real, common case (~33% in the source export)."""
    m = _manifest()
    frac = m["nameless_cards"] / m["total_cards"]
    assert 0.2 <= frac <= 0.45, f"nameless fraction {frac:.2f} outside the realistic 20-45% band"


def test_carries_categories_and_grouped_emails():
    m = _manifest()
    assert m["categories_cards"] > 0, "CATEGORIES (group membership) must be present"
    assert m["grouped_email_lines"] > m["bare_email_lines"], "itemN-grouped email should dominate"


def test_is_roughly_a_thousand():
    assert _manifest()["total_cards"] >= 1000


def main() -> int:
    if not VCF.exists() or not (EXPECTED / "bulk.json").exists():
        print("missing fixture/manifest; run generate_source.py", file=sys.stderr)
        return 1
    problems = check()
    m = _manifest()
    status = "OK" if not problems else "FAIL"
    print(f"[{status}] google_takeout_bulk: {m['total_cards']} cards, {m['nameless_cards']} nameless, "
          f"{m['cards_with_email']} with email, {m['categories_cards']} CATEGORIES, {m['uid_count']} UIDs")
    if problems:
        print("\nMismatches:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
