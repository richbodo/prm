"""The local daemon: serves the workspace SPA + the JSON API over the two stores.

Reads go through ``core.projection`` (the canonical, merged contact a user sees); duplicate review goes
through ``core.candidates`` (detect) and ``core.apply`` (apply / dismiss / undo). The daemon writes
canonical **private** data (merge decisions, relationship values) and — via the workspace import surface
— drives the raw **Shared DB** write by invoking the pure ``cli.ingest`` library (the same code the CLI
uses), so INV-2's "one ingest path" holds and the AC-PRM-C file-lock serializes every writer. Export is
a read-only download. It binds **127.0.0.1 only**: a local tool, never a network service (INV-1).

The router (``route``) is a pure function of ``(method, path, query, home, body)`` → ``(status,
content_type, body[, headers])`` — no sockets — so the whole API is unit-testable without binding a port.
"""

from __future__ import annotations

import base64
import errno
import json
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cli.config import PrmHome
from core import (apply, build_label, candidates, diag, media, relationships_db, projection, proposals,
                  schema, shared_db, suggest)
from core.lock import LockError

_MAX_IMAGE_BYTES = 16 * 1024 * 1024          # a soft cap so an accidental huge upload can't bloat the home
_MAX_IMPORT_BYTES = 256 * 1024 * 1024        # per import session (all staged files) — exports are bigger than photos
_STAGING_MAX_AGE_S = 24 * 3600               # an abandoned upload session is swept after this long
_TOKEN_RE = re.compile(r"^[0-9a-f]{16,}$")   # a server-minted staging token; validated before any path join

WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "workspace"

_API_PREFIX = "/api/"
_IMPORT_PREFIX = "/api/import/"
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
        label = build_label.build_label()                    # AC-15: source revision, served at runtime
        if not db.exists():
            return _json(200, {"shared_db": False, "home": str(home.root), "build_label": label})
        out = {"shared_db": True, "home": str(home.root), "build_label": label, **shared_db.stats(db)}
        if home.relationships_db.exists():
            p = relationships_db.stats(home.relationships_db)
            out["contacts"] = p["contacts"]              # canonical (post-merge) count
            out["merged_contacts"] = p["merged_contacts"]
        return _json(200, out)

    if path == "/api/diag":                                  # AC-7: sanitized self-diagnosis (no PII), pre-db
        return _json(200, diag.state_dump(home))

    if not db.exists():
        return _json(409, {"error": "no shared.db yet — run `prm import` first"})

    if path == "/api/search":
        q = (query.get("q") or [""])[0]
        imap = relationships_db.identity_map(home.relationships_db) if home.relationships_db.exists() else {}
        seen, results = set(), []
        for name, email, org, srid in shared_db.search(db, q, limit=_int(query.get("limit"), 30)):
            cid = imap.get(srid, srid)                   # map the raw hit to its canonical contact
            if cid in seen:
                continue
            seen.add(cid)
            results.append({"id": cid, "name": name, "email": email, "org": org})
        # also match the private overlay (tags / notes / custom values) — LOCAL ONLY; the MCP search
        # surface never does this, so a cloud client can't match against sealed relationship values.
        if home.relationships_db.exists():
            for cid in relationships_db.search_values(home.relationships_db, q):
                if cid in seen:
                    continue
                seen.add(cid)
                c = projection.get_contact(home, cid)
                if c:
                    results.append({"id": cid, "name": c["fn"], "email": c["email"], "org": c["org"]})
        return _json(200, {"query": q, "results": results})

    if path == "/api/contacts":
        return _json(200, projection.list_contacts(home, limit=_int(query.get("limit"), 50),
                                                    offset=_int(query.get("offset"), 0)))

    if path == "/api/export":                                # download: merged vCard, or the lossless raw backup
        return _export(home, query)

    if path == "/api/candidates":
        clusters = candidates.find_duplicate_candidates(home)
        meta = {m["key"]: m for m in projection.clusters_meta(home, clusters)}
        for cl in clusters:                                  # annotate for the bulk-approve surface
            m = meta.get(cl["key"], {})
            cl["conflicts"] = m.get("conflicts", [])
            cl["into"] = m.get("into") or (cl["member_ids"][0] if cl.get("member_ids") else None)
        return _json(200, {"clusters": clusters})

    if path == "/api/merge-preview":
        ids = [i for i in (query.get("ids") or [""])[0].split(",") if i]
        if len(ids) < 2:
            return _json(400, {"error": "merge-preview needs ≥2 contact ids"})
        return _json(200, projection.preview_merge(home, ids))

    if path == "/api/proposals":
        props = proposals.list_proposals(home, status="pending")
        for p in props:                                      # cluster_key lets the SPA dedup a proposal
            mids = p.get("member_ids") or []                 # against its detected candidate (same hash)
            p["cluster_key"] = candidates.cluster_key(mids) if mids else None
        return _json(200, {"proposals": props})

    if path == "/api/schema":                                # the field definitions (for the edit form + R6 builder)
        return _json(200, {"fields": schema.list_fields(home.relationships_db)})

    if path == "/api/suggest-photo-match":                   # R7c: which contact does this image filename fit?
        name = (query.get("name") or [""])[0]
        if not name:
            return _json(400, {"error": "suggest-photo-match needs a `name`"})
        return _json(200, {"suggestions": suggest.suggest_for_filename(home, name, limit=_int(query.get("limit"), 5))})

    if path.startswith(_CONTACT_PREFIX):
        rest = path[len(_CONTACT_PREFIX):]
        if rest.endswith("/photo"):                          # serve the avatar BYTES (never JSON) — local only
            got = projection.contact_photo(home, rest[: -len("/photo")])
            if got is None:
                return _json(404, {"error": "no photo"})
            data, mime = got
            return 200, mime, data
        contact = projection.get_contact(home, rest)
        if contact is None:
            return _json(404, {"error": "no such contact"})
        return _json(200, contact)

    return _json(404, {"error": "not found", "path": path})


