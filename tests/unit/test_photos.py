#!/usr/bin/env python3
"""Tests for v0.2 R7 contact images end to end: an imported embedded PHOTO is served as bytes (not
dumped into the JSON), a manual upload overrides it through the audited/Undo-able path, a URL-only photo
is never fetched (INV-1), and the MCP-shaped projection never leaks photo bytes.

    python tests/unit/test_photos.py
    pytest tests/unit/test_photos.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import backup, ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, diag, projection, relationships_db, shared_db  # noqa: E402
from daemon import server  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDRtiny-png-body"
PNG_B64 = base64.b64encode(PNG).decode("ascii")
GIF = b"GIF89a" + b"tiny-gif-body"


def _ingest(tmp, body: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "c.vcf"
    v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\n{body}\r\nEND:VCARD\r\n", encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home, list(relationships_db.identity_map(home.relationships_db))[0]


# ------------------------------------------------------------------ imported PHOTO is served, not in JSON
def test_imported_photo_served_as_bytes_not_in_json():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _ingest(tmp, f"FN:Ada\r\nPHOTO;ENCODING=b;TYPE=PNG:{PNG_B64}")
        # the contact JSON carries a presence marker, never the base64 bytes
        status, _, raw = server.route("GET", f"/api/contact/{cid}", {}, home)
        c = json.loads(raw)
        assert c["photo"] == {"present": True, "source": "import"}
        assert not any(f["name"] == "photo" for f in c["fields"])   # no base64 text row
        assert "iVBOR" not in raw.decode() and PNG_B64 not in raw.decode()
        # the bytes come from the photo endpoint with the right mime
        status, ctype, body = server.route("GET", f"/api/contact/{cid}/photo", {}, home)
        assert status == 200 and ctype == "image/png" and body == PNG


# ------------------------------------------------------------------ upload overrides + is reversible
def test_upload_overrides_import_and_undo_restores():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _ingest(tmp, f"FN:Ada\r\nPHOTO;ENCODING=b;TYPE=PNG:{PNG_B64}")
        # upload a different image (a GIF) — replaces the imported avatar
        status, _, body = server.route("POST", "/api/set-photo", {}, home,
                                       {"contact_id": cid, "data_base64": base64.b64encode(GIF).decode(), "mime": "image/gif"})
        assert status == 200
        s, ctype, served = server.route("GET", f"/api/contact/{cid}/photo", {}, home)
        assert ctype == "image/gif" and served == GIF                # user override wins over import
        assert json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])["photo"]["source"] == "user"

        apply.undo(home)                                             # the upload is an audited field value
        s, ctype, served = server.route("GET", f"/api/contact/{cid}/photo", {}, home)
        assert ctype == "image/png" and served == PNG                # back to the imported photo


# ------------------------------------------------------------------ clear an uploaded avatar
def test_clear_photo_removes_override():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _ingest(tmp, "FN:Ada\r\nEMAIL:ada@x.org")        # no imported photo
        server.route("POST", "/api/set-photo", {}, home, {"contact_id": cid, "data_base64": PNG_B64})
        assert server.route("GET", f"/api/contact/{cid}/photo", {}, home)[0] == 200
        server.route("POST", "/api/clear-value", {}, home, {"contact_id": cid, "field_id": "photo"})
        assert server.route("GET", f"/api/contact/{cid}/photo", {}, home)[0] == 404
        assert json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])["photo"]["present"] is False


# ------------------------------------------------------------------ INV-1: a URL-only photo is never fetched
def test_url_only_photo_is_not_present_and_not_fetched():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _ingest(tmp, "FN:Remote\r\nPHOTO;VALUE=uri:https://example.com/a.jpg")
        c = json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])
        assert c["photo"]["present"] is False                        # not shown — we don't fetch remote
        assert projection.contact_photo(home, cid) is None
        assert server.route("GET", f"/api/contact/{cid}/photo", {}, home)[0] == 404


# ------------------------------------------------------------------ no photo at all → 404
def test_no_photo_is_404():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _ingest(tmp, "FN:Plain\r\nEMAIL:plain@x.org")
        assert server.route("GET", f"/api/contact/{cid}/photo", {}, home)[0] == 404
        assert server.route("POST", "/api/set-photo", {}, home, {"contact_id": cid})[0] == 400  # needs data


# ------------------------------------------------------------------ §11: --raw backup stays lossless
def test_imported_photo_survives_raw_backup_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _ingest(tmp, f"FN:Ada\r\nPHOTO;ENCODING=b;TYPE=PNG:{PNG_B64}")
        before = projection.contact_photo(home, cid)
        assert before == (PNG, "image/png")
        # export --raw (a JSON dump of shared.db) → re-import into a fresh home
        backup_path = Path(tmp) / "backup.json"
        backup_path.write_text(backup.dump(shared_db.all_records(home.shared_db)), encoding="utf-8")
        home2 = resolve_home(Path(tmp) / "h2")
        ingest_mod.ingest([backup_path], home=home2, dry_run=False)
        cid2 = list(relationships_db.identity_map(home2.relationships_db))[0]
        assert projection.contact_photo(home2, cid2) == before    # bytes + mime preserved — lossless


# ------------------------------------------------------------------ prm doctor flags an orphaned blob
def test_doctor_reports_media_orphan():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _ingest(tmp, "FN:Ada\r\nEMAIL:ada@x.org")
        server.route("POST", "/api/set-photo", {}, home, {"contact_id": cid, "data_base64": PNG_B64})
        assert diag.state_dump(home)["media"]["stored"] == 1
        server.route("POST", "/api/clear-value", {}, home, {"contact_id": cid, "field_id": "photo"})
        # clearing the value leaves the blob on disk, now referenced by nobody → an orphan doctor reports
        med = diag.state_dump(home)["media"]
        assert med["stored"] == 1 and len(med["orphans"]) == 1 and not med["corrupt"]


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
