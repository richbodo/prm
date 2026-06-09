#!/usr/bin/env python3
"""End-to-end dedup/merge scenario through the daemon ``route()`` — the bulk-approve happy path in one
test, replacing most of the manual workspace click-through.

Flow: import a multi-source corpus → detect candidates → an AI *stages* a proposal → bulk-approve one
batch that mixes (a) the AI proposal, (b) a candidate whose single-valued conflict is resolved **inline**,
and (c) two overlapping fuzzy pairs (the second must be **skipped**, never chain-merged) → assert the
batch is one atomic apply, the inline resolution beat the survivorship default, the skip was reported, and
the proposal was marked applied → a single **Undo** reverses the whole batch.

    python tests/e2e/test_dedup_flow.py
    pytest tests/e2e/test_dedup_flow.py
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
from core import audit, proposals  # noqa: E402
from daemon import server  # noqa: E402
from mcp_servers import tools as mcp_tools  # noqa: E402


def _body(resp) -> dict:
    return json.loads(resp[2])


def _total(home) -> int:
    return _body(server.route("GET", "/api/contacts", {}, home))["total"]


def _seed(tmp: str):
    """7 records across 5 sources: an Ada confident pair (no conflict), a Grace confident pair (an `fn`
    conflict), and three name-only `Sam Lee` records that form overlapping fuzzy pairs."""
    home = resolve_home(Path(tmp) / "h")
    recs = [
        ("apple_icloud",   "FN:Ada Lovelace\r\nEMAIL:ada@x.org"),
        ("google_takeout", "FN:Ada Lovelace\r\nEMAIL:ada@x.org"),
        ("apple_icloud",   "FN:Grace Hopper\r\nEMAIL:grace@y.org"),
        ("google_csv",     "FN:Grace B. Hopper\r\nEMAIL:grace@y.org"),
        ("facebook",       "FN:Sam Lee"),
        ("linkedin",       "FN:Sam Lee"),
        ("google_takeout", "FN:Sam Lee"),
    ]
    for i, (src, body) in enumerate(recs):
        v = Path(tmp) / f"r{i}.vcf"
        v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\n{body}\r\nEND:VCARD\r\n", encoding="utf-8")
        ingest_mod.ingest([v], source=src, home=home, dry_run=False)
    return home


def test_bulk_flow_scenario():
    with tempfile.TemporaryDirectory() as tmp:
        home = _seed(tmp)
        assert _total(home) == 7

        clusters = _body(server.route("GET", "/api/candidates", {}, home))["clusters"]
        ada   = next(c for c in clusters if c["tier"] == "confident" and any("Ada" in m["name"] for m in c["members"]))
        grace = next(c for c in clusters if c["tier"] == "confident" and any("Grace" in m["name"] for m in c["members"]))
        fuzzy = [c for c in clusters if c["tier"] == "fuzzy"]
        assert len(fuzzy) >= 2                                   # the three Sam Lee pairs overlap pairwise

        # the AI stages a proposal for the Ada cluster — propose-only, nothing applied yet
        pid = mcp_tools.submit_merge_proposal(home, ada["member_ids"], ada["into"],
                                              rationale="same exact email across two sources")["proposal_id"]
        assert _total(home) == 7                                # a proposal only stages; no merge yet

        # one bulk batch: AI proposal + a candidate whose `fn` conflict we resolve inline to the *non*-default
        # value + two overlapping fuzzy pairs (the second must be skipped, never chain-merged)
        items = [
            {"kind": "proposal", "proposal_id": pid},
            {"kind": "candidate", "member_ids": grace["member_ids"], "into": grace["into"],
             "resolutions": [{"field": "fn", "chosen_value": "Grace B. Hopper",
                              "chosen_source": "google_csv", "rule": "user"}]},
            {"kind": "candidate", "member_ids": fuzzy[0]["member_ids"], "into": fuzzy[0]["into"]},
            {"kind": "candidate", "member_ids": fuzzy[1]["member_ids"], "into": fuzzy[1]["into"]},
        ]
        res = _body(server.route("POST", "/api/merge-batch", {}, home, {"items": items}))

        # atomic: 3 merges applied, 1 overlapping pair skipped, exactly ONE audit entry
        assert res["ok"] and res["merged"] == 3 and len(res["skipped"]) == 1
        log = audit.read(home)
        assert len(log) == 1 and log[-1]["kind"] == "apply"
        assert _total(home) == 4                                # 7 → 4 (Ada, Grace, one Sam pair; one Sam left over)

        # the inline resolution beat the survivorship default (Apple would otherwise have won "Grace Hopper")
        grace_contact = _body(server.route("GET", f"/api/contact/{grace['into']}", {}, home))
        assert grace_contact["fn"] == "Grace B. Hopper"
        # the constituent AI proposal is marked applied
        assert proposals.load(home, pid)["status"] == "applied"

        # a single Undo reverses the entire batch
        assert _body(server.route("POST", "/api/undo", {}, home, {}))["ok"]
        assert _total(home) == 7


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
