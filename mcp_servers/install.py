"""One-shot installer: register PRM's MCP servers in Claude Desktop — safely.

`just mcp-install` runs this so the user never hand-edits `claude_desktop_config.json` (where a stray
comma silently makes Claude Desktop ignore *every* server). Non-destructive by construction:

- **Locate** the platform config (macOS / Windows / Linux), or `--config PATH`.
- **Back up** the existing file (timestamped) before any write.
- **Merge, never replace** — PRM's two `prm-*` entries are added/updated under `mcpServers`; every
  other server *and* every other key (Claude Desktop writes `preferences`, `coworkUserFilesPath`, … in
  this same file) is preserved exactly.
- **Idempotent** — re-running updates the `prm-*` entries to the current paths (self-healing if the repo
  moved, or if Claude Desktop reset `mcpServers`) and reports "already up to date" when nothing changed.
- **Atomic write** (temp + replace) so a crash can't leave a half-written config.

Pure stdlib; no `mcp` SDK import. Honors INV-1 — it only touches the local Claude config and prints a
restart reminder. Entry paths come from `claude_config` (one source of truth, shared with
`--print-config`).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # import cli / mcp_servers without installing prm

from cli.config import resolve_home  # noqa: E402
from mcp_servers import claude_config  # noqa: E402

# (server_key, script filename) — the servers PRM registers. Order = display order.
SERVERS = [("prm-shared-data", "shared_data_ops.py"), ("prm-dedup", "dedup_ops.py"),
           ("prm-private-data", "private_data_ops.py")]
PRM_KEYS = [k for k, _ in SERVERS]


class ConfigError(RuntimeError):
    pass


# --------------------------------------------------------------------------- locate
def default_config_path() -> Path:
    """Claude Desktop's config path for this OS."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"   # Linux best-effort


# --------------------------------------------------------------------------- read / write
def load_config(path: Path) -> dict:
    """Parse the existing config. Missing/empty → ``{}``. Invalid JSON → ``ConfigError`` (never clobber:
    a malformed file is likely *why* Claude Desktop is ignoring servers, so we refuse to overwrite it)."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path} exists but is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} is not a JSON object")
    return data


def backup(path: Path) -> Path | None:
    """Copy the existing config aside (timestamped) before writing. Returns the backup path, or None."""
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = path.with_name(f"{path.name}.bak.prm-{stamp}")
    dest.write_bytes(path.read_bytes())
    return dest


def write_atomic(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.prm-tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- merge (pure)
def desired_entries(home) -> dict:
    """The `mcpServers` entries PRM wants, bound to `home`."""
    merged: dict = {}
    for key, script in SERVERS:
        merged.update(claude_config.server_entry(key, script, home.root))
    return merged


def plan(config: dict, entries: dict) -> list[tuple[str, str]]:
    """[(server_key, 'add'|'update'|'unchanged')] for the install."""
    existing = config.get("mcpServers") or {}
    out = []
    for key, val in entries.items():
        if key not in existing:
            out.append((key, "add"))
        elif existing[key] != val:
            out.append((key, "update"))
        else:
            out.append((key, "unchanged"))
    return out


def apply_entries(config: dict, entries: dict) -> dict:
    """Return a new config with `entries` merged under `mcpServers` — all other keys preserved."""
    new = dict(config)
    servers = dict(new.get("mcpServers") or {})
    servers.update(entries)
    new["mcpServers"] = servers
    return new


def remove_entries(config: dict, keys) -> tuple[dict, list[str]]:
    """Return a new config with `keys` removed from `mcpServers`, plus the keys actually removed."""
    new = dict(config)
    servers = dict(new.get("mcpServers") or {})
    removed = [k for k in keys if k in servers]
    for k in removed:
        servers.pop(k, None)
    new["mcpServers"] = servers
    return new, removed


# --------------------------------------------------------------------------- CLI
def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


_RESTART = ("Now FULLY QUIT Claude Desktop (⌘Q — closing the window isn't enough) and reopen it. "
            "Then Settings ▸ Developer ▸ Local MCP servers should list them.")


def _cmd_uninstall(path: Path, *, assume_yes: bool) -> int:
    config = load_config(path)
    new, removed = remove_entries(config, PRM_KEYS)
    if not removed:
        print(f"PRM's servers are not in {path} — nothing to remove.")
        return 0
    print(f"Will remove from {path}:  " + ", ".join(removed))
    if not _confirm("Remove them?", assume_yes=assume_yes):
        print("Aborted — nothing changed.")
        return 0
    bak = backup(path)
    write_atomic(path, new)
    if bak:
        print(f"  backed up → {bak}")
    print(f"✓ Removed {', '.join(removed)}. {_RESTART}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="prm mcp-install",
        description="Register PRM's MCP servers in Claude Desktop's config (safe, idempotent merge).")
    ap.add_argument("--config", help="path to claude_desktop_config.json (default: the OS location)")
    ap.add_argument("--data-dir", help="PRM home the servers read (else $PRM_HOME, else ./prm-data/)")
    ap.add_argument("--print", dest="dry", action="store_true", help="show what would change; write nothing")
    ap.add_argument("--non-interactive", action="store_true", help="apply without prompting")
    ap.add_argument("--uninstall", action="store_true", help="remove PRM's servers from the config")
    args = ap.parse_args(argv)

    path = Path(args.config).expanduser() if args.config else default_config_path()

    try:
        if args.uninstall:
            return _cmd_uninstall(path, assume_yes=args.non_interactive)

        home = resolve_home(args.data_dir)
        entries = desired_entries(home)
        try:
            config = load_config(path)
        except ConfigError as exc:
            print(f"⚠ {exc}", file=sys.stderr)
            print("  A malformed config makes Claude Desktop ignore ALL servers — this is likely why "
                  "yours vanished.\n  Your file is left untouched. Fix the JSON (or move it aside) and "
                  "re-run; a backup is in the same folder.", file=sys.stderr)
            return 2

        changes = plan(config, entries)
        if all(state == "unchanged" for _, state in changes):
            print(f"✓ PRM's servers are already installed and up to date in {path}.")
            return 0

        # Preview.
        print(f"Claude Desktop config:  {path}{'  (will be created)' if not path.exists() else ''}")
        print(f"PRM home the servers read:  {home.root.resolve()}")
        other = [k for k in (config.get('mcpServers') or {}) if k not in PRM_KEYS]
        if other:
            print(f"Other MCP servers already configured (kept as-is):  {', '.join(other)}")
        for key, state in changes:
            print(f"  {'+ add   ' if state == 'add' else '~ update' if state == 'update' else '= keep  '} {key}")
        if not claude_config.VENV_PYTHON.exists():
            print(f"⚠ {claude_config.VENV_PYTHON} doesn't exist yet — run `just mcp-install-deps` so the "
                  "registered command works.", file=sys.stderr)

        if args.dry:
            print("\n(--print) Would write these entries under \"mcpServers\":")
            print(claude_config.block(entries))
            return 0
        if not _confirm("\nApply this to your Claude Desktop config?", assume_yes=args.non_interactive):
            print("Aborted — nothing changed.")
            return 0

        bak = backup(path)
        write_atomic(path, apply_entries(config, entries))
        if bak:
            print(f"  backed up → {bak}")
        print(f"✓ Installed PRM's MCP servers into {path}.")
        print(f"  {_RESTART}")
        print("  (Undo anytime with `just mcp-uninstall`.)")
        return 0
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
