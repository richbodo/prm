"""The local daemon: serves the workspace SPA + the JSON API over the two stores.

Reads go through ``core.projection`` (the canonical, merged contact a user sees); duplicate review goes
through ``core.candidates`` (detect) and ``core.apply`` (apply / dismiss / undo). The daemon is the
**only writer of canonical private data** — every write takes the AC-PRM-C file-lock and snapshots
first (handled in ``core.apply``). It binds **127.0.0.1 only**: a local tool, never a network service
(INV-1).

The router (``route``) is a pure function of ``(method, path, query, home, body)`` → ``(status,
content_type, body)`` — no sockets — so the whole API is unit-testable without binding a port.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cli.config import PrmHome
from core import apply, candidates, private_db, projection, proposals, shared_db

WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "workspace"

_API_PREFIX = "/api/"
_CONTACT_PREFIX = "/api/contact/"
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


# --------------------------------------------------------------------------- GET (read)
def _get(path: str, query: dict, home: PrmHome) -> tuple[int, str, bytes]:
    db = home.shared_db
    if path == "/api/status":
        if not db.exists():
            return _json(200, {"shared_db": False, "home": str(home.root)})
        out = {"shared_db": True, "home": str(home.root), **shared_db.stats(db)}
        if home.private_db.exists():
            p = private_db.stats(home.private_db)
            out["contacts"] = p["contacts"]              # canonical (post-merge) count
            out["merged_contacts"] = p["merged_contacts"]
        return _json(200, out)

    if not db.exists():
        return _json(409, {"error": "no shared.db yet — run `prm import` first"})

    if path == "/api/search":
        q = (query.get("q") or [""])[0]
        imap = private_db.identity_map(home.private_db) if home.private_db.exists() else {}
        seen, results = set(), []
        for name, email, org, srid in shared_db.search(db, q, limit=_int(query.get("limit"), 30)):
            cid = imap.get(srid, srid)                   # map the raw hit to its canonical contact
            if cid in seen:
                continue
            seen.add(cid)
            results.append({"id": cid, "name": name, "email": email, "org": org})
        return _json(200, {"query": q, "results": results})

    if path == "/api/contacts":
        return _json(200, projection.list_contacts(home, limit=_int(query.get("limit"), 50),
                                                    offset=_int(query.get("offset"), 0)))

    if path == "/api/candidates":
        return _json(200, {"clusters": candidates.find_duplicate_candidates(home)})

    if path == "/api/merge-preview":
        ids = [i for i in (query.get("ids") or [""])[0].split(",") if i]
        if len(ids) < 2:
            return _json(400, {"error": "merge-preview needs ≥2 contact ids"})
        return _json(200, projection.preview_merge(home, ids))

    if path == "/api/proposals":
        return _json(200, {"proposals": proposals.list_proposals(home, status="pending")})

    if path.startswith(_CONTACT_PREFIX):
        contact = projection.get_contact(home, path[len(_CONTACT_PREFIX):])
        if contact is None:
            return _json(404, {"error": "no such contact"})
        return _json(200, contact)

    return _json(404, {"error": "not found", "path": path})


# --------------------------------------------------------------------------- POST (write — review)
def _post(path: str, home: PrmHome, body: dict) -> tuple[int, str, bytes]:
    if not home.shared_db.exists():
        return _json(409, {"error": "no shared.db yet"})
    try:
        if path == "/api/merge":
            members, into = body.get("member_ids") or [], body.get("into")
            if not into or into not in members:
                return _json(400, {"error": "merge needs member_ids and an `into` among them"})
            cs = apply.build_merge_changeset(home, members, into, resolutions=body.get("resolutions") or [],
                                             created_by="manual:workspace", rationale=body.get("rationale", ""))
            result = apply.apply_changeset(home, cs)
            return _json(200, {"ok": True, "into": into, "proposal_id": result["proposal_id"]})

        if path == "/api/reject":
            key = body.get("key")
            if not key:
                return _json(400, {"error": "reject needs a cluster `key`"})
            apply.reject_cluster(home, key, by="manual:workspace")
            return _json(200, {"ok": True})

        if path == "/api/undo":
            restored = apply.undo(home)
            return _json(200, {"ok": bool(restored), "restored": restored})

        if path == "/api/apply-proposal":
            cs = proposals.load(home, body.get("proposal_id") or "")
            if cs is None:
                return _json(404, {"error": "no such proposal"})
            if cs.get("status") != "pending":
                return _json(409, {"error": f"proposal already {cs.get('status')}"})
            apply.apply_changeset(home, cs)                       # the human approves the AI's proposal
            proposals.set_status(home, cs["proposal_id"], "applied")
            return _json(200, {"ok": True, "proposal_id": cs["proposal_id"], "into": cs.get("into")})

        if path == "/api/dismiss-proposal":
            cs = proposals.load(home, body.get("proposal_id") or "")
            if cs is None:
                return _json(404, {"error": "no such proposal"})
            proposals.set_status(home, cs["proposal_id"], "dismissed")
            if cs.get("member_ids"):                              # also suppress the underlying candidate
                apply.reject_cluster(home, candidates.cluster_key(cs["member_ids"]), by="manual:workspace")
            return _json(200, {"ok": True})
    except (KeyError, ValueError) as exc:
        return _json(400, {"error": str(exc)})

    return _json(404, {"error": "not found", "path": path})


def route(method: str, path: str, query: dict, home: PrmHome, body: dict | None = None) -> tuple[int, str, bytes]:
    """Pure API router. ``query`` is the ``parse_qs`` dict; ``body`` is the parsed JSON for POST."""
    if method == "GET":
        return _get(path, query, home)
    if method == "POST":
        return _post(path, home, body or {})
    return _json(405, {"error": "method not allowed", "method": method})


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
    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith(_API_PREFIX):
            self._send(*route("GET", parsed.path, parse_qs(parsed.query), self.server.home))
        else:
            self._send(*_static(parsed.path))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith(_API_PREFIX):
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except (ValueError, TypeError):
            self._send(*_json(400, {"error": "invalid JSON body"}))
            return
        self._send(*route("POST", parsed.path, parse_qs(parsed.query), self.server.home, body))

    def log_message(self, *args) -> None:  # quiet; a local tool
        pass


def make_server(home: PrmHome, *, host: str = "127.0.0.1", port: int = 8770) -> ThreadingHTTPServer:
    """Build (but don't run) the daemon bound to ``host:port``. Localhost only (INV-1). Split from
    ``serve()`` so tests can bind an ephemeral port and ``shutdown()`` cleanly."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.home = home
    return httpd


def serve(home: PrmHome, *, host: str = "127.0.0.1", port: int = 8770) -> None:
    """Run the workspace daemon until interrupted."""
    httpd = make_server(home, host=host, port=port)
    print(f"PRM workspace → http://{host}:{port}   (serving {home.root})")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        httpd.server_close()
