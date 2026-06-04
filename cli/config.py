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

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIRNAME = "prm-data"
ENV_HOME = "PRM_HOME"


@dataclass(frozen=True)
class PrmHome:
    """The one directory holding all of an instance's stores."""

    root: Path

    @property
    def shared_db(self) -> Path:
        return self.root / "shared.db"

    @property
    def private_db(self) -> Path:
        return self.root / "private.db"

    @property
    def proposals_dir(self) -> Path:
        return self.root / "proposals"

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
    def lock_file(self) -> Path:
        return self.root / ".lock"

    def exists(self) -> bool:
        return self.root.is_dir()

    def create(self) -> "PrmHome":
        """Create the home directory skeleton (the DB files are created by the loader, M1c)."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(exist_ok=True)
        self.snapshots_dir.mkdir(exist_ok=True)
        return self


def resolve_home(data_dir: str | os.PathLike | None = None, *, env=None, cwd=None) -> PrmHome:
    """Resolve the PRM home per the documented precedence."""
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else Path(cwd)

    if data_dir:
        root = Path(data_dir)
    elif env.get(ENV_HOME):
        root = Path(env[ENV_HOME])
    else:
        # v0.2 will consult a config file here before falling back.
        root = cwd / DEFAULT_DIRNAME

    return PrmHome(root=root.expanduser())
