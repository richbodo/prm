#!/usr/bin/env python3
"""Per-request disclosure review (P4): while a grant is active and review is on, the AI's read of a
contact's shareable fields is **staged for approval** instead of returned — the user approves/denies per
contact in the workspace, and an approved contact stays readable until the mode changes. Sealed never
crosses; shared contacts unaffected. Drives the pure tool bodies + the daemon router (no socket).

    python tests/unit/test_disclosure_review.py
    pytest tests/unit/test_disclosure_review.py
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
from core import apply, disclosure, relationships_db  # noqa: E402
from daemon import server  # noqa: E402
from mcp_servers import tools  # noqa: E402


def _home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    apply.set_field_value(home, cid, "notes", "sealed note")
    apply.define_field(home, label="Project", kind="text", disclosure_tier="private-shareable-on-consent")
    apply.set_field_value(home, cid, "project", "PRM")
    apply.set_disclosure_mode(home, disclosure.CLOUD_EXCEPTION)   # a grant is active
    return home, cid


def _names(c) -> set:
    return {f["name"] for f in c["fields"]}


def test_review_off_shareable_crosses():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        assert "project" in _names(tools.get_contact(home, cid))   # default: review off


def test_review_on_stages_request_and_withholds():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.set_review(home, True)
        c = tools.get_contact(home, cid)
        assert "project" not in _names(c) and "notes" not in _names(c)   # both withheld
        assert c["disclosure"]["review"] == "pending"
        reqs = disclosure.list_requests(home)
        assert len(reqs) == 1 and reqs[0]["contact_id"] == cid and "project" in reqs[0]["fields"]


def test_approve_releases_then_mode_change_resets():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.set_review(home, True)
        tools.get_contact(home, cid)                       # stages the request
        apply.approve_read(home, cid)
        c = tools.get_contact(home, cid)
        assert "project" in _names(c) and c["disclosure"]["review"] == "approved" and "notes" not in _names(c)
        assert disclosure.list_requests(home) == []        # request cleared on approve
        # a mode change resets the per-request session (approvals + requests)
        apply.set_disclosure_mode(home, disclosure.LOCAL_AI)
        assert disclosure.approvals(home) == []
        assert "project" not in _names(tools.get_contact(home, cid))   # must re-approve


def test_deny_keeps_withheld():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.set_review(home, True)
        tools.get_contact(home, cid)
        apply.deny_read(home, cid)
        assert disclosure.list_requests(home) == []
        assert "project" not in _names(tools.get_contact(home, cid))   # still withheld; the read re-stages
        assert len(disclosure.list_requests(home)) == 1


def test_daemon_review_endpoints():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        s, _, b = server.route("POST", "/api/disclosure/review", {}, home, {"enabled": True})
        assert s == 200 and json.loads(b)["review"] is True

        tools.get_contact(home, cid)                       # an AI read stages a request
        s, _, b = server.route("GET", "/api/disclosure/requests", {}, home)
        reqs = json.loads(b)["requests"]
        assert s == 200 and len(reqs) == 1 and reqs[0]["contact_id"] == cid

        st = json.loads(server.route("GET", "/api/disclosure", {}, home)[2])
        assert "review-pending" in st["banners"] and st["pending_requests"] == 1 and st["review"] is True

        s, _, b = server.route("POST", "/api/disclosure/requests", {}, home, {"contact_id": cid, "action": "approve"})
        assert s == 200 and json.loads(b)["requests"] == []
        assert "project" in _names(tools.get_contact(home, cid))
        assert server.route("POST", "/api/disclosure/requests", {}, home, {"contact_id": cid, "action": "x"})[0] == 400


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
