#!/usr/bin/env python3
"""Tests for the workspace import surface (D1): stage -> preview -> commit / cancel over the pure
``route()`` (no socket). Uploads are base64'd fixture bytes; the daemon stages them to a temp dir and
runs the same path-based ``cli.ingest`` the CLI uses. Mirrors the CLI's preview->confirm flow.

    python tests/unit/test_daemon_import_api.py
    pytest tests/unit/test_daemon_import_api.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import shared_db  # noqa: E402
from core.lock import file_lock  # noqa: E402
from daemon import server  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"


def _b64(path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _post(home, path, body):
    status, ctype, body_bytes = server.route("POST", path, {}, home, body)[:3]
    assert "application/json" in ctype
    return status, json.loads(body_bytes)


def _stage(home, relpath, data_b64, token=None):
    body = {"relpath": relpath, "data_base64": data_b64}
    if token:
        body["token"] = token
    return _post(home, "/api/import/stage", body)


# ------------------------------------------------------------------ the happy path (into an EMPTY home)
def test_stage_preview_commit_into_empty_home():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        assert not home.shared_db.exists()                   # the empty-state case import must serve

        status, out = _stage(home, "icloud-export.vcf", _b64(APPLE))
        assert status == 200 and out["staged"] == 1
        token = out["token"]

        # preview is a dry run — a report, nothing written
        status, pv = _post(home, "/api/import/preview", {"token": token})
        assert status == 200
        rep = pv["report"]
        assert rep["dry_run"] is True and rep["persisted"] is False
        assert rep["total"] == 4 and rep["stored"] is None
        assert rep["paths"][0]["source"] == "apple_icloud"   # inferred from the Apple PRODID
        assert not home.shared_db.exists()

        # commit persists + seeds identities, then cleans the staging dir
        status, cm = _post(home, "/api/import/commit", {"token": token})
        assert status == 200
        rep = cm["report"]
        assert rep["persisted"] is True and rep["dry_run"] is False and rep["stored"] == 4
        assert home.shared_db.exists() and shared_db.stats(home.shared_db)["records"] == 4
        assert not (home.imports_dir / token).exists()

        # and the contacts read back through the normal API
        gstatus, _, gbody = server.route("GET", "/api/contacts", {"limit": ["50"]}, home)
        assert gstatus == 200 and json.loads(gbody)["total"] == 4


# ------------------------------------------------------------------ source override (the CLI's --source)
def test_source_override_flows_through():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        _, out = _stage(home, "contacts.vcf", _b64(APPLE))
        _, pv = _post(home, "/api/import/preview", {"token": out["token"], "source": "google_takeout"})
        rep = pv["report"]
        assert "google_takeout" in rep["by_source"]
        assert rep["paths"][0]["source"] == "google_takeout" and rep["paths"][0]["confidence"] == "override"


# ------------------------------------------------------------------ honest skips (fail-loudly)
def test_unrecognized_file_is_reported_skipped_not_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        _, out = _stage(home, "notes.json", base64.b64encode(b'{"hello": 1}').decode("ascii"))
        _, pv = _post(home, "/api/import/preview", {"token": out["token"]})
        rep = pv["report"]
        assert rep["total"] == 0
        assert rep["paths"] and rep["paths"][0]["skipped_reason"]   # surfaced, not silently ignored


# ------------------------------------------------------------------ safety: traversal + bad token
def test_rejects_path_traversal_and_bad_token():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        assert _stage(home, "../evil.vcf", _b64(APPLE))[0] == 400
        assert _post(home, "/api/import/preview", {"token": "not-a-hex-token"})[0] == 400
        assert _post(home, "/api/import/preview", {"token": "abc"})[0] == 400        # hex but too short
        assert _post(home, "/api/import/commit", {"token": "deadbeefdeadbeef"})[0] == 404  # no such session


# ------------------------------------------------------------------ cancel cleans up
def test_cancel_removes_staging_dir():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        _, out = _stage(home, "c.vcf", _b64(APPLE))
        token = out["token"]
        assert (home.imports_dir / token).is_dir()
        status, _ = _post(home, "/api/import/cancel", {"token": token})
        assert status == 200 and not (home.imports_dir / token).exists()


# ------------------------------------------------------------------ lock contention → clean 409
def test_commit_under_lock_returns_409_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        _, out = _stage(home, "c.vcf", _b64(APPLE))
        token = out["token"]
        home.create()
        with file_lock(home.lock_file):                      # simulate a concurrent writer (a merge / CLI import)
            status, err = _post(home, "/api/import/commit", {"token": token})
        assert status == 409 and "writing" in err["error"]
        assert not home.shared_db.exists()                   # nothing persisted under contention
        assert (home.imports_dir / token).is_dir()           # staging kept so the user can retry


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
