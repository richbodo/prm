#!/usr/bin/env python3
"""Tests for `prm app` (cli.prm_import.cmd_app) — the import guard for the optional desktop extra.

The window itself (pywebview) is manual QA — it needs a GUI main loop. What's testable here is the
graceful failure when the `[app]` extra isn't installed: a clean, actionable message + exit 1, not a
raw ImportError traceback. We force the guard by masking `webview` in sys.modules.

    python tests/unit/test_cli_app.py
    pytest  tests/unit/test_cli_app.py
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli.prm_import import build_parser, cmd_app  # noqa: E402


def test_app_requires_extra():
    saved = sys.modules.get("webview", "__absent__")
    sys.modules["webview"] = None                       # → `import webview` raises ImportError
    try:
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(["--data-dir", tmp, "app"])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cmd_app(args)
            assert rc == 1
            msg = err.getvalue()
            assert "app" in msg and "install" in msg     # points the user at how to get the extra
    finally:
        if saved == "__absent__":
            sys.modules.pop("webview", None)
        else:
            sys.modules["webview"] = saved


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
