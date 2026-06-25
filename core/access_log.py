"""mcp_access.log.jsonl — a best-effort, append-only record of every read the MCP surface serves.

One JSONL line per ``get_contact`` / ``get_provenance``: which contact, the disclosure ``mode``, whether
per-request review was on and the contact ``approved``, the ``decision`` (``released`` | ``withheld`` |
``no-shareable``), the overlay field **names** returned vs withheld (**NEVER values** — INV-1), and the
server **build label** (so a stale server is detectable — prm#65). This is the "what did an external
system read, and when" signal the External Access surface renders.

Written by the **MCP server** (a separate process — a plain file append, the same pattern P4 uses for
staged requests in ``disclosure_requests/``); read by the daemon / CLI. Bounded like the snapshot ring:
once the file passes a size cap it is trimmed to its most-recent tail. A **leaf** — stdlib only, no
``cli``/``core`` peer imports — so any layer may log without a cycle.

Best-effort by design: a logging failure must never break the read it records, so ``append`` swallows
``OSError`` and returns a bool. Rationale: ``plans/external-access-visibility.md`` §2.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_MAX_BYTES = 512 * 1024     # trim once the log passes this …
_KEEP_BYTES = 256 * 1024    # … down to this most-recent tail (hysteresis amortizes the rewrites)


def append(home, entry: dict) -> bool:
    """Append ``entry`` as one JSON line. **Never raises** — a logging failure must not break the read it
    records. Trims to the recent tail once the file passes the size cap. Returns whether it was written."""
    try:
        p = Path(home.access_log)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")   # O_APPEND: one small line is atomic
        if p.stat().st_size > _MAX_BYTES:
            _trim(p)
        return True
    except OSError:
        return False


def _trim(p: Path) -> None:
    """Keep only the most-recent ``_KEEP_BYTES`` (aligned to a whole line). Best-effort + atomic swap; a
    concurrent append racing the rewrite may be dropped — acceptable for a diagnostic log."""
    try:
        data = p.read_bytes()
        if len(data) <= _KEEP_BYTES:
            return
        tail = data[-_KEEP_BYTES:]
        nl = tail.find(b"\n")                       # drop the partial leading line
        tail = tail[nl + 1:] if nl != -1 else tail
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_bytes(tail)
        tmp.replace(p)
    except OSError:
        pass


def _read(home) -> list:
    p = Path(home.access_log)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:                          # tolerate a torn line from a concurrent append
            continue
    return out


def recent(home, n: int = 50) -> list:
    """The most recent ``n`` log entries, **newest first**. Read-only; tolerant of partial/corrupt lines."""
    entries = _read(home)
    entries.reverse()
    return entries[: max(0, n)]


def last_read(home, contact_id: str | None = None) -> dict | None:
    """The most recent read entry overall, or for one ``contact_id``. ``None`` if there is none."""
    for e in recent(home, n=1_000_000):
        if contact_id is None or e.get("contact_id") == contact_id:
            return e
    return None
