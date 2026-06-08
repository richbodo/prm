#!/usr/bin/env python3
"""Tests for v0.1 M5 — opt-in, non-destructive re-import (cli.ingest.reimport): the added / updated /
unchanged / stale diff, INV-6 (merge decisions survive re-import), the merges-with-stale heads-up, and
apply wiring (stale records kept, snapshot + audit written).

    python tests/unit/test_reimport.py
    pytest tests/unit/test_reimport.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, audit, private_db, projection, shared_db, snapshots  # noqa: E402

SRC = "apple_icloud"


def _vcf(*records: str) -> str:
    return "".join(f"BEGIN:VCARD\r\nVERSION:3.0\r\n{r}\r\nEND:VCARD\r\n" for r in records)


def _write(tmp: str, name: str, text: str) -> Path:
    p = Path(tmp) / name
    p.write_text(text, encoding="utf-8")
    return p


def _home(tmp: str, vcf: str):
    home = resolve_home(Path(tmp) / "h")
    ingest_mod.ingest([_write(tmp, "in.vcf", vcf)], source=SRC, home=home, dry_run=False)
    return home


# ------------------------------------------------------------------ diff
def test_reimport_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        vcf = _vcf("FN:Ann Lee\r\nEMAIL:ann@x.com", "FN:Bo Tan\r\nEMAIL:bo@x.com")
        home = _home(tmp, vcf)
        t = ingest_mod.reimport([_write(tmp, "again.vcf", vcf)], source=SRC, home=home, apply=False).totals()
        assert (t["unchanged"], t["added"], t["updated"], t["stale"]) == (2, 0, 0, 0)


def test_reimport_added_updated_stale():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp, _vcf("FN:Ann Lee\r\nEMAIL:ann@x.com", "FN:Bo Tan\r\nEMAIL:bo@x.com"))
        # Ann gains an ORG (same email → same id → updated); Bo is gone (stale); Cy is new (added).
        new = _vcf("FN:Ann Lee\r\nEMAIL:ann@x.com\r\nORG:Acme", "FN:Cy Ng\r\nEMAIL:cy@x.com")
        t = ingest_mod.reimport([_write(tmp, "new.vcf", new)], source=SRC, home=home, apply=False).totals()
        assert (t["added"], t["updated"], t["stale"], t["unchanged"]) == (1, 1, 1, 0)


# ------------------------------------------------------------------ apply (non-destructive)
def test_reimport_apply_keeps_stale_and_snapshots():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp, _vcf("FN:Ann\r\nEMAIL:ann@x.com", "FN:Bo\r\nEMAIL:bo@x.com"))
        before = shared_db.stats(home.shared_db)["records"]
        new = _vcf("FN:Ann\r\nEMAIL:ann@x.com", "FN:Cy\r\nEMAIL:cy@x.com")   # Bo stale, Cy added
        report = ingest_mod.reimport([_write(tmp, "new.vcf", new)], source=SRC, home=home, apply=True)
        assert report.applied and report.snapshot
        assert shared_db.stats(home.shared_db)["records"] == before + 1     # Cy added; Bo KEPT (non-destructive)
        assert snapshots.list_snapshots(home)                              # pre-apply snapshot
        assert audit.read(home)[-1]["kind"] == "reimport"                  # audited


# ------------------------------------------------------------------ INV-6 + stale-merge heads-up
def _merged_home(tmp: str):
    home = _home(tmp, _vcf("FN:Robert\r\nEMAIL:r@x.com", "FN:Bob\r\nEMAIL:bob@y.com"))
    a, b = list(private_db.identity_map(home.private_db))[:2]
    apply.apply_changeset(home, apply.build_merge_changeset(home, [a, b], a, created_by="manual:test"))
    assert projection.list_contacts(home)["total"] == 1
    return home


def test_reimport_preserves_merges():
    with tempfile.TemporaryDirectory() as tmp:
        home = _merged_home(tmp)
        same = _vcf("FN:Robert\r\nEMAIL:r@x.com", "FN:Bob\r\nEMAIL:bob@y.com")
        ingest_mod.reimport([_write(tmp, "again.vcf", same)], source=SRC, home=home, apply=True)
        assert projection.list_contacts(home)["total"] == 1                # merge survived re-import (INV-6)


def test_reimport_flags_merges_with_stale():
    with tempfile.TemporaryDirectory() as tmp:
        home = _merged_home(tmp)
        new = _vcf("FN:Robert\r\nEMAIL:r@x.com")                            # bob@y.com omitted → stale
        t = ingest_mod.reimport([_write(tmp, "new.vcf", new)], source=SRC, home=home, apply=False).totals()
        assert t["stale"] == 1 and t["merges_with_stale"] == 1


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
