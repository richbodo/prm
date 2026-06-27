#!/usr/bin/env python3
"""R11c daemon surface: POST /api/observation {obs_id, action in promote|unpromote|reject}.

The workspace's promote/un-promote/reject buttons call this one route (driven here through the pure
``route()``); observations themselves are filed by the MCP ``observe_contact_field`` tool, so the test
seeds one via ``core.apply`` and then drives the route. The route owns shape + existence checks; the
audited one-deep mechanics are covered in ``test_observations_promote.py``.

    python tests/unit/test_daemon_observations_api.py
    pytest tests/unit/test_daemon_observations_api.py
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
from core import apply, projection, relationships_db  # noqa: E402
from daemon import server  # noqa: E402


def _body(resp) -> dict:
    return json.loads(resp[2])


def _home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    return home, cid


def _obs(home, cid, field="tel", value="+1-555-0100"):
    return apply.observe_field(home, cid, field, value, written_by="ai:test", source="web")["obs_id"]


def _post(home, body):
    return server.route("POST", "/api/observation", {}, home, body)


def test_promote_then_unpromote_route():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _obs(home, cid)

        status, _, _ = _post(home, {"obs_id": oid, "action": "promote"})
        assert status == 200
        c = _body(server.route("GET", f"/api/contact/{cid}", {}, home))
        assert any(f["name"] == "tel" for f in c["fields"]) and not c["suggestions"]

        status, _, _ = _post(home, {"obs_id": oid, "action": "unpromote"})
        assert status == 200
        c = _body(server.route("GET", f"/api/contact/{cid}", {}, home))
        assert not any(f["name"] == "tel" for f in c["fields"]) and c["suggestions"]


def test_reject_route():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _obs(home, cid)
        status, _, body = _post(home, {"obs_id": oid, "action": "reject"})
        assert status == 200 and json.loads(body)["ok"]
        assert relationships_db.get_observation(home.relationships_db, oid)["status"] == "rejected"


def test_reject_accepted_is_a_clean_400():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _obs(home, cid)
        apply.promote_observation(home, oid)
        status, _, body = _post(home, {"obs_id": oid, "action": "reject"})
        assert status == 400 and "un-promote" in json.loads(body)["error"]


def test_bad_action_and_missing_obs():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _obs(home, cid)
        assert _post(home, {"obs_id": oid, "action": "frobnicate"})[0] == 400
        assert _post(home, {"action": "promote"})[0] == 400          # missing obs_id
        assert _post(home, {"obs_id": "nope", "action": "promote"})[0] == 404


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
