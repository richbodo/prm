#!/usr/bin/env python3
"""Tests for the v0.2 R3 contact Edit mode (backend): editing a single-valued source field writes a
`field_resolutions` override (the override model — shared.db stays immutable, INV-2) that the projection
layers over the source value, reversibly; plus the daemon GET /api/schema and POST /api/resolve-field
routes that the edit UI drives.

    python tests/unit/test_contact_edit.py
    pytest tests/unit/test_contact_edit.py
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
from core import apply, projection, relationships_db, shared_db  # noqa: E402
from daemon import server  # noqa: E402

_VCARD = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\n"
          "ORG:Crown\r\nTITLE:Countess\r\nEND:VCARD\r\n")


def _home_with_contact(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text(_VCARD, encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home, list(relationships_db.identity_map(home.relationships_db))[0]


# ------------------------------------------------------------------ override + reversibility
def test_edit_single_field_overrides_source_and_is_reversible():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        assert projection.get_contact(home, cid)["fn"] == "Ada Lovelace"

        apply.resolve_field(home, cid, "fn", "Ada L.")
        assert projection.get_contact(home, cid)["fn"] == "Ada L."     # override layered over the source

        # shared.db (the immutable mirror) is untouched — INV-2
        srid = next(iter(relationships_db.identity_map(home.relationships_db)))
        rec = shared_db.get_record(home.shared_db, srid)
        assert "Ada Lovelace" in rec["raw_jcard"] and "Ada L." not in rec["raw_jcard"]

        apply.undo(home)                                               # snapshot restore reverts the edit
        assert projection.get_contact(home, cid)["fn"] == "Ada Lovelace"


# ------------------------------------------------------------------ daemon routes the edit UI uses
def test_daemon_schema_and_resolve_field_routes():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)

        sch = json.loads(server.route("GET", "/api/schema", {}, home)[2])
        assert {f["field_id"] for f in sch["fields"]} >= {"photo", "tags", "notes"}

        status, _, _ = server.route("POST", "/api/resolve-field", {}, home,
                                    {"contact_id": cid, "field": "org", "value": "Analytical Engines"})
        assert status == 200
        c = json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])
        org = next(f for f in c["fields"] if f["name"] == "org")
        assert org["values"][0]["value"] == "Analytical Engines"

        # a missing field is a clean 400, not a 500
        assert server.route("POST", "/api/resolve-field", {}, home, {"contact_id": cid})[0] == 400


def test_edit_contact_is_atomic_and_one_undo_reverts_everything():
    """The edit-mode save: many changes (source overrides + a custom value) apply as ONE changeset, so a
    single Undo reverts the whole edit. (Also why the UI never fires N concurrent writes that would
    collide on the file-lock.)"""
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        status, _, _ = server.route("POST", "/api/edit-contact", {}, home, {
            "contact_id": cid,
            "resolutions": [{"field": "fn", "value": "Ada L."}, {"field": "org", "value": "Engines"}],
            "values": [{"field_id": "notes", "value": "a note"}],
        })
        assert status == 200
        c = json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])
        assert c["fn"] == "Ada L."
        assert next(f for f in c["fields"] if f["name"] == "org")["values"][0]["value"] == "Engines"
        assert next(f for f in c["fields"] if f["name"] == "notes")["values"][0]["value"] == "a note"

        apply.undo(home)                                          # ONE undo reverses the whole edit
        c = json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])
        assert c["fn"] == "Ada Lovelace" and not any(f["name"] == "notes" for f in c["fields"])


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
