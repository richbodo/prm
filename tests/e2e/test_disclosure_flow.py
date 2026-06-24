#!/usr/bin/env python3
"""End-to-end EX-CLOUD-LLM data-floor lifecycle: the workspace records disclosure consent (the daemon
``route()``) and the **separate-process MCP read surface** honors it — sealed never crosses, shareable
crosses only with consent, return-to-PNA re-withholds on the next call. This is the cross-process story:
the consent WRITE (workspace) governs the read ENFORCEMENT (MCP).

    python tests/e2e/test_disclosure_flow.py
    pytest tests/e2e/test_disclosure_flow.py
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
from core import apply, relationships_db  # noqa: E402
from daemon import server  # noqa: E402
from mcp_servers import tools as mcp_tools  # noqa: E402


def _body(resp) -> dict:
    return json.loads(resp[2])


def _mcp_names(contact) -> set:
    return {f["name"] for f in contact["fields"]}


def _seed(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@x.org\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    apply.set_field_value(home, cid, "notes", "a sealed private note")            # sealed (the default tier)
    apply.define_field(home, label="Project", kind="text", disclosure_tier="private-shareable-on-consent")
    apply.set_field_value(home, cid, "project", "the Analytical Engine")          # shareable-on-consent
    return home, cid


def test_consent_gate_governs_the_mcp_floor_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _seed(tmp)

        # 1) default PNA: no banner; the MCP surface withholds BOTH overlay fields; the daemon sees all
        st = _body(server.route("GET", "/api/disclosure", {}, home))
        assert st["mode"] == "pna" and st["banners"] == [] and st["shareable_counts"]["values"] == 1
        mc = mcp_tools.get_contact(home, cid)
        assert "project" not in _mcp_names(mc) and "notes" not in _mcp_names(mc)
        assert mc["disclosure"]["shareable_withheld"] >= 1
        local = _body(server.route("GET", f"/api/contact/{cid}", {}, home))       # audience="local"
        assert {"project", "notes"} <= {f["name"] for f in local["fields"]}

        # 2) the user consents in the workspace (cloud) → banner raised; the MCP surface returns the
        #    shareable field — but NEVER the sealed one
        consented = _body(server.route("POST", "/api/disclosure/consent", {}, home, {"mode": "cloud-exception"}))
        assert "cloud-exception" in consented["banners"]
        mc2 = mcp_tools.get_contact(home, cid)
        assert "project" in _mcp_names(mc2) and "notes" not in _mcp_names(mc2)

        # 3) return to PNA → the very next MCP read withholds the shareable field again (cross-process)
        back = _body(server.route("POST", "/api/disclosure/return-to-pna", {}, home, {}))
        assert back["mode"] == "pna" and back["banners"] == []
        assert "project" not in _mcp_names(mcp_tools.get_contact(home, cid))


def test_sealed_field_never_crosses_in_any_mode():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _seed(tmp)
        for mode in ("pna", "local-ai", "cloud-exception"):
            if mode == "pna":
                server.route("POST", "/api/disclosure/return-to-pna", {}, home, {})
            else:
                server.route("POST", "/api/disclosure/consent", {}, home, {"mode": mode})
            assert "notes" not in _mcp_names(mcp_tools.get_contact(home, cid)), f"sealed leaked in {mode}"


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