# --------------------------------------------------------------------------- POST (write — review)
def _post(path: str, home: PrmHome, body: dict) -> tuple[int, str, bytes]:
    # Import is dispatched FIRST — it is the one write you do when there is *no* shared.db yet (the
    # empty-state case), so it must run before the "no shared.db" guard the review endpoints rely on.
    if path.startswith(_IMPORT_PREFIX):
        return _import(path[len(_IMPORT_PREFIX):], home, body)
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

        if path == "/api/merge-batch":
            items = body.get("items") or []
            if not isinstance(items, list) or not items:
                return _json(400, {"error": "merge-batch needs a non-empty `items` list"})
            for it in items:                                  # thin shape checks; apply_batch owns the rest
                kind = it.get("kind")
                if kind == "candidate":
                    mids, into = it.get("member_ids") or [], it.get("into")
                    if not into or into not in mids:
                        return _json(400, {"error": "candidate item needs member_ids and an `into` among them"})
                elif kind == "proposal":
                    cs = proposals.load(home, it.get("proposal_id") or "")
                    if cs is None:
                        return _json(404, {"error": f"no such proposal {it.get('proposal_id')!r}"})
                    if cs.get("status") != "pending":         # refuse before applying anything
                        return _json(409, {"error": f"proposal {cs.get('proposal_id')} already {cs.get('status')}"})
                else:
                    return _json(400, {"error": f"unknown item kind: {kind!r}"})
            return _json(200, apply.apply_batch(home, items, created_by="manual:workspace-batch",
                                                rationale=body.get("rationale", "")))

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

        if path == "/api/set-value":                             # set a relationship-overlay value
            cid, fid = body.get("contact_id"), body.get("field_id")
            if not cid or not fid:
                return _json(400, {"error": "set-value needs contact_id + field_id"})
            apply.set_field_value(home, cid, fid, body.get("value"), value_json=body.get("value_json"),
                                  written_by="manual:workspace", source=body.get("source") or "manual")
            return _json(200, {"ok": True})

        if path == "/api/set-photo":                             # upload/replace a contact's avatar (single image)
            cid = body.get("contact_id")
            data_b64 = body.get("data_base64") or body.get("data")
            if not cid or not data_b64:
                return _json(400, {"error": "set-photo needs contact_id + data_base64"})
            try:
                raw = base64.b64decode(data_b64, validate=False)
            except (ValueError, TypeError):
                return _json(400, {"error": "invalid base64 image data"})
            if not raw:
                return _json(400, {"error": "empty image"})
            if len(raw) > _MAX_IMAGE_BYTES:
                return _json(413, {"error": f"image too large (> {_MAX_IMAGE_BYTES // (1024 * 1024)} MB)"})
            mime = body.get("mime") or media.sniff_mime(raw) or "application/octet-stream"
            h = media.put(home, raw, mime=mime)              # content-addressed; idempotent
            apply.set_field_value(home, cid, "photo", h, value_json=json.dumps({"mime": mime, "byte_size": len(raw)}),
                                  written_by="manual:workspace", source="manual")   # audited + Undo-able
            return _json(200, {"ok": True, "hash": h})

        if path == "/api/clear-value":                           # clear a value (whole field, or one entry)
            cid, fid = body.get("contact_id"), body.get("field_id")
            if not cid or not fid:
                return _json(400, {"error": "clear-value needs contact_id + field_id"})
            apply.clear_field_value(home, cid, fid, body.get("value"), written_by="manual:workspace")
            return _json(200, {"ok": True})

        if path == "/api/resolve-field":                         # edit a single-valued source field (override)
            cid, field = body.get("contact_id"), body.get("field")
            if not cid or not field:
                return _json(400, {"error": "resolve-field needs contact_id + field"})
            apply.resolve_field(home, cid, field, body.get("value"), written_by="manual:workspace")
            return _json(200, {"ok": True})

        if path == "/api/edit-contact":                          # all of a contact's edits → one atomic changeset
            cid = body.get("contact_id")
            if not cid:
                return _json(400, {"error": "edit-contact needs contact_id"})
            apply.edit_contact(home, cid, resolutions=body.get("resolutions"), values=body.get("values"),
                               clears=body.get("clears"), multi=body.get("multi"), written_by="manual:workspace")
            return _json(200, {"ok": True})

        if path == "/api/update-field":                          # workspace-only schema edit (tag vocab, R6 builder)
            fid = body.get("field_id")
            if not fid:
                return _json(400, {"error": "update-field needs field_id"})
            changes = {k: body[k] for k in
                       ("label", "config", "ai_write_policy", "disclosure_tier", "required", "position") if k in body}
            field = apply.update_field(home, fid, **changes)     # SchemaError is a ValueError → clean 400
            return _json(200, {"ok": True, "field": field})

        if path == "/api/define-field":                          # create a user custom field (schema builder)
            if not body.get("label") or not body.get("kind"):
                return _json(400, {"error": "define-field needs label + kind"})
            field = apply.define_field(
                home, label=body["label"], kind=body["kind"], config=body.get("config"),
                required=bool(body.get("required")),
                ai_write_policy=body.get("ai_write_policy", "review-required"),
                disclosure_tier=body.get("disclosure_tier", "private-sealed"))
            return _json(200, {"ok": True, "field": field})

        if path == "/api/remove-field":                          # delete a user field (cascades values; snapshotted)
            fid = body.get("field_id")
            if not fid:
                return _json(400, {"error": "remove-field needs field_id"})
            return _json(200, apply.remove_field(home, fid))
    except (KeyError, ValueError) as exc:
        return _json(400, {"error": str(exc)})

    return _json(404, {"error": "not found", "path": path})


