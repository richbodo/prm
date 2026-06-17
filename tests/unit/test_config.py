#!/usr/bin/env python3
"""Tests for cli.config — PRM home resolution + the installed-mode per-user config (data dir).

The resolution precedence is load-bearing: the repo-local ./prm-data/ default MUST stand when nothing
is configured (so demo/dev never writes outside the repo), and `just install` opts into a per-user dir
by writing the config file. Every test injects an isolated env rooted at a temp HOME, so it never reads
or writes the real user config on the host.

    python tests/unit/test_config.py
    pytest  tests/unit/test_config.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cli import config  # noqa: E402


def _env(home: Path, **extra) -> dict:
    """A minimal isolated environment rooted at a temp HOME (no PRM_HOME / XDG unless given)."""
    return {"HOME": str(home), **extra}


# ------------------------------------------------------------------ platform dirs
def test_platform_dirs_macos():
    with tempfile.TemporaryDirectory() as tmp:
        data, cfg = config._platform_dirs(_env(Path(tmp)), plat="darwin", osname="posix")
        assert data == Path(tmp) / "Library" / "Application Support" / "PRM"
        assert cfg == data / "config.json"


def test_platform_dirs_linux_xdg():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data, cfg = config._platform_dirs(_env(tmp), plat="linux", osname="posix")
        assert data == tmp / ".local" / "share" / "prm"
        assert cfg == tmp / ".config" / "prm" / "config.json"
        # XDG_* overrides are honored
        env = _env(tmp, XDG_DATA_HOME=str(tmp / "xd"), XDG_CONFIG_HOME=str(tmp / "xc"))
        data2, cfg2 = config._platform_dirs(env, plat="linux", osname="posix")
        assert data2 == tmp / "xd" / "prm" and cfg2 == tmp / "xc" / "prm" / "config.json"


def test_platform_dirs_windows():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        env = _env(tmp, LOCALAPPDATA=str(tmp / "Local"), APPDATA=str(tmp / "Roaming"))
        data, cfg = config._platform_dirs(env, plat="win32", osname="nt")
        assert data == tmp / "Local" / "PRM"
        assert cfg == tmp / "Roaming" / "PRM" / "config.json"


# ------------------------------------------------------------------ resolution precedence
def test_resolve_default_is_repo_local_when_unset():
    # The invariant: nothing configured → ./prm-data under cwd (demo/dev never escapes the repo).
    with tempfile.TemporaryDirectory() as tmp:
        home = config.resolve_home(None, env=_env(Path(tmp)), cwd=Path(tmp) / "repo")
        assert home.root == Path(tmp) / "repo" / "prm-data"


def test_resolve_precedence():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        config.write_user_config(tmp / "mydata", env=_env(tmp))         # user config file present…
        assert config.resolve_home(None, env=_env(tmp), cwd=tmp / "repo").root == tmp / "mydata"
        env_h = _env(tmp, PRM_HOME=str(tmp / "envhome"))                # …PRM_HOME beats the config file…
        assert config.resolve_home(None, env=env_h, cwd=tmp / "repo").root == tmp / "envhome"
        assert config.resolve_home(tmp / "explicit", env=env_h).root == tmp / "explicit"  # …--data-dir wins


def test_explicit_env_is_hermetic():
    # An explicit env (not os.environ) must NOT leak the real $HOME into the config path — otherwise a
    # user config on the host would make resolution non-reproducible in tests (regression: it did).
    _, cfg = config._platform_dirs({})                               # no HOME/USERPROFILE given
    assert not str(cfg).startswith(str(Path.home()))                 # didn't reach into the real home
    assert config.resolve_home(None, env={}, cwd="/work").root == Path("/work/prm-data")


def test_user_config_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        env = _env(tmp)
        assert config.read_user_config(env) == {}                      # absent → empty, never raises
        path = config.write_user_config(tmp / "data", env=env)
        assert path == config.user_config_path(env) and path.exists()
        assert config.read_user_config(env)["data_dir"] == str(tmp / "data")


def test_describe_resolution_reports_source():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        env = _env(tmp)
        assert config.describe_resolution(None, env=env)["resolved_from"].startswith("default")
        config.write_user_config(tmp / "data", env=env)
        assert config.describe_resolution(None, env=env)["resolved_from"] == "user config file"
        env_h = _env(tmp, PRM_HOME=str(tmp / "eh"))
        assert config.describe_resolution(None, env=env_h)["resolved_from"] == "PRM_HOME env"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append(t.__name__)
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
