#!/usr/bin/env python3
"""Unit tests for ``cli.vcard_writer`` — the dependency-free vCard 3.0 emitter.

Pins the mechanical jCard -> vCard mapping: a fixed header, value escaping, structured (``N``) vs
multi-valued (``CATEGORIES``) vs scalar values, ``TYPE`` params + the ``group`` line prefix, long-line
folding, and the vCard-3.0 ``FN`` requirement. Pure string-in/string-out; no DB, no I/O.

    python tests/unit/test_vcard_writer.py
    pytest tests/unit/test_vcard_writer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli.vcard_writer import write_vcards  # noqa: E402


def _lines(text: str) -> list[str]:
    return text.split("\r\n")


def test_header_and_required_fn():
    out = write_vcards([["vcard", [["fn", {}, "text", "Ada Lovelace"]]]])
    assert out.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")
    assert out.endswith("END:VCARD\r\n")
    assert "FN:Ada Lovelace\r\n" in out
    # CRLF line endings throughout — no bare LF anywhere.
    assert "\r\n" in out and "\n" not in out.replace("\r\n", "")
    assert _lines(out) == ["BEGIN:VCARD", "VERSION:3.0", "FN:Ada Lovelace", "END:VCARD", ""]


def test_nameless_contact_gets_empty_fn():
    out = write_vcards([["vcard", [["email", {}, "text", "x@y.org"]]]])
    assert "FN:\r\n" in out                       # vCard 3.0 requires FN even when name-less
    assert "EMAIL:x@y.org" in out


def test_structured_name_joins_with_semicolons():
    out = write_vcards([["vcard", [["n", {}, "text", ["Lovelace", "Ada", "", "", ""]]]]])
    assert "N:Lovelace;Ada;;;\r\n" in out


def test_multivalued_categories_join_with_commas():
    out = write_vcards([["vcard", [["categories", {}, "text", "Friends", "Work"]]]])
    assert "CATEGORIES:Friends,Work\r\n" in out


def test_text_value_escaping():
    # A literal comma, semicolon, backslash and newline in a NOTE must be escaped, not treated as
    # vCard structure.
    out = write_vcards([["vcard", [["note", {}, "text", "a,b; c\\d\ne"]]]])
    assert "NOTE:a\\,b\\; c\\\\d\\ne\r\n" in out


def test_params_and_group_prefix():
    prop = ["email", {"type": "home", "group": "item1"}, "text", "ada@x.org"]
    out = write_vcards([["vcard", [prop]]])
    assert "item1.EMAIL;TYPE=home:ada@x.org\r\n" in out


def test_multivalue_param_joins_with_comma():
    prop = ["tel", {"type": ["cell", "voice"]}, "text", "+15551234567"]
    out = write_vcards([["vcard", [prop]]])
    assert "TEL;TYPE=cell,voice:+15551234567\r\n" in out


def test_long_line_is_folded_at_75_octets():
    note = "x" * 200
    out = write_vcards([["vcard", [["fn", {}, "text", "N"], ["note", {}, "text", note]]]])
    physical = _lines(out)
    # Every physical line is within the octet budget...
    assert all(len(ln.encode("utf-8")) <= 75 for ln in physical)
    # ...and every continuation line begins with a single space (the fold marker).
    note_block = [ln for ln in physical if ln.startswith("NOTE:") or ln.startswith(" x")]
    assert len(note_block) > 1 and all(ln.startswith(" ") for ln in note_block[1:])


def test_version_property_in_input_is_never_echoed():
    out = write_vcards([["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", "A"]]]])
    assert out.count("VERSION:") == 1 and "VERSION:3.0" in out and "VERSION:4.0" not in out


def test_multiple_cards_concatenate():
    out = write_vcards([
        ["vcard", [["fn", {}, "text", "A"]]],
        ["vcard", [["fn", {}, "text", "B"]]],
    ])
    assert out.count("BEGIN:VCARD") == 2 and out.count("END:VCARD") == 2


def test_rejects_non_jcard():
    try:
        write_vcards([["not-a-vcard", []]])
    except ValueError:
        return
    raise AssertionError("expected ValueError for a non-jCard array")


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
