#!/usr/bin/env python3
"""Current Access Exceptions (Phase 2 of External Access visibility): the itemized per-contact approval
list (name · shareable fields · approved-when · last-read) and per-item **Revoke** — the inverse of
approve_read that drops ONE approval while leaving the others and the mode intact. Sealed fields are never
listed. Drives the daemon router (no socket) + the apply path.

    python tests/unit/test_disclosure_exceptions.py
    pytest tests/unit/test_disclosure_exceptions.py
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
from core import apply, disclosure  # noqa: E402
from daemon import server  # noqa: E402
from mcp_servers import tools  # noqa: E402


def _home(tmp: str):
    """Two contacts (Ada, Charles), each with a shareable ``project`` + a sealed ``notes``; a cloud grant
    active and per-request review ON."""
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Charles Babbage\r\nEMAIL:charles@x.org\r\nEND:VCARD\r\n",
        encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    ada = tools.search_contacts(home, "Ada")["results"][0]["id"]
    charles = tools.search_contacts(home, "Charles")["results"][0]["id"]
    apply.define_field(home, label="Project", kind="text", disclosure_tier="private-shareable-on-consent")
    for cid in (ada, charles):
        apply.set_field_value(home, cid, "project", "PRM")
        apply.set_field_value(home, cid, "notes", "sealed")
    apply.set_disclosure_mode(home, disclosure.CLOUD_EXCEPTION)
    apply.set_review(home, True)
    return home, ada, charles


def _exceptions(home):
    s, _, b = server.route("GET", "/api/disclosure/exceptions", {}, home)
    assert s == 200
    return json.loads(b)["approvals"]


def test_revoke_keeps_other_approvals_and_mode():
    with tempfile.TemporaryDirectory() as tmp:
        home, ada, charles = _home(tmp)
        apply.approve_read(home, ada)
        apply.approve_read(home, charles)
        assert set(disclosure.approvals(home)) == {ada, charles}

        apply.revoke_read(home, ada)
        assert disclosure.approvals(home) == [charles]                 # only Ada dropped
        assert disclosure.mode(home) == disclosure.CLOUD_EXCEPTION      # mode intact
        assert disclosure.review_required(home) is True                 # review intact
        # the next MCP read of Ada re-stages; Charles still releases
        assert "project" not in {f["name"] for f in tools.get_contact(home, ada)["fields"]}
        assert "project" in {f["name"] for f in tools.get_contact(home, charles)["fields"]}


def test_exceptions_endpoint_enriches_and_hides_sealed():
    with tempfile.TemporaryDirectory() as tmp:
        home, ada, charles = _home(tmp)
        assert _exceptions(home) == []                                  # nothing approved yet
        apply.approve_read(home, ada)
        tools.get_contact(home, ada)                                    # a served read → sets last-read

        rows = _exceptions(home)
        assert len(rows) == 1
        row = rows[0]
        assert row["contact_id"] == ada and row["name"] == "Ada Lovelace"
        assert row["fields"] == ["project"]                            # sealed 'notes' never listed
        assert row["approved_at"] and row["last_read_at"]              # both timestamps resolved


def test_revoke_endpoint():
    with tempfile.TemporaryDirectory() as tmp:
        home, ada, charles = _home(tmp)
        apply.approve_read(home, ada)
        s, _, b = server.route("POST", "/api/disclosure/revoke", {}, home, {"contact_id": ada})
        assert s == 200 and json.loads(b)["approvals"] == []
        assert disclosure.approvals(home) == []
        assert server.route("POST", "/api/disclosure/revoke", {}, home, {})[0] == 400   # needs contact_id


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
