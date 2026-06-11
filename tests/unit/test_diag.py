#!/usr/bin/env python3
"""AC-7 diagnostics (core/diag.py): a sanitized, PII-free state dump; a local error log that redacts
emails; and the lock-state probe (the AC-6 / AC-11 surface).

    python tests/unit/test_diag.py
    pytest tests/unit/test_diag.py
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
from core import diag  # noqa: E402
from core import lock as lock_mod  # noqa: E402
from core.lock import file_lock  # noqa: E402


def _home(tmp):
    home = resolve_home(Path(tmp) / "h")
    v = Path(tmp) / "a.vcf"
    v.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.org\r\nEND:VCARD\r\n", encoding="utf-8")
    ingest_mod.ingest([v], source="apple_icloud", home=home, dry_run=False)
    return home


def test_state_dump_is_sanitized_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        d = diag.state_dump(home)
        assert d["build_label"] and d["home"] == str(home.root)
        assert d["stores"]["shared"]["records"] == 1
        assert d["lock"] in ("free", "absent")
        blob = json.dumps(d)                                 # no contact PII anywhere in the dump
        assert "ada@example.org" not in blob and "Ada Lovelace" not in blob


def test_error_log_redacts_and_tails():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h").create()
        diag.capture_error(home, ValueError("boom for ada@example.org while parsing"), context="GET /api/x")
        errs = diag.tail_errors(home)
        assert errs and errs[-1]["type"] == "ValueError" and errs[-1]["context"] == "GET /api/x"
        assert "<email>" in errs[-1]["message"] and "ada@example.org" not in errs[-1]["message"]


def test_lock_state_held_vs_free():
    with tempfile.TemporaryDirectory() as tmp:
        home = resolve_home(Path(tmp) / "h").create()
        assert diag.lock_state(home) == "absent"
        with file_lock(home.lock_file):
            assert diag.lock_state(home) == "held" or not lock_mod._HAVE_FCNTL
        assert diag.lock_state(home) in ("free", "absent")   # released


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
