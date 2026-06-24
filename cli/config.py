"""PRM home resolution — where all stores live.

Everything for one instance lives under a single **PRM home** directory so every component (ingester,
daemon, MCP server, snapshot ring) agrees on one location. v0.1 resolves it cheaply; the config
*file* and platform data dirs (XDG / macOS / Windows) are a v0.2 addition that slots in here.

Resolution order (first that is set wins):

    --data-dir DIR  ->  PRM_HOME env  ->  [v0.2: config file's data_dir]  ->  ./prm-data/ (default)

The repo-local ``./prm-data/`` default is deliberate: demo/dev runs never write outside the working
tree (see ``plans/v0.1-implementation-plan.md`` §5).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIRNAME = "prm-data"
ENV_HOME = "PRM_HOME"

# Per-user install locations (installed mode — distinct from the repo-local ./prm-data/ dev default).
# Hand-rolled per platform rather than taking a `platformdirs` dependency, to keep the core tiny-deps.
_APP_DIRNAME = "PRM"       # macOS / Windows application-dir name
_XDG_DIRNAME = "prm"       # XDG (Linux) lowercase app-dir name


@dataclass(frozen=True)
class PrmHome:
    """The one directory holding all of an instance's stores."""

    root: Path

    @property
    def shared_db(self) -> Path:
        return self.root / "shared.db"

    @property
    def relationships_db(self) -> Path:
        return self.root / "relationships.db"

    @property
    def legacy_private_db(self) -> Path:
        """The v0.1 store name. Present only until the one-time rename migration runs (see
        ``core.relationships_db.migrate_legacy``)."""
        return self.root / "private.db"

    @property
    def media_dir(self) -> Path:
        """Content-addressed image/media store (bytes on disk; refs in relationships.db)."""
        return self.root / "media"

    @property
    def imports_dir(self) -> Path:
        """Ephemeral staging area for workspace uploads awaiting preview/commit (one subdir per
        upload session, keyed by a server-minted token). Swept on daemon startup; removed on
        commit/cancel. Distinct from the durable stores so a half-finished upload is never mistaken
        for data."""
        return self.root / "imports"

    @property
    def proposals_dir(self) -> Path:
        return self.root / "proposals"

    @property
    def disclosure_requests_dir(self) -> Path:
        """Staged per-request AI-read approvals (P4): one JSON file per contact the AI wants to read while
        per-request review is on. Written by the MCP server (a file write, like ``proposals/``), reviewed in
        the workspace; swept when the disclosure mode changes."""
        return self.root / "disclosure_requests"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    @property
    def audit_log(self) -> Path:
        return self.root / "audit.log.jsonl"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def workspace_url_file(self) -> Path:
        """Where a running daemon writes its session URL (carrying the one-time key) so ``prm open`` can
        launch the browser at it. Present only while a server runs (removed on clean shutdown)."""
        return self.root / "workspace.url"

    @property
    def lock_file(self) -> Path:
        return self.root / ".lock"

    def exists(self) -> bool:
        return self.root.is_dir()

    def create(self) -> "PrmHome":
        """Create the home directory skeleton (the DB files are created by the loader, M1c)."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(exist_ok=True)
        self.disclosure_requests_dir.mkdir(exist_ok=True)
        self.snapshots_dir.mkdir(exist_ok=True)
        self.media_dir.mkdir(exist_ok=True)
        self.imports_dir.mkdir(exist_ok=True)
        return self


# --------------------------------------------------------------------------- installed-mode locations
def _platform_dirs(env, *, plat: str | None = None, osname: str | None = None) -> tuple[Path, Path]:
    """``(default_data_dir, user_config_path)`` for the platform. macOS → Application Support; Windows →
    LOCALAPPDATA/APPDATA; else XDG (~/.local/share, ~/.config). ``plat``/``osname`` are injectable so the
    branches stay testable on any host."""
    plat = sys.platform if plat is None else plat
    osname = os.name if osname is None else osname
    home_str = env.get("HOME") or env.get("USERPROFILE")
    if not home_str and env is os.environ:     # real-home fallback for production only — an explicit env
        home_str = os.path.expanduser("~")     # (e.g. {} in tests) stays hermetic, never touching $HOME
    home = Path(home_str or ".")
    if plat == "darwin":
        base = home / "Library" / "Application Support" / _APP_DIRNAME
        return base, base / "config.json"
    if osname == "nt" or plat.startswith("win"):
        data = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / _APP_DIRNAME
        cfg = Path(env.get("APPDATA") or home / "AppData" / "Roaming") / _APP_DIRNAME / "config.json"
        return data, cfg
    data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / _XDG_DIRNAME
    cfg = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / _XDG_DIRNAME / "config.json"
    return data, cfg


def default_data_dir(env=None) -> Path:
    """The recommended per-user data directory for installed mode (see ``_platform_dirs``)."""
    return _platform_dirs(os.environ if env is None else env)[0]


def user_config_path(env=None) -> Path:
    """The fixed per-user config file (records the active ``data_dir``). Read at startup to find the home,
    so it lives at a platform path independent of the home it points to."""
    return _platform_dirs(os.environ if env is None else env)[1]


def read_user_config(env=None) -> dict:
    """The user config as a dict (``{}`` if absent or unreadable — never raises)."""
    path = user_config_path(env)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_user_config(data_dir, env=None) -> Path:
    """Record ``data_dir`` as the active data location (creating the config dir). Returns the config path.
    This is what ``just install`` / ``prm config --set-data-dir`` write so an installed ``prm`` finds the
    home with no env var set."""
    path = user_config_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = read_user_config(env)
    config["data_dir"] = str(Path(data_dir).expanduser())
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def relocate_data(src, dst) -> str:
    """Safely move a PRM home from ``src`` to ``dst`` (used by ``just install`` before recording it).
    Never overwrites, merges, nests, or deletes ``src``'s data. Returns an action string:

    - ``"moved"``                 — ``src`` moved to ``dst`` (dst was absent or an empty dir).
    - ``"skipped-dst-has-store"`` — ``dst`` already holds a PRM store (``shared.db``); both left in place.
    - ``"skipped-dst-nonempty"``  — ``dst`` exists, is non-empty, has no store; refused (no nesting).
    - ``"no-source"``             — ``src`` has no ``shared.db``; nothing to move.
    """
    src, dst = Path(src).expanduser(), Path(dst).expanduser()
    if not (src / "shared.db").exists():
        return "no-source"
    if (dst / "shared.db").exists():
        return "skipped-dst-has-store"
    if dst.exists() and any(dst.iterdir()):
        return "skipped-dst-nonempty"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():                      # an empty dir — remove it so the move lands AT dst, not inside it
        dst.rmdir()
    shutil.move(str(src), str(dst))       # shutil (not rename) so a cross-filesystem move still works
    return "moved"


def resolve_home(data_dir: str | os.PathLike | None = None, *, env=None, cwd=None) -> PrmHome:
    """Resolve the PRM home per the documented precedence:
    ``--data-dir`` → ``PRM_HOME`` → the user config file's ``data_dir`` (installed mode) → ``./prm-data/``.
    The repo-local default stands when nothing is configured, so demo/dev runs never write outside the
    repo; ``just install`` opts into a per-user location by writing the config file."""
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else Path(cwd)

    if data_dir:
        root = Path(data_dir)
    elif env.get(ENV_HOME):
        root = Path(env[ENV_HOME])
    elif (configured := read_user_config(env).get("data_dir")):
        root = Path(configured)
    else:
        root = cwd / DEFAULT_DIRNAME

    return PrmHome(root=root.expanduser())


def describe_resolution(data_dir=None, *, env=None) -> dict:
    """How the home resolves right now and which rule won — backs ``prm config --show``."""
    env = os.environ if env is None else env
    home = resolve_home(data_dir, env=env)
    if data_dir:
        source = "--data-dir"
    elif env.get(ENV_HOME):
        source = f"{ENV_HOME} env"
    elif read_user_config(env).get("data_dir"):
        source = "user config file"
    else:
        source = f"default (./{DEFAULT_DIRNAME})"
    cfg = user_config_path(env)
    return {"home": str(home.root), "exists": home.exists(), "resolved_from": source,
            "config_file": str(cfg), "config_exists": cfg.exists()}
