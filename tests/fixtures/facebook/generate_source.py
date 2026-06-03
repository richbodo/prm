#!/usr/bin/env python3
"""Generate the synthetic Facebook "friends" fixtures.

Single source of truth for the fixtures under ``sources/``. Pure-stdlib and deterministic, so the
committed output can be regenerated and diffed in review.

Facebook's "Download Your Information" (DYI) export gives almost no contact data: for friends it is
just a **name and a connected-on timestamp** — no URL, no email, no phone, no id. It is the weakest
source the ingester handles, and the canonical "no-identifier" case for the stable-id fallback
(content hash) and for fuzzy, name-only cross-source dedup (propose-only).

In the PRM model a Facebook friend maps to:
  * ``FN`` — the display name (Facebook gives no structured given/family split),
  * a connected-on date — derived from the Unix ``timestamp`` (relationship metadata),
  * ``source = facebook`` — the constant/default provenance field,
  * stable id — a content hash of ``name + timestamp + source`` (no stronger key exists).

All data is SYNTHETIC. No real export is committed — real ones go in ``tests/fixtures/incoming/``.

Two fixtures:
  * ``friends-basic.json`` — the shape confirmed with the maintainer: ``{"friends": [{"name",
    "timestamp"}]}``. Clean UTF-8. Includes an "Ada Lovelace" name-only cross-source hook.
  * ``friends-export-variant.json`` — closer to a real DYI export: top-level key ``friends_v2`` and
    Facebook's well-known **UTF-8-decoded-as-latin1 mojibake** (e.g. ``José`` stored as ``JosÃ©``).
    The ingester must re-encode latin-1 → decode UTF-8 to recover the real name.

Usage:
    python generate_source.py          # regenerate sources/ and expected/
    python generate_source.py --check  # fail if regeneration would change committed output
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
EXPECTED = HERE / "expected"


def mojibake(name: str) -> str:
    """Reproduce Facebook's encoding bug: UTF-8 bytes mis-read as latin-1.

    e.g. 'José' (U+00E9 -> UTF-8 C3 A9) becomes 'JosÃ©'. The ingester reverses this with
    name.encode('latin-1').decode('utf-8').
    """
    return name.encode("utf-8").decode("latin-1")


def iso_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


# (name, unix_timestamp). Timestamps are fixed (deterministic).
BASIC_FRIENDS = [
    ("Ada Lovelace", 1576022400),    # 2019-12-11 — name-only cross-source hook
    ("John Doe", 1645392000),        # 2022-02-20 (the maintainer's example values)
    ("Jane Smith", 1678915200),      # 2023-03-16
    ("José Ñoño", 1597017600),       # 2020-08-10 — clean UTF-8
    ("田中 太郎", 1612137600),         # 2021-02-01 — CJK, clean UTF-8
    ("Mary O'Brien", 1500000000),    # 2017-07-14 — apostrophe
]

# Real-DYI variant: friends_v2 key + mojibake'd names. recovered == the true name.
VARIANT_FRIENDS = [
    ("José Ñoño", 1597017600),
    ("Bjørn Müller", 1620000000),    # 2021-05-03
    ("Renée Dubois", 1630000000),    # 2021-08-26
]


def write_basic():
    data = {"friends": [{"name": n, "timestamp": ts} for n, ts in BASIC_FRIENDS]}
    (SOURCES / "friends-basic.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "description": "Maintainer-confirmed shape: {friends:[{name,timestamp}]}, clean UTF-8.",
        "json_file": "friends-basic.json",
        "top_level_key": "friends",
        "friend_count": len(BASIC_FRIENDS),
        "mojibake": False,
        "names": [n for n, _ in BASIC_FRIENDS],
        "connected_on": {n: iso_date(ts) for n, ts in BASIC_FRIENDS},
    }


def write_variant():
    data = {"friends_v2": [{"name": mojibake(n), "timestamp": ts} for n, ts in VARIANT_FRIENDS]}
    (SOURCES / "friends-export-variant.json").write_text(
        # ensure_ascii=True here so the mojibake survives as the literal escaped bytes a real
        # export ships (and so the file is pure ASCII, like Facebook's).
        json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return {
        "description": (
            "Real-DYI variant: top-level 'friends_v2' key and Facebook UTF-8-as-latin1 mojibake. "
            "'name' holds the corrupted bytes; 'recovered' is what name.encode('latin-1')"
            ".decode('utf-8') should yield."
        ),
        "json_file": "friends-export-variant.json",
        "top_level_key": "friends_v2",
        "friend_count": len(VARIANT_FRIENDS),
        "mojibake": True,
        "names_raw": [mojibake(n) for n, _ in VARIANT_FRIENDS],
        "names_recovered": [n for n, _ in VARIANT_FRIENDS],
        "connected_on": {n: iso_date(ts) for n, ts in VARIANT_FRIENDS},
    }


def generate() -> None:
    if SOURCES.exists():
        shutil.rmtree(SOURCES)
    if EXPECTED.exists():
        shutil.rmtree(EXPECTED)
    SOURCES.mkdir(parents=True)
    EXPECTED.mkdir(parents=True)

    basic = write_basic()
    variant = write_variant()
    (EXPECTED / "friends-basic.json").write_text(
        json.dumps(basic, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (EXPECTED / "friends-export-variant.json").write_text(
        json.dumps(variant, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
        print("OK: committed fixtures match generator output.")
        return 0

    generate()
    print(f"Generated Facebook fixtures: friends-basic.json ({len(BASIC_FRIENDS)}), "
          f"friends-export-variant.json ({len(VARIANT_FRIENDS)}, mojibake)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
