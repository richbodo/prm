#!/usr/bin/env python3
"""R11b projection fold — AI-gathered observations in the canonical-contact projection.

Pending (``observed``) observations surface as a local-only ``suggestions`` block and are NEVER folded
into a field; **accepted** observations enrich the canonical field (UNION adds a value; RECONCILE treats
it as a candidate a ``field_resolution`` can point at, and an AI source never wins survivorship alone);
a field that exists ONLY in the overlay (no source value) still surfaces. Drives the projection directly.

    python tests/unit/test_observations_fold.py
    pytest tests/unit/test_observations_fold.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, projection, relationships_db  # noqa: E402


def _home(tmp: str, *, vcard: str | None = None):
    """Ingest one contact (FN + EMAIL, no tel/org by default) and return (home, contact_id)."""
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text(vcard or "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    return home, cid


def _accept(home, obs_id: str) -> None:
    """Flip an observation to ``accepted`` in place — status only, deliberately NOT the real promote API
    (``apply.promote_observation``, R11c), which would also write a field_resolution. This isolates the
    projection's *fold* behavior (how an accepted observation competes) from the promotion *workflow*; the
    promote/un-promote/reject path is covered in ``test_observations_promote.py``."""
    con = relationships_db.connect(home.relationships_db)
    try:
        con.execute("UPDATE observations SET status='accepted' WHERE obs_id=?", (obs_id,))
        con.commit()
    finally:
        con.close()


def _field(contact, name):
    return next((f for f in contact["fields"] if f["name"] == name), None)


def test_pending_observation_surfaces_as_suggestion_not_a_field():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.observe_field(home, cid, "tel", "+1-555-0100", written_by="ai:test", source="web", confidence=0.8)

        c = projection.get_contact(home, cid)                      # audience="local"
        assert _field(c, "tel") is None, "a pending observation must not fold into a canonical field"
        sug = c["suggestions"]
        assert len(sug) == 1 and sug[0]["field"] == "tel" and sug[0]["value"] == "+1-555-0100"
        assert sug[0]["source"] == "web" and sug[0]["obs_id"] and sug[0]["confidence"] == 0.8


def test_accepted_multi_valued_observation_unions_in():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        res = apply.observe_field(home, cid, "tel", "+1-555-0100", written_by="ai:test", source="web")
        _accept(home, res["obs_id"])

        c = projection.get_contact(home, cid)
        tel = _field(c, "tel")
        assert tel and tel["kind"] == "union"
        vals = {v["value"] for v in tel["values"]}
        assert "+1-555-0100" in vals                               # the overlay-only field now surfaces
        assert any(v["source"] == "ai:test" for v in tel["values"])  # attributed to the AI session
        assert not c["suggestions"], "an accepted observation leaves the pending suggestions block"


def test_accepted_observation_loses_to_a_real_source_without_a_resolution():
    """RECONCILE field: an accepted observation is just another candidate — an AI source carries no
    source-priority rank, so the real (apple_icloud) value wins survivorship until the user promotes it
    via a field_resolution."""
    vcard = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\n"
             "ORG:RealCorp\r\nEND:VCARD\r\n")
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp, vcard=vcard)
        res = apply.observe_field(home, cid, "org", "GuessCorp", written_by="ai:test", source="web")
        _accept(home, res["obs_id"])

        org = _field(projection.get_contact(home, cid), "org")
        assert org and org["kind"] == "single" and org["values"][0]["value"] == "RealCorp"

        # the user promotes it: a field_resolution pointing at the gathered value now wins
        apply.resolve_field(home, cid, "org", "GuessCorp")
        org2 = _field(projection.get_contact(home, cid), "org")
        assert org2["values"][0]["value"] == "GuessCorp"


def test_overlay_only_reconcile_field_surfaces_with_no_source_value():
    """A name-only contact (no source org) gets an accepted org observation — the reconcile field must
    appear even though no source record ever carried it."""
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        res = apply.observe_field(home, cid, "org", "ACME", written_by="ai:test", source="github")
        _accept(home, res["obs_id"])

        c = projection.get_contact(home, cid)
        org = _field(c, "org")
        assert org and org["values"][0]["value"] == "ACME"
        assert c["org"] == "ACME"                                  # the top-level pick reflects it too


def test_resolution_only_field_surfaces_even_without_source_or_observation():
    """A user override (field_resolution) for a field the raw record never had must still surface
    (plan §5: union source ∪ accepted-observations ∪ resolutions)."""
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.resolve_field(home, cid, "title", "Countess of Lovelace")
        title = _field(projection.get_contact(home, cid), "title")
        assert title and title["values"][0]["value"] == "Countess of Lovelace"


def test_accepted_observation_is_exported():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        res = apply.observe_field(home, cid, "tel", "+1-555-0100", written_by="ai:test", source="web")

        cards = projection.export_jcards(home)
        props = cards[0][1]
        assert not any(p[0].lower() == "tel" for p in props), "a pending observation is not exported"

        _accept(home, res["obs_id"])
        props2 = projection.export_jcards(home)[0][1]
        tel = next((p for p in props2 if p[0].lower() == "tel"), None)
        assert tel and "+1-555-0100" in tel[3:]


def test_list_contacts_carries_no_observation_for_pending():
    """A pending observation must not change the summary row (no accidental fold via _all)."""
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        apply.observe_field(home, cid, "org", "ACME", written_by="ai:test", source="web")
        row = next(r for r in projection.list_contacts(home)["records"] if r["id"] == cid)
        assert row["org"] == "", "pending org observation must not leak into the list summary"


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
