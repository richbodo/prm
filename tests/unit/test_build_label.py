#!/usr/bin/env python3
"""AC-15 build label tied to the source revision (core/build_label.py): a git checkout yields a
``<YYYY-MM-DD>-<short-sha>`` label; archived (no-git) source falls back to a committed ``BUILD_LABEL``
stamp, then to the packaged version — never empty, so a field bug report can always name its source.

    python tests/unit/test_build_label.py
    pytest tests/unit/test_build_label.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core import build_label as bl  # noqa: E402

_GIT_LABEL = re.compile(r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{7,}(-dirty)?$")


def test_running_label_is_git_date_sha():
    if bl._git(REPO, "rev-parse", "HEAD") is None:
        return                                               # no git here; git-label path not exercisable
    assert _GIT_LABEL.match(bl.build_label()), bl.build_label()


def test_archived_source_uses_stamp_file():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)                                     # no .git
        (repo / "BUILD_LABEL").write_text("2026-06-10-deadbeef00\n", encoding="utf-8")
        assert bl.compute_build_label(repo) == "2026-06-10-deadbeef00"


def test_no_git_no_stamp_is_non_empty_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        label = bl.compute_build_label(Path(tmp))            # no git, no stamp
        assert label and not _GIT_LABEL.match(label)         # 'v<version>' or 'unknown', never a git label


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
