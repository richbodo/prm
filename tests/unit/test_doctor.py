#!/usr/bin/env python3
"""AC-6 always-reachable escape + AC-7 `prm doctor`: a force-unlock that clears a stale lock file,
refuses cleanly when the lock is held by a live process (no double-writer), and a sanitized state dump.

    python tests/unit/test_doctor.py
    pytest tests/unit/test_doctor.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli.config import resolve_home  # noqa: E402
from cli.prm_import import main as cli_main  # noqa: E402
from core import lock as lock_mod  # noqa: E402
from core.lock import file_lock  # noqa: E402


def _run(home, *argv) -> int:
    return cli_main(["--data-dir", str(home.root), *argv])


def test_unlock_clears_stale_lock():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h").create()
        home.lock_file.write_text("", encoding="utf-8")      # a stale lock file, no live holder
        assert home.lock_file.exists()
        assert _run(home, "doctor", "--unlock") == 0
        assert not home.lock_file.exists()                   # cleared (AC-6 escape)


def test_unlock_refuses_when_held_live():
    if not lock_mod._HAVE_FCNTL:
        return                                               # no real lock without fcntl
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h").create()
        with file_lock(home.lock_file):                      # a live holder
            rc = _run(home, "doctor", "--unlock")
        assert rc == 1 and home.lock_file.exists()           # refused; lock left intact (no double-writer)


def test_doctor_dump_runs():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h").create()
        assert _run(home, "doctor", "--json") == 0
        assert _run(home, "doctor") == 0


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
