#!/usr/bin/env python3
"""Manual-QA / diagnostic: show exactly what the MCP (AI) read surface would return for a contact — the
**data-floor** in action. `private-sealed` relationship fields NEVER appear; `private-shareable-on-consent`
fields appear only after you grant access in the workspace (the AI-access tab). Reads the default-resolved
PRM home (the same one `prm serve` uses with no --data-dir); pass --data-dir to point elsewhere. Reads
locally and sends nothing anywhere.

    just floor-check "Aaron Frank"
    python scripts/floor_check.py "Aaron Frank" [--data-dir DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.config import resolve_home  # noqa: E402
from core import disclosure  # noqa: E402
from mcp_servers import tools  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Show what the MCP (AI) surface would return for a contact.")
    ap.add_argument("name", help='contact name to look up (e.g. "Aaron Frank")')
    ap.add_argument("--data-dir", help="PRM home (else $PRM_HOME, else the configured/default home)")
    args = ap.parse_args(argv)

    home = resolve_home(args.data_dir)
    print(f"home : {home.root}")
    print(f"mode : {disclosure.mode(home)}   (set in the AI-access tab)")
    if not home.shared_db.exists():
        print("no shared.db in this home — point --data-dir at the home you're serving")
        return 1
    res = tools.search_contacts(home, args.name)["results"]
    if not res:
        print(f"!! no contact matching {args.name!r} in this home")
        return 1
    c = tools.get_contact(home, res[0]["id"])
    print(f"contact     : {res[0].get('name') or '(no name)'}")
    print(f"MCP can see : {sorted(f['name'] for f in c['fields'])}")
    print(f"disclosure  : {c.get('disclosure')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
