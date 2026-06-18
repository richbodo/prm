#!/usr/bin/env python3
"""Tests for the daemon's loopback auth guard — "Surface 1" of the local-daemon trust surface
(docs/design-notes/local-daemon-trust-surface.md): the Host allowlist (DNS-rebinding defense), the
write-side Origin check, the per-process session token, the cookie bootstrap, and the refusal to bind a
non-loopback address. Pure guards are unit-tested; the token + Host + bootstrap are exercised over a
real ephemeral socket. Runs as a plain script or under pytest.

    python tests/unit/test_daemon_security.py
    pytest tests/unit/test_daemon_security.py
"""

from __future__ import annotations

import http.client
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import ingest as ingest_mod  # noqa: E402
from cli.config import resolve_home  # noqa: E402
from daemon import server  # noqa: E402

FIX = REPO / "tests" / "fixtures"
APPLE = FIX / "apple_icloud" / "sources" / "icloud-export.vcf"


def _home(tmp: str):
    home = resolve_home(Path(tmp) / "h")
    ingest_mod.ingest([APPLE], home=home, dry_run=False)
    return home


def _serve(home):
    httpd = server.make_server(home, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t


# ------------------------------------------------------------------ pure guards
def test_host_is_loopback():
    for ok in ("127.0.0.1", "127.0.0.1:8770", "localhost", "localhost:8770", "::1", "[::1]:8770"):
        assert server.host_is_loopback(ok), ok
    for bad in ("", "evil.com", "evil.com:8770", "10.0.0.5:8770", "0.0.0.0:8770"):
        assert not server.host_is_loopback(bad), bad


def test_origin_is_loopback():
    assert server.origin_is_loopback("http://127.0.0.1:8770")
    assert server.origin_is_loopback("http://localhost:8770")
    assert not server.origin_is_loopback("http://evil.com")
    assert not server.origin_is_loopback("https://attacker.example:8770")
    assert not server.origin_is_loopback("")


def test_token_ok():
    assert server.token_ok({"X-PRM-Token": "abc"}, {}, "abc")
    assert not server.token_ok({"X-PRM-Token": "bad"}, {}, "abc")
    assert server.token_ok({}, {"t": ["abc"]}, "abc")                 # the ?t= bootstrap key
    assert server.token_ok({"Cookie": "prm_session=abc"}, {}, "abc")  # the session cookie
    assert not server.token_ok({}, {}, "abc")                         # no credential presented
    assert not server.token_ok({"X-PRM-Token": "abc"}, {}, "")        # no server token ⇒ never ok


# ------------------------------------------------------------------ loopback-pin
def test_make_server_refuses_non_loopback_bind():
    with tempfile.TemporaryDirectory() as tmp:
        home = _home(tmp)
        try:
            server.make_server(home, host="0.0.0.0", port=0)
        except server.NonLoopbackBindError as exc:
            assert "0.0.0.0" in str(exc)
        else:
            raise AssertionError("expected NonLoopbackBindError for a non-loopback bind")


# ------------------------------------------------------------------ live socket
def test_api_requires_token_over_socket():
    with tempfile.TemporaryDirectory() as tmp:
        httpd, t = _serve(_home(tmp))
        host, port = httpd.server_address[:2]
        base = f"http://{host}:{port}"
        try:
            try:                                                      # no token → 401
                urllib.request.urlopen(f"{base}/api/contacts", timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 401
            else:
                raise AssertionError("expected 401 without the session token")
            req = urllib.request.Request(f"{base}/api/contacts", headers={"X-PRM-Token": httpd.auth_token})
            with urllib.request.urlopen(req, timeout=5) as r:         # valid token → 200
                assert r.status == 200
            bad = urllib.request.Request(f"{base}/api/contacts", headers={"X-PRM-Token": "wrong"})
            try:
                urllib.request.urlopen(bad, timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 401
            else:
                raise AssertionError("expected 401 for a wrong token")
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=5)


def test_non_loopback_host_header_refused():
    # DNS-rebinding: a request whose Host names an attacker domain is refused even with a valid token.
    with tempfile.TemporaryDirectory() as tmp:
        httpd, t = _serve(_home(tmp))
        host, port = httpd.server_address[:2]
        try:
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.putrequest("GET", "/api/contacts", skip_host=True)
            conn.putheader("Host", "evil.com")
            conn.putheader("X-PRM-Token", httpd.auth_token)
            conn.endheaders()
            resp = conn.getresponse()
            assert resp.status == 403
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=5)


def test_cross_origin_write_refused():
    with tempfile.TemporaryDirectory() as tmp:
        httpd, t = _serve(_home(tmp))
        host, port = httpd.server_address[:2]
        try:
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("POST", "/api/undo", body=b"{}",
                         headers={"Origin": "http://evil.com", "X-PRM-Token": httpd.auth_token,
                                  "Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 403
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=5)


def test_bootstrap_sets_cookie_then_serves():
    with tempfile.TemporaryDirectory() as tmp:
        httpd, t = _serve(_home(tmp))
        host, port = httpd.server_address[:2]
        try:
            # GET /?t=<token> → 303 + Set-Cookie (token stripped from the URL by the redirect)
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", f"/?t={httpd.auth_token}")
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 303 and resp.getheader("Location") == "/"
            set_cookie = resp.getheader("Set-Cookie") or ""
            assert "prm_session=" in set_cookie
            conn.close()
            # the bootstrapped cookie then authorizes the API
            cookie = set_cookie.split(";", 1)[0]
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", "/api/contacts", headers={"Cookie": cookie})
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 200
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=5)


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
