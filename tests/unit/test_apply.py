#!/usr/bin/env python3
"""Tests for the v0.1 M3c apply path: snapshot → run changeset → audit, plus dismissal and undo.
Covers the merge applier (projection reflects merges), resolve_field as field-level Update, reject →
re-detection exclusion, undo via snapshot-restore, changeset validation, and the snapshot/audit
primitives.

    python tests/unit/test_apply.py
    pytest tests/unit/test_apply.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, audit, candidates, relationships_db, projection, proposals, snapshots  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"


def _apple_home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    ingest_mod.ingest([APPLE], home=home, dry_run=False)   # 4 contacts, 1:1 baseline
    return home


# ------------------------------------------------------------------ merge + undo
def test_apply_merge_then_undo():
    with tempfile.TemporaryDirectory() as tmp:
        home = _apple_home(tmp)
        a, b = list(relationships_db.identity_map(home.relationships_db))[:2]

        cs = apply.build_merge_changeset(home, [a, b], a, created_by="manual:test", rationale="same person")
        assert any(op["op"] == "merge" and b in op["source_record_ids"] for op in cs["operations"])
        result = apply.apply_changeset(home, cs)

        assert projection.list_contacts(home)["total"] == 3          # 4 → 3
        assert projection.get_contact(home, a)["member_count"] == 2   # b folded into a
        assert projection.get_contact(home, b) is None
        assert result["snapshot"] and snapshots.list_snapshots(home)  # pre-apply snapshot kept
        assert audit.read(home)[-1]["kind"] == "apply"               # logged

        assert apply.undo(home)                                       # restore the snapshot
        assert projection.list_contacts(home)["total"] == 4          # merge reversed
        assert projection.get_contact(home, b) is not None


# ------------------------------------------------------------------ resolve_field == field-Update
def test_resolve_field_overrides_projection():
    with tempfile.TemporaryDirectory() as tmp:
        home = _apple_home(tmp)
        a = list(relationships_db.identity_map(home.relationships_db))[0]
        cs = proposals.build([proposals.resolve_field_op(a, "fn", "Edited Name", rule="user")],
                             created_by="manual:test")
        apply.apply_changeset(home, cs)
        assert projection.get_contact(home, a)["fn"] == "Edited Name"   # a manual edit is just a resolution


# ------------------------------------------------------------------ reject → exclusion
def test_reject_excludes_candidate():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        for name, src in (("Robert Smith", "apple_icloud"), ("Bob Smith", "google_takeout")):
            v = Path(tmp) / f"{src}.vcf"
            v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
            ingest_mod.ingest([v], source=src, home=home, dry_run=False)
        conf = [c for c in candidates.find_duplicate_candidates(home) if c["tier"] == "confident"]
        assert conf
        key = conf[0]["key"]
        apply.reject_cluster(home, key)
        assert key in relationships_db.rejected_pairs(home.relationships_db)
        assert key not in {c["key"] for c in candidates.find_duplicate_candidates(home)}   # gone


# ------------------------------------------------------------------ validation
def test_validate_rejects_bad_changesets():
    for bad in ({}, {"operations": []}, {"operations": [{"op": "nope"}], "created_by": "x"},
                {"operations": [{"op": "merge"}], "created_by": "x"}):
        try:
            proposals.validate(bad)
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass
    proposals.validate(proposals.build([proposals.merge_op(["s1"], "c1")], created_by="manual:test"))  # ok


# ------------------------------------------------------------------ primitives
def test_snapshot_and_restore():
    with tempfile.TemporaryDirectory() as tmp:
        home = _apple_home(tmp)
        snap = snapshots.snapshot(home)
        assert snap.exists() and snap in snapshots.list_snapshots(home)
        # mutate, then restore brings the baseline back
        relationships_db.record_decision(home.relationships_db, "k", "not_duplicate")
        assert relationships_db.rejected_pairs(home.relationships_db) == {"k"}
        snapshots.restore(home, snap)
        assert relationships_db.rejected_pairs(home.relationships_db) == set()


def test_audit_is_append_only_jsonl():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h").create()
        audit.append(home, {"kind": "apply", "n": 1})
        audit.append(home, {"kind": "undo", "n": 2})
        entries = audit.read(home)
        assert [e["n"] for e in entries] == [1, 2] and all("at" in e for e in entries)


# ------------------------------------------------------------------ bulk approve (apply_batch)
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


def test_apply_batch_atomic_single_undo():
    with tempfile.TemporaryDirectory() as tmp:
        home = _two_pairs_home(tmp)
        assert projection.list_contacts(home)["total"] == 4
        conf = [c for c in candidates.find_duplicate_candidates(home) if c["tier"] == "confident"]
        assert len(conf) == 2
        items = [{"kind": "candidate", "member_ids": c["member_ids"], "into": c["member_ids"][0]} for c in conf]

        res = apply.apply_batch(home, items)
        assert res["ok"] and res["merged"] == 2
        assert projection.list_contacts(home)["total"] == 2                  # both pairs merged
        assert len(snapshots.list_snapshots(home)) == 1                      # ONE snapshot for the batch
        log = audit.read(home)
        assert len(log) == 1 and log[-1]["kind"] == "apply"                  # ONE audit entry
        assert sum(1 for op in log[-1]["operations"] if op["op"] == "merge") == 2

        assert apply.undo(home)                                              # ONE undo reverses everything
        assert projection.list_contacts(home)["total"] == 4


def test_apply_batch_rejects_empty():
    with tempfile.TemporaryDirectory() as tmp:
        home = _two_pairs_home(tmp)
        try:
            apply.apply_batch(home, [])
            raise AssertionError("expected ValueError for an empty batch")
        except ValueError:
            pass


def test_apply_batch_skips_overlapping_items():
    with tempfile.TemporaryDirectory() as tmp:
        home = _two_pairs_home(tmp)
        a, b, c = list(relationships_db.identity_map(home.relationships_db))[:3]
        # two items chain on b → the second is skipped (not fatal); the first still merges
        res = apply.apply_batch(home, [{"kind": "candidate", "member_ids": [a, b], "into": a},
                                       {"kind": "candidate", "member_ids": [b, c], "into": b}])
        assert res["ok"] and res["merged"] == 1
        assert len(res["skipped"]) == 1                                   # the (b, c) item was skipped
        assert projection.get_contact(home, a)["member_count"] == 2       # a + b merged
        assert projection.get_contact(home, c) is not None                # c untouched — resurfaces next pass


def test_apply_batch_proposal_override_wins():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        for name, src in (("Robert Smith", "apple_icloud"), ("Bob Smith", "google_takeout")):
            v = Path(tmp) / f"{src}.vcf"
            v.write_text(f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
            ingest_mod.ingest([v], source=src, home=home, dry_run=False)
        a, b = list(relationships_db.identity_map(home.relationships_db))[:2]
        cs = apply.build_merge_changeset(home, [a, b], a, created_by="ai:local-dedup",
                                         resolutions=[{"field": "fn", "chosen_value": "Robert Smith", "chosen_source": "apple_icloud"}])
        pid = proposals.store(home, cs, status="pending")

        apply.apply_batch(home, [{"kind": "proposal", "proposal_id": pid,
                                  "resolutions": [{"field": "fn", "chosen_value": "Bob Smith", "chosen_source": "google_takeout", "rule": "user"}]}])
        assert projection.get_contact(home, a)["fn"] == "Bob Smith"          # inline override beats the AI's pick
        assert proposals.load(home, pid)["status"] == "applied"              # constituent proposal marked applied


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
