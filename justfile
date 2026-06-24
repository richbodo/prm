# PRM — command runner. Run `just` with no args for the menu.
# A local-only Personal Relationship Manager (PNA reference design). See docs/users-guide.md.
#
# First time:  just setup   (creates ./.venv, installs the package + dev deps)
# Then try:    just demo && just serve     # seed synthetic data, open the workspace

set shell := ["bash", "-euo", "pipefail", "-c"]

venv := ".venv"
port := "8770"

# Prefer the venv's python once `just setup` has built it; fall back to system
# python3 on a fresh clone. Evaluated at parse time, so the first run after
# `just setup` switches over automatically — no shell re-source needed.
python := `if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi`

# App commands run as `python -m cli …` from the repo root (the justfile dir).
# That keeps the repo on sys.path so the `cli`/`daemon` packages always resolve —
# sidestepping the stale-console-script import error a plain `prm` can hit after
# new packages are added but the editable install hasn't been refreshed.
prm := python + " -m cli"

opener := if os() == "macos" { "open" } else { "xdg-open" }

# Show the recipe menu.
default:
    @just --list


# ---- setup ---------------------------------------------------------------

# Create ./.venv and install the package + dev deps (editable). Run this first.
[group('setup')]
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d {{venv}} ]; then
        python3 -m venv {{venv}}
    fi
    {{venv}}/bin/pip install --upgrade pip --quiet
    {{venv}}/bin/pip install -e ".[dev]"
    echo
    echo "Setup complete. Next:  just demo   (seed synthetic data)  then  just serve"

# Install PRM for daily use — a per-user data dir + editable pipx install with the desktop app (interactive, re-runnable).
[group('setup')]
install:
    #!/usr/bin/env bash
    set -uo pipefail

    echo "PRM install — set PRM up for daily use (your data kept safe from code updates)."
    echo

    # 1) Suggest a per-user data location by platform; let the user override.
    case "$(uname -s)" in
        Darwin) default_dir="$HOME/Library/Application Support/PRM" ;;
        Linux)  default_dir="${XDG_DATA_HOME:-$HOME/.local/share}/prm" ;;
        *)      default_dir="$HOME/.prm" ;;
    esac
    read -r -p "Where should PRM keep your data? [$default_dir] " target
    target="${target:-$default_dir}"
    target="${target/#\~/$HOME}"                              # expand a leading ~
    echo "  → data dir: $target"
    echo

    # 2) Offer to move existing repo-local data (./prm-data) into the new location. We only *decide* here;
    #    the move itself is done by `prm config --move-from` (Python) so it can never overwrite/merge/nest.
    move_from=""
    if [ -e "prm-data/shared.db" ] && [ "$target" != "$PWD/prm-data" ]; then
        read -r -p "Move your existing ./prm-data into $target? [Y/n] " ans
        case "${ans:-Y}" in
            [Nn]*) echo "  okay, keeping ./prm-data where it is." ;;
            *) move_from="prm-data" ;;
        esac
    fi
    echo

    # 3) Install editable (required so the workspace assets resolve from source) with the desktop app.
    #    Prefer pipx (an isolated `prm` on your PATH). If it's missing, ASK before falling back — don't
    #    silently install into ./.venv. (`python3 -m pipx` is accepted too, in case the script dir is
    #    off PATH after a --user install.)
    pipx_cmd=""
    if command -v pipx >/dev/null 2>&1; then
        pipx_cmd="pipx"
    elif python3 -m pipx --version >/dev/null 2>&1; then
        pipx_cmd="python3 -m pipx"
    else
        echo "pipx is not installed — it gives you an isolated 'prm' command on your PATH (recommended)."
        read -r -p "Install pipx now? [Y/n] " ans
        case "${ans:-Y}" in
            [Nn]*) echo "  okay, skipping pipx — I'll use ./.venv instead." ;;
            *)
                if command -v brew >/dev/null 2>&1; then
                    echo "  installing pipx with Homebrew…"; brew install pipx || true
                else
                    echo "  installing pipx with pip (--user)…"
                    python3 -m pip install --user pipx \
                        || python3 -m pip install --user --break-system-packages pipx || true
                fi
                hash -r 2>/dev/null || true
                if command -v pipx >/dev/null 2>&1; then pipx_cmd="pipx"
                elif python3 -m pipx --version >/dev/null 2>&1; then pipx_cmd="python3 -m pipx"; fi
                if [ -n "$pipx_cmd" ]; then
                    $pipx_cmd ensurepath >/dev/null 2>&1 || true
                else
                    echo "  couldn't install pipx automatically — see https://pipx.pypa.io ; using ./.venv for now."
                fi
                ;;
        esac
    fi

    if [ -n "$pipx_cmd" ]; then
        echo "Installing with pipx (editable + desktop app)…"
        $pipx_cmd install --force --editable ".[app]" || { echo "pipx install failed."; exit 1; }
        $pipx_cmd ensurepath >/dev/null 2>&1 || true
        bindir="$($pipx_cmd environment --value PIPX_BIN_DIR 2>/dev/null || true)"
        prm_cmd="${bindir:-$HOME/.local/bin}/prm"
        [ -x "$prm_cmd" ] || prm_cmd="$(command -v prm || echo "$prm_cmd")"
    else
        echo "Installing into ./.venv instead (run from the repo with 'just app' or './.venv/bin/prm')."
        [ -d {{venv}} ] || python3 -m venv {{venv}}
        {{venv}}/bin/pip install --upgrade pip --quiet
        {{venv}}/bin/pip install -e ".[app]" || { echo "pip install failed."; exit 1; }
        prm_cmd="{{venv}}/bin/prm"
    fi
    echo

    # 4) Record the data location (and safely move existing data, if chosen) so `prm` finds it.
    if [ -n "$move_from" ]; then
        "$prm_cmd" config --set-data-dir "$target" --move-from "$move_from"
    else
        "$prm_cmd" config --set-data-dir "$target"
    fi
    echo
    echo "Done — PRM is installed."
    echo "  launch :  $prm_cmd app          (or:  just app)"
    echo "  update :  git pull              (your data in $target is untouched)"
    echo "  verify :  $prm_cmd config --show   ·   $prm_cmd status"
    [ -n "$pipx_cmd" ] && echo "  (if 'prm' isn't found in new terminals, run  $pipx_cmd ensurepath  and open a new shell)"

