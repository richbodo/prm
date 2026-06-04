"""The ingest orchestrator — **pure and non-interactive**.

``ingest(paths, ...)`` walks files, detects format, infers source, parses → normalizes → builds an
``ImportReport``. It touches no terminal and prompts for nothing, so the CLI *and* the MCP ingestion
surface drive the same core and tests exercise it directly. Interactive confirmation and the future
Downloads discovery live one layer up, in the CLI.

Persistence (the ``shared.db`` load path) is the next milestone (plan §11 M1c); until then ``ingest``
parses and reports what *would* load, and ``load_into`` is a clearly-marked stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import PrmHome
from .normalize import CanonicalContact, normalize_all
from .parsers import takeout, vcard
from .parsers.base import SourceFormat, detect_format
from .report import ImportReport

# File extensions we will attempt (others are ignored when walking a directory).
_KNOWN_SUFFIXES = {".vcf", ".zip", ".csv"}


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
        if path.name == "Connections.csv":
            return "linkedin", "high"
        head = path.read_bytes()[:512].decode("utf-8", "replace")
        if "Given Name" in head or "Group Membership" in head:
            return "google_csv", "medium"
        return "vendor_csv", "low"
    return "unknown", "low"


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
        return PathResult(path, fmt, source, confidence, skipped_reason="CSV parser lands next (plan §11 M1)")
    else:
        return PathResult(path, fmt, source, confidence, skipped_reason="unrecognized format")

    return PathResult(path, fmt, source, confidence, contacts=normalize_all(records))


def collect(paths, *, source: str | None = None) -> list[PathResult]:
    return [parse_one(p, source) for p in _expand(paths)]


def load_into(home: PrmHome, contacts: list[CanonicalContact]) -> int:
    """Persist canonical contacts into shared.db. Not yet implemented (plan §11 M1c)."""
    raise NotImplementedError("the shared.db load path is the next milestone (plan §11 M1c)")


def ingest(paths, *, source: str | None = None, home: PrmHome | None = None,
           dry_run: bool = True) -> ImportReport:
    """Parse + normalize + report. Persists only when ``dry_run`` is False *and* a loader exists."""
    results = collect(paths, source=source)
    contacts = [c for r in results for c in r.contacts]
    notes: list[str] = []
    persisted = False

    if not dry_run:
        if home is None:
            notes.append("no PRM home given; nothing persisted")
        else:
            try:
                load_into(home, contacts)
                persisted = True
            except NotImplementedError as exc:
                notes.append(str(exc))

    return ImportReport.from_results(results, persisted=persisted, dry_run=dry_run, notes=notes)
