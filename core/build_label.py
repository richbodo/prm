"""AC-15 build label — a unique label tied to the source revision, available at run/serve time.

PRM's flavor is `never-distributed-single-user`: it is built/installed by the user from source, so there
is **no build step** to substitute a label into. The toolkit's "substituted at build *and* serve time"
collapses to one site here — the label is derived at run time from the git checkout the user is running:
``<YYYY-MM-DD>-<short-sha>`` (the HEAD commit's date + short SHA, with ``-dirty`` when the tree has
uncommitted changes). For archived/exported source with no ``.git`` (e.g. a Software-Heritage snapshot),
it falls back to a committed ``BUILD_LABEL`` file (stamped at archival), then to the packaged version.

The label surfaces in ``prm status``, ``prm doctor``, ``/api/status`` + ``/api/diag``, and the workspace
footer, so a field bug report can name the exact source revision it came from (AC-7 / AC-15).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    val = out.stdout.strip()
    return val if out.returncode == 0 and val else None


def compute_build_label(repo: Path) -> str:
    """The build label for a source tree at ``repo`` — git-derived, else stamp-file, else packaged."""
    sha = _git(repo, "rev-parse", "--short=10", "HEAD")
    if sha:
        date = (_git(repo, "show", "-s", "--format=%cd", "--date=format:%Y-%m-%d", "HEAD")
                or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        dirty = "-dirty" if _git(repo, "status", "--porcelain") else ""
        return f"{date}-{sha}{dirty}"
    stamp = repo / "BUILD_LABEL"                              # archival fallback (no .git)
    if stamp.exists():
        text = stamp.read_text(encoding="utf-8").strip()
        if text:
            return text
    return _packaged_version()


def _packaged_version() -> str:
    try:
        from importlib.metadata import version
        return f"v{version('prm')}"
    except Exception:  # noqa: BLE001  (PackageNotFoundError / ImportError → fall through)
        return "unknown"


@lru_cache(maxsize=1)
def build_label() -> str:
    """This running PRM's build label (computed once per process)."""
    return compute_build_label(_REPO)
