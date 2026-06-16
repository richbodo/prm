"""AC-7 field-debug substrate (sanitized) — a diagnostic state dump and a local error log, so a user can
self-diagnose and file a useful bug report that names the exact source revision (AC-15) **without leaking
contact data**.

`state_dump(home)` returns **metadata only** — store existence, record/contact counts, schema versions,
lock state, snapshot + pending-proposal counts, the build label, recent (sanitized) errors, and the host
platform. No names, emails, or jCards cross this surface. `capture_error()` appends one sanitized line to
`<home>/diag/errors.log.jsonl` (error type + redacted message + context) — the field-error capture AC-7
asks for, kept **local** (PRM is never-SaaS — there is no error HTTP sink). `lock_state()` probes whether
another process holds the canonical-writer lock (the AC-6 escape / AC-11 detection surface).
"""

from __future__ import annotations

import json
import platform
import re
import sys
from datetime import datetime, timezone

from core import build_label, media, projection, relationships_db, shared_db
from core.lock import LockError, file_lock

_MAX_ERRORS = 20
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact(s: str) -> str:
    """Strip obvious PII (email addresses) and cap length — error text may echo a contact value."""
    return _EMAIL.sub("<email>", str(s))[:500]


def _diag_dir(home):
    return home.root / "diag"


def lock_state(home) -> str:
    """`held` (another process owns the home's writer lock) / `free` (acquirable now) / `absent`."""
    if not home.lock_file.exists():
        return "absent"
    try:
        with file_lock(home.lock_file):
            return "free"                                    # we acquired it → no live holder
    except LockError:
        return "held"
    except OSError:
        return "unknown"


def tail_errors(home, n: int = _MAX_ERRORS) -> list:
    log = _diag_dir(home) / "errors.log.jsonl"
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def capture_error(home, exc, *, context: str = "") -> None:
    """Append one sanitized error entry to the local diag log. Never raises — diagnostics must not crash
    the caller."""
    try:
        d = _diag_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        entry = {"at": _now_iso(), "context": context, "type": type(exc).__name__,
                 "message": _redact(str(exc)), "build_label": build_label.build_label()}
        with open(d / "errors.log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def state_dump(home) -> dict:
    """A sanitized, PII-free snapshot of this PRM home for self-diagnosis / bug reports (AC-7)."""
    out = {
        "build_label": build_label.build_label(),
        "generated_at": _now_iso(),
        "home": str(home.root),
        "home_exists": home.exists(),
        "lock": lock_state(home),
        "platform": {"python": sys.version.split()[0], "os": platform.platform()},
        "stores": {},
        "snapshots": len(list(home.snapshots_dir.glob("private-*.db"))) if home.snapshots_dir.is_dir() else 0,
        "proposals_pending": len(list(home.proposals_dir.glob("*.json"))) if home.proposals_dir.is_dir() else 0,
        "recent_errors": tail_errors(home),
    }
    for name, db in (("shared", home.shared_db), ("private", home.relationships_db)):
        if not db.exists():
            continue
        stats = shared_db.stats if name == "shared" else relationships_db.stats
        try:
            out["stores"][name] = stats(db)
        except Exception as exc:  # noqa: BLE001  (a corrupt/mismatched store is exactly what diag must report)
            out["stores"][name] = {"error": _redact(str(exc))}
    # Media store health: stored blob count + orphan/missing/corrupt hashes (no PII — hashes only).
    try:
        out["media"] = media.verify(home, projection.referenced_image_hashes(home))
    except Exception as exc:  # noqa: BLE001
        out["media"] = {"error": _redact(str(exc))}
    return out
