#!/usr/bin/env python3
"""Tests for the cloud-disclosure mode + read-side data-floor state (core/disclosure.py) — P1 of the
EX-CLOUD-LLM workspace handler. The mode governs whether the private-shareable-on-consent overlay
projection may cross the MCP surface; it lives in relationships.db settings, written via the audited apply
path (lock + audit) and read by both the daemon and the separate-process MCP servers.

    python tests/unit/test_disclosure.py
    pytest tests/unit/test_disclosure.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, disclosure, relationships_db  # noqa: E402


def _home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    return home, cid


def test_default_mode_is_pna():
    with tempfile.TemporaryDirectory() as tmp:
        home, _ = _home(tmp)
        st = disclosure.current(home)
        assert st["mode"] == disclosure.PNA
        assert not disclosure.discloses(st["mode"])


def test_mode_before_store_exists_defaults_pna():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "empty")          # no store on disk yet
        assert disclosure.current(home)["mode"] == disclosure.PNA


def test_set_mode_and_return_to_pna():
    with tempfile.TemporaryDirectory() as tmp:
        home, _ = _home(tmp)
        apply.set_disclosure_mode(home, disclosure.CLOUD_EXCEPTION)
        st = disclosure.current(home)
        assert st["mode"] == disclosure.CLOUD_EXCEPTION and st["consented_at"]
        assert disclosure.discloses(st["mode"])

        apply.return_to_pna(home)
        st2 = disclosure.current(home)
        assert st2["mode"] == disclosure.PNA and not st2["consented_at"]
        # history records both transitions (mode only — the data is not recalled)
        assert [h["mode"] for h in st2["history"]][-2:] == [disclosure.CLOUD_EXCEPTION, disclosure.PNA]


def test_unknown_mode_refused():
    with tempfile.TemporaryDirectory() as tmp:
        home, _ = _home(tmp)
        try:
            apply.set_disclosure_mode(home, "bogus")
            raise AssertionError("expected ValueError for an unknown disclosure mode")
        except ValueError:
            pass


def test_scope_and_scope_grew_drives_reconsent():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.define_field(home, label="Project", kind="text",
                           disclosure_tier="private-shareable-on-consent")
        apply.set_field_value(home, cid, "project", "PRM")

        scope = disclosure.shareable_scope(home)
        assert "project" in scope["field_ids"] and scope["contacts"] == 1 and scope["values"] >= 1

        apply.set_disclosure_mode(home, disclosure.LOCAL_AI)          # consent to the current scope
        assert disclosure.scope_grew(home) is False

        apply.define_field(home, label="Hobby", kind="text",         # a NEW shareable field after consent
                           disclosure_tier="private-shareable-on-consent")
        assert disclosure.scope_grew(home) is True                   # → re-consent needed

        apply.return_to_pna(home)
        assert disclosure.scope_grew(home) is False                  # never True in pna (nothing crosses)


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
