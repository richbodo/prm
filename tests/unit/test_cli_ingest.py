#!/usr/bin/env python3
"""Unit tests for the Phase-A CLI: config resolver, source inference, pure ingest, demo manifest.

Drives the library directly (no prompts, no process spawn) — the payoff of the pure-``ingest()``
design. Runs as a plain script or under pytest.

    python tests/unit/test_cli_ingest.py
    pytest tests/unit/test_cli_ingest.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli import prm_import  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from cli.parsers.base import SourceFormat  # noqa: E402
from cli.report import ImportReport  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"
LINKEDIN = FIX / "linkedin" / "sources" / "Connections.csv"
GOOGLE_CSV = FIX / "google_csv" / "sources" / "contacts.csv"
BASIC_TREE = FIX / "google_takeout" / "sources" / "takeout-basic"


# ------------------------------------------------------------------ config resolution
def test_resolve_home_precedence():
    assert resolve_home("/tmp/explicit", env={}, cwd="/work").root == Path("/tmp/explicit")
    assert resolve_home(None, env={"PRM_HOME": "/tmp/env"}, cwd="/work").root == Path("/tmp/env")
    assert resolve_home(None, env={}, cwd="/work").root == Path("/work/prm-data")


def test_home_layout_and_create():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h", env={}).create()
        assert home.exists() and home.proposals_dir.is_dir() and home.snapshots_dir.is_dir()
        assert home.shared_db == home.root / "shared.db"


# ------------------------------------------------------------------ source inference
def test_infer_source():
    assert ingest_mod.infer_source(APPLE, SourceFormat.VCARD) == ("apple_icloud", "high")
    assert ingest_mod.infer_source(LINKEDIN, SourceFormat.VENDOR_CSV) == ("linkedin", "high")
    assert ingest_mod.infer_source(GOOGLE_CSV, SourceFormat.VENDOR_CSV)[0] == "google_csv"


# ------------------------------------------------------------------ pure ingest
def test_ingest_apple_dry_run():
    report = ingest_mod.ingest([APPLE], dry_run=True)
    assert report.total == 4 and report.dry_run and not report.persisted
    assert report.by_source == {"apple_icloud": 4}
    assert report.by_key_kind.get("email", 0) >= 3


def test_ingest_takeout_zip_labels_google_takeout():
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "takeout.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in BASIC_TREE.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(BASIC_TREE).as_posix())
        report = ingest_mod.ingest([zip_path], dry_run=True)
    assert report.total == 3 and report.by_source == {"google_takeout": 3}


def test_csv_now_parses():
    [result] = ingest_mod.collect([LINKEDIN])
    assert result.fmt is SourceFormat.VENDOR_CSV
    assert len(result.contacts) >= 20 and result.skipped_reason is None


def test_source_override():
    [result] = ingest_mod.collect([APPLE], source="my_label")
    assert result.source == "my_label" and result.confidence == "override"


def test_ingest_persists_and_status_reads_back():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        report = ingest_mod.ingest([APPLE], home=home, dry_run=False)
        assert report.persisted and report.stored == 4
        # Both stores exist as separate files: import writes shared.db and seeds the private store's
        # 1:1 identity baseline (M3a). The two-store separation is exercised in test_private_store.py.
        assert home.shared_db.exists() and home.relationships_db.exists()
        # round-trips through the real status reader
        assert prm_import.main(["--data-dir", str(home.root), "status", "--json"]) == 0


def test_report_json_roundtrip():
    report = ingest_mod.ingest([APPLE], dry_run=True)
    data = json.loads(report.to_json())
    assert data["total"] == 4 and "by_key_kind" in data


# ------------------------------------------------------------------ demo manifest
def test_demo_manifest_resolves_and_vcard_entries_parse():
    entries = prm_import._demo_paths()
    assert entries, "demo manifest should list sources"
    for _, path in entries:
        assert path.exists(), f"demo source missing: {path}"
    results = [ingest_mod.parse_one(p, source) for source, p in entries]
    report = ImportReport.from_results(results, persisted=False, dry_run=True)
    assert report.by_source.get("google_takeout", 0) == 1000     # per-entry source honored
    assert report.total >= 1000


# ------------------------------------------------------------------ CLI main()
def test_cli_init_and_status_main():
    with tempfile.TemporaryDirectory() as tmp:
        assert prm_import.main(["--data-dir", tmp + "/h", "init"]) == 0
        assert (Path(tmp) / "h").is_dir()
        assert prm_import.main(["--data-dir", tmp + "/h", "status", "--json"]) == 0


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
