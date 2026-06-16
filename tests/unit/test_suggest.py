#!/usr/bin/env python3
"""Tests for the v0.2 R7c photo-match suggester (core/suggest.py + /api/suggest-photo-match): a loose
image filename → ranked candidate contacts, reusing the dedup normalizers — exact names, no-separator /
camelCase filenames, emails, nicknames, surname-only, and the `(N)` copy suffix.

    python tests/unit/test_suggest.py
    pytest tests/unit/test_suggest.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import suggest  # noqa: E402
from daemon import server  # noqa: E402

_PEOPLE = [("Ada Lovelace", "ada@example.com"), ("Robert Smith", "rob@example.com"),
           ("Jose Garcia", "jose@example.com"), ("Grace Hopper", "grace@example.com")]


def _home(tmp):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "c.vcf"
    v.write_text("".join(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{fn}\r\nEMAIL:{e}\r\nEND:VCARD\r\n"
                         for fn, e in _PEOPLE), encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home


def _top(home, filename):
    s = suggest.suggest_for_filename(home, filename)
    return (s[0]["name"], s[0]["basis"]) if s else (None, None)


def test_filename_variants_resolve_to_the_right_contact():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        assert _top(home, "Ada Lovelace.jpg") == ("Ada Lovelace", "name")     # exact
        assert _top(home, "AdaLovelace.png") == ("Ada Lovelace", "name")      # camelCase split
        assert _top(home, "ada_lovelace.jpeg")[0] == "Ada Lovelace"           # underscores
        assert _top(home, "adalovelace.jpg")[0] == "Ada Lovelace"             # no separators (fuzzy)
        assert _top(home, "Ada Lovelace(2).jpg") == ("Ada Lovelace", "name")  # (N) copy suffix stripped
        assert _top(home, "Lovelace.jpg")[0] == "Ada Lovelace"               # surname only (partial)


def test_email_in_filename_is_a_strong_match():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        assert _top(home, "ada@example.com.jpg") == ("Ada Lovelace", "email")
        # gmail canonicalization flows through normalize_email (dots ignored on gmail)
        assert _top(home, "rob@example.com.png") == ("Robert Smith", "email")


def test_nickname_and_accent_fold():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        assert _top(home, "Bob.jpg")[0] == "Robert Smith"     # nickname table: bob → robert
        assert _top(home, "José.jpg")[0] == "Jose Garcia"     # accent-folded


def test_no_match_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        assert suggest.suggest_for_filename(home, "Zxqwptl.jpg") == []
        assert suggest.suggest_for_filename(home, ".jpg") == []   # empty stem


def test_endpoint():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        status, _, body = server.route("GET", "/api/suggest-photo-match",
                                       {"name": ["Grace Hopper.jpg"]}, home)
        assert status == 200
        sug = json.loads(body)["suggestions"]
        assert sug and sug[0]["name"] == "Grace Hopper"
        assert server.route("GET", "/api/suggest-photo-match", {}, home)[0] == 400   # needs name


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