# --------------------------------------------------------------------------- export (download)
def _export(home: PrmHome, query: dict) -> tuple[int, str, bytes, dict]:
    """Download the contacts: the merged portable **vCard** (default), or the lossless **raw** JSON
    backup (``?raw=1``). Read-only — reuses the exact ``prm export`` code. The filename rides in
    ``Content-Disposition`` so a direct hit downloads sensibly; the SPA also names it client-side."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if (query.get("raw") or ["0"])[0] in ("1", "true", "yes"):
        from cli import backup                                # lazy — keeps the read hot-path light
        text = backup.dump(shared_db.all_records(home.shared_db), home=home)
        ctype, fname = "application/json; charset=utf-8", f"prm-backup-{today}.json"
    else:
        from cli.vcard_writer import write_vcards
        text = write_vcards(projection.export_jcards(home))
        ctype, fname = "text/vcard; charset=utf-8", f"prm-contacts-{today}.vcf"
    return 200, ctype, text.encode("utf-8"), {"Content-Disposition": f'attachment; filename="{fname}"'}


# --------------------------------------------------------------------------- import (upload → stage → ingest)
def _staging_dir(home: PrmHome, token: str) -> Path:
    return home.imports_dir / token


def _safe_relpath(relpath) -> Path | None:
    """A client-supplied upload path → a safe relative path under the staging dir, or ``None`` if it
    escapes it. Rejects absolute paths, ``..`` segments, and NULs; preserves folder structure (a
    ``webkitdirectory`` upload keeps its ``Takeout/Contacts/…`` tree so the ingester walks it as a dir)."""
    rp = str(relpath or "").strip().replace("\\", "/")
    if not rp:
        return None
    parts = []
    for seg in rp.split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." or "\x00" in seg:
            return None
        parts.append(seg)
    return Path(*parts) if parts else None


def _dir_size(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def _import(action: str, home: PrmHome, body: dict) -> tuple[int, str, bytes]:
    """The workspace import surface: stage uploaded bytes, preview (dry-run report), commit (persist via
    the pure ``cli.ingest`` under the file-lock), or cancel. Mirrors the CLI's preview→confirm flow."""
    try:
        if action == "stage":
            return _import_stage(home, body)
        if action in ("preview", "commit"):
            return _import_run(home, body, commit=(action == "commit"))
        if action == "cancel":
            token = body.get("token") or ""
            if _TOKEN_RE.match(token):
                shutil.rmtree(_staging_dir(home, token), ignore_errors=True)
            return _json(200, {"ok": True})
    except (KeyError, ValueError) as exc:
        return _json(400, {"error": str(exc)})
    return _json(404, {"error": "not found", "path": _IMPORT_PREFIX + action})


