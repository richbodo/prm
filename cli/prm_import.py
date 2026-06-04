"""``prm`` — the ingestor CLI entry point.

Argparse + dispatch **only**. Every command resolves the PRM home, calls a pure library function
(``cli.ingest``), renders the result, and returns an exit code. The interactive source-confirm lives
here and nowhere else; ``--non-interactive``/``--json``/``--dry-run`` (or a non-tty stdin) bypass it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ingest as ingest_mod
from .config import resolve_home
from .report import ImportReport

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_MANIFEST = REPO_ROOT / "demo" / "manifest.json"


# --------------------------------------------------------------------------- helpers
def _emit(report: ImportReport, as_json: bool) -> None:
    print(report.to_json() if as_json else report.render_human())


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _interactive(args) -> bool:
    return not (args.non_interactive or args.json or getattr(args, "dry_run", False)) and sys.stdin.isatty()


def _demo_paths() -> list[tuple[str, Path]]:
    manifest = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    return [(s["source"], (REPO_ROOT / s["path"])) for s in manifest["sources"]]


# --------------------------------------------------------------------------- commands
def cmd_init(args) -> int:
    home = resolve_home(args.data_dir).create()
    print(f"PRM home ready at {home.root}")
    if not args.demo:
        return 0

    entries = _demo_paths()
    missing = [str(p) for _, p in entries if not p.exists()]
    if missing:
        print("ERROR: demo sources missing (run the fixture generators?):", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        return 1
    # Each manifest entry carries its own source label (a bare Takeout .vcf can't be inferred).
    results = [ingest_mod.parse_one(p, source) for source, p in entries]
    report = ImportReport.from_results(results, persisted=False, dry_run=True)
    print("Demo seed (persistence lands with the loader — plan §11 M1c):")
    _emit(report, args.json)
    return 0


def cmd_import(args) -> int:
    home = resolve_home(args.data_dir)
    paths = [Path(p) for p in args.paths]

    # Non-interactive / json / dry-run: one pure call, no prompt.
    if not _interactive(args):
        report = ingest_mod.ingest(paths, source=args.source, home=home, dry_run=args.dry_run)
        _emit(report, args.json)
        return 0

    # Interactive: preview -> confirm -> attempt persist.
    results = ingest_mod.collect(paths, source=args.source)
    preview = ImportReport.from_results(results, persisted=False, dry_run=True)
    print(preview.render_human())
    if not _confirm("Proceed with import?"):
        print("Aborted — nothing written.")
        return 0

    contacts = [c for r in results for c in r.contacts]
    notes, persisted = [], False
    try:
        ingest_mod.load_into(home, contacts)
        persisted = True
    except NotImplementedError as exc:
        notes.append(str(exc))
    final = ImportReport.from_results(results, persisted=persisted, dry_run=False, notes=notes)
    print(final.render_human())
    return 0


def cmd_status(args) -> int:
    home = resolve_home(args.data_dir)
    info = {
        "home": str(home.root),
        "exists": home.exists(),
        "shared_db": home.shared_db.exists(),
        "private_db": home.private_db.exists(),
    }
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"PRM home: {home.root} ({'exists' if info['exists'] else 'not created — run `prm init`'})")
        if not info["shared_db"]:
            print("  shared.db: none yet (the load path is the next milestone — plan §11 M1c)")
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="prm", description="PRM ingestor (v0.1).")
    p.add_argument("--data-dir", help="PRM home directory (else $PRM_HOME, else ./prm-data/).")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="create the PRM home")
    pi.add_argument("--demo", action="store_true", help="seed a demo home from the synthetic fixtures")
    pi.add_argument("--json", action="store_true", help="machine-readable output")
    pi.set_defaults(func=cmd_init)

    pm = sub.add_parser("import", help="import contact files into the PRM home")
    pm.add_argument("paths", nargs="+", help="file(s) or directory(ies) to import")
    pm.add_argument("--source", help="override the inferred provenance source label")
    pm.add_argument("--dry-run", action="store_true", help="parse + report only; write nothing")
    pm.add_argument("--non-interactive", action="store_true", help="best-effort, no prompts")
    pm.add_argument("--json", action="store_true", help="machine-readable output")
    pm.set_defaults(func=cmd_import)

    ps = sub.add_parser("status", help="show the PRM home and store status")
    ps.add_argument("--json", action="store_true", help="machine-readable output")
    ps.set_defaults(func=cmd_status)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
