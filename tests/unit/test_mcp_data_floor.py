#!/usr/bin/env python3
"""The cloud-facing MCP data-floor (AC-MCP-C / PR-7 / enforced EX-H7) — P1 enforcement. A `private-sealed`
overlay field NEVER crosses the MCP surface; a `private-shareable-on-consent` field crosses ONLY after the
workspace records disclosure consent. The daemon (local workspace, audience="local") still sees everything.
Drives the pure tool bodies + the projection directly — no `mcp` SDK needed.

    python tests/unit/test_mcp_data_floor.py
    pytest tests/unit/test_mcp_data_floor.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, disclosure, projection, relationships_db  # noqa: E402
from mcp_servers import tools  # noqa: E402


def _home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    apply.set_field_value(home, cid, "notes", "private note")        # builtin `notes` is sealed by default
    apply.define_field(home, label="Project", kind="text", disclosure_tier="private-shareable-on-consent")
    apply.set_field_value(home, cid, "project", "PRM")
    return home, cid


def _names(contact) -> set:
    return {f["name"] for f in contact["fields"]}


def test_sealed_never_crosses_and_shareable_needs_consent():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)

        # default pna: neither the sealed nor the shareable overlay field crosses; shared fields still do
        mc = tools.get_contact(home, cid)
        names = _names(mc)
        assert "notes" not in names and "project" not in names
        assert {"fn", "email"} & names
        assert mc["disclosure"]["mode"] == disclosure.PNA and mc["disclosure"]["shareable_withheld"] >= 1

        # consent (local-ai): the shareable field crosses; the sealed one still never does
        apply.set_disclosure_mode(home, disclosure.LOCAL_AI)
        mc2 = tools.get_contact(home, cid)
        assert "project" in _names(mc2) and "notes" not in _names(mc2)
        assert mc2["disclosure"]["shareable_withheld"] == 0

        # return to pna: it is withheld again on the very next call (cross-process enforcement)
        apply.return_to_pna(home)
        assert "project" not in _names(tools.get_contact(home, cid))


def test_sealed_absent_from_mcp_projection_in_every_mode():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        for m in (disclosure.PNA, disclosure.LOCAL_AI, disclosure.CLOUD_EXCEPTION):
            apply.set_disclosure_mode(home, m)
            c = projection.get_contact(home, cid, audience="mcp")
            assert not any(f["name"] == "notes" for f in c["fields"]), f"sealed field leaked in mode {m}"


def test_local_audience_is_unaffected_by_the_floor():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        names = {f["name"] for f in projection.get_contact(home, cid)["fields"]}   # audience="local"
        assert "notes" in names and "project" in names


def test_get_provenance_respects_the_floor():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        prov = tools.get_provenance(home, cid)
        assert not any(f["name"] in ("notes", "project") for f in prov["fields"])
        apply.set_disclosure_mode(home, disclosure.CLOUD_EXCEPTION)
        prov2 = tools.get_provenance(home, cid)
        assert any(f["name"] == "project" for f in prov2["fields"])
        assert not any(f["name"] == "notes" for f in prov2["fields"])


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
