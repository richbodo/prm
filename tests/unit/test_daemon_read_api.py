#!/usr/bin/env python3
"""Tests for the v0.1 M2 read-only daemon: the shared.db read facade, the pure ``route()`` for every
endpoint + error case, jCard rendering, and one real-socket smoke test through ``make_server``.

Drives the library directly (the payoff of the pure-``route()`` design); the socket test binds an
ephemeral port so it is hermetic. Runs as a plain script or under pytest.

    python tests/unit/test_daemon_read_api.py
    pytest tests/unit/test_daemon_read_api.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import shared_db  # noqa: E402
from daemon import server  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"


def _seeded_home(tmp: str):
    """A PRM home with the 4-contact Apple fixture loaded into shared.db."""
    home = resolve_home(Path(tmp) / "h")
    report = ingest_mod.ingest([APPLE], home=home, dry_run=False)
    assert report.persisted and report.stored == 4
    return home


def _body(resp) -> dict:
    status, ctype, body = resp
    assert "application/json" in ctype
    return json.loads(body)


# ------------------------------------------------------------------ read facade (shared_db)
def test_list_records_paginates_and_totals():
    with tempfile.TemporaryDirectory() as tmp:
        db = _seeded_home(tmp).shared_db
        page = shared_db.list_records(db, limit=2, offset=0)
        assert page["total"] == 4 and len(page["records"]) == 2
        assert all({"id", "name", "email", "org", "source"} <= r.keys() for r in page["records"])
        # offset walks the same name-ordered sequence without overlap
        rest = shared_db.list_records(db, limit=10, offset=2)
        assert len(rest["records"]) == 2
        ids = {r["id"] for r in page["records"]} | {r["id"] for r in rest["records"]}
        assert len(ids) == 4


def test_get_record_returns_jcard_and_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        db = _seeded_home(tmp).shared_db
        an_id = shared_db.list_records(db, limit=1)["records"][0]["id"]
        rec = shared_db.get_record(db, an_id)
        assert rec["id"] == an_id and rec["source"] == "apple_icloud"
        assert json.loads(rec["raw_jcard"])[0] == "vcard"        # stored jCard, unparsed (leaf discipline)
        assert isinstance(rec["provenance"], list) and rec["provenance"]
        assert shared_db.get_record(db, "nope") is None


# ------------------------------------------------------------------ pure router
def test_route_status_before_and_after_import():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        before = _body(server.route("GET", "/api/status", {}, home))
        assert before["shared_db"] is False
        _seeded_home(tmp)  # creates h/shared.db at the same home root
        after = _body(server.route("GET", "/api/status", {}, home))
        assert after["shared_db"] is True and after["records"] == 4


def test_route_search_and_contacts_and_detail():
    with tempfile.TemporaryDirectory() as tmp:
        home = _seeded_home(tmp)
        listing = _body(server.route("GET", "/api/contacts", {"limit": ["50"]}, home))
        assert listing["total"] == 4
        an_id = listing["records"][0]["id"]

        detail = _body(server.route("GET", f"/api/contact/{an_id}", {}, home))
        assert detail["id"] == an_id and "fields" in detail and "provenance" in detail
        assert all({"name", "params", "values"} <= f.keys() for f in detail["fields"])

        # search a token that exists in the fixture's names
        name = next((r["name"] for r in listing["records"] if r["name"]), "")
        token = name.split()[0] if name else "a"
        found = _body(server.route("GET", "/api/search", {"q": [token]}, home))
        assert found["query"] == token and isinstance(found["results"], list)


def test_route_errors():
    with tempfile.TemporaryDirectory() as tmp:
        home = _seeded_home(tmp)
        assert server.route("POST", "/api/status", {}, home)[0] == 405          # read-only surface
        assert server.route("GET", "/api/contact/missing", {}, home)[0] == 404
        assert server.route("GET", "/api/nope", {}, home)[0] == 404
    with tempfile.TemporaryDirectory() as tmp:
        empty = resolve_home(Path(tmp) / "h")          # no shared.db
        assert server.route("GET", "/api/contacts", {}, empty)[0] == 409


# ------------------------------------------------------------------ real socket smoke test
def test_make_server_serves_over_socket():
    with tempfile.TemporaryDirectory() as tmp:
        home = _seeded_home(tmp)
        httpd = server.make_server(home, port=0)            # ephemeral port
        host, port = httpd.server_address
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://{host}:{port}"
            with urllib.request.urlopen(f"{base}/api/contacts", timeout=5) as r:
                assert r.status == 200 and json.loads(r.read())["total"] == 4
            with urllib.request.urlopen(f"{base}/", timeout=5) as r:        # static shell
                assert r.status == 200 and b"PRM" in r.read()
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=5)


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
