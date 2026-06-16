#!/usr/bin/env python3
"""Tests for the v0.2 R2 relationship-value layer: setting/clearing values through the audited apply
path (single-valued replace, multi-valued add/remove), reversibility via Undo, the value→definition FK
lock through the op path, the projection composing relationship fields (with disclosure_tier), and the
MCP **seal** — private-sealed relationship fields are structurally dropped from the MCP read surface
while the daemon (local workspace) still sees them.

    python tests/unit/test_values.py
    pytest tests/unit/test_values.py
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
from mcp_servers import tools  # noqa: E402


def _home_with_contact(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    return home, cid


def _field(home, cid, name):
    return next((f for f in projection.get_contact(home, cid)["fields"] if f["name"] == name), None)


# ------------------------------------------------------------------ single-valued (notes): set + replace
def test_set_single_value_and_replace():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.set_field_value(home, cid, "notes", "met at PyCon")
        notes = _field(home, cid, "notes")
        assert notes["custom"] is True and notes["disclosure_tier"] == "private-sealed"
        assert [v["value"] for v in notes["values"]] == ["met at PyCon"]
        apply.set_field_value(home, cid, "notes", "old friend")          # single → replace, not append
        assert [v["value"] for v in _field(home, cid, "notes")["values"]] == ["old friend"]


# ------------------------------------------------------------------ multi-valued (tags): add + clear
def test_multi_value_add_and_clear():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.set_field_value(home, cid, "tags", "family")
        apply.set_field_value(home, cid, "tags", "mentor")
        assert sorted(v["value"] for v in _field(home, cid, "tags")["values"]) == ["family", "mentor"]
        apply.set_field_value(home, cid, "tags", "family")               # idempotent add (no duplicate)
        assert sorted(v["value"] for v in _field(home, cid, "tags")["values"]) == ["family", "mentor"]
        apply.clear_field_value(home, cid, "tags", "family")             # clear one entry
        assert [v["value"] for v in _field(home, cid, "tags")["values"]] == ["mentor"]
        apply.clear_field_value(home, cid, "tags")                       # clear the whole field
        assert _field(home, cid, "tags") is None


# ------------------------------------------------------------------ reversibility (Undo)
def test_value_write_is_reversible_by_undo():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.set_field_value(home, cid, "notes", "secret")
        assert _field(home, cid, "notes") is not None
        apply.undo(home)                                                 # restore the pre-write snapshot
        assert _field(home, cid, "notes") is None


# ------------------------------------------------------------------ the INV-3/4 FK lock via the op path
def test_set_value_for_undefined_field_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        try:
            apply.set_field_value(home, cid, "no_such_field", "x")
            raise AssertionError("expected a failure writing a value for an undefined field")
        except relationships_db.RelationshipsDbError:
            pass
        assert _field(home, cid, "no_such_field") is None


# ------------------------------------------------------------------ the MCP seal (data-floor / AC-MCP-C)
def test_mcp_seal_hides_sealed_fields_until_marked_shareable():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.set_field_value(home, cid, "notes", "private note")

        # the daemon (local workspace) sees the sealed relationship field…
        assert _field(home, cid, "notes") is not None
        # …the MCP surface does NOT (sealed by default), but shared source fields still cross
        mc = tools.get_contact(home, cid)
        assert not any(f["name"] == "notes" for f in mc["fields"])
        assert any(f["name"] in ("fn", "email") for f in mc["fields"])

        # mark the field shareable → it now crosses the MCP surface (enforced at the projection layer)
        schema.update_field(home.relationships_db, "notes", disclosure_tier="private-shareable-on-consent")
        assert any(f["name"] == "notes" for f in tools.get_contact(home, cid)["fields"])


# ------------------------------------------------------------------ daemon set/clear endpoints
def test_daemon_set_and_clear_value_endpoints():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        status, _, _ = server.route("POST", "/api/set-value", {}, home,
                                    {"contact_id": cid, "field_id": "notes", "value": "hi"})
        assert status == 200
        c = json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])
        assert any(f["name"] == "notes" for f in c["fields"])

        status, _, _ = server.route("POST", "/api/clear-value", {}, home,
                                    {"contact_id": cid, "field_id": "notes"})
        assert status == 200
        c = json.loads(server.route("GET", f"/api/contact/{cid}", {}, home)[2])
        assert not any(f["name"] == "notes" for f in c["fields"])

        # a missing field_id is a clean 400, not a 500
        assert server.route("POST", "/api/set-value", {}, home, {"contact_id": cid})[0] == 400


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
