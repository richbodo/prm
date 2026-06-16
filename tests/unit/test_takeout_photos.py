#!/usr/bin/env python3
"""Tests for v0.2 R7b: Google Takeout sidecar photos auto-matched at ingest. A contact whose FN matches
exactly one image gets it (stored in the media store, referenced from shared.db by a `prm-media:` ref —
not base64 in the DB); an ambiguous name is skipped; a URL-only photo is never fetched (INV-1); a matched
photo supersedes a hosted URL; the portable vCard inlines it; and `--raw` round-trips it losslessly.

    python tests/unit/test_takeout_photos.py
    pytest tests/unit/test_takeout_photos.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import backup, ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from cli.vcard_writer import write_vcards  # noqa: E402
from core import media, projection, shared_db  # noqa: E402
from daemon import server  # noqa: E402

JPG_ADA = b"\xff\xd8\xff\xe0ada-image-bytes"
JPG_DAVE = b"\xff\xd8\xff\xe0dave-image-bytes"
IMG_X = b"\xff\xd8\xff\xe0bob-image-x"
IMG_Y = b"\xff\xd8\xff\xe0bob-image-y-distinct"

_CARDS = [
    ("Ada Lovelace", ""),                                              # sidecar only → matched
    ("Grace Hopper", ""),                                              # no photo → none
    ("Bob Twin", ""),                                                  # two distinct sidecars → ambiguous
    ("Carol Url", "PHOTO;VALUE=uri:https://example.com/carol.jpg\r\n"),  # URL only → not fetched (INV-1)
    ("Dave Both", "PHOTO;VALUE=uri:https://example.com/dave.jpg\r\n"),   # URL + sidecar → sidecar wins
]
_PHOTOS = {
    "Takeout/Contacts/local/Ada Lovelace.jpg": JPG_ADA,
    "Takeout/Contacts/My Contacts/Bob Twin.jpg": IMG_X,
    "Takeout/Contacts/local/Bob Twin.jpg": IMG_Y,                      # distinct bytes → ambiguous
    "Takeout/Contacts/local/Dave Both.jpg": JPG_DAVE,
}


def _make_zip(path: Path) -> None:
    vcf = "".join(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{fn}\r\nEMAIL:{fn.split()[0].lower()}@x.org\r\n{extra}END:VCARD\r\n"
                  for fn, extra in _CARDS)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Takeout/Contacts/All Contacts/All Contacts.vcf", vcf)
        for name, data in _PHOTOS.items():
            zf.writestr(name, data)
    path.write_bytes(buf.getvalue())


def _ingested():
    tmp = tempfile.mkdtemp()
    home = resolve_home(Path(tmp) / "h")
    z = Path(tmp) / "takeout.zip"
    _make_zip(z)
    ingest_mod.ingest([z], home=home, dry_run=False)
    return home, tmp


def _photo_of(home, name):
    cid = next((c["id"] for c in projection.all_contacts(home) if c["fn"] == name), None)
    return cid, projection.contact_photo(home, cid) if cid else None


# ------------------------------------------------------------------ match / ambiguity / INV-1 / supersede
def test_sidecar_photos_matched_by_fn():
    home, tmp = _ingested()
    try:
        # Ada: matched to her sidecar, served as bytes; the marker says imported
        cid, got = _photo_of(home, "Ada Lovelace")
        assert got == (JPG_ADA, "image/jpeg")
        assert projection.get_contact(home, cid)["photo"] == {"present": True, "source": "import"}
        # Dave: sidecar supersedes the hosted URL
        assert _photo_of(home, "Dave Both")[1] == (JPG_DAVE, "image/jpeg")
        # Grace: no photo · Carol: URL only, never fetched (INV-1) · Bob: two distinct images → ambiguous
        assert _photo_of(home, "Grace Hopper")[1] is None
        assert _photo_of(home, "Carol Url")[1] is None
        assert _photo_of(home, "Bob Twin")[1] is None
    finally:
        __import__("shutil").rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ lean DB: a ref, not base64
def test_shared_db_holds_a_ref_not_bytes():
    home, tmp = _ingested()
    try:
        refs = 0
        for r in shared_db.all_records(home.shared_db):
            for prop in json.loads(r["raw_jcard"])[1]:
                if prop[0].lower() == "photo" and media.parse_ref(prop[3]):
                    assert len(prop[3]) < 120                 # a prm-media ref, not inline base64
                    refs += 1
        assert refs == 2                                     # Ada + Dave (Carol's unfetched URL passes through)
        # gc must NOT reclaim imported blobs (they're in the reference set)
        assert media.gc(home, projection.referenced_image_hashes(home)) == []
    finally:
        __import__("shutil").rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ daemon serves it; portable vCard inlines it
def test_daemon_serves_and_export_inlines():
    home, tmp = _ingested()
    try:
        cid = next(c["id"] for c in projection.all_contacts(home) if c["fn"] == "Ada Lovelace")
        status, ctype, body = server.route("GET", f"/api/contact/{cid}/photo", {}, home)
        assert status == 200 and ctype == "image/jpeg" and body == JPG_ADA
        vcf = write_vcards(projection.export_jcards(home))
        assert "prm-media" not in vcf                        # no internal ref leaks into a portable vCard
        assert "PHOTO;ENCODING=b" in vcf                     # the real image is inlined as base64
    finally:
        __import__("shutil").rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ --raw round-trips the photo losslessly
def test_raw_backup_roundtrip_preserves_photo():
    home, tmp = _ingested()
    try:
        cid, before = _photo_of(home, "Ada Lovelace")
        doc = backup.dump(shared_db.all_records(home.shared_db), home=home)
        assert '"media"' in doc
        bpath = Path(tmp) / "b.json"
        bpath.write_text(doc, encoding="utf-8")
        home2 = resolve_home(Path(tmp) / "h2")
        ingest_mod.ingest([bpath], home=home2, dry_run=False)
        assert projection.contact_photo(home2, cid) == before    # same id (stable key) + same bytes
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
