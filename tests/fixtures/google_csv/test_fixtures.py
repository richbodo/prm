#!/usr/bin/env python3
"""Validate the Google CSV fixture against its expected manifest (stdlib only).

Self-contained on purpose — runs before the ingester exists. Also asserts the defining Google CSV
quirks: there is **no UID/id column** (so a stable id must be derived, not read), and
``Group Membership`` joins values with `` ::: `` around the ``* myContacts`` system marker.

    python test_fixtures.py        # plain script
    pytest test_fixtures.py        # discovered as test_* functions
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"

GROUP_SEPARATOR = " ::: "
SYSTEM_GROUP = "* myContacts"


def parse(raw: bytes) -> dict:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    line_ending = "CRLF" if crlf == lf and crlf > 0 else ("LF" if crlf == 0 else "MIXED")

    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    header, data = rows[0], rows[1:]
    col = {name: idx for idx, name in enumerate(header)}

    def filled(name):
        i = col[name]
        return sum(1 for r in data if i < len(r) and r[i].strip())

    def groups(row):
        return [g for g in row[col["Group Membership"]].split(GROUP_SEPARATOR) if g]

    return {
        "csv_file": "contacts.csv",
        "line_ending": line_ending,
        "columns": header,
        "has_uid_column": any("uid" in c.lower() or c.lower() == "id" for c in header),
        "group_separator": GROUP_SEPARATOR,
        "system_group_marker": SYSTEM_GROUP,
        "data_rows": len(data),
        "with_email": filled("E-mail 1 - Value"),
        "with_multi_email": filled("E-mail 2 - Value"),
        "without_email": sum(1 for r in data if not r[col["E-mail 1 - Value"]].strip()),
        "with_phone": filled("Phone 1 - Value"),
        "with_org": filled("Organization 1 - Name"),
        "with_website": filled("Website 1 - Value"),
        "with_birthday": filled("Birthday"),
        "starred": sum(1 for r in data if "Starred in Android" in groups(r)),
        "fns": sorted(r[col["Name"]] for r in data if r[col["Name"]].strip()),
    }


FIELDS = [
    "csv_file", "line_ending", "columns", "has_uid_column", "group_separator",
    "system_group_marker", "data_rows", "with_email", "with_multi_email", "without_email",
    "with_phone", "with_org", "with_website", "with_birthday", "starred", "fns",
]


def check() -> list[str]:
    exp = json.loads((EXPECTED / "contacts.json").read_text(encoding="utf-8"))
    act = parse((SOURCES / "contacts.csv").read_bytes())
    return [f"{f}: expected {exp[f]!r}, got {act[f]!r}" for f in FIELDS if act[f] != exp[f]]


def test_fixture_matches_expected():
    assert (EXPECTED / "contacts.json").exists(), "run generate_source.py first"
    problems = check()
    assert not problems, "fixture mismatches:\n" + "\n".join(problems)


def test_no_uid_column():
    """The defining quirk: Google CSV carries no id, so identity must fall back (INV-5)."""
    header = next(csv.reader(io.StringIO((SOURCES / "contacts.csv").read_bytes().decode("utf-8"))))
    assert not any("uid" in c.lower() or c.lower() == "id" for c in header), \
        "Google CSV export must have no UID/id column"


def test_group_membership_uses_triple_colon_separator():
    """Group Membership joins values with ' ::: ' around the '* myContacts' system marker."""
    raw = (SOURCES / "contacts.csv").read_bytes().decode("utf-8")
    rows = list(csv.reader(io.StringIO(raw)))
    gm = rows[0].index("Group Membership")
    cells = [r[gm] for r in rows[1:]]
    assert any(GROUP_SEPARATOR in c for c in cells), "expected at least one ' ::: '-joined membership"
    assert all(SYSTEM_GROUP in c for c in cells), "every contact should carry the '* myContacts' marker"


def test_quoted_notes_roundtrip():
    """A Notes cell with a comma and embedded quotes must parse back intact (CSV-quoting hazard)."""
    raw = (SOURCES / "contacts.csv").read_bytes().decode("utf-8")
    rows = list(csv.reader(io.StringIO(raw)))
    name, notes = rows[0].index("Name"), rows[0].index("Notes")
    turing = next(r for r in rows[1:] if r[name] == "Alan Turing")
    assert turing[notes] == 'Met at NPL; said "machines will think", then laughed.'


def main() -> int:
    if not (EXPECTED / "contacts.json").exists():
        print("missing expected manifest; run generate_source.py", file=sys.stderr)
        return 1
    problems = check()
    exp = json.loads((EXPECTED / "contacts.json").read_text(encoding="utf-8"))
    status = "OK" if not problems else "FAIL"
    print(f"[{status}] google_csv: {exp['data_rows']} contacts, no-UID={not exp['has_uid_column']}, "
          f"email {exp['with_email']}/{exp['data_rows']} ({exp['without_email']} none), "
          f"multi-email {exp['with_multi_email']}, starred {exp['starred']}")
    if problems:
        print("\nMismatches:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