def _import_stage(home: PrmHome, body: dict) -> tuple[int, str, bytes]:
    """Append one uploaded file to a staging session (client uploads sequentially, showing progress).
    Mints a token on the first call; later calls pass it back to add to the same session."""
    token = body.get("token")
    if token is None:
        token = secrets.token_hex(16)
    elif not _TOKEN_RE.match(token):
        return _json(400, {"error": "bad staging token"})
    rel = _safe_relpath(body.get("relpath"))
    if rel is None:
        return _json(400, {"error": "bad or missing relpath"})
    try:
        raw = base64.b64decode(body.get("data_base64") or "", validate=False)
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid base64 data"})
    sdir = _staging_dir(home, token)
    if _dir_size(sdir) + len(raw) > _MAX_IMPORT_BYTES:
        return _json(413, {"error": f"import too large (> {_MAX_IMPORT_BYTES // (1024 * 1024)} MB per session)"})
    dest = sdir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    staged = sum(1 for p in sdir.rglob("*") if p.is_file())
    return _json(200, {"token": token, "staged": staged})


def _import_run(home: PrmHome, body: dict, *, commit: bool) -> tuple[int, str, bytes]:
    """Preview (dry-run report, nothing written) or commit (persist + seed identities under the
    file-lock) the staged files. Both run the pure path-based ``cli.ingest`` over the staging dir, so a
    single file and a whole folder are handled identically to the CLI. Commit removes the staging dir."""
    token = body.get("token") or ""
    if not _TOKEN_RE.match(token):
        return _json(400, {"error": "bad staging token"})
    sdir = _staging_dir(home, token)
    if not sdir.is_dir() or not any(p.is_file() for p in sdir.rglob("*")):
        return _json(404, {"error": "no staged files for this token (stage files first)"})
    source = body.get("source") or None
    from cli import ingest as ingest_mod                      # lazy — the ingest deps stay off the read path
    report = ingest_mod.ingest([sdir], source=source, home=(home if commit else None), dry_run=not commit)
    if commit:
        shutil.rmtree(sdir, ignore_errors=True)              # only on success — a failed commit keeps the upload
    return _json(200, {"report": report.to_dict()})


