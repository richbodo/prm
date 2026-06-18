#!/usr/bin/env python3
"""Tests for the loopback-surface lint (scripts/loopback_surface_lint.py): it flags a non-loopback bind
and an unauthenticated HTTP handler, passes a guarded one, and — the local demonstration — finds the
real PRM tree clean now that the daemon authenticates its session (Surface 1). Runs as a script or under
pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import loopback_surface_lint as lint  # noqa: E402

_HANDLER = (
    "from http.server import BaseHTTPRequestHandler\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200)\n"
)


def _codes(text):
    return {f.code for f in lint.lint_text(text, "x.py")}


# ------------------------------------------------------------------ L1: non-loopback bind
def test_flags_non_loopback_server_bind():
    src = "from http.server import ThreadingHTTPServer\nThreadingHTTPServer(('0.0.0.0', 80), object)\n"
    assert "L1" in _codes(src)


def test_flags_non_loopback_default_param():
    src = "def serve(home, *, host='0.0.0.0'):\n    return host\n"
    assert "L1" in _codes(src)


def test_loopback_bind_and_default_are_clean():
    src = ("from http.server import ThreadingHTTPServer\n"
           "def serve(home, *, host='127.0.0.1'):\n"
           "    return ThreadingHTTPServer((host, 0), object)\n")  # host is a variable → not flagged
    assert _codes(src) == set()


# ------------------------------------------------------------------ L2: unauthenticated handler
def test_flags_unauthenticated_handler():
    assert "L2" in _codes(_HANDLER)


def test_authenticated_handler_is_clean():
    guarded = _HANDLER + "import hmac\nok = hmac.compare_digest('a', 'b')\n"
    assert "L2" not in _codes(guarded)


def test_handler_with_host_guard_is_clean():
    guarded = _HANDLER + "def host_is_loopback(h):\n    return h == '127.0.0.1'\n"
    assert "L2" not in _codes(guarded)


# ------------------------------------------------------------------ the local demonstration
def test_real_prm_tree_is_clean():
    findings = lint.lint_paths([REPO / d for d in ("daemon", "cli", "core", "mcp_servers")])
    assert findings == [], "PRM should pass its own loopback-surface lint:\n" + "\n".join(
        f"  {f.path}:{f.line} [{f.code}] {f.message}" for f in findings)


# ------------------------------------------------------------------ severity split (L1 gates / L2 advisory)
def test_main_gates_on_l1_bind():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        Path(td, "s.py").write_text(
            "from http.server import ThreadingHTTPServer\n"
            "ThreadingHTTPServer(('0.0.0.0', 80), object)\n")
        assert lint.main([td]) == 1              # L1 gates regardless of --strict
        assert lint.main([td, "--strict"]) == 1


def test_main_l2_is_advisory_by_default_strict_gates():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        Path(td, "s.py").write_text(_HANDLER)    # loopback-less handler, no auth → L2 only
        assert lint.main([td]) == 0              # advisory: reported, does not gate
        assert lint.main([td, "--strict"]) == 1  # --strict promotes L2 to a gate


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = []
    for fn in tests:
        try:
            fn()
            print(f"[OK]   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append(fn.__name__)
            print(f"[FAIL] {fn.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
