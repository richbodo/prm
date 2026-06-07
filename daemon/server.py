"""Read-only local daemon (v0.1 M2): serves the workspace SPA + a JSON read API over ``shared.db``.

Read path only — every store access goes through ``core.shared_db`` opened **read-only** (INV-2).
The write surface (apply approved proposals, the daemon as the only writer of private data) lands
with M3 and will add ``POST`` routes guarded by the single-instance file-lock.

The request router (``route``) is a **pure function** of ``(method, path, query, home)`` returning
``(status, content_type, body)`` — no sockets, no globals — so the API is unit-testable without
binding a port. ``serve()`` is the thin ``http.server`` glue around it. The server binds
**127.0.0.1 only**: the workspace is a local tool, never a network service (INV-1).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cli.config import PrmHome
from cli.jcard import JCard
from core import shared_db

WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "workspace"

_API_PREFIX = "/api/"
_CONTACT_PREFIX = "/api/contact/"

# Static assets the SPA shell is allowed to request (explicit allow-list — no path traversal).
_STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
_CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
           ".css": "text/css; charset=utf-8"}


# --------------------------------------------------------------------------- helpers
def _json(status: int, payload) -> tuple[int, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _int(values, default: int) -> int:
    try:
        return int(values[0]) if values else default
    except (TypeError, ValueError):
        return default


def _render_contact(record: dict) -> dict:
    """Parse the stored jCard into a render-friendly shape (the daemon owns jCard rendering)."""
    jc = JCard.from_json(record["raw_jcard"])
    return {
        "id": record["id"],
        "source": record["source"],
        "source_uid": record["source_uid"],
        "stable_key": record["stable_key"],
        "ingested_at": record["ingested_at"],
        "fn": jc.first_value("fn"),
        "fields": [{"name": p.name, "params": p.params, "values": p.values} for p in jc.properties],
        "provenance": record["provenance"],
    }


# --------------------------------------------------------------------------- pure router
def route(method: str, path: str, query: dict, home: PrmHome) -> tuple[int, str, bytes]:
    """Pure read-only API router. ``query`` is the ``parse_qs`` dict ({name: [values]})."""
    if method != "GET":
        return _json(405, {"error": "method not allowed", "method": method})

    db = home.shared_db
    if path == "/api/status":
        if not db.exists():
            return _json(200, {"shared_db": False, "home": str(home.root)})
        return _json(200, {"shared_db": True, "home": str(home.root), **shared_db.stats(db)})

    if not db.exists():
        return _json(409, {"error": "no shared.db yet — run `prm import` first"})

    if path == "/api/search":
        q = (query.get("q") or [""])[0]
        rows = shared_db.search(db, q, limit=_int(query.get("limit"), 20))
        return _json(200, {"query": q,
                           "results": [{"id": rid, "name": n, "email": e, "org": o} for n, e, o, rid in rows]})

    if path == "/api/contacts":
        return _json(200, shared_db.list_records(db, limit=_int(query.get("limit"), 50),
                                                 offset=_int(query.get("offset"), 0)))

    if path.startswith(_CONTACT_PREFIX):
        rid = path[len(_CONTACT_PREFIX):]
        record = shared_db.get_record(db, rid)
        if record is None:
            return _json(404, {"error": "no such contact", "id": rid})
        return _json(200, _render_contact(record))

    return _json(404, {"error": "not found", "path": path})


def _static(path: str) -> tuple[int, str, bytes]:
    name = _STATIC.get(path)
    if name is None:
        return 404, "text/plain; charset=utf-8", b"not found"
    asset = WORKSPACE_DIR / name
    if not asset.exists():
        return 404, "text/plain; charset=utf-8", b"missing workspace asset"
    return 200, _CTYPES.get(asset.suffix, "application/octet-stream"), asset.read_bytes()


# --------------------------------------------------------------------------- socket glue
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        if parsed.path.startswith(_API_PREFIX):
            status, ctype, body = route("GET", parsed.path, parse_qs(parsed.query), self.server.home)
        else:
            status, ctype, body = _static(parsed.path)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # quiet; the daemon is a local tool
        pass


def make_server(home: PrmHome, *, host: str = "127.0.0.1", port: int = 8770) -> ThreadingHTTPServer:
    """Build (but don't run) the read-only daemon bound to ``host:port``. Localhost only (INV-1).

    Split from ``serve()`` so tests can bind an ephemeral port (``port=0``), drive it over a real
    socket, and ``shutdown()`` cleanly. Read ``httpd.server_address`` for the actual bound port.
    """
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.home = home  # read by _Handler.do_GET via self.server
    return httpd


def serve(home: PrmHome, *, host: str = "127.0.0.1", port: int = 8770) -> None:
    """Run the read-only workspace daemon until interrupted."""
    httpd = make_server(home, host=host, port=port)
    print(f"PRM workspace → http://{host}:{port}   (serving {home.root}, read-only)")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        httpd.server_close()