def _sweep_staging(home: PrmHome, *, max_age_s: int = _STAGING_MAX_AGE_S) -> None:
    """Remove abandoned staging sessions on daemon startup (a preview that was never committed/cancelled),
    so uploads can't accumulate. Best-effort; never raises."""
    d = home.imports_dir
    if not d.is_dir():
        return
    cutoff = time.time() - max_age_s
    for child in d.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass


def route(method: str, path: str, query: dict, home: PrmHome, body: dict | None = None) -> tuple[int, str, bytes]:
    """Pure API router. ``query`` is the ``parse_qs`` dict; ``body`` is the parsed JSON for POST.

    A store schema/availability error refuses **mutations** cleanly with a 409 (reads don't version-check,
    so they continue — AC-4); any other unexpected error is captured to the diag log (AC-7) and returned
    sanitized, never a raw traceback to the client."""
    try:
        if method == "GET":
            return _get(path, query, home)
        if method == "POST":
            return _post(path, home, body or {})
        return _json(405, {"error": "method not allowed", "method": method})
    except LockError as exc:                                  # a concurrent writer (import / merge / CLI) holds the lock
        diag.capture_error(home, exc, context=f"{method} {path}")
        return _json(409, {"error": "another operation is writing — try again in a moment"})
    except (shared_db.SharedDbError, relationships_db.RelationshipsDbError) as exc:
        diag.capture_error(home, exc, context=f"{method} {path}")
        return _json(409, {"error": f"store schema/availability error — mutations refused, reads continue: {exc}"})
    except Exception as exc:  # noqa: BLE001
        diag.capture_error(home, exc, context=f"{method} {path}")
        return _json(500, {"error": "internal error — run `prm doctor` for diagnostics"})


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
    def _send(self, status: int, ctype: str, body: bytes, extra_headers: dict | None = None) -> None:
        # ``route()`` may return a 3-tuple (most endpoints) or a 4-tuple ``(…, headers)`` (export, which
        # adds Content-Disposition); ``self._send(*route(...))`` splats either, so existing returns are
        # untouched.
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if ctype.startswith("image/"):
            # The avatar URL is content-stable (/api/contact/<id>/photo), so a replaced photo would show
            # stale until reload; revalidate so an upload/remove is reflected everywhere immediately.
            self.send_header("Cache-Control", "no-cache")
        for key, val in (extra_headers or {}).items():
            self.send_header(key, val)
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


class PortInUseError(RuntimeError):
    """The requested port is already bound — another PRM server (or some other process) holds it.
    Raised in place of a raw ``OSError`` so the caller can fail with a clean, actionable message."""


def make_server(home: PrmHome, *, host: str = "127.0.0.1", port: int = 8770) -> ThreadingHTTPServer:
    """Build (but don't run) the daemon bound to ``host:port``. Localhost only (INV-1). Split from
    ``serve()`` so tests can bind an ephemeral port and ``shutdown()`` cleanly. Raises
    ``PortInUseError`` (not a raw traceback) when ``port`` is already taken, so ``prm serve`` /
    ``just serve`` can report it and exit gracefully."""
    relationships_db.migrate(home.relationships_db, home.legacy_private_db)   # v0.1→v0.2 rename + schema upgrade
    _sweep_staging(home)                                                      # drop abandoned uploads
    try:
        httpd = ThreadingHTTPServer((host, port), _Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise PortInUseError(
                f"port {port} is already in use — stop the running server (`just stop`) "
                f"or serve on a different port (`just serve <port>`)"
            ) from exc
        raise
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


def start_background(home: PrmHome, *, host: str = "127.0.0.1",
                     port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Start the daemon on a background thread and return ``(httpd, thread, url)``. ``port=0`` binds a
    free ephemeral port (so the desktop window never collides with a running ``just serve``). The caller
    owns shutdown: ``httpd.shutdown(); httpd.server_close(); thread.join()``. Used by ``prm app``, where
    the GUI must own the main thread."""
    httpd = make_server(home, host=host, port=port)
    bound_host, bound_port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, f"http://{bound_host}:{bound_port}"
