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


# ---- dev -----------------------------------------------------------------

# Seed a realistic ~1000-contact demo home from synthetic fixtures (no personal data).
[group('dev')]
demo:
    {{prm}} init --demo

# Serve the read-only workspace at http://127.0.0.1:8770 (default). Pass a port: `just serve 9000`. Needs data first (`just demo`); Ctrl-C to stop.
[group('dev')]
serve port=port:
    {{prm}} serve --port {{port}}

# Open the workspace in your browser (start `just serve` in another terminal first).
[group('dev')]
open:
    {{opener}} "http://127.0.0.1:{{port}}/"

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

# Full-text search your imported contacts (e.g. `just search ada`).
[group('dev')]
search *args:
    {{prm}} search {{args}}

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
    {{mcp_venv}}/bin/pip install -r mcp_servers/requirements.txt
    echo "MCP venv ready ({{mcp_venv}}). See mcp_servers/README.md for Claude Desktop config."

# Run the read-only Shared-Data-Ops MCP server over stdio (./prm-data by default; pass --data-dir).
[group('mcp')]
mcp-shared-data-ops *args="--data-dir prm-data":
    {{mcp_python}} mcp_servers/shared_data_ops.py {{args}}

# Run the propose-only Dedup-Ops MCP server over stdio.
[group('mcp')]
mcp-dedup-ops *args="--data-dir prm-data":
    {{mcp_python}} mcp_servers/dedup_ops.py {{args}}
