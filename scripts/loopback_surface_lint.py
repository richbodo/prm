#!/usr/bin/env python3
"""Loopback-surface lint — the static half of "checked, not asserted" for a PNA's app-opened HTTP
surface (docs/design-notes/local-daemon-trust-surface.md, "Surface 1").

A local-first app that stands up an HTTP surface over its own data must keep that surface the app's own
transport, not a tap other local processes can read. This lint flags two concrete, statically-checkable
ways that breaks:

  L1  **non-loopback bind** — a server constructed (or a host-ish parameter defaulting) to a hardcoded
      ``0.0.0.0`` / ``""`` / ``::`` / public host. A host passed as a *variable* is not flagged (it may
      be guarded at runtime, as PRM's ``host_is_loopback`` pin does).
  L2  **unauthenticated handler** — a module that defines an HTTP request handler
      (``BaseHTTPRequestHandler`` subclass, or a ``do_GET``/``do_POST``/… method) but shows no auth
      guard at all (a constant-time token check, a Host allowlist, a session/cookie check). That is an
      app-opened surface any other same-host process can dial.

**Bounded claim (honest).** L2 flags the *absence of any* auth guard in a handler module; it does not
prove every code path enforces one — that is for tests and review. Minimization + data-floor checks for
the *MCP private* surface are a planned extension, not yet implemented. This is a tripwire, not a proof —
the same posture as a grep-style egress lint.

Candidate to propose upstream as a PNT ``tools/`` lint (the static companion to the runtime egress probe).

Usage:
    python scripts/loopback_surface_lint.py [path ...]     # default: the package dirs
Exit status is 1 if there is any finding, else 0.
"""

from __future__ import annotations

import ast
import sys
from collections import namedtuple
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DIRS = ["daemon", "cli", "core", "mcp_servers"]

# "" and "0.0.0.0" / "::" bind every interface — NOT loopback. Anything else that isn't one of these is
# treated as a non-loopback host literal and flagged.
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_SERVER_CTORS = {"HTTPServer", "ThreadingHTTPServer", "TCPServer", "ThreadingTCPServer",
                 "ForkingTCPServer", "UDPServer", "WSGIServer"}
_HANDLER_METHODS = {"do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH", "do_HEAD"}
_HOST_PARAMS = {"host", "bind", "address", "interface", "bind_host"}
# Any of these appearing in a handler module is taken as "an auth guard is present". Deliberately broad
# (a tripwire): the goal is to catch a surface with *no* guard at all.
_AUTH_SIGNALS = ("compare_digest", "host_is_loopback", "origin_is_loopback", "token_ok",
                 "auth_token", "x-prm-token", "authorization", "samesite", "csrf",
                 "_request_token", "prm_session")

Finding = namedtuple("Finding", "path line code message")


def _name(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _host_literal(arg) -> str | None:
    """The host string of a ``(host, port)`` bind tuple, when it is a literal; else None."""
    if isinstance(arg, ast.Tuple) and arg.elts:
        head = arg.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


def _check_defaults(node, filename, findings) -> None:
    """Flag a host-ish parameter that *defaults* to a non-loopback literal."""
    args = node.args
    positional = args.posonlyargs + args.args
    for a, d in zip(positional[len(positional) - len(args.defaults):], args.defaults):
        _flag_default(a, d, node.lineno, filename, findings)
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        _flag_default(a, d, node.lineno, filename, findings)


def _flag_default(arg, default, line, filename, findings) -> None:
    if (default is not None and arg.arg in _HOST_PARAMS
            and isinstance(default, ast.Constant) and isinstance(default.value, str)
            and default.value not in _LOOPBACK):
        findings.append(Finding(filename, line, "L1",
                                f"parameter {arg.arg!r} defaults to non-loopback {default.value!r} — "
                                f"a surface bound here is reachable off-device"))


def lint_text(source: str, filename: str = "<src>") -> list:
    """Lint one module's source. Returns a list of ``Finding``."""
    findings: list = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [Finding(filename, exc.lineno or 0, "L0", f"syntax error: {exc.msg}")]

    handlers: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = _name(node.func)
            if (fname in _SERVER_CTORS or fname == "bind") and node.args:
                host = _host_literal(node.args[0])
                if host is not None and host not in _LOOPBACK:
                    findings.append(Finding(filename, node.lineno, "L1",
                        f"{fname} bound to non-loopback host {host!r} — exposes the surface off-device "
                        f"(bind 127.0.0.1, or gate a non-loopback host behind an explicit opt-out)"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_defaults(node, filename, findings)
        elif isinstance(node, ast.ClassDef):
            base_handler = any(_name(b).endswith("BaseHTTPRequestHandler") for b in node.bases)
            method_handler = any(isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                                 and m.name in _HANDLER_METHODS for m in node.body)
            if base_handler or method_handler:
                handlers.append((node.name, node.lineno))

    if handlers and not any(sig in source.lower() for sig in _AUTH_SIGNALS):
        name, line = handlers[0]
        findings.append(Finding(filename, line, "L2",
            f"HTTP request handler {name!r}, but the module shows no auth guard (token check / Host "
            f"allowlist / session) — an unauthenticated app-opened surface other local processes can read"))
    return findings


# Vendored / generated trees are not the app's own surface — never lint into them.
_SKIP_DIRS = {".venv", "venv", "site-packages", "node_modules", "__pycache__", ".git",
              "build", "dist", ".tox", ".mypy_cache", ".pytest_cache", ".eggs"}


def lint_paths(paths) -> list:
    """Lint every ``*.py`` under each path (files or dirs), skipping tests and vendored trees."""
    findings: list = []
    for p in paths:
        p = Path(p)
        files = [p] if p.is_file() else sorted(p.rglob("*.py"))
        for f in files:
            if set(f.parts) & _SKIP_DIRS:
                continue
            rel = str(f).replace("\\", "/")
            if "/tests/" in rel or f.name.startswith("test_"):
                continue
            findings.extend(lint_text(f.read_text(encoding="utf-8"), str(f)))
    return findings


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    targets = argv or [str(REPO / d) for d in DEFAULT_DIRS]
    findings = lint_paths(targets)
    for fnd in findings:
        print(f"{fnd.path}:{fnd.line}: [{fnd.code}] {fnd.message}")
    if findings:
        print(f"\nloopback-surface lint: {len(findings)} finding(s) — see docs/design-notes/"
              "local-daemon-trust-surface.md (Surface 1).")
        return 1
    print("loopback-surface lint: clean — app-opened HTTP surfaces are loopback-bound and authenticated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
