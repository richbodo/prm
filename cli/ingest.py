"""The ingest orchestrator — **pure and non-interactive**.

``ingest(paths, ...)`` walks files, detects format, infers source, parses → normalizes → builds an
``ImportReport``, and (when not a dry run) loads into ``shared.db``. It touches no terminal and
prompts for nothing, so the CLI *and* the MCP ingestion surface drive the same core and tests
exercise it directly. Interactive confirmation and the future Downloads discovery live one layer up,
in the CLI.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core import shared_db
from core.lock import file_lock

from .config import PrmHome
from .normalize import CanonicalContact, normalize_all
from .parsers import csv as csv_parser
from .parsers import facebook, takeout, vcard
from .parsers.base import SourceFormat, detect_format
from .report import ImportReport

# File extensions we will attempt (others are ignored when walking a directory).
_KNOWN_SUFFIXES = {".vcf", ".zip", ".csv", ".json"}


@dataclass
class PathResult:
    path: Path
    fmt: SourceFormat
    source: str
    confidence: str
    contacts: list[CanonicalContact] = field(default_factory=list)
    skipped_reason: str | None = None


def infer_source(path: Path, fmt: SourceFormat) -> tuple[str, str]:
    """Infer the provenance *source* label and a confidence from format + light content sniffing."""
    if fmt is SourceFormat.GOOGLE_TAKEOUT:
        return "google_takeout", "high"
    if fmt is SourceFormat.VCARD:
        head = path.read_bytes()[:4096].decode("utf-8", "replace")
        prodid = next((ln for ln in head.splitlines() if ln.upper().startswith("PRODID")), "")
        if "apple" in prodid.lower():
            return "apple_icloud", "high"
        return "vcard", "low"
    if fmt is SourceFormat.VENDOR_CSV:
        # A .zip detected as vendor CSV is a LinkedIn export (it contains Connections.csv).
        if path.suffix.lower() == ".zip" or path.name == "Connections.csv":
            return "linkedin", "high"
        head = path.read_bytes()[:512].decode("utf-8", "replace")
        if "Given Name" in head or "Group Membership" in head:
            return "google_csv", "medium"
        return "vendor_csv", "low"
    if fmt is SourceFormat.FACEBOOK_JSON:
        return "facebook", "high"
    return "unknown", "low"


def _read_csv_bytes(path: Path) -> bytes:
    """Read CSV bytes from a bare .csv, or extract Connections.csv from a LinkedIn export .zip."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            member = next((n for n in names if n.endswith("Connections.csv")), None) \
                or next((n for n in names if n.lower().endswith(".csv")), None)
            if member is None:
                raise ValueError("no .csv inside the zip")
            return zf.read(member)
    return path.read_bytes()


def _read_facebook_bytes(path: Path) -> bytes:
    """Read a bare friends .json, or extract a friends .json from a Facebook export .zip."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            member = next((n for n in zf.namelist()
                           if "friend" in n.lower() and n.lower().endswith(".json")), None)
            if member is None:
                raise ValueError("no friends .json inside the zip")
            return zf.read(member)
    return path.read_bytes()


def _expand(paths) -> list[Path]:
    """Expand inputs to a flat list of candidate files (directories are walked)."""
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in _KNOWN_SUFFIXES))
        else:
            out.append(p)
    return out


def parse_one(path: Path, source_override: str | None = None) -> PathResult:
    """Parse a single file into a ``PathResult`` (never raises on an unsupported format — it skips)."""
    fmt = detect_format(path)
    source, confidence = infer_source(path, fmt)
    if source_override:
        source, confidence = source_override, "override"

    if fmt is SourceFormat.VCARD:
        records = vcard.parse(path.read_bytes(), source)
    elif fmt is SourceFormat.GOOGLE_TAKEOUT:
        records = takeout.parse(path)
        if not source_override:
            source = takeout.SOURCE
    elif fmt is SourceFormat.VENDOR_CSV:
        try:
            records = csv_parser.parse(_read_csv_bytes(path), source)
        except (ValueError, KeyError) as exc:
            return PathResult(path, fmt, source, confidence, skipped_reason=str(exc))
    elif fmt is SourceFormat.FACEBOOK_JSON:
        try:
            records = facebook.parse(_read_facebook_bytes(path), source)
        except (ValueError, json.JSONDecodeError) as exc:
            return PathResult(path, fmt, source, confidence, skipped_reason=str(exc))
    else:
        return PathResult(path, fmt, source, confidence, skipped_reason="unrecognized format")

    return PathResult(path, fmt, source, confidence, contacts=normalize_all(records))


def collect(paths, *, source: str | None = None) -> list[PathResult]:
    return [parse_one(p, source) for p in _expand(paths)]


def load_into(home: PrmHome, contacts: list[CanonicalContact], *,
              ingested_at: str | None = None) -> shared_db.LoadResult:
    """Persist canonical contacts into the home's shared.db, under the single-instance lock."""
    home.create()
    with file_lock(home.lock_file):
        return shared_db.load(home.shared_db, contacts, ingested_at=ingested_at)


def ingest(paths, *, source: str | None = None, home: PrmHome | None = None,
           dry_run: bool = True) -> ImportReport:
    """Parse + normalize + report. Persists to shared.db when ``dry_run`` is False and a home given."""
    results = collect(paths, source=source)
    contacts = [c for r in results for c in r.contacts]
    notes: list[str] = []
    persisted = False
    stored = None

    if not dry_run:
        if home is None:
            notes.append("no PRM home given; nothing persisted")
        else:
            result = load_into(home, contacts)
            persisted = True
            stored = result.total

    return ImportReport.from_results(results, persisted=persisted, dry_run=dry_run, stored=stored, notes=notes)
