#!/usr/bin/env python3
"""Validate the Facebook friends fixtures against their expected manifests (stdlib only).

Self-contained — runs before the ingester exists. Also asserts the two Facebook realities: the
friends array can live under ``friends`` or ``friends_v2``, and the variant's mojibake names
recover to real names via the latin-1 → UTF-8 round-trip the ingester will perform.

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

FRIEND_KEYS = ("friends", "friends_v2")


def load_friends(doc: dict):
    for key in FRIEND_KEYS:
        if key in doc:
            return key, doc[key]
    raise KeyError(f"no friends array under {FRIEND_KEYS}")


def recover(name: str) -> str:
    """Reverse Facebook's UTF-8-as-latin1 mojibake; identity if it doesn't apply."""
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def check_basic() -> list[str]:
    exp = json.loads((EXPECTED / "friends-basic.json").read_text(encoding="utf-8"))
    doc = json.loads((SOURCES / "friends-basic.json").read_text(encoding="utf-8"))
    key, friends = load_friends(doc)
    problems = []
    if key != exp["top_level_key"]:
        problems.append(f"top_level_key: expected {exp['top_level_key']!r}, got {key!r}")
    if len(friends) != exp["friend_count"]:
        problems.append(f"friend_count: expected {exp['friend_count']}, got {len(friends)}")
    names = [f["name"] for f in friends]
    if names != exp["names"]:
        problems.append(f"names: expected {exp['names']!r}, got {names!r}")
    if not all(isinstance(f.get("timestamp"), int) for f in friends):
        problems.append("every friend must have an integer timestamp")
    return problems


def check_variant() -> list[str]:
    exp = json.loads((EXPECTED / "friends-export-variant.json").read_text(encoding="utf-8"))
    doc = json.loads((SOURCES / "friends-export-variant.json").read_text(encoding="utf-8"))
    key, friends = load_friends(doc)
    problems = []
    if key != exp["top_level_key"]:
        problems.append(f"top_level_key: expected {exp['top_level_key']!r}, got {key!r}")
    raw = [f["name"] for f in friends]
    if raw != exp["names_raw"]:
        problems.append(f"names_raw: expected {exp['names_raw']!r}, got {raw!r}")
    recovered = [recover(n) for n in raw]
    if recovered != exp["names_recovered"]:
        problems.append(f"names_recovered: expected {exp['names_recovered']!r}, got {recovered!r}")
    # The raw names must actually be corrupted (otherwise the quirk isn't represented).
    if raw == recovered:
        problems.append("variant names are not mojibake'd — quirk not represented")
    return problems


def test_basic_matches_expected():
    assert (EXPECTED / "friends-basic.json").exists(), "run generate_source.py first"
    problems = check_basic()
    assert not problems, "friends-basic mismatches:\n" + "\n".join(problems)


def test_variant_matches_and_recovers():
    assert (EXPECTED / "friends-export-variant.json").exists(), "run generate_source.py first"
    problems = check_variant()
    assert not problems, "friends-export-variant mismatches:\n" + "\n".join(problems)


def main() -> int:
    if not (EXPECTED / "friends-basic.json").exists():
        print("missing expected manifests; run generate_source.py", file=sys.stderr)
        return 1
    problems = check_basic() + check_variant()
    b = json.loads((EXPECTED / "friends-basic.json").read_text(encoding="utf-8"))
    v = json.loads((EXPECTED / "friends-export-variant.json").read_text(encoding="utf-8"))
    print(f"[{'OK' if not problems else 'FAIL'}] facebook: "
          f"basic={b['friend_count']} friends (key '{b['top_level_key']}'), "
          f"variant={v['friend_count']} friends (key '{v['top_level_key']}', mojibake recovered)")
    if problems:
        print("\nMismatches:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
