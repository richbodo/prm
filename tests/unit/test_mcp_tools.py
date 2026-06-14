#!/usr/bin/env python3
"""Tests for the v0.1 M4 MCP tool bodies (mcp_servers/tools.py) — read tools and, crucially, the
**propose-only** invariant: ``submit_merge_proposal`` stages a changeset but never applies it
(INV-11 / AC-PRM-F). Drives the pure tool functions directly — no ``mcp`` SDK needed.

    python tests/unit/test_mcp_tools.py
    pytest tests/unit/test_mcp_tools.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, relationships_db, projection, proposals  # noqa: E402
from mcp_servers import tools  # noqa: E402


def _dup_home(tmp: str):
    """Two cross-source records sharing an email → two contacts, one confident duplicate."""
    home = resolve_home(Path(tmp) / "h")
    for name, src in (("Robert Smith", "apple_icloud"), ("Bob Smith", "google_takeout")):
        v = Path(tmp) / f"{src}.vcf"
        v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
        ingest_mod.ingest([v], source=src, home=home, dry_run=False)
    return home


# ------------------------------------------------------------------ read tools
def test_read_tools():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        page = tools.list_contacts(home)
        assert page["total"] == 2
        cid = page["records"][0]["id"]
        assert tools.get_contact(home, cid)["id"] == cid
        assert "fields" in tools.get_provenance(home, cid)
        assert tools.search_contacts(home, "smith")["results"]
        assert tools.find_duplicate_candidates(home)["clusters"]
        assert "error" in tools.get_contact(home, "nope")


# ------------------------------------------------------------------ propose-only (INV-11)
def test_submit_is_propose_only():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = tools.find_duplicate_candidates(home)["clusters"][0]
        before = relationships_db.stats(home.relationships_db)["contacts"]

        res = tools.submit_merge_proposal(home, cl["member_ids"], cl["member_ids"][0], rationale="same email")
        assert res["ok"] and res["status"] == "pending"
        # NOTHING applied: the private store is unchanged and the projection still shows two contacts
        assert relationships_db.stats(home.relationships_db)["contacts"] == before
        assert projection.list_contacts(home)["total"] == 2
        # the proposal is staged for the human, authored by the AI
        staged = tools.list_proposals(home)["proposals"]
        assert staged and staged[0]["proposal_id"] == res["proposal_id"]
        assert tools.get_proposal(home, res["proposal_id"])["created_by"] == "ai:local-dedup"


def test_proposal_then_human_applies():
    # The AI proposes; the human (workspace/apply path) is what actually merges.
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = tools.find_duplicate_candidates(home)["clusters"][0]
        res = tools.submit_merge_proposal(home, cl["member_ids"], cl["member_ids"][0])
        apply.apply_changeset(home, proposals.load(home, res["proposal_id"]))
        assert projection.list_contacts(home)["total"] == 1


def test_submit_validates_into():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        assert "error" in tools.submit_merge_proposal(home, ["a", "b"], "nope")


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
