#!/usr/bin/env python3
"""Manual-QA / diagnostic: show the most recent reads the MCP (AI) surface has served — the "what did an
external system read, and when" log. Each line: when · tool · contact · decision (released / withheld /
no-shareable) · the **server build** that served it (so a stale server is obvious — prm#65). Reads the
default-resolved PRM home (the same one ``prm serve`` and the MCP servers use); pass --data-dir to point
elsewhere. Reads locally and sends nothing anywhere.

    just access-log
    just access-log 100
    python scripts/access_log.py [-n N] [--data-dir DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.config import resolve_home  # noqa: E402
from core import access_log  # noqa: E402

_FLAG = {"released": "→ released", "withheld": "✋ withheld", "no-shareable": "· no overlay"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Show recent MCP (AI) reads PRM has served.")
    ap.add_argument("-n", type=int, default=30, help="how many recent reads to show (default 30)")
    ap.add_argument("--data-dir", help="PRM home (else $PRM_HOME, else the configured/default home)")
    args = ap.parse_args(argv)

    home = resolve_home(args.data_dir)
    print(f"home : {home.root}")
    rows = access_log.recent(home, n=args.n)
    if not rows:
        print("no MCP reads logged yet (no AI has read a contact through PRM in this home)")
        return 0
    for e in rows:
        flag = _FLAG.get(e.get("decision"), str(e.get("decision")))
        fields = ", ".join(e.get("withheld_overlay") or e.get("returned_overlay") or []) or "—"
        review = "on" if e.get("review_on") else "off"
        print(f"  {e.get('ts','?')}  {str(e.get('tool','?')):<14} "
              f"{str(e.get('contact_id') or '')[:12]:<12} {flag:<12} "
              f"mode={e.get('mode','?')} review={review} approved={e.get('approved')}  "
              f"fields[{fields}]  build={e.get('build','?')}")
    print(f"{len(rows)} read(s) shown — newest first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
