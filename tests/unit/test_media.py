#!/usr/bin/env python3
"""Tests for the v0.2 R7 content-addressed media store (core/media.py): put is content-addressed +
idempotent (dedup), read recovers the mime, gc reclaims unreferenced blobs, and verify reports
corrupt/orphan/missing.

    python tests/unit/test_media.py
    pytest tests/unit/test_media.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli.config import resolve_home  # noqa: E402
from core import media  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngbody"
JPG = b"\xff\xd8\xff\xe0" + b"fakejpgbody"


def _home(tmp):
    return resolve_home(Path(tmp) / "h").create()


def test_put_is_content_addressed_and_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        h1 = media.put(home, PNG)
        h2 = media.put(home, PNG)                       # same bytes → same hash, no duplicate file
        assert h1 == h2 == media.content_hash(PNG)
        blobs = [p for shard in home.media_dir.iterdir() for p in shard.iterdir()]
        assert len(blobs) == 1
        assert blobs[0].name == f"{h1}.png"             # sharded by first 2 hex chars, .png from sniff
        assert blobs[0].parent.name == h1[:2]


def test_read_recovers_bytes_and_mime():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        h = media.put(home, JPG, mime="image/jpeg")
        data, mime = media.read(home, h)
        assert data == JPG and mime == "image/jpeg"
        assert media.exists(home, h)
        assert media.read(home, "0" * 64) is None       # unknown hash
        assert media.locate(home, "") is None


def test_gc_reclaims_unreferenced():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        keep = media.put(home, PNG)
        drop = media.put(home, JPG)
        removed = media.gc(home, {keep})
        assert removed == [drop]
        assert media.exists(home, keep) and not media.exists(home, drop)


def test_verify_flags_corrupt_orphan_missing():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        good = media.put(home, PNG)
        orphan = media.put(home, JPG)
        # corrupt a blob in place (rewrite its bytes so it no longer matches its name)
        bad_path = media.locate(home, good)
        bad_path.write_bytes(b"\x89PNG\r\n\x1a\ntampered")
        report = media.verify(home, {good, "deadbeef"})
        assert good in report["corrupt"]
        assert orphan in report["orphans"]
        assert "deadbeef" in report["missing"]
        assert report["stored"] == 2


def test_sniff_and_ext_helpers():
    assert media.sniff_mime(PNG) == "image/png"
    assert media.sniff_mime(JPG) == "image/jpeg"
    assert media.sniff_mime(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP") == "image/webp"
    assert media.sniff_mime(b"not an image") is None
    assert media.ext_for_mime("image/png") == "png"
    assert media.ext_for_mime(None) == "bin"
    assert media.mime_for_ext(".jpg") == "image/jpeg"


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
