#!/usr/bin/env python3
"""Generate the synthetic Google Contacts "Google CSV" export fixture.

Single source of truth for the fixture under ``sources/``. Pure-stdlib and deterministic, so the
committed output can be regenerated and diffed in review.

This models the **legacy Google CSV export** — what you get from contacts.google.com via
*Export → Google CSV* (and the CSV option in Takeout). It is a *different* path from the Google
**Takeout vCard** fixture (``../google_takeout/``): same Google account, a wholly different file
shape. vCard carries a ``UID``; **Google CSV has no id column at all** — which is exactly why it
exercises the stable-id fallback (``UID → email → … → content hash``, INV-5) differently.

All data is SYNTHETIC. Names are historical computing figures; emails use the reserved
``example.com``/``example.org`` domains (RFC 2606) and phone numbers use the 555-01xx fictional
block (NANP). No real export is committed — real ones go in the repo-root ``ignore-data/``
(gitignored).

Google CSV quirks this fixture reproduces:
  * **Wide, sparse columns** with split name fields (``Name`` plus ``Given/Additional/Family
    Name``) and numbered, typed multi-values (``E-mail 1 - Type``/``E-mail 1 - Value``,
    ``E-mail 2 - …``, ``Phone 1 - …``).
  * **No ``UID``/id column** — the defining quirk vs. the Takeout vCard. Stable identity must fall
    back to the normalized email, or to a content hash when no email exists.
  * **``Group Membership`` joins values with `` ::: ``** and includes the system ``* myContacts``
    marker (e.g. ``* myContacts ::: Starred in Android``).
  * **CSV-quoting hazards** in free text (``Notes`` containing commas and embedded quotes).
  * Unicode names, hyphenated surnames, a birthday/nickname, a multi-email contact, and a
    **no-email contact** (the content-hash fallback case).

Cross-source dedup hooks (matching the other fixtures by email — confident matches):
  * **Ada Lovelace** — ``ada.lovelace@example.com`` (matches Apple + Takeout Ada).
  * **Grace Hopper** — ``grace.hopper@example.org`` (matches Apple + Takeout Grace), plus a second
    email to exercise the multi-value path.
  * **Barbara Liskov** — ``barbara.liskov@example.com`` (matches the LinkedIn Barbara, the rare
    opted-in-email case there).

⚠️ Built from the documented legacy Google CSV column set, *not* from a committed real sample
(none was on hand). When a real Google CSV export is dropped in ``ignore-data/``, validate the
exact column set, the email/phone ``Type`` label syntax, and the line endings against it.

Usage:
    python generate_source.py          # regenerate sources/ and expected/
    python generate_source.py --check  # fail if regeneration would change committed output
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"

# Legacy Google CSV export header (representative, faithful subset). Note: no UID/id column.
HEADER = [
    "Name", "Given Name", "Additional Name", "Family Name", "Name Prefix", "Name Suffix",
    "Nickname", "Birthday", "Notes", "Group Membership",
    "E-mail 1 - Type", "E-mail 1 - Value", "E-mail 2 - Type", "E-mail 2 - Value",
    "Phone 1 - Type", "Phone 1 - Value",
    "Organization 1 - Name", "Organization 1 - Title",
    "Website 1 - Type", "Website 1 - Value",
]

GROUP_SEPARATOR = " ::: "
SYSTEM_GROUP = "* myContacts"  # Google's system marker for the default "My Contacts" group
STARRED = "Starred in Android"

# Each contact is a dict of the populated fields; everything else renders blank. ``groups`` is a
# list joined by GROUP_SEPARATOR. Order is fixed (deterministic output).
CONTACTS = [
    {  # confident cross-source anchor (email matches Apple + Takeout Ada); starred
        "Name": "Ada Lovelace", "Given Name": "Ada", "Family Name": "Lovelace",
        "groups": [SYSTEM_GROUP, STARRED],
        "E-mail 1 - Type": "Work", "E-mail 1 - Value": "ada.lovelace@example.com",
        "Phone 1 - Type": "Mobile", "Phone 1 - Value": "+1-555-0101",
        "Organization 1 - Name": "Analytical Engines", "Organization 1 - Title": "Mathematician",
    },
    {  # multi-email quirk + confident cross-source anchor
        "Name": "Grace Hopper", "Given Name": "Grace", "Family Name": "Hopper",
        "groups": [SYSTEM_GROUP],
        "E-mail 1 - Type": "Work", "E-mail 1 - Value": "grace.hopper@example.org",
        "E-mail 2 - Type": "Home", "E-mail 2 - Value": "ghopper@example.com",
        "Phone 1 - Type": "Work", "Phone 1 - Value": "+1-555-0102",
        "Organization 1 - Name": "US Navy", "Organization 1 - Title": "Rear Admiral",
    },
    {  # unicode (é) in given name; source-unique
        "Name": "André Truong", "Given Name": "André", "Family Name": "Truong",
        "groups": [SYSTEM_GROUP],
        "E-mail 1 - Type": "Home", "E-mail 1 - Value": "andre.truong@example.com",
        "Organization 1 - Name": "R2E", "Organization 1 - Title": "Engineer",
    },
    {  # NO email — forces the content-hash stable-id fallback
        "Name": "Hedy Lamarr", "Given Name": "Hedy", "Family Name": "Lamarr",
        "groups": [SYSTEM_GROUP],
        "Phone 1 - Type": "Mobile", "Phone 1 - Value": "+1-555-0110",
        "Organization 1 - Name": "Inventions Inc", "Organization 1 - Title": "Inventor",
    },
    {  # CSV-quoting hazard: Notes with a comma and embedded quotes
        "Name": "Alan Turing", "Given Name": "Alan", "Family Name": "Turing",
        "groups": [SYSTEM_GROUP, STARRED],
        "Notes": 'Met at NPL; said "machines will think", then laughed.',
        "E-mail 1 - Type": "Home", "E-mail 1 - Value": "alan.turing@example.com",
        "Organization 1 - Name": "National Physical Laboratory", "Organization 1 - Title": "Researcher",
    },
    {
        "Name": "Katherine Johnson", "Given Name": "Katherine", "Family Name": "Johnson",
        "groups": [SYSTEM_GROUP],
        "E-mail 1 - Type": "Work", "E-mail 1 - Value": "katherine.johnson@example.org",
        "Phone 1 - Type": "Work", "Phone 1 - Value": "+1-555-0103",
        "Organization 1 - Name": "NASA", "Organization 1 - Title": "Aerospace Technologist",
    },
    {  # hyphenated surname + website
        "Name": "Tim Berners-Lee", "Given Name": "Tim", "Family Name": "Berners-Lee",
        "groups": [SYSTEM_GROUP],
        "E-mail 1 - Type": "Work", "E-mail 1 - Value": "tim@example.org",
        "Organization 1 - Name": "W3C", "Organization 1 - Title": "Director",
        "Website 1 - Type": "Profile", "Website 1 - Value": "https://example.org/timbl",
    },
    {  # minimal: name + one email only
        "Name": "Radia Perlman", "Given Name": "Radia", "Family Name": "Perlman",
        "groups": [SYSTEM_GROUP],
        "E-mail 1 - Type": "Home", "E-mail 1 - Value": "radia.perlman@example.com",
    },
    {  # birthday + nickname; starred
        "Name": "Vint Cerf", "Given Name": "Vint", "Family Name": "Cerf",
        "Nickname": "Vint", "Birthday": "1943-06-23",
        "groups": [SYSTEM_GROUP, STARRED],
        "E-mail 1 - Type": "Work", "E-mail 1 - Value": "vint.cerf@example.com",
        "Organization 1 - Name": "Google", "Organization 1 - Title": "VP & Chief Internet Evangelist",
    },
    {  # confident cross-source anchor (email matches the LinkedIn Barbara)
        "Name": "Barbara Liskov", "Given Name": "Barbara", "Family Name": "Liskov",
        "groups": [SYSTEM_GROUP],
        "E-mail 1 - Type": "Work", "E-mail 1 - Value": "barbara.liskov@example.com",
        "Organization 1 - Name": "MIT", "Organization 1 - Title": "Institute Professor",
    },
]


def _cell(contact: dict, column: str) -> str:
    if column == "Group Membership":
        return GROUP_SEPARATOR.join(contact.get("groups", []))
    return contact.get(column, "")


def render_csv() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")  # Google CSV export uses CRLF
    writer.writerow(HEADER)
    for contact in CONTACTS:
        writer.writerow([_cell(contact, col) for col in HEADER])
    return buf.getvalue().encode("utf-8")


def expected() -> dict:
    def has(c, col):
        return bool(_cell(c, col).strip())

    return {
        "description": (
            "Synthetic legacy Google CSV export (Export → Google CSV). Wide sparse columns, split "
            "name fields, numbered typed E-mail/Phone, Group Membership joined by ' ::: ' with the "
            "'* myContacts' system marker, and NO UID/id column (stable-id fallback required). Ada "
            "Lovelace, Grace Hopper, and Barbara Liskov are confident email-based cross-source "
            "hooks; Hedy Lamarr (no email) exercises the content-hash fallback."
        ),
        "csv_file": "contacts.csv",
        "line_ending": "CRLF",
        "columns": HEADER,
        "has_uid_column": any("uid" in col.lower() or col.lower() == "id" for col in HEADER),
        "group_separator": GROUP_SEPARATOR,
        "system_group_marker": SYSTEM_GROUP,
        "data_rows": len(CONTACTS),
        "with_email": sum(1 for c in CONTACTS if has(c, "E-mail 1 - Value")),
        "with_multi_email": sum(1 for c in CONTACTS if has(c, "E-mail 2 - Value")),
        "without_email": sum(1 for c in CONTACTS if not has(c, "E-mail 1 - Value")),
        "with_phone": sum(1 for c in CONTACTS if has(c, "Phone 1 - Value")),
        "with_org": sum(1 for c in CONTACTS if has(c, "Organization 1 - Name")),
        "with_website": sum(1 for c in CONTACTS if has(c, "Website 1 - Value")),
        "with_birthday": sum(1 for c in CONTACTS if has(c, "Birthday")),
        "starred": sum(1 for c in CONTACTS if STARRED in c.get("groups", [])),
        "fns": sorted(c["Name"] for c in CONTACTS),
    }


def generate() -> None:
    if SOURCES.exists():
        shutil.rmtree(SOURCES)
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    SOURCES.mkdir(parents=True)
    EXPECTED.mkdir(parents=True)
    (SOURCES / "contacts.csv").write_bytes(render_csv())
    (EXPECTED / "contacts.json").write_text(
        json.dumps(expected(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
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

    if args.check:
        import filecmp
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for sub in ("sources", "expected"):
                src = HERE / sub
                if src.exists():
                    shutil.copytree(src, tmp_path / sub)
            generate()
            mismatch = [sub for sub in ("sources", "expected")
                        if _dircmp_differs(filecmp.dircmp(tmp_path / sub, HERE / sub))]
            if mismatch:
                print(f"ERROR: committed {', '.join(mismatch)} differ from generated output. "
                      f"Run: python {Path(__file__).name}", file=sys.stderr)
                return 1
        print("OK: committed fixture matches generator output.")
        return 0

    generate()
    print(f"Generated Google CSV fixture ({len(CONTACTS)} contacts) into sources/contacts.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
