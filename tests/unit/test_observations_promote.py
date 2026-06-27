#!/usr/bin/env python3
"""R11c promotion — promote / un-promote / reject a gathered observation, with one-deep retention.

The workspace act that turns a pending AI observation into a value the user sees (and reverses it). There
is **no MCP promote path** (INV-11), so this is exercised through ``core.apply`` (the audited, file-locked,
snapshotted writer the daemon route is a thin wrapper over) and read back through ``core.projection``:

  - **multi-valued** (tel/email): promote unions the value in; un-promote drops it from the union.
  - **single-valued** (org/title): promote writes a user ``field_resolution`` that beats survivorship;
    un-promote drops it (restoring the one-deep previous, else the source value).
  - **one-deep retention**: a second single-valued promotion supersedes the first and keeps exactly one
    previous; an older superseded is gone.
  - **reject** dismisses a pending suggestion (won't resurface); rejecting an *accepted* one is refused.

    python tests/unit/test_observations_promote.py
    pytest tests/unit/test_observations_promote.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, audit, projection, relationships_db  # noqa: E402


def _home(tmp: str, *, vcard: str | None = None):
    """Ingest one contact (FN + EMAIL by default) and return (home, contact_id)."""
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text(vcard or "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    cid = list(relationships_db.identity_map(home.relationships_db))[0]
    return home, cid


def _observe(home, cid, field, value, *, source="web"):
    """File an observation and return its obs_id."""
    return apply.observe_field(home, cid, field, value, written_by="ai:test", source=source)["obs_id"]


def _field(contact, name):
    return next((f for f in contact["fields"] if f["name"] == name), None)


def _status(home, obs_id):
    return relationships_db.get_observation(home.relationships_db, obs_id)["status"]


# --------------------------------------------------------------------------- multi-valued
def test_promote_multi_valued_unions_in():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _observe(home, cid, "tel", "+1-555-0100")
        assert projection.get_contact(home, cid)["suggestions"], "filed observation is a pending suggestion"

        apply.promote_observation(home, oid)
        c = projection.get_contact(home, cid)
        tel = _field(c, "tel")
        assert tel and tel["kind"] == "union" and "+1-555-0100" in {v["value"] for v in tel["values"]}
        assert not c["suggestions"], "a promoted observation leaves the suggestions block"
        assert _status(home, oid) == "accepted"


def test_unpromote_multi_valued_drops_from_union():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _observe(home, cid, "tel", "+1-555-0100")
        apply.promote_observation(home, oid)
        apply.unpromote_observation(home, oid)

        c = projection.get_contact(home, cid)
        assert _field(c, "tel") is None, "un-promoted tel leaves the canonical field"
        assert [s for s in c["suggestions"] if s["obs_id"] == oid], "and returns to the suggestions block"
        assert _status(home, oid) == "observed"


# --------------------------------------------------------------------------- single-valued
def test_promote_single_valued_writes_resolution_and_wins():
    vcard = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\n"
             "ORG:RealCorp\r\nEND:VCARD\r\n")
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp, vcard=vcard)
        # before promotion an accepted org observation only *competes* and loses to the real source
        oid = _observe(home, cid, "org", "GuessCorp")
        assert _field(projection.get_contact(home, cid), "org")["values"][0]["value"] == "RealCorp"

        apply.promote_observation(home, oid)                     # promotion writes the user resolution → it wins
        org = _field(projection.get_contact(home, cid), "org")
        assert org["kind"] == "single" and org["values"][0]["value"] == "GuessCorp"


def test_unpromote_single_valued_falls_back_to_source():
    vcard = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\n"
             "ORG:RealCorp\r\nEND:VCARD\r\n")
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp, vcard=vcard)
        oid = _observe(home, cid, "org", "GuessCorp")
        apply.promote_observation(home, oid)
        apply.unpromote_observation(home, oid)                   # no previous → the source value re-appears

        assert _field(projection.get_contact(home, cid), "org")["values"][0]["value"] == "RealCorp"
        assert _status(home, oid) == "observed"


# --------------------------------------------------------------------------- one-deep retention
def test_one_deep_retention_supersedes_and_prunes():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)                                   # name-only org (no source value)
        a = _observe(home, cid, "org", "Acme")
        b = _observe(home, cid, "org", "Beta")
        d = _observe(home, cid, "org", "Delta")

        apply.promote_observation(home, a)
        apply.promote_observation(home, b)                       # a → superseded (the one-deep previous)
        assert _status(home, a) == "superseded" and _status(home, b) == "accepted"

        apply.promote_observation(home, d)                       # b → superseded; a pruned (older previous gone)
        assert _status(home, d) == "accepted" and _status(home, b) == "superseded"
        assert relationships_db.get_observation(home.relationships_db, a) is None, "older superseded is pruned"
        assert _field(projection.get_contact(home, cid), "org")["values"][0]["value"] == "Delta"


def test_unpromote_restores_one_deep_previous():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        a = _observe(home, cid, "org", "Acme")
        b = _observe(home, cid, "org", "Beta")
        apply.promote_observation(home, a)
        apply.promote_observation(home, b)

        apply.unpromote_observation(home, b)                     # revert one step → Acme returns, not the source
        assert _field(projection.get_contact(home, cid), "org")["values"][0]["value"] == "Acme"
        assert _status(home, a) == "accepted" and _status(home, b) == "observed"


# --------------------------------------------------------------------------- reject
def test_reject_pending_dismisses_and_stays_dismissed():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _observe(home, cid, "tel", "+1-555-0100")
        apply.reject_observation(home, oid)

        assert not projection.get_contact(home, cid)["suggestions"], "a rejected suggestion is gone"
        assert _status(home, oid) == "rejected"
        # the AI re-finds the same value (same obs_id) → idempotent no-op, stays rejected (won't resurface)
        again = _observe(home, cid, "tel", "+1-555-0100")
        assert again == oid and _status(home, oid) == "rejected"
        assert not projection.get_contact(home, cid)["suggestions"]


def test_reject_accepted_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _observe(home, cid, "tel", "+1-555-0100")
        apply.promote_observation(home, oid)
        try:
            apply.reject_observation(home, oid)
        except ValueError:
            pass
        else:
            raise AssertionError("rejecting an accepted observation must be refused (un-promote first)")
        assert _status(home, oid) == "accepted", "the refused reject left the observation untouched"


# --------------------------------------------------------------------------- list discoverability marker
def test_list_has_suggestions_flag_tracks_pending():
    """The contacts list flags a contact with pending gathered data (the 🤖 row marker) — true only while
    an observation is pending, false again once it's accepted or rejected."""
    def row(home, cid):
        return next(r for r in projection.list_contacts(home)["records"] if r["id"] == cid)
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        assert row(home, cid)["has_suggestions"] is False
        oid = _observe(home, cid, "tel", "+1-555-0100")
        assert row(home, cid)["has_suggestions"] is True
        apply.promote_observation(home, oid)                     # accepted → no longer pending
        assert row(home, cid)["has_suggestions"] is False


# --------------------------------------------------------------------------- audit
def test_promote_and_unpromote_are_audited():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home(tmp)
        oid = _observe(home, cid, "tel", "+1-555-0100")
        apply.promote_observation(home, oid)
        apply.unpromote_observation(home, oid)
        kinds = [e.get("kind") for e in audit.read(home)]
        assert "promote_observation" in kinds and "unpromote_observation" in kinds


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
