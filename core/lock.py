"""Single-instance advisory file-lock (AC-PRM-C).

Guards the canonical writers so two PRM processes never race on a PRM home (the ingester at import
time, the daemon at apply time). Advisory `flock` on Unix; a best-effort no-op where `fcntl` is
absent (Windows support is a later concern — v0.1 targets macOS/Linux).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-Unix
    _HAVE_FCNTL = False


class LockError(RuntimeError):
    """Raised when another process already holds the home's lock."""


@contextmanager
def file_lock(lock_path):
    """Acquire an exclusive, non-blocking lock for the duration of the context."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")
    try:
        if _HAVE_FCNTL:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LockError(
                    f"another PRM process is using this home (lock held at {lock_path})"
                ) from exc
        yield
    finally:
        try:
            if _HAVE_FCNTL:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
