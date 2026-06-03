#!/usr/bin/env python3
"""Validate the LinkedIn Connections.csv fixture against its expected manifest (stdlib only).

Self-contained on purpose — runs before the ingester exists. Also asserts the defining LinkedIn
quirk: the file has a preamble before the header, so a naive read (no skip) does NOT yield the
expected columns, while skipping the preamble does.

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

EXPECTED_HEADER = ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"]


def find_header(lines: list[str]) -> int:
    """Index of the real header row (skips LinkedIn's Notes: preamble)."""
    for i, line in enumerate(lines):
        if line.startswith("First Name,"):
            return i
    raise ValueError("header row not found")


def parse(raw: bytes) -> dict:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    line_ending = "CRLF" if crlf == lf and crlf > 0 else ("LF" if crlf == 0 else "MIXED")

    text = raw.decode("utf-8")
    lines = text.splitlines()
    preamble = find_header(lines)
    rows = list(csv.reader(io.StringIO("\r\n".join(lines[preamble:]))))
    header, data = rows[0], rows[1:]
    col = {name: idx for idx, name in enumerate(header)}

    def filled(name):
        i = col[name]
        return sum(1 for r in data if i < len(r) and r[i].strip())

    fns = sorted(
        f"{r[col['First Name']]} {r[col['Last Name']]}".strip()
        for r in data if (r[col["First Name"]].strip() or r[col["Last Name"]].strip())
    )
    return {
        "csv_file": "Connections.csv",
        "line_ending": line_ending,
        "preamble_lines": preamble,
        "columns": header,
        "data_rows": len(data),
        "with_name": sum(1 for r in data if r[col["First Name"]].strip() or r[col["Last Name"]].strip()),
        "with_url": filled("URL"),
        "with_email": filled("Email Address"),
        "with_company": filled("Company"),
        "with_position": filled("Position"),
        "fns": fns,
    }


def check() -> list[str]:
    exp = json.loads((EXPECTED / "connections.json").read_text(encoding="utf-8"))
    act = parse((SOURCES / "Connections.csv").read_bytes())
    fields = ["csv_file", "line_ending", "preamble_lines", "columns", "data_rows",
              "with_name", "with_url", "with_email", "with_company", "with_position", "fns"]
    return [f"{f}: expected {exp[f]!r}, got {act[f]!r}" for f in fields if act[f] != exp[f]]


def test_fixture_matches_expected():
    assert (EXPECTED / "connections.json").exists(), "run generate_source.py first"
    problems = check()
    assert not problems, "fixture mismatches:\n" + "\n".join(problems)


def test_naive_read_without_skipping_preamble_is_wrong():
    """The quirk in one assertion: row 0 is the Notes preamble, not the header."""
    raw = (SOURCES / "Connections.csv").read_bytes().decode("utf-8")
    first_row = next(csv.reader(io.StringIO(raw)))
    assert first_row != EXPECTED_HEADER, "preamble should precede the header"
    assert first_row[0].startswith("Notes"), "expected the Notes: preamble as the first CSV row"


def main() -> int:
    if not (EXPECTED / "connections.json").exists():
        print("missing expected manifest; run generate_source.py", file=sys.stderr)
        return 1
    problems = check()
    exp = json.loads((EXPECTED / "connections.json").read_text(encoding="utf-8"))
    status = "OK" if not problems else "FAIL"
    print(f"[{status}] linkedin: {exp['data_rows']} connections, {exp['preamble_lines']}-line preamble, "
          f"email {exp['with_email']}/{exp['data_rows']}, url {exp['with_url']}/{exp['data_rows']}, "
          f"named {exp['with_name']}/{exp['data_rows']}")
    if problems:
        print("\nMismatches:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
