#!/usr/bin/env python3
"""Access log (Phase 1 of External Access visibility): every MCP read PRM serves appends one JSONL line —
which contact, the per-request-review decision, overlay field NAMES (never values — INV-1), and the
serving build label (so a stale server is detectable — prm#65). Bounded like the snapshot ring.

    python tests/unit/test_access_log.py
    pytest tests/unit/test_access_log.py
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
from core import access_log, apply, disclosure, relationships_db  # noqa: E402
from mcp_servers import tools  # noqa: E402


def _home(tmp: str):
    """A home with one contact (Ada) carrying a shareable ``project`` + a sealed ``notes``, a cloud grant
    active (review OFF by default) — the P4 gate is live."""
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    apply.set_field_value(home, cid, "notes", "sealed note")
    apply.define_field(home, label="Project", kind="text", disclosure_tier="private-shareable-on-consent")
    apply.set_field_value(home, cid, "project", "PRM")
    apply.set_disclosure_mode(home, disclosure.CLOUD_EXCEPTION)
    return home, cid


# --------------------------------------------------------------------------- unit: file mechanics
def test_append_recent_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        for i in range(3):
            assert access_log.append(home, {"ts": f"t{i}", "contact_id": f"c{i}"})
        assert [e["ts"] for e in access_log.recent(home, n=10)] == ["t2", "t1", "t0"]


def test_last_read_overall_and_by_contact():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        access_log.append(home, {"ts": "t0", "contact_id": "a"})
        access_log.append(home, {"ts": "t1", "contact_id": "b"})
        access_log.append(home, {"ts": "t2", "contact_id": "a"})
        assert access_log.last_read(home)["ts"] == "t2"
        assert access_log.last_read(home, "a")["ts"] == "t2"
        assert access_log.last_read(home, "b")["ts"] == "t1"
        assert access_log.last_read(home, "zzz") is None


def test_recent_empty_and_corrupt_tolerant():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        assert access_log.recent(home) == []
        home.access_log.parent.mkdir(parents=True, exist_ok=True)
        home.access_log.write_text('{"ts":"ok"}\nnot json\n\n{"ts":"ok2"}\n', encoding="utf-8")
        assert [e["ts"] for e in access_log.recent(home)] == ["ok2", "ok"]   # torn line skipped


def test_trim_bounds_the_file():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        keep = (access_log._MAX_BYTES, access_log._KEEP_BYTES)
        access_log._MAX_BYTES, access_log._KEEP_BYTES = 2000, 1000
        try:
            for i in range(400):
                access_log.append(home, {"ts": f"t{i:04d}", "pad": "x" * 40})
            assert home.access_log.stat().st_size <= access_log._MAX_BYTES   # bounded
            assert access_log.recent(home, n=5)[0]["ts"] == "t0399"          # newest survives
            for ln in home.access_log.read_text().splitlines():             # every survivor still parses
                json.loads(ln)
        finally:
            access_log._MAX_BYTES, access_log._KEEP_BYTES = keep


# --------------------------------------------------------------------------- integration: served reads
def test_served_read_logs_withheld_then_released():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.set_review(home, True)
        tools.get_contact(home, cid)                                  # review on, not approved → withheld
        e = access_log.last_read(home)
        assert e["tool"] == "get_contact" and e["contact_id"] == cid
        assert e["decision"] == "withheld" and "project" in e["withheld_overlay"]
        assert e["review_on"] is True and e["approved"] is False
        assert e["build"]                                              # the serving build is stamped
        assert "notes" not in e["withheld_overlay"] and "notes" not in e["returned_overlay"]  # sealed: never named

        apply.approve_read(home, cid)
        tools.get_contact(home, cid)                                  # approved → released
        e = access_log.last_read(home)
        assert e["decision"] == "released" and "project" in e["returned_overlay"] and e["approved"] is True


def test_review_off_logs_released():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)                                        # cloud-exception, review OFF
        tools.get_provenance(home, cid)
        e = access_log.last_read(home)
        assert e["tool"] == "get_provenance" and e["decision"] == "released"
        assert "project" in e["returned_overlay"]


def test_pna_read_logs_no_shareable():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.set_disclosure_mode(home, disclosure.PNA)              # query-layer floor withholds the overlay
        tools.get_contact(home, cid)
        e = access_log.last_read(home)
        assert e["decision"] == "no-shareable" and e["returned_overlay"] == [] and e["mode"] == "pna"


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
