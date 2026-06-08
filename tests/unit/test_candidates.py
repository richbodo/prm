#!/usr/bin/env python3
"""Tests for v0.1 M3b duplicate-candidate detection (core/candidates.py): normalization, the tiered
pair scoring, blocking + clustering with the transitive over-merge guard, rejection exclusion, and
end-to-end cross-source detection through the projection.

    python tests/unit/test_candidates.py
    pytest tests/unit/test_candidates.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from core import candidates  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"


def _contact(cid, fn, *, emails=(), phones=(), urls=(), org="", source="test"):
    """A projection-shaped canonical contact (what candidates.detect consumes)."""
    fields = []
    for nm, vals in (("email", emails), ("tel", phones), ("url", urls)):
        if vals:
            fields.append({"name": nm, "kind": "union", "values": [{"value": v, "source": source} for v in vals]})
    if org:
        fields.append({"name": "org", "kind": "single", "values": [{"value": org, "source": source}]})
    if fn:
        fields.append({"name": "fn", "kind": "single", "values": [{"value": fn, "source": source}]})
    return {"id": cid, "fn": fn, "email": emails[0] if emails else "", "org": org, "source": source, "fields": fields}


# ------------------------------------------------------------------ normalization
def test_normalize_email():
    assert candidates.normalize_email("Bob.Smith+news@Gmail.com") == "bobsmith@gmail.com"   # gmail dots+tag
    assert candidates.normalize_email("a+x@example.com") == "a@example.com"                  # non-gmail +tag
    assert candidates.normalize_email("J.Doe@Example.COM") == "j.doe@example.com"            # dots kept off-gmail
    assert candidates.normalize_email("not-an-email") == ""


def test_normalize_phone_and_name():
    assert candidates.normalize_phone("+1 (555) 010-0142") == "5550100142"
    assert candidates.normalize_phone("123") == ""                          # too short → no key
    assert candidates.name_tokens("Bob O'Brien") == ["robert", "obrien"]    # nickname expanded
    assert candidates.fold("José Ñoño") == "jose nono"                      # accent fold
    assert candidates.soundex("Smith") == candidates.soundex("Smyth")        # phonetic match


# ------------------------------------------------------------------ scoring tiers
def test_score_tiers():
    a = candidates._matchable(_contact("a", "Robert Smith", emails=["bob@x.com"], phones=["5550100"]))
    b = candidates._matchable(_contact("b", "Bob Smith", emails=["bob@x.com"], phones=["5550100"]))
    assert candidates.score(a, b)[0] == candidates.CONFIDENT                  # shared email → confident

    # shared phone, dissimilar names → strong (household phone, needs a look)
    c = candidates._matchable(_contact("c", "Ann Lee", phones=["5550199"]))
    d = candidates._matchable(_contact("d", "Zed Park", phones=["5550199"]))
    assert candidates.score(c, d)[0] == candidates.STRONG

    # shared email but different phones + unrelated names → conflict guard demotes to strong
    e = candidates._matchable(_contact("e", "Ann Lee", emails=["home@x.com"], phones=["5550111"]))
    f = candidates._matchable(_contact("f", "Zed Park", emails=["home@x.com"], phones=["5550222"]))
    assert candidates.score(e, f)[0] == candidates.STRONG and "shared_email_conflict" in candidates.score(e, f)[1]

    # name-only similarity → fuzzy (the Facebook case); never confident
    g = candidates._matchable(_contact("g", "Katherine Johnson"))
    h = candidates._matchable(_contact("h", "Katherine Johnston"))
    assert candidates.score(g, h)[0] == candidates.FUZZY

    # genuinely different people → not a candidate
    i = candidates._matchable(_contact("i", "Alan Turing", emails=["alan@x.com"]))
    j = candidates._matchable(_contact("j", "Grace Hopper", emails=["grace@y.com"]))
    assert candidates.score(i, j)[0] is None


# ------------------------------------------------------------------ clustering + guards
def test_detect_clusters_and_tiers():
    contacts = [
        _contact("a", "Robert Smith", emails=["bob@x.com"]),
        _contact("b", "Bob Smith", emails=["bob@x.com"]),          # a+b share email → confident pair
        _contact("c", "Katherine Johnson"),
        _contact("d", "Katherine Johnston"),                        # c+d name-only → fuzzy
        _contact("e", "Alan Turing", emails=["alan@x.com"]),        # distinct → no cluster
    ]
    clusters = candidates.detect(contacts)
    by_tier = {c["tier"]: c for c in clusters}
    assert by_tier["confident"]["member_ids"] == ["a", "b"]
    assert "email_exact" in by_tier["confident"]["signals"]
    assert set(by_tier["fuzzy"]["member_ids"]) == {"c", "d"}
    assert "e" not in {i for c in clusters for i in c["member_ids"]}


def test_transitive_overmerge_guard():
    # A–B share x, B–C share y (B bridges), but A and C are different people → flag the chain, don't
    # present it as a clean merge.
    bridged = [
        _contact("a", "Ann Smith", emails=["x@a.com"]),
        _contact("b", "Ann Smith", emails=["x@a.com", "y@b.com"]),
        _contact("c", "Zoe Jones", emails=["y@b.com"]),
    ]
    [cl] = candidates.detect(bridged)
    assert set(cl["member_ids"]) == {"a", "b", "c"} and cl["tier"] == "review" and "oversized" in cl["signals"]

    # same chain but all the same person → a clean confident cluster of 3
    clean = [
        _contact("a", "Ada Lovelace", emails=["x@a.com"]),
        _contact("b", "Ada Lovelace", emails=["x@a.com", "y@b.com"]),
        _contact("c", "Ada Lovelace", emails=["y@b.com"]),
    ]
    [cl2] = candidates.detect(clean)
    assert set(cl2["member_ids"]) == {"a", "b", "c"} and cl2["tier"] == "confident"


def test_rejected_pairs_excluded():
    contacts = [_contact("a", "Bob Smith", emails=["bob@x.com"]), _contact("b", "Robert Smith", emails=["bob@x.com"])]
    key = candidates.cluster_key(["a", "b"])
    assert candidates.detect(contacts)                              # found by default
    assert candidates.detect(contacts, rejected={key}) == []        # dismissed → gone


# ------------------------------------------------------------------ end to end (through projection)
def test_find_duplicate_candidates_cross_source():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        v1 = Path(tmp) / "a.vcf"
        v1.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Robert Smith\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
        v2 = Path(tmp) / "b.vcf"
        v2.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Bob Smith\r\nEMAIL:bob@example.com\r\nEND:VCARD\r\n", encoding="utf-8")
        # same email, different sources → two separate contacts that should be flagged as duplicates
        ingest_mod.ingest([v1], source="apple_icloud", home=home, dry_run=False)
        ingest_mod.ingest([v2], source="google_takeout", home=home, dry_run=False)
        clusters = candidates.find_duplicate_candidates(home)
        assert any(c["tier"] == "confident" and "email_exact" in c["signals"] for c in clusters)


def test_no_false_confident_on_distinct_contacts():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h")
        ingest_mod.ingest([APPLE], home=home, dry_run=False)        # 4 genuinely distinct people
        clusters = candidates.find_duplicate_candidates(home)
        assert not [c for c in clusters if c["tier"] == "confident"]


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