# A recipe runs in a child process, so it cannot activate your *current* shell —
# this execs a new one with the venv active. Type `exit` (or Ctrl-D) to return.
# Drop into a sub-shell with the venv activated (prm/pytest/python on PATH).
[group('setup')]
shell:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d {{venv}} ]; then
        echo "No {{venv}} yet — running setup first…"
        just setup
    fi
    source {{venv}}/bin/activate
    echo "venv active: $(command -v python). Type 'exit' to leave."
    exec "${SHELL:-/bin/sh}"

# Sanity-check the dev environment (python, deps, FTS5, port).
[group('setup')]
doctor:
    #!/usr/bin/env bash
    set -uo pipefail
    ok=1
    say() { if [ "$1" = 0 ]; then echo "  OK   $2"; else echo "  FAIL $2"; ok=0; fi; }
    {{python}} -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)'; say $? "python ≥ 3.10 ({{python}})"
    if [ -d {{venv}} ]; then echo "  OK   .venv present"; else echo "  WARN no .venv — run 'just setup'"; fi
    {{python}} -c 'import cli, daemon' 2>/dev/null; say $? "cli + daemon importable"
    ( {{python}} -c 'import vobjectx' || {{python}} -c 'import vobject' ) >/dev/null 2>&1; say $? "vCard lexer (vobjectx or vobject)"
    {{python}} -c 'import sqlite3; sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")' 2>/dev/null; say $? "sqlite3 FTS5 available (search depends on it — M0 gate)"
    {{python}} -m pytest --version >/dev/null 2>&1; say $? "pytest available"
    if lsof -ti:{{port}} >/dev/null 2>&1; then echo "  WARN port {{port}} in use (run 'just port')"; else echo "  OK   port {{port}} free"; fi
    echo
    if [ "$ok" = 1 ]; then echo "All checks passed."; else echo "Some checks failed — see above."; exit 1; fi

# Remove ./.venv (leaves your PRM data alone).
[group('setup')]
clean:
    rm -rf {{venv}}
    @echo "Removed {{venv}}. PRM data (./prm-data/) left in place."


# ---- tests ---------------------------------------------------------------

# Run the test suite. Pass extra args after `--` (e.g. `just test -- -k vcard -v`).
[group('test')]
test *args="":
    {{python}} -m pytest {{args}}

