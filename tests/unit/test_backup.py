#!/usr/bin/env python3
"""Unit tests for ``cli.backup`` — the lossless raw-records backup dump/restore.

Pins the document shape, that ``dump`` -> ``load`` reconstructs each record's ``source``, stored
``stable_key`` (verbatim — never re-derived), jCard, and provenance, and that ``load`` rejects
anything that isn't a current-version PRM backup. Also pins ``StableKey.from_str`` as the exact
inverse of ``as_str`` (the property the verbatim restore relies on).

    python tests/unit/test_backup.py
    pytest tests/unit/test_backup.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import backup  # noqa: E402
from cli.identity import StableKey  # noqa: E402
from cli.jcard import JCard  # noqa: E402


def _record(source: str, stable_key: str, *, props, provenance, uid=None) -> dict:
    """An envelope shaped like ``core.shared_db.all_records`` returns (raw_jcard as stored TEXT)."""
    jc = JCard()
    for name, value in props:
        jc.add(name, {}, "text", value)
    return {"id": f"{source}-{stable_key}", "source": source, "source_uid": uid,
            "stable_key": stable_key, "ingested_at": "2026-06-11T00:00:00Z",
            "raw_jcard": jc.to_json(), "provenance": provenance}


def test_dump_load_roundtrip_preserves_everything():
    rec = _record("apple_icloud", "email:ada@x.org",
                  props=[("fn", "Ada Lovelace"), ("email", "ada@x.org")],
                  provenance=[{"field": "fn", "value": "Ada Lovelace", "observed_at": "2026-01-01T00:00:00Z"},
                              {"field": "email", "value": "ada@x.org", "observed_at": None}])
    [c] = backup.load(backup.dump([rec]))
    assert c.source == "apple_icloud"
    assert c.stable_key.as_str() == "email:ada@x.org"          # verbatim, not re-derived
    assert c.jcard.to_json() == rec["raw_jcard"]               # jCard reconstructs byte-for-byte
    assert [(p.field, p.value, p.observed_at, p.source) for p in c.provenance] == [
        ("fn", "Ada Lovelace", "2026-01-01T00:00:00Z", "apple_icloud"),
        ("email", "ada@x.org", None, "apple_icloud"),
    ]


def test_dump_is_valid_versioned_json():
    doc = json.loads(backup.dump([_record("linkedin", "url:https://linkedin.com/in/ada",
                                           props=[("fn", "Ada")], provenance=[])]))
    assert doc["prm_backup"] == backup.BACKUP_VERSION
    assert "exported_at" in doc and len(doc["records"]) == 1
    assert isinstance(doc["records"][0]["jcard"], list)        # embedded as a parsed array, not a string


def test_stable_key_from_str_inverts_as_str():
    for key in (StableKey("email", "a@b.org"), StableKey("hash", "deadbeef"),
                StableKey("url", "https://x/y"), StableKey("uid", "urn:uuid:1:2:3")):
        assert StableKey.from_str(key.as_str()) == key


def test_load_rejects_non_backup():
    for bad in ({"friends": []}, {"records": []}, []):
        try:
            backup.load(bad if isinstance(bad, dict) else json.dumps(bad))
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_load_rejects_unsupported_version():
    try:
        backup.load({"prm_backup": 999, "records": []})
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unsupported backup version")


def test_load_accepts_bytes_and_str():
    rec = _record("facebook", "hash:abc", props=[("fn", "Sam")], provenance=[])
    text = backup.dump([rec])
    assert len(backup.load(text)) == 1 and len(backup.load(text.encode("utf-8"))) == 1


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
