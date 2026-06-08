"""core/audit.py — the append-only audit log (INV-12).

Every applied change (a merge changeset, a dismissal, an undo) appends one JSON line to
``audit.log.jsonl``. Append-only is the contract: entries are never edited or removed, so the log is a
durable, replayable record of what happened to the private store. The daemon appends under the
file-lock; this module only formats and appends.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append(home, entry: dict) -> dict:
    """Append one entry (stamped with ``at`` if absent) to audit.log.jsonl. Returns the entry."""
    home.root.mkdir(parents=True, exist_ok=True)
    entry = {"at": _now_iso(), **entry}
    with open(home.audit_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read(home) -> list:
    """Every audit entry, oldest → newest (malformed lines are skipped, never raised)."""
    if not home.audit_log.exists():
        return []
    out = []
    for line in home.audit_log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
