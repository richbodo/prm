#!/usr/bin/env python3
"""Validate the Apple iCloud fixture against its expected manifest (stdlib only).

Apple's export is a single ``.vcf`` (no zip), so this reads ``sources/icloud-export.vcf``
directly. Self-contained on purpose — runs before the ingester and its ``vobjectx`` dependency
exist. Also checks the Apple-specific quirks: LF line endings, per-card PRODID, no UID.

    python test_fixtures.py        # plain script
    pytest test_fixtures.py        # discovered as test_* functions
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"


def unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def parse(raw: bytes) -> dict:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    line_ending = "LF" if crlf == 0 and lf > 0 else ("CRLF" if crlf == lf else "MIXED")

    text = unfold(raw.decode("utf-8"))
    cards = [c for c in text.split("BEGIN:VCARD") if c.strip()]
    fns, prodids = [], set()
    with_uid = with_xablabel = 0
    for c in cards:
        has_label = False
        for line in c.splitlines():
            if line.startswith("FN:"):
                fns.append(line[3:].strip())
            elif line.startswith("PRODID:"):
                prodids.add(line[7:].strip())
            elif line.startswith("UID:"):
                with_uid += 1
            elif re.match(r"^(item\d+\.)?X-ABLabel:", line):
                has_label = True
        if has_label:
            with_xablabel += 1

    return {
        "vcf_file": "icloud-export.vcf",
        "line_ending": line_ending,
        "contact_count": len(cards),
        "with_uid": with_uid,
        "distinct_prodids": sorted(prodids),
        "with_xablabel": with_xablabel,
        "fns": sorted(fns),
    }


def check() -> list[str]:
    expected = json.loads((EXPECTED / "icloud-export.json").read_text(encoding="utf-8"))
    actual = parse((SOURCES / "icloud-export.vcf").read_bytes())
    fields = ["vcf_file", "line_ending", "contact_count", "with_uid",
              "distinct_prodids", "with_xablabel", "fns"]
    return [f"{f}: expected {expected[f]!r}, got {actual[f]!r}"
            for f in fields if actual[f] != expected[f]]


def test_apple_icloud_matches_expected():
    assert (EXPECTED / "icloud-export.json").exists(), "run generate_source.py first"
    problems = check()
    assert not problems, "fixture mismatches:\n" + "\n".join(problems)


def main() -> int:
    if not (EXPECTED / "icloud-export.json").exists():
        print("missing expected manifest; run generate_source.py", file=sys.stderr)
        return 1
    problems = check()
    exp = json.loads((EXPECTED / "icloud-export.json").read_text(encoding="utf-8"))
    status = "OK" if not problems else "FAIL"
    print(f"[{status}] apple_icloud: {exp['contact_count']} contacts, {exp['line_ending']} endings, "
          f"{exp['with_uid']} UIDs, {len(exp['distinct_prodids'])} distinct PRODIDs")
    if problems:
        print("\nMismatches:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
