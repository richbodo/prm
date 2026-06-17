# Plan — Desktop app + daily-use install

*Status: implemented (branch `feat/desktop-app-install`). Companion to
[`v0.2-implementation-plan.md`](v0.2-implementation-plan.md); advances the v0.2 roadmap item
"config file (records the active `data_dir`) + platform data dirs (XDG / macOS / Windows) for installed
(non-repo) use." This doc owns the implementation detail **and** the rationale for this feature.*

## Goal

Make PRM usable as part of a daily routine — built from source, installed for the user, launched as a
native window — **without code updates ever touching the user's data**. Three pieces, in dependency
order:

1. **Data lives outside the source tree**, in a per-user location, found automatically.
2. **`prm app`** — a native desktop window (the daily launcher).
3. **`just install`** — an interactive wizard that ties it together.

## Why this shape (decisions + rationale)

- **PRM is a Python daemon + a static SPA, not a JS app.** Any "native shell" is just a window pointed
  at the local daemon; the daemon still runs underneath. So the *frameworks* question (Electron/Tauri)
  is secondary — the load-bearing requirement ("updates don't clobber data") is an **install-layout**
  problem, solved by separating code from data, not by a windowing toolkit.
- **Window = pywebview** (the `[app]` optional extra), not Electron/Tauri. It stays in Python (no new
  language/toolchain), uses the OS-native webview (WKWebView / WebView2 / WebKitGTK) so there's **no
  bundled Chromium**, and it's one optional dep that never touches the core's tiny-deps invariant.
  Electron (bundles Chromium + Node, ~150 MB) clashes hardest with both the tiny-deps ethos and PRM's
  *build-from-source-and-verify* distribution posture (a downloaded Chromium binary is exactly the
  opaque-binary end of the spectrum the docs flag as worse). Tauri is great for distributing to others
  later (Rust + a sidecar for the Python daemon) — overkill for single-user-from-source today.
- **Data resolution via a per-user config file**, not a shell-profile env hack. `resolve_home` gains one
  step: `--data-dir` → `PRM_HOME` → **user config file's `data_dir`** → `./prm-data/`. When nothing is
  configured the repo-local default stands, so **demo/dev never writes outside the repo** (the standing
  invariant). The config file lives at a fixed platform path (independent of the home it points to), so
  there's no bootstrap circularity. This is the roadmap's v0.2 config-file item.
- **Install must be editable (`pipx install --editable ".[app]"`).** Load-bearing and easy to get wrong:
  `workspace/` is **not** a packaged asset (it's resolved relative to the source tree), so a non-editable
  install would ship a broken UI. Editable points the install at the source dir → `git pull` updates the
  live app, and `workspace/` + the SQL schema resolve correctly. It also matches the build-from-source
  posture. (Fallback when `pipx` is absent: editable `pip install -e` into `./.venv`.)
- **`git pull` *is* the updater.** For a single-user-from-source workflow you don't want Sparkle-style
  silent auto-update; `git pull` + relaunch is the most transparent updater, and data lives elsewhere so
  it's never touched. Reversibility is already covered by the snapshot ring + audit log.
- **Platform dirs are hand-rolled in stdlib**, not a `platformdirs` dependency — the core stays
  tiny-deps. **pywebview is an optional `[app]` extra**, never a core runtime dep.
- **`just demo` is pinned to the `./prm-data` sandbox** (`PRM_HOME=prm-data`). After install,
  `resolve_home` returns the per-user dir, so an unpinned `just demo` would seed the user's *real* data
  with synthetic contacts. Pinning keeps demo sandboxed forever; every other command follows the
  resolver. (`just clean-data` already only removes `./prm-data`, so it can't touch real data.)

## What was built

**Phase 1 — data location + `prm config`** (`cli/config.py`, `cli/prm_import.py`, `justfile`):
- `default_data_dir()` / `user_config_path()` (macOS / Windows / XDG, env-injectable + platform-injectable
  so the branches are testable on any host); `read_user_config()` / `write_user_config()`;
  `describe_resolution()`.
- `resolve_home` gains the user-config step (precedence above).
- `prm config --set-data-dir DIR` (records the location; the wizard calls it) and `prm config [--show]`
  (resolved home + config path + which rule won; `--json` too).
- `just demo` pinned to the sandbox.

**Phase 2 — `prm app`** (`pyproject.toml`, `daemon/server.py`, `cli/prm_import.py`, `justfile`):
- `[project.optional-dependencies] app = ["pywebview>=5"]`.
- `daemon.start_background(home, port=0) → (httpd, thread, url)` — reuses `make_server` on a **free
  ephemeral port** (so the window never collides with a running `just serve`), serves on a background
  thread; the caller owns shutdown.
- `cmd_app` — starts the daemon in the background, opens a pywebview window at the url, runs until the
  window closes, then shuts the server down. If the `[app]` extra isn't installed → a clean message +
  exit 1 (never a raw `ImportError`). Opens even with no data yet (the SPA shows its empty state).
- `just app`.

**Phase 3 — `just install`** (`justfile`): an interactive, re-runnable wizard — platform-default data dir
(overridable) → offer to **move** an existing `./prm-data` (never overwriting a populated target) →
`pipx install --editable ".[app]"` → `prm config --set-data-dir` → a summary (launch / update / verify).
Every destructive step is gated by a prompt. **When pipx is missing it asks before falling back** — and on
yes installs pipx via Homebrew (or `pip install --user`, with a `--break-system-packages` retry for
PEP-668), accepting `python3 -m pipx` if the script dir is off PATH; only on *decline* (or a failed pipx
install) does it use the `./.venv` fallback. (The first cut silently fell back to `./.venv` — fixed.)

## Testing

- `tests/unit/test_config.py` — resolution precedence (incl. the **repo-default-when-unset** invariant),
  platform dirs for macOS/Linux/Windows with env overrides, user-config round-trip, `describe_resolution`
  sources. All hermetic (injected temp-HOME env; never reads the real user config).
- `tests/unit/test_daemon_read_api.py::test_start_background_serves_then_shuts_down` — socket smoke for
  the background-server helper.
- `tests/unit/test_cli_app.py` — `prm app` fails gracefully without the `[app]` extra.
- **Manual QA** (the GUI window + the real pipx install + the real config write are not unit-tested — the
  bash wizard keeps its risky logic in tested Python, and the bash is syntax-checked + sandbox-run with
  stubs): `just install` (defaults) → data at the per-user dir, `prm config --show` agrees → `prm app` /
  `just app` opens, contacts load, set-a-photo works, closing stops the server with no orphan port →
  `git pull` (simulated) → relaunch, change present + data intact → re-run `just install` (idempotent) →
  `just demo` seeds only `./prm-data`.

## Out of scope (follow-ups)

- A double-click macOS `.app`/`.command` bundle (deferred deliberately — `prm app` / `just app` covers
  launching; a real bundle adds notarization/packaging fuss).
- Windows/Linux desktop shortcuts (`.desktop`, `.lnk`/`.bat`).
- A non-editable wheel (would need to package `workspace/` + the schema).
- Auto-update beyond `git pull`; code-signing / notarization.