# Excluded from the pytest run by design (same-named files + sibling imports; see
# conftest.py); each checks its source tree against the committed expected manifests.
# Run the standalone fixture-corpus validators.
[group('test')]
test-fixtures:
    #!/usr/bin/env bash
    set -uo pipefail
    rc=0
    for t in tests/fixtures/*/test_fixtures.py; do
        echo "── $t"
        {{python}} "$t" || rc=1
    done
    exit $rc

# Everything: the pytest suite + the fixture validators.
[group('test')]
test-all: test test-fixtures


# ---- conformance (M6 PNT reference-design attestation) -------------------

# Regenerate the conformance artifacts (evaluate-report.json + report.md) from docs/Architecture.md.
[group('conformance')]
evaluate-report:
    {{python}} scripts/evaluate_report.py
    {{python}} scripts/conformance_report.py

# Lint app-opened surfaces: loopback-bound + authenticated (Surface 1; --strict so L2 gates as PRM's own regression guard).
[group('conformance')]
lint:
    {{python}} scripts/loopback_surface_lint.py --strict

# Conformance gate: the attestation-evidence shape check + the surface lint (--strict) + the full suite.
[group('conformance')]
conformance:
    {{python}} scripts/evaluate_report.py --check
    {{python}} scripts/loopback_surface_lint.py --strict
    {{python}} -m pytest -q


# ---- dev -----------------------------------------------------------------

# Seed a realistic ~1000-contact demo home from synthetic fixtures — pinned to the ./prm-data sandbox (never your real data).
[group('dev')]
demo:
    PRM_HOME=prm-data {{prm}} init --demo

# Serve the read-only workspace at http://127.0.0.1:8770 (default). Pass a port: `just serve 9000`. Needs data first (`just demo`); Ctrl-C to stop.
[group('dev')]
serve port=port:
    {{prm}} serve --port {{port}}

# Stop the workspace server running on the port (default 8770; pass another: `just stop 9000`). Graceful — asks it to shut down, escalates only if it won't.
[group('dev')]
stop port=port:
    #!/usr/bin/env bash
    set -uo pipefail
    pids=$(lsof -ti:{{port}} 2>/dev/null || true)
    if [ -z "$pids" ]; then
        echo "No server running on port {{port}}."
        exit 0
    fi
    kill $pids 2>/dev/null || true                                  # ask it to stop (SIGTERM)
    for _ in 1 2 3 4 5; do
        sleep 0.2
        lsof -ti:{{port}} >/dev/null 2>&1 || { echo "Stopped the server on port {{port}}."; exit 0; }
    done
    kill -9 $(lsof -ti:{{port}} 2>/dev/null) 2>/dev/null || true    # still up? force it
    echo "Stopped the server on port {{port}} (forced)."

# Open the workspace as a native desktop window (needs `just install`, or the `[app]` extra). Picks a free port; close the window to stop. Needs data first (`just demo` / `just ingest`).
[group('dev')]
app:
    {{prm}} app

# Open the running workspace in your browser (start `just serve` in another terminal first). Uses the server's one-time session key.
[group('dev')]
open:
    {{prm}} open

# Import contact file(s)/dir(s) into your PRM home (e.g. `just ingest ~/Downloads/takeout.zip`).
[group('dev')]
ingest *paths:
    {{prm}} import {{paths}}

# Inspect an export without saving anything (`prm import --dry-run`).
[group('dev')]
dry-run *paths:
    {{prm}} import {{paths}} --dry-run

# Re-import an updated export — preview changes, then confirm. E.g. `just reimport ~/Downloads/takeout.zip --source google_takeout`.
[group('dev')]
reimport *paths:
    {{prm}} reimport {{paths}}

# Show PRM home + store status.
[group('dev')]
status:
    {{prm}} status

# Export your contacts: merged portable vCard, or a lossless re-importable backup with `--raw`.
# E.g. `just export --out contacts.vcf`  ·  `just export --raw --out backup.json`.
[group('dev')]
export *args:
    {{prm}} export {{args}}

# Full-text search your imported contacts (e.g. `just search ada`).
[group('dev')]
search *args:
    {{prm}} search {{args}}

# Show what the MCP (AI) surface would return for a contact — the data-floor check. Sealed fields never
# show; shareable fields show only after you grant access in the AI-access tab. E.g. `just floor-check "Aaron Frank"`.
[group('dev')]
floor-check name *args:
    {{python}} scripts/floor_check.py "{{name}}" {{args}}

# Free port 8770 if a previous `just serve` didn't shut down cleanly.
[group('dev')]
port:
    @lsof -ti:{{port}} | xargs -r kill -9 2>/dev/null || true
    @echo "Port {{port}} freed."

# Delete the local demo/dev PRM home (./prm-data/). Real data elsewhere is untouched.
[group('dev')]
clean-data:
    rm -rf prm-data
    @echo "Removed ./prm-data/ (the demo/dev home)."


# ---- mcp servers ---------------------------------------------------------
mcp_venv   := "mcp_servers/.venv"
mcp_python := mcp_venv / "bin/python"

# Create an isolated venv and install the mcp SDK (kept out of the project venv so core/ stays tiny-deps).
[group('mcp')]
mcp-install-deps:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d {{mcp_venv}} ]; then python3 -m venv {{mcp_venv}}; fi
    {{mcp_venv}}/bin/pip install --upgrade pip --quiet
    {{mcp_venv}}/bin/pip install -r mcp_servers/requirements.txt --quiet
    echo "MCP venv ready ({{mcp_venv}}). See mcp_servers/README.md for Claude Desktop config."

# Pass --data-dir DIR to bind a non-default home, --print to preview, --non-interactive to skip the prompt.
# One-shot: register both PRM servers in Claude Desktop (backs up + merges, never clobbers; idempotent).
[group('mcp')]
mcp-install *args="": mcp-install-deps
    @{{python}} -m mcp_servers.install {{args}}

# Remove PRM's servers from Claude Desktop's config (backs up first; leaves other servers untouched).
[group('mcp')]
mcp-uninstall *args="":
    @{{python}} -m mcp_servers.install --uninstall {{args}}

# Run the read-only Shared-Data-Ops MCP server over stdio (./prm-data by default; pass --data-dir).
[group('mcp')]
mcp-shared-data-ops *args="--data-dir prm-data":
    {{mcp_python}} mcp_servers/shared_data_ops.py {{args}}

# Run the propose-only Dedup-Ops MCP server over stdio.
[group('mcp')]
mcp-dedup-ops *args="--data-dir prm-data":
    {{mcp_python}} mcp_servers/dedup_ops.py {{args}}
