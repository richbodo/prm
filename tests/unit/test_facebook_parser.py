#!/usr/bin/env python3
"""Tests for the Facebook friends-JSON parser, including the DYI mojibake repair.

Asserts against the fixture's expected manifests (names, recovered mojibake, connect dates), then
loads through to a searchable shared.db. Runs as a script or under pytest.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import normalize  # noqa: E402
from cli.parsers import facebook  # noqa: E402
from cli.parsers.base import SourceFormat, detect_format  # noqa: E402
from cli.parsers.facebook import _repair_mojibake  # noqa: E402
from core import shared_db  # noqa: E402

SRC = REPO / "tests" / "fixtures" / "facebook" / "sources"
EXP = REPO / "tests" / "fixtures" / "facebook" / "expected"
BASIC, VARIANT = SRC / "friends-basic.json", SRC / "friends-export-variant.json"


def _contacts(path):
    return normalize.normalize_all(facebook.parse(path.read_bytes(), "facebook"))


def _manifest(name):
    return json.loads((EXP / name).read_text(encoding="utf-8"))


def test_basic_matches_manifest():
    exp = _manifest("friends-basic.json")
    cc = _contacts(BASIC)
    assert [c.display_name for c in cc] == exp["names"]
    assert all(c.stable_key.kind == "hash" for c in cc)        # name + date only -> content hash
    for c in cc:
        assert c.jcard.first_value("x-connected-on") == exp["connected_on"][c.display_name]


def test_variant_mojibake_is_recovered():
    exp = _manifest("friends-export-variant.json")
    cc = _contacts(VARIANT)
    assert [c.display_name for c in cc] == exp["names_recovered"]
    for c in cc:
        assert c.jcard.first_value("x-connected-on") == exp["connected_on"][c.display_name]


def test_repair_is_targeted():
    assert _repair_mojibake("JosÃ©") == "José"   # mojibake "JosÃ©" -> "José"
    assert _repair_mojibake("José") == "José"         # already-clean accent kept
    assert _repair_mojibake("田中") == "田中"   # CJK kept (not latin-1-encodable)
    assert _repair_mojibake("John Doe") == "John Doe"           # ASCII unchanged


def test_distinct_identities():
    cc = _contacts(BASIC)
    # name + connect-date hashes are distinct across friends
    assert len(Counter(c.stable_key.value for c in cc)) == len(cc)


def test_detect_and_load_and_search():
    assert detect_format(BASIC) is SourceFormat.FACEBOOK_JSON
    cc = _contacts(BASIC)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "shared.db"
        result = shared_db.load(db, cc, ingested_at="2026-06-04T00:00:00Z")
        assert result.by_source == {"facebook": result.total}
        assert any(name == "Ada Lovelace" for name, *_ in shared_db.search(db, "lovelace"))


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
