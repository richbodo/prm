#!/usr/bin/env python3
"""Single-instance advisory file-lock contention (AC-PRM-C / AC-11): a second holder of a PRM home's
lock is refused **cleanly with a specific message** naming the held lock, never silently proceeding into
a concurrent canonical write. Releasing the lock makes the home re-acquirable (no stale-lock deadlock).

    python tests/unit/test_lock.py
    pytest tests/unit/test_lock.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core import lock as lock_mod  # noqa: E402
from core.lock import LockError, file_lock  # noqa: E402


def test_second_holder_refused_with_message():
    if not lock_mod._HAVE_FCNTL:                              # advisory lock is a best-effort no-op w/o fcntl
        return                                               # (Windows; v0.1 targets macOS/Linux)
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / ".lock"
        with file_lock(lock):                                # first holder owns the home
            try:
                with file_lock(lock):                        # a second concurrent acquirer
                    raise AssertionError("second holder should have been refused, not granted")
            except LockError as exc:
                assert "PRM process" in str(exc)             # specific message, not a generic failure
                assert str(lock) in str(exc)                 # names the held lock


def test_release_allows_reacquire():
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / ".lock"
        with file_lock(lock):
            pass
        with file_lock(lock):                                # released → re-acquirable, no deadlock
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
