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

from core import shared_db

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
    contacts = [c for r in results for c in r.contacts]
    result = ingest_mod.load_into(home, contacts)
    report = ImportReport.from_results(results, persisted=True, dry_run=False, stored=result.total)
    print(f"Demo seeded into {home.shared_db}:")
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


def cmd_reimport(args) -> int:
    home = resolve_home(args.data_dir)
    if not home.shared_db.exists():
        print("no shared.db yet — run `prm import` first", file=sys.stderr)
        return 1
    paths = [Path(p) for p in args.paths]
    if args.dry_run:
        preview = ingest_mod.reimport(paths, source=args.source, home=home, apply=False)
        print(preview.to_json() if args.json else preview.render_human())
        return 0
    if not (args.non_interactive or args.json) and sys.stdin.isatty():     # preview, then confirm
        print(ingest_mod.reimport(paths, source=args.source, home=home, apply=False).render_human())
        if not _confirm("Apply this re-import?"):
            print("Aborted — nothing written.")
            return 0
    final = ingest_mod.reimport(paths, source=args.source, home=home, apply=True)
    print(final.to_json() if args.json else final.render_human())
    return 0


def cmd_status(args) -> int:
    home = resolve_home(args.data_dir)
    info = {"home": str(home.root), "exists": home.exists(), "shared_db": home.shared_db.exists()}
    if info["shared_db"]:
        info["shared"] = shared_db.stats(home.shared_db)

    if args.json:
        print(json.dumps(info, indent=2))
        return 0

    print(f"PRM home: {home.root} ({'exists' if info['exists'] else 'not created — run `prm init`'})")
    if not info["shared_db"]:
        print("  shared.db: none yet — run `prm import` to create it")
        return 0
    s = info["shared"]
    print(f"  shared.db: {s['records']} record(s), {s['distinct_identities']} distinct identities "
          f"(schema v{s['schema_version']})")
    if s["by_source"]:
        print("    by source: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_source"].items())))
    if s["last_ingested_at"]:
        print(f"    last import: {s['last_ingested_at']}")
    return 0


def cmd_serve(args) -> int:
    home = resolve_home(args.data_dir)
    if not home.shared_db.exists():
        print("no shared.db yet — run `prm import` first", file=sys.stderr)
        return 1
    from daemon.server import serve  # imported lazily so the ingest path never pays for it
    serve(home, host=args.host, port=args.port)
    return 0


def cmd_export(args) -> int:
    home = resolve_home(args.data_dir)
    if not home.shared_db.exists():
        print("no shared.db yet — run `prm import` first", file=sys.stderr)
        return 1
    from core import projection                 # imported lazily so the ingest path never pays for it
    from .vcard_writer import write_vcards

    cards = projection.export_jcards(home)
    text = write_vcards(cards)
    if args.out:
        out = Path(args.out)
        out.write_text(text, encoding="utf-8")
        print(f"Exported {len(cards)} contact(s) to {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_search(args) -> int:
    home = resolve_home(args.data_dir)
    if not home.shared_db.exists():
        print("no shared.db yet — run `prm import` first", file=sys.stderr)
        return 1
    rows = shared_db.search(home.shared_db, args.query, limit=args.limit)
    if args.json:
        print(json.dumps([{"name": n, "email": e, "org": o} for n, e, o, _ in rows], indent=2, ensure_ascii=False))
        return 0
    for name, email, org, _ in rows:
        print(f"  {name or '(no name)'} · {email or '—'} · {org or '—'}")
    print(f"{len(rows)} result(s) for {args.query!r}")
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

    pr = sub.add_parser("reimport", help="re-import an updated export — preview changes, then confirm")
    pr.add_argument("paths", nargs="+", help="updated export file(s)/dir(s)")
    pr.add_argument("--source", help="match the original source label (e.g. apple_icloud) — recommended")
    pr.add_argument("--dry-run", action="store_true", help="preview the changes only; write nothing")
    pr.add_argument("--non-interactive", action="store_true", help="apply without prompting")
    pr.add_argument("--json", action="store_true", help="machine-readable output")
    pr.set_defaults(func=cmd_reimport)

    ps = sub.add_parser("status", help="show the PRM home and store status")
    ps.add_argument("--json", action="store_true", help="machine-readable output")
    ps.set_defaults(func=cmd_status)

    pv = sub.add_parser("serve", help="serve the local read-only workspace (search/view)")
    pv.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1 — local only)")
    pv.add_argument("--port", type=int, default=8770, help="port (default 8770)")
    pv.set_defaults(func=cmd_serve)

    pe = sub.add_parser("export", help="export your merged contacts to a portable vCard (.vcf)")
    pe.add_argument("--out", help="write to this file (default: stdout)")
    pe.add_argument("--format", choices=["vcard"], default="vcard", help="export format (vCard 3.0)")
    pe.set_defaults(func=cmd_export)

    pq = sub.add_parser("search", help="full-text search the imported contacts")
    pq.add_argument("query", help="search terms (prefix-matched across name/email/org/notes)")
    pq.add_argument("--limit", type=int, default=20, help="max results (default 20)")
    pq.add_argument("--json", action="store_true", help="machine-readable output")
    pq.set_defaults(func=cmd_search)
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
