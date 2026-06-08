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
from core import apply, audit, candidates, private_db, projection, proposals, snapshots  # noqa: E402

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
        a, b = list(private_db.identity_map(home.private_db))[:2]

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
        a = list(private_db.identity_map(home.private_db))[0]
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
        assert key in private_db.rejected_pairs(home.private_db)
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
        private_db.record_decision(home.private_db, "k", "not_duplicate")
        assert private_db.rejected_pairs(home.private_db) == {"k"}
        snapshots.restore(home, snap)
        assert private_db.rejected_pairs(home.private_db) == set()


def test_audit_is_append_only_jsonl():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h").create()
        audit.append(home, {"kind": "apply", "n": 1})
        audit.append(home, {"kind": "undo", "n": 2})
        entries = audit.read(home)
        assert [e["n"] for e in entries] == [1, 2] and all("at" in e for e in entries)


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
