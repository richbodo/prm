#!/usr/bin/env python3
"""Validate the Google Takeout fixtures against their expected manifests.

Stdlib-only and self-contained on purpose: this runs before the ingester (and its `vobjectx`
dependency) exists, so it parses just enough vCard to verify the corpus is well-formed and
matches `expected/<name>.json`. It also exercises `build.py` end-to-end by reading the built
`.zip`s rather than the unpacked `sources/` tree.

Run either way:
    python test_fixtures.py        # plain script, prints a summary, exits non-zero on failure
    pytest test_fixtures.py        # discovered as test_* functions
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = HERE / "expected"

import build  # noqa: E402  (sibling module)

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")


def unfold(text: str) -> str:
    """Reverse RFC 6350 line folding: a CRLF/LF followed by a single space/tab continues a line."""
    return text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")


def parse_zip(zip_path: Path) -> dict:
    """Extract verifiable metrics from a built Takeout zip using stdlib only."""
    contact_count = with_uid = with_embedded_photo = 0
    fns: list[str] = []
    vcf_files: list[str] = []
    external_images: list[str] = []

    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            lower = name.lower()
            if lower.endswith(".vcf"):
                vcf_files.append(name)
                text = unfold(zf.read(name).decode("utf-8"))
                # Split into individual cards (drop the empty head before the first BEGIN).
                for block in text.split("BEGIN:VCARD")[1:]:
                    contact_count += 1
                    for line in block.splitlines():
                        if line.startswith("FN:"):
                            fns.append(line[3:].strip())
                        elif line.startswith("UID:"):
                            with_uid += 1
                        elif line.startswith("PHOTO"):
                            with_embedded_photo += 1
            elif lower.endswith(IMAGE_SUFFIXES):
                external_images.append(name)

    return {
        "vcf_files": sorted(vcf_files),
        "external_images": sorted(external_images),
        "contact_count": contact_count,
        "with_uid": with_uid,
        "without_uid": contact_count - with_uid,
        "with_embedded_photo": with_embedded_photo,
        "fns": sorted(fns),
    }


def check_fixture(name: str) -> list[str]:
    """Return a list of mismatch strings for one fixture ([] == pass)."""
    expected = json.loads((EXPECTED / f"{name}.json").read_text(encoding="utf-8"))
    zip_path = build.build_one(name)

    if not zipfile.is_zipfile(zip_path):
        return [f"{name}: built archive is not a valid zip"]

    actual = parse_zip(zip_path)
    fields = [
        "vcf_files",
        "external_images",
        "contact_count",
        "with_uid",
        "without_uid",
        "with_embedded_photo",
        "fns",
    ]
    problems = []
    for field in fields:
        if actual[field] != expected[field]:
            problems.append(f"{name}.{field}: expected {expected[field]!r}, got {actual[field]!r}")
    return problems


def all_fixture_names() -> list[str]:
    return sorted(p.stem for p in EXPECTED.glob("*.json"))


# ---- pytest entry points ----
def test_expected_manifests_exist():
    assert all_fixture_names(), "no expected/*.json manifests found; run generate_sources.py"


def test_fixtures_match_expected():
    problems = []
    for name in all_fixture_names():
        problems.extend(check_fixture(name))
    assert not problems, "fixture mismatches:\n" + "\n".join(problems)


# ---- plain-script entry point ----
def main() -> int:
    names = all_fixture_names()
    if not names:
        print("no expected/*.json manifests found; run generate_sources.py", file=sys.stderr)
        return 1
    all_problems = []
    for name in names:
        problems = check_fixture(name)
        status = "OK" if not problems else "FAIL"
        exp = json.loads((EXPECTED / f"{name}.json").read_text(encoding="utf-8"))
        print(f"[{status}] {name}: {exp['contact_count']} contacts, "
              f"{exp['without_uid']} without UID, {exp['with_embedded_photo']} embedded photo(s)")
        all_problems.extend(problems)
    if all_problems:
        print("\nMismatches:", file=sys.stderr)
        for p in all_problems:
            print("  " + p, file=sys.stderr)
        return 1
    print(f"\nAll {len(names)} fixtures match their expected manifests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
