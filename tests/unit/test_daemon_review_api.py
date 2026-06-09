#!/usr/bin/env python3
"""Tests for the v0.1 M3d review API: the daemon's candidate + write surface (GET /api/candidates,
POST /api/merge | /api/reject | /api/undo) driven through the pure ``route()`` — the daemon as the
private-data writer (apply path under the file-lock).

    python tests/unit/test_daemon_review_api.py
    pytest tests/unit/test_daemon_review_api.py
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
from core import candidates, proposals  # noqa: E402
from daemon import server  # noqa: E402
from mcp_servers import tools as mcp_tools  # noqa: E402  (pure tools, no SDK import)


def _body(resp) -> dict:
    status, ctype, body = resp
    return json.loads(body)


def _dup_home(tmp: str):
    """Two cross-source records sharing an email → two contacts, one confident duplicate."""
    home = resolve_home(Path(tmp) / "h")
    for name, src in (("Robert Smith", "apple_icloud"), ("Bob Smith", "google_takeout")):
        v = Path(tmp) / f"{src}.vcf"
        v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
        ingest_mod.ingest([v], source=src, home=home, dry_run=False)
    return home


def _confident(home):
    clusters = _body(server.route("GET", "/api/candidates", {}, home))["clusters"]
    return next(c for c in clusters if c["tier"] == "confident")


def test_candidates_route():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        assert len(cl["member_ids"]) == 2 and "email_exact" in cl["signals"]


def test_merge_route_applies():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        into = cl["member_ids"][0]
        status, _, body = server.route("POST", "/api/merge", {}, home,
                                       {"member_ids": cl["member_ids"], "into": into})
        assert status == 200 and json.loads(body)["ok"]
        # the two contacts are now one; no confident candidates remain
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 1
        detail = _body(server.route("GET", f"/api/contact/{into}", {}, home))
        assert detail["member_count"] == 2
        assert not [c for c in _body(server.route("GET", "/api/candidates", {}, home))["clusters"] if c["tier"] == "confident"]


def test_merge_preview_route():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        pv = _body(server.route("GET", "/api/merge-preview", {"ids": [",".join(cl["member_ids"])]}, home))
        assert "fields" in pv and "fn" in pv["conflicts"]            # Robert vs Bob → reviewer picks
        assert server.route("GET", "/api/merge-preview", {"ids": ["onlyone"]}, home)[0] == 400


def test_merge_route_validates():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        # `into` not among member_ids → 400
        assert server.route("POST", "/api/merge", {}, home, {"member_ids": cl["member_ids"], "into": "nope"})[0] == 400
        assert server.route("POST", "/api/merge", {}, home, {})[0] == 400


def test_reject_route_excludes():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        key = _confident(home)["key"]
        assert _body(server.route("POST", "/api/reject", {}, home, {"key": key}))["ok"]
        assert key not in {c["key"] for c in _body(server.route("GET", "/api/candidates", {}, home))["clusters"]}
        assert server.route("POST", "/api/reject", {}, home, {})[0] == 400      # missing key


def test_undo_route_restores():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        server.route("POST", "/api/merge", {}, home, {"member_ids": cl["member_ids"], "into": cl["member_ids"][0]})
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 1
        assert _body(server.route("POST", "/api/undo", {}, home, {}))["ok"]
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 2   # merge reversed


def _stage_ai_proposal(home):
    cl = _confident(home)
    return mcp_tools.submit_merge_proposal(home, cl["member_ids"], cl["member_ids"][0], rationale="same email")["proposal_id"]


def test_proposals_route():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        pid = _stage_ai_proposal(home)
        props = _body(server.route("GET", "/api/proposals", {}, home))["proposals"]
        assert any(p["proposal_id"] == pid and p["created_by"] == "ai:local-dedup" and p["member_ids"] for p in props)


def test_apply_proposal_route():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        pid = _stage_ai_proposal(home)
        assert _body(server.route("POST", "/api/apply-proposal", {}, home, {"proposal_id": pid}))["ok"]
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 1      # the AI's merge applied
        assert not _body(server.route("GET", "/api/proposals", {}, home))["proposals"]   # no longer pending
        assert server.route("POST", "/api/apply-proposal", {}, home, {"proposal_id": "nope"})[0] == 404


def test_dismiss_proposal_route():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        pid = _stage_ai_proposal(home)
        assert _body(server.route("POST", "/api/dismiss-proposal", {}, home, {"proposal_id": pid}))["ok"]
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 2       # NOT merged
        assert not _body(server.route("GET", "/api/proposals", {}, home))["proposals"]    # dismissed
        # dismissing the proposal also suppresses the underlying deterministic candidate
        assert not [c for c in _body(server.route("GET", "/api/candidates", {}, home))["clusters"] if c["tier"] == "confident"]


# ------------------------------------------------------------------ bulk approve (/api/merge-batch)
def _two_pairs_home(tmp: str):
    """4 records → 2 confident duplicate pairs (each pair shares an exact email across two sources)."""
    home = resolve_home(Path(tmp) / "h")
    recs = [("Ada Lovelace", "apple_icloud", "ada@x.org"), ("Ada Lovelace", "google_takeout", "ada@x.org"),
            ("Alan Turing", "apple_icloud", "alan@y.uk"), ("Alan Turing", "google_csv", "alan@y.uk")]
    for i, (name, src, email) in enumerate(recs):
        v = Path(tmp) / f"{src}-{i}.vcf"
        v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:{email}\r\nEND:VCARD\r\n", encoding="utf-8")
        ingest_mod.ingest([v], source=src, home=home, dry_run=False)
    return home


def _confident_clusters(home):
    return [c for c in _body(server.route("GET", "/api/candidates", {}, home))["clusters"] if c["tier"] == "confident"]


def test_candidates_route_enriched():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        assert cl["into"] in cl["member_ids"]            # recommended survivor for bulk
        assert "fn" in cl["conflicts"]                   # Robert vs Bob → reviewer must pick


def test_proposals_route_has_cluster_key():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        pid = _stage_ai_proposal(home)
        p = next(p for p in _body(server.route("GET", "/api/proposals", {}, home))["proposals"] if p["proposal_id"] == pid)
        assert p["cluster_key"] == candidates.cluster_key(p["member_ids"])   # same hash the SPA dedups against


def test_merge_batch_applies_all_and_one_undo():
    with tempfile.TemporaryDirectory() as tmp:
        home = _two_pairs_home(tmp)
        conf = _confident_clusters(home)
        assert len(conf) == 2
        items = [{"kind": "candidate", "member_ids": c["member_ids"], "into": c["into"]} for c in conf]
        res = _body(server.route("POST", "/api/merge-batch", {}, home, {"items": items}))
        assert res["ok"] and res["merged"] == 2
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 2
        assert _body(server.route("POST", "/api/undo", {}, home, {}))["ok"]      # one undo reverses the batch
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 4


def test_merge_batch_equals_sequential_merges():
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
        hb, hs = _two_pairs_home(t1), _two_pairs_home(t2)
        server.route("POST", "/api/merge-batch", {}, hb,                        # one batch
                     {"items": [{"kind": "candidate", "member_ids": c["member_ids"], "into": c["into"]} for c in _confident_clusters(hb)]})
        for c in _confident_clusters(hs):                                       # N single merges
            server.route("POST", "/api/merge", {}, hs, {"member_ids": c["member_ids"], "into": c["into"]})
        names_b = sorted(r["name"] for r in _body(server.route("GET", "/api/contacts", {}, hb))["records"])
        names_s = sorted(r["name"] for r in _body(server.route("GET", "/api/contacts", {}, hs))["records"])
        assert names_b == names_s                                               # same final projection


def test_merge_batch_validates():
    with tempfile.TemporaryDirectory() as tmp:
        home = _two_pairs_home(tmp)
        assert server.route("POST", "/api/merge-batch", {}, home, {"items": []})[0] == 400          # empty
        cl = _confident(home)
        assert server.route("POST", "/api/merge-batch", {}, home,
                            {"items": [{"kind": "candidate", "member_ids": cl["member_ids"], "into": "nope"}]})[0] == 400
        assert server.route("POST", "/api/merge-batch", {}, home,
                            {"items": [{"kind": "proposal", "proposal_id": "nope"}]})[0] == 404
        assert server.route("POST", "/api/merge-batch", {}, home,
                            {"items": [{"kind": "what"}]})[0] == 400


def test_merge_batch_rejects_non_pending_proposal():
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        pid = _stage_ai_proposal(home)
        proposals.set_status(home, pid, "applied")                              # already applied
        assert server.route("POST", "/api/merge-batch", {}, home,
                            {"items": [{"kind": "proposal", "proposal_id": pid}]})[0] == 409


def test_candidates_route_recommends_authoritative_survivor():
    # a cross-source confident cluster — the recommended `into` is the more authoritative source
    # (Apple > LinkedIn by source priority), not the arbitrary first-by-id member.
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        for src in ("linkedin", "apple_icloud"):           # linkedin loaded first, on purpose
            v = Path(tmp) / f"{src}.vcf"
            v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nEND:VCARD\r\n", encoding="utf-8")
            ingest_mod.ingest([v], source=src, home=home, dry_run=False)
        cl = _confident(home)
        apple_id = next(m["id"] for m in cl["members"] if m["source"] == "apple_icloud")
        assert cl["into"] == apple_id


def test_merge_batch_collapses_proposal_and_candidate():
    # a pending proposal AND its detected candidate (identical member-set) in one batch → applied once,
    # with the proposal winning (the backend safety-net for the SPA's cluster_key dedup).
    with tempfile.TemporaryDirectory() as tmp:
        home = _dup_home(tmp)
        cl = _confident(home)
        pid = _stage_ai_proposal(home)
        items = [{"kind": "candidate", "member_ids": cl["member_ids"], "into": cl["member_ids"][0]},
                 {"kind": "proposal", "proposal_id": pid}]
        res = _body(server.route("POST", "/api/merge-batch", {}, home, {"items": items}))
        assert res["ok"] and res["merged"] == 1                                 # deduped → one merge, not two
        assert _body(server.route("GET", "/api/contacts", {}, home))["total"] == 1
        assert proposals.load(home, pid)["status"] == "applied"                 # the proposal beat its candidate


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
