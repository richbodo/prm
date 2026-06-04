#!/usr/bin/env python3
"""Generate the synthetic LinkedIn Connections.csv fixture.

Single source of truth for the fixture under ``sources/``. Pure-stdlib and deterministic, so the
committed output can be regenerated and diffed in review.

A LinkedIn "Basic" data export is a zip of ~29 CSVs; only **Connections.csv** is contact data
(the ``Email Addresses`` / ``PhoneNumbers`` / ``Profile`` CSVs are the *account owner's* own data,
not the connections'). So this fixture is a single ``Connections.csv``; there is no zip step.

All data is SYNTHETIC. Names are historical computing figures; the few emails use the reserved
``example.com``/``example.org`` domains (RFC 2606). No real export is committed — real ones go in
the repo-root ``ignore-data/`` (gitignored).

LinkedIn quirks this fixture reproduces, distilled from a real export (1,174 connections; values
redacted):
  * A **3-line preamble before the header**: ``Notes:``, a quoted privacy notice, then a blank
    line. A naive ``csv`` read that doesn't skip it parses garbage.
  * Header: ``First Name, Last Name, URL, Email Address, Company, Position, Connected On``.
  * **Email Address is blank for ~98% of rows** — LinkedIn only includes it when the connection
    opted in (the preamble says exactly this). The real export had 22/1,174 filled.
  * **No phone column and no stable id.** The closest thing to a stable key is the **profile
    URL**; otherwise a contact hashes on name + company.
  * Some rows have a **blank name** (connection withheld it) but still carry a URL; ~2% of a real
    export have **no name AND no URL** (a deactivated profile — only company/position/date remain),
    the thinnest record, identified only by company + the connected-on date.
  * ``Company``/``Position`` are usually present; ``Connected On`` is a ``DD Mon YYYY`` date.

Cross-source dedup hooks (both no-UID, matching the Google/Apple fixtures):
  * **Ada Lovelace** — *no email* (the 98% case), Company "Analytical Engines". Matching the
    Google/Apple Ada is therefore a **fuzzy name+company** decision — propose-only, never
    auto-merge (AC-PRM-B).
  * **Grace Hopper** — *has* an email matching the Apple Grace (the rare ~2% case), enabling a
    confident email-based cross-source match.

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

HEADER = ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"]

# LinkedIn's real export prefixes Connections.csv with this 3-line preamble (boilerplate, not PII).
PREAMBLE = [
    "Notes:",
    '"When exporting your connection data, you may notice that some of the email addresses are '
    "missing. You will only see email addresses for connections who have allowed their connections "
    'to see or download their email address using this setting."',
    "",
]


# (First, Last, url_slug, email, company, position, connected_on)
# email == "" reproduces the dominant LinkedIn case.
ROWS = [
    # Cross-source fuzzy hook: no email; matches Google/Apple Ada on name+company only.
    ("Ada", "Lovelace", "adalovelace", "", "Analytical Engines", "Mathematician", "10 Dec 2019"),
    # Cross-source confident hook: email matches the Apple Grace (rare opted-in case).
    ("Grace", "Hopper", "gracehopper", "grace.hopper@example.org", "US Navy", "Rear Admiral", "03 Feb 2018"),
    ("Alan", "Turing", "alanturing", "", "National Physical Laboratory", "Researcher", "21 Jun 2020"),
    ("Katherine", "Johnson", "katherinejohnson", "", "NASA", "Aerospace Technologist", "14 Mar 2017"),
    ("Edsger", "Dijkstra", "ewdijkstra", "", "Eindhoven University", "Professor", "09 Nov 2016"),
    ("Barbara", "Liskov", "barbaraliskov", "barbara.liskov@example.com", "MIT", "Institute Professor", "27 Aug 2021"),
    ("Donald", "Knuth", "donaldknuth", "", "Stanford University", "Professor Emeritus", "02 May 2015"),
    ("Margaret", "Hamilton", "margarethamilton", "", "Hamilton Technologies", "CEO", "18 Jan 2019"),
    ("Tim", "Berners-Lee", "timbl", "", "W3C", "Director", "30 Sep 2014"),
    ("Radia", "Perlman", "radiaperlman", "", "", "", "11 Jul 2022"),          # missing company/position
    ("Vint", "Cerf", "vintcerf", "", "Google", "VP & Chief Internet Evangelist", "05 Apr 2013"),
    ("Frances", "Allen", "francesallen", "", "IBM", "Fellow", "23 Oct 2016"),
    ("John", "McCarthy", "johnmccarthy", "", "Stanford University", "Professor", "08 Feb 2012"),
    ("Adele", "Goldberg", "adelegoldberg", "", "ParcPlace Systems", "Co-founder", "19 Jun 2018"),
    ("", "", "private-member-1a2b3c", "", "Confidential", "", "01 Jan 2023"),  # name withheld, URL only
    ("Ken", "Thompson", "kenthompson", "", "Bell Labs", "Researcher", "12 Dec 2011"),
    ("Dennis", "Ritchie", "dmr", "", "Bell Labs", "Researcher", "12 Dec 2011"),
    ("Shafi", "Goldwasser", "shafigoldwasser", "", "MIT", "Professor", "07 Mar 2020"),
    ("", "", "private-member-4d5e6f", "", "", "", "15 Aug 2022"),              # name + company withheld
    ("Leslie", "Lamport", "leslielamport", "", "Microsoft Research", "Scientist", "29 Nov 2017"),
    # Deactivated profile: no name AND no URL — only company/position/date remain (~2% of a real
    # export). No profile-URL key, so identity falls to a content hash over company + connected-date.
    ("", "", "", "", "Defunct Robotics", "Former Engineer", "04 Jun 2026"),
]


def render_csv() -> bytes:
    buf = io.StringIO()
    # Preamble lines are written raw (the quoted notice already contains its own quoting).
    for line in PREAMBLE:
        buf.write(line + "\r\n")
    writer = csv.writer(buf, lineterminator="\r\n")  # LinkedIn exports use CRLF in the CSV body
    writer.writerow(HEADER)
    for first, last, slug, email, company, position, connected in ROWS:
        url = f"https://www.linkedin.com/in/{slug}" if slug else ""
        writer.writerow([first, last, url, email, company, position, connected])
    return buf.getvalue().encode("utf-8")


def expected() -> dict:
    fns = sorted(f"{f} {l}".strip() for f, l, *_ in ROWS if (f or l))
    with_email = sum(1 for r in ROWS if r[3])
    with_name = sum(1 for r in ROWS if (r[0] or r[1]))
    with_company = sum(1 for r in ROWS if r[4])
    with_position = sum(1 for r in ROWS if r[5])
    with_url = sum(1 for r in ROWS if r[2])
    return {
        "description": (
            "Synthetic LinkedIn Connections.csv: 3-line Notes preamble before the header, "
            "email blank for most rows, no phone/UID, profile URL as the near-stable id, some "
            "name-withheld rows. Ada Lovelace (no email) is a fuzzy name+company cross-source "
            "hook; Grace Hopper (with email) is a confident cross-source hook."
        ),
        "csv_file": "Connections.csv",
        "line_ending": "CRLF",
        "preamble_lines": len(PREAMBLE),
        "columns": HEADER,
        "data_rows": len(ROWS),
        "with_name": with_name,
        "with_url": with_url,
        "with_email": with_email,
        "with_company": with_company,
        "with_position": with_position,
        "fns": fns,
    }


def generate() -> None:
    if SOURCES.exists():
        shutil.rmtree(SOURCES)
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    SOURCES.mkdir(parents=True)
    EXPECTED.mkdir(parents=True)
    (SOURCES / "Connections.csv").write_bytes(render_csv())
    (EXPECTED / "connections.json").write_text(
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
    print(f"Generated LinkedIn fixture ({len(ROWS)} connections) into sources/Connections.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
