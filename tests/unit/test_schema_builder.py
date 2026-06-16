#!/usr/bin/env python3
"""Tests for the v0.2 R6 schema builder: defining a user custom field (it appears in the schema and
becomes settable/visible on a contact), removing one (its values cascade and the whole removal is
reversible by Undo, because it snapshots first), the built-in lock, and input validation.

    python tests/unit/test_schema_builder.py
    pytest tests/unit/test_schema_builder.py
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
from core import apply, projection, relationships_db, schema  # noqa: E402
from daemon import server  # noqa: E402


def _home_with_contact(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home, list(relationships_db.identity_map(home.relationships_db))[0]


def _has_field(home, cid, fid):
    return any(f["name"] == fid for f in projection.get_contact(home, cid)["fields"])


# ------------------------------------------------------------------ define → appears + editable
def test_define_field_appears_in_schema_and_on_contact():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        status, _, body = server.route("POST", "/api/define-field", {}, home,
                                       {"label": "How We Met", "kind": "date"})
        assert status == 200
        fid = json.loads(body)["field"]["field_id"]
        assert fid == "how_we_met"
        sch = json.loads(server.route("GET", "/api/schema", {}, home)[2])
        assert any(f["field_id"] == fid and f["class"] == "user" for f in sch["fields"])
        # a value on the new field shows on the contact (the edit form renders editable kinds generically)
        server.route("POST", "/api/set-value", {}, home, {"contact_id": cid, "field_id": fid, "value": "2020-01-01"})
        c = json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])
        assert next(f for f in c["fields"] if f["name"] == fid)["values"][0]["value"] == "2020-01-01"


# ------------------------------------------------------------------ remove cascades values + is reversible
def test_remove_user_field_cascades_values_and_undo_restores():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        fid = apply.define_field(home, label="Project", kind="text")["field_id"]
        apply.set_field_value(home, cid, fid, "PRM")
        assert _has_field(home, cid, fid)

        apply.remove_field(home, fid)                            # snapshots first (destructive)
        assert schema.get_field(home.relationships_db, fid) is None
        assert not _has_field(home, cid, fid)                    # the value cascaded away with the definition

        apply.undo(home)                                         # restore the pre-remove snapshot
        assert schema.get_field(home.relationships_db, fid) is not None
        assert _has_field(home, cid, fid)                        # field AND its value are back


# ------------------------------------------------------------------ built-in lock + validation
def test_builtin_cannot_be_removed_and_define_validates():
    with tempfile.TemporaryDirectory() as tmp:
        home, _ = _home_with_contact(tmp)
        assert server.route("POST", "/api/remove-field", {}, home, {"field_id": "tags"})[0] == 400
        assert schema.get_field(home.relationships_db, "tags") is not None
        # define-field validation: missing kind, and an unknown kind, are clean 400s
        assert server.route("POST", "/api/define-field", {}, home, {"label": "X"})[0] == 400
        assert server.route("POST", "/api/define-field", {}, home, {"label": "X", "kind": "rainbow"})[0] == 400


def test_define_select_field_with_options():
    with tempfile.TemporaryDirectory() as tmp:
        home, _ = _home_with_contact(tmp)
        f = apply.define_field(home, label="Closeness", kind="single_select",
                               config={"options": [{"value": "inner", "description": "core"}, "outer"]})
        opts = f["config"]["options"]
        assert {o["value"] for o in opts} == {"inner", "outer"}
        assert next(o for o in opts if o["value"] == "inner")["description"] == "core"


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
