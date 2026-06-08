"""core/snapshots.py — the pre-apply snapshot ring for private.db (AC-9, INV-12).

Before the daemon applies a merge it snapshots private.db here; **restoring a snapshot is v0.1 Undo**
(the locked reversibility choice — see docs/design-notes/dedupe-design.md). Copies use sqlite's online
backup API so a WAL-mode database is captured consistently (a bare file copy could miss the -wal).
ISO-named, ISO-sorted; an old-tail rotation keeps the ring bounded.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core import private_db

KEEP = 20   # snapshots retained in the ring


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


def _copy_db(src: Path, dest: Path) -> None:
    s = sqlite3.connect(src)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def snapshot(home) -> Path:
    """Capture private.db into the ring and return the snapshot path. Rotates the ring afterward."""
    private_db.ensure(home.private_db)
    home.snapshots_dir.mkdir(parents=True, exist_ok=True)
    dest = home.snapshots_dir / f"private-{_stamp()}.db"
    _copy_db(home.private_db, dest)
    rotate(home)
    return dest


def list_snapshots(home) -> list:
    """Snapshot paths, oldest → newest."""
    if not home.snapshots_dir.is_dir():
        return []
    return sorted(home.snapshots_dir.glob("private-*.db"))


def restore(home, snapshot_path) -> None:
    """Overwrite private.db with a snapshot's contents (Undo). Caller holds the file-lock."""
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.exists():
        raise FileNotFoundError(f"no such snapshot: {snapshot_path}")
    _copy_db(snapshot_path, home.private_db)


def rotate(home, *, keep: int = KEEP) -> None:
    snaps = list_snapshots(home)
    for old in snaps[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
