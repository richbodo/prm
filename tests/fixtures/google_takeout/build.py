#!/usr/bin/env python3
"""Assemble the unpacked Takeout fixture trees under ``sources/`` into ``.zip`` archives.

A real Google Takeout export is a zip, so this is the last step that turns the diffable
unpacked trees into the artifact the ingester actually sees. Pure stdlib; deterministic
(fixed mtimes), so rebuilt archives are byte-stable in review.

Built archives land in ``build/`` (gitignored) — they are derived artifacts, not committed.

Usage:
    python build.py            # build every fixture into build/<name>.zip
    python build.py --list     # list the fixtures available to build
    python build.py --clean    # remove the build/ directory
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"
BUILD = HERE / "build"

# Fixed timestamp for reproducible archives (1980-01-01, the zip epoch floor).
ZIP_MTIME = (1980, 1, 1, 0, 0, 0)


def fixture_names() -> list[str]:
    if not SOURCES.exists():
        return []
    return sorted(p.name for p in SOURCES.iterdir() if p.is_dir())


def build_one(name: str) -> Path:
    root = SOURCES / name
    if not root.is_dir():
        raise FileNotFoundError(f"no such fixture: {name} (run generate_sources.py first?)")
    BUILD.mkdir(exist_ok=True)
    out = BUILD / f"{name}.zip"
    # Files are sorted for a stable archive order.
    members = sorted(p for p in root.rglob("*") if p.is_file())
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in members:
            arcname = str(path.relative_to(root))
            info = zipfile.ZipInfo(arcname, date_time=ZIP_MTIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    return out


def build_all() -> list[Path]:
    names = fixture_names()
    if not names:
        raise FileNotFoundError(
            f"no fixtures under {SOURCES}; run: python {(HERE / 'generate_sources.py').name}"
        )
    return [build_one(n) for n in names]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="List available fixtures and exit.")
    group.add_argument("--clean", action="store_true", help="Remove the build/ directory and exit.")
    args = parser.parse_args(argv)

    if args.list:
        names = fixture_names()
        if not names:
            print("(no fixtures generated yet — run generate_sources.py)")
        for n in names:
            print(n)
        return 0

    if args.clean:
        import shutil

        if BUILD.exists():
            shutil.rmtree(BUILD)
            print(f"removed {BUILD}")
        else:
            print("nothing to clean")
        return 0

    built = build_all()
    for path in built:
        print(f"built {path.relative_to(HERE)}  ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
