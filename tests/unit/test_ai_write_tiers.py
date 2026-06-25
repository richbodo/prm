#!/usr/bin/env python3
"""Tests for R11a — the AI value-write tiers (AC-PRM-E), the core library slice.

`apply.write_field_value` dispatches by the field's `ai_write_policy`:
- **review-required** (default) stages a proposal and writes NOTHING directly (the structural INV-11 test);
- **append-only** appends a provenance-stamped row, NON-destructively (a human value survives), idempotent
  on a re-found value;
- **free-write** sets directly.
Plus the bounds: a per-`ai:<session>` quota (INV-13) and a per-value size cap, both for AI authors only.

    python tests/unit/test_ai_write_tiers.py
    pytest tests/unit/test_ai_write_tiers.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import apply, proposals, relationships_db  # noqa: E402


def _home_with_contact(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n",
                 encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home, list(relationships_db.identity_map(home.relationships_db))[0]


def _values(home, cid, fid):
    return [v["value"] for v in relationships_db.field_values_for(home.relationships_db, cid)
            if v["field_id"] == fid]


# ------------------------------------------------------------------ review-required: stage, never write
def test_review_required_stages_and_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        fid = apply.define_field(home, label="Notes2", kind="long_text")["field_id"]  # default policy
        res = apply.write_field_value(home, cid, fid, "AI says hi", written_by="ai:s1")

        assert res["status"] == "staged" and res["proposal_id"]            # staged, not applied
        assert _values(home, cid, fid) == []                              # INV-11: nothing written directly
        assert relationships_db.count_ai_writes(home.relationships_db, "ai:s1") == 0
        staged = proposals.load(home, res["proposal_id"])
        assert staged["status"] == "pending" and staged["operations"][0]["op"] == "set_field_value"


# ------------------------------------------------------------------ append-only: non-destructive + attributed
def test_append_only_appends_without_destroying_human_value():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        fid = apply.define_field(home, label="Employer", kind="text", ai_write_policy="append-only")["field_id"]
        apply.set_field_value(home, cid, fid, "Acme (manual)")            # the human value (single-valued set)

        res = apply.write_field_value(home, cid, fid, "Globex (AI)", written_by="ai:s1", source="web")
        assert res["status"] == "appended"
        assert set(_values(home, cid, fid)) == {"Acme (manual)", "Globex (AI)"}   # human value survived
        assert relationships_db.count_ai_writes(home.relationships_db, "ai:s1") == 1


def test_append_only_is_idempotent_on_a_refound_value():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        fid = apply.define_field(home, label="Site", kind="url", ai_write_policy="append-only")["field_id"]
        apply.write_field_value(home, cid, fid, "https://x.test", written_by="ai:s1")
        apply.write_field_value(home, cid, fid, "https://x.test", written_by="ai:s1")   # same value again
        assert _values(home, cid, fid) == ["https://x.test"]             # one row — re-find is a no-op
        assert relationships_db.count_ai_writes(home.relationships_db, "ai:s1") == 1


# ------------------------------------------------------------------ free-write: direct set
def test_free_write_sets_directly():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        fid = apply.define_field(home, label="Scratch", kind="text", ai_write_policy="free-write")["field_id"]
        res = apply.write_field_value(home, cid, fid, "anything", written_by="ai:s1")
        assert res["status"] == "set" and _values(home, cid, fid) == ["anything"]


# ------------------------------------------------------------------ INV-13 quota bound on direct AI writes
def test_quota_refuses_past_the_cap():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        relationships_db.set_setting(home.relationships_db, apply.AI_WRITE_QUOTA_KEY, "2")
        fid = apply.define_field(home, label="Findings", kind="text", ai_write_policy="append-only")["field_id"]
        apply.write_field_value(home, cid, fid, "a", written_by="ai:s1")   # 1 — ok
        apply.write_field_value(home, cid, fid, "b", written_by="ai:s1")   # 2 — ok
        try:
            apply.write_field_value(home, cid, fid, "c", written_by="ai:s1")   # 3 — over cap
            raise AssertionError("expected WritePolicyError past the quota")
        except apply.WritePolicyError:
            pass
        assert set(_values(home, cid, fid)) == {"a", "b"}                # the third never landed
        # the quota is per-session — a different session still has its own budget
        assert apply.write_field_value(home, cid, fid, "d", written_by="ai:s2")["status"] == "appended"


# ------------------------------------------------------------------ size cap on an AI write
def test_size_cap_refuses_oversize_ai_value():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        fid = apply.define_field(home, label="Blob", kind="long_text", ai_write_policy="free-write")["field_id"]
        big = "x" * (apply.MAX_AI_VALUE_BYTES + 1)
        try:
            apply.write_field_value(home, cid, fid, big, written_by="ai:s1")
            raise AssertionError("expected WritePolicyError for an oversize value")
        except apply.WritePolicyError:
            pass
        assert _values(home, cid, fid) == []


# ------------------------------------------------------------------ unknown field is refused
def test_unknown_field_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        try:
            apply.write_field_value(home, cid, "no_such_field", "v", written_by="ai:s1")
            raise AssertionError("expected WritePolicyError for an unknown field")
        except apply.WritePolicyError:
            pass


# ------------------------------------------------------------------ R11b: observations (canonical enrichment)
def test_observe_files_a_pending_observation_idempotently():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        res = apply.observe_field(home, cid, "tel", "+1-555-0100", written_by="ai:s1", source="web")
        assert res["status"] == "observed" and res["new"] is True
        obs = relationships_db.observations_for(home.relationships_db, cid)
        assert len(obs) == 1 and obs[0]["field"] == "tel" and obs[0]["status"] == "observed"
        assert obs[0]["written_by"] == "ai:s1"
        again = apply.observe_field(home, cid, "tel", "+1-555-0100", written_by="ai:s1", source="web")
        assert again["new"] is False                                       # re-find from same source = no-op
        assert len(relationships_db.observations_for(home.relationships_db, cid)) == 1


def test_observe_does_not_touch_the_canonical_view():
    # An observation is a suggestion: it must NOT become a field value or a resolution (no promote path).
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        apply.observe_field(home, cid, "org", "Globex", written_by="ai:s1")
        assert relationships_db.field_values_for(home.relationships_db, cid) == []     # not a field value
        assert relationships_db.field_resolutions(home.relationships_db) == {}         # not a resolution


def test_observe_rejects_non_enrichable_field():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        for bad in ("fn", "photo", "made_up"):                             # identity fields + junk are refused
            try:
                apply.observe_field(home, cid, bad, "x", written_by="ai:s1")
                raise AssertionError(f"expected WritePolicyError for {bad!r}")
            except apply.WritePolicyError:
                pass


def test_observe_quota_and_size_cap():
    with tempfile.TemporaryDirectory() as tmp:
        home, cid = _home_with_contact(tmp)
        relationships_db.set_setting(home.relationships_db, apply.AI_WRITE_QUOTA_KEY, "2")
        apply.observe_field(home, cid, "tel", "a", written_by="ai:s1")     # 1
        apply.observe_field(home, cid, "tel", "b", written_by="ai:s1")     # 2
        try:
            apply.observe_field(home, cid, "tel", "c", written_by="ai:s1")  # 3 — over the per-session cap
            raise AssertionError("expected the observation quota to refuse")
        except apply.WritePolicyError:
            pass
        big = "x" * (apply.MAX_AI_VALUE_BYTES + 1)
        try:
            apply.observe_field(home, cid, "email", big, written_by="ai:s2")
            raise AssertionError("expected the size cap to refuse")
        except apply.WritePolicyError:
            pass


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
