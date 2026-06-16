#!/usr/bin/env python3
"""Tests for the v0.2 R4 tags layer: assigning/replacing tags on a contact atomically (the multi-valued
edit path), managing the tag vocabulary (the built-in `tags` field's options + descriptions), and tag
search — found by the LOCAL workspace search but NOT matchable over the (sealed) MCP search surface.

    python tests/unit/test_tags.py
    pytest tests/unit/test_tags.py
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
    return home, list(relationships_db.identity_map(home.relationships_db))[0]


def _tags(home, cid):
    f = next((f for f in projection.get_contact(home, cid)["fields"] if f["name"] == "tags"), None)
    return sorted(v["value"] for v in (f["values"] if f else []))


# ------------------------------------------------------------------ assign / replace tags (atomic, multi)
def test_assign_and_replace_tags():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.edit_contact(home, cid, multi=[{"field_id": "tags", "values": ["family", "mentor"]}])
        assert _tags(home, cid) == ["family", "mentor"]
        apply.edit_contact(home, cid, multi=[{"field_id": "tags", "values": ["mentor", "colleague"]}])
        assert _tags(home, cid) == ["colleague", "mentor"]       # replace-the-whole-set semantics
        apply.edit_contact(home, cid, multi=[{"field_id": "tags", "values": []}])
        assert _tags(home, cid) == []


def test_tags_and_note_save_atomically_then_one_undo():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.edit_contact(home, cid, resolutions=[{"field": "fn", "value": "Ada L."}],
                           values=[{"field_id": "notes", "value": "met at PyCon"}],
                           multi=[{"field_id": "tags", "values": ["friend"]}])
        c = projection.get_contact(home, cid)
        assert c["fn"] == "Ada L." and _tags(home, cid) == ["friend"]
        assert next(f for f in c["fields"] if f["name"] == "notes")["values"][0]["value"] == "met at PyCon"
        apply.undo(home)                                          # one Undo reverses the whole edit
        c = projection.get_contact(home, cid)
        assert c["fn"] == "Ada Lovelace" and _tags(home, cid) == []


# ------------------------------------------------------------------ the tag vocabulary
def test_update_tag_vocabulary_with_descriptions():
    with tempfile.TemporaryDirectory() as tmp:
        home, _ = _home_with_contact(tmp)
        apply.update_field(home, "tags", config={"options": [{"value": "family", "description": "kin"}, "work"]})
        opts = schema.get_field(home.relationships_db, "tags")["config"]["options"]
        assert {o["value"] for o in opts} == {"family", "work"}
        assert next(o for o in opts if o["value"] == "family")["description"] == "kin"
        # the daemon route + a clean 400 for a missing field_id
        assert server.route("POST", "/api/update-field", {}, home,
                            {"field_id": "tags", "config": {"options": [{"value": "vip"}]}})[0] == 200
        assert server.route("POST", "/api/update-field", {}, home, {})[0] == 400


# ------------------------------------------------------------------ search: local yes, MCP no (sealed)
def test_tag_search_is_local_only_not_over_mcp():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.edit_contact(home, cid, multi=[{"field_id": "tags", "values": ["sailing"]}])
        # the local workspace search finds the contact by its tag…
        res = json.loads(server.route("GET", "/api/search", {"q": ["sailing"]}, home)[2])
        assert any(r["id"] == cid for r in res["results"])
        # …but the MCP search surface does NOT match the sealed tag value (no leak by inference)
        assert not any(r["id"] == cid for r in tools.search_contacts(home, "sailing")["results"])


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
