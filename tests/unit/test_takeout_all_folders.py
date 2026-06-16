#!/usr/bin/env python3
"""Tests for the v0.2 "fuller Takeout import" (`prm import --all-folders`). By default only the
`All Contacts` superset is read (and the skipped label folders are reported — no silent gap);
`--all-folders` also imports the label folders, pulling in archival contacts. Same-email duplicates
collapse on the stable id (All Contacts wins, loaded last), and the **email bridge** reattaches an
archival sidecar photo to a current contact even when the display name has drifted.

    python tests/unit/test_takeout_all_folders.py
    pytest tests/unit/test_takeout_all_folders.py
"""

from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import projection  # noqa: E402

PHOTO_BOB = b"\xff\xd8\xff\xe0bob-photo-bytes"
PHOTO_CAROL = b"\xff\xd8\xff\xe0carol-photo-bytes"


def _card(fn, email):
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{fn}\r\nEMAIL:{email}\r\nEND:VCARD\r\n"


def _zip(path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # current address book: Robert Smith (renamed) + Grace (only here)
        zf.writestr("Takeout/Contacts/All Contacts/All Contacts.vcf",
                    _card("Robert Smith", "bob@x.org") + _card("Grace Hopper", "grace@x.org"))
        # an archival label folder: an old copy of Bob (same email) + Carol (only here)
        zf.writestr("Takeout/Contacts/Work/Work.vcf",
                    _card("Bob Smith", "bob@x.org") + _card("Carol Archive", "carol@x.org"))
        zf.writestr("Takeout/Contacts/Work/Bob Smith.jpg", PHOTO_BOB)        # matched by FN-in-folder
        zf.writestr("Takeout/Contacts/Work/Carol Archive.jpg", PHOTO_CAROL)
    path.write_bytes(buf.getvalue())


def _ingest(all_folders: bool):
    tmp = tempfile.mkdtemp()
    home = resolve_home(Path(tmp) / "h")
    z = Path(tmp) / "takeout.zip"
    _zip(z)
    report = ingest_mod.ingest([z], home=home, dry_run=False, all_folders=all_folders)
    return home, report, tmp


def _by_name(home):
    return {c["fn"]: c for c in projection.all_contacts(home)}


# ------------------------------------------------------------------ default: All Contacts only + heads-up
def test_default_reads_all_contacts_only_and_reports_skipped():
    home, report, tmp = _ingest(all_folders=False)
    try:
        names = set(_by_name(home))
        assert names == {"Robert Smith", "Grace Hopper"}          # the Work folder's Carol is NOT imported
        assert any("Work" in n and "--all-folders" in n for n in report.notes)   # discoverable, not silent
    finally:
        __import__("shutil").rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ --all-folders: archival contacts + dedup
def test_all_folders_adds_archival_and_dedups_on_email():
    home, report, tmp = _ingest(all_folders=True)
    try:
        by = _by_name(home)
        # Carol (archival, Work-only) now imported; Bob/Robert collapse on the shared email…
        assert set(by) == {"Robert Smith", "Grace Hopper", "Carol Archive"}
        # …and All Contacts wins the collapse (loaded last) — the current name, not the archival "Bob Smith"
        assert "Bob Smith" not in by
        assert not report.notes                                    # nothing skipped → no heads-up
    finally:
        __import__("shutil").rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ the email bridge: photo despite FN drift
def test_email_bridge_attaches_photo_across_name_change():
    home, _, tmp = _ingest(all_folders=True)
    try:
        by = _by_name(home)
        # "Robert Smith" (current) gets the archival "Bob Smith.jpg" via the shared email bob@x.org —
        # a match FN-direct could never make (the names differ).
        assert projection.contact_photo(home, by["Robert Smith"]["id"]) == (PHOTO_BOB, "image/jpeg")
        assert projection.contact_photo(home, by["Carol Archive"]["id"]) == (PHOTO_CAROL, "image/jpeg")
        assert by["Grace Hopper"]["photo"]["present"] is False
    finally:
        __import__("shutil").rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ default mode can't bridge (no label vcf read)
def test_default_mode_misses_the_drifted_name_photo():
    home, _, tmp = _ingest(all_folders=False)
    try:
        by = _by_name(home)
        # default reads no label vcf, so the FN-drifted photo can't be reattached — Robert has no avatar
        assert by["Robert Smith"]["photo"]["present"] is False
    finally:
        __import__("shutil").rmtree(tmp, ignore_errors=True)


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
