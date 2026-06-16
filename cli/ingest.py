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
from datetime import datetime, timezone
from pathlib import Path

from core import audit, media, relationships_db, shared_db, snapshots
from core.lock import file_lock

from . import backup
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
    elif fmt is SourceFormat.PRM_BACKUP:
        # A PRM raw backup already holds canonical contacts (source + stable_key + provenance), so it
        # restores verbatim — bypassing normalization to preserve identity exactly (INV-5).
        try:
            contacts = backup.load(path.read_bytes())
        except (ValueError, json.JSONDecodeError) as exc:
            return PathResult(path, fmt, source, confidence, skipped_reason=str(exc))
        return PathResult(path, fmt, source_override or "prm_backup", "override" if source_override else "high",
                          contacts=contacts)
    else:
        return PathResult(path, fmt, source, confidence, skipped_reason="unrecognized format")

    return PathResult(path, fmt, source, confidence, contacts=normalize_all(records))


def collect(paths, *, source: str | None = None) -> list[PathResult]:
    return [parse_one(p, source) for p in _expand(paths)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _persist(home: PrmHome, contacts, ingested_at: str) -> shared_db.LoadResult:
    """Upsert into shared.db + seed the private 1:1 identity baseline. **Assumes the caller holds the
    file-lock.** Seeding is idempotent and preserves prior merge decisions (re-attach by stable id)."""
    for c in contacts:                                   # write any matched/imported photos to the media store
        if getattr(c, "photo_bytes", None):             # (the jcard already carries the `prm-media:<hash>` ref)
            media.put(home, c.photo_bytes, mime=c.photo_mime)
    result = shared_db.load(home.shared_db, contacts, ingested_at=ingested_at)
    srids = [shared_db.source_record_id(c.source, c.stable_key.as_str()) for c in contacts]
    relationships_db.seed_identities(home.relationships_db, srids, created_at=ingested_at)
    return result


def load_into(home: PrmHome, contacts: list[CanonicalContact], *,
              ingested_at: str | None = None) -> shared_db.LoadResult:
    """Persist canonical contacts into shared.db and seed the private store's 1:1 identity baseline,
    both under the single-instance lock (AC-PRM-C)."""
    home.create()
    with file_lock(home.lock_file):
        return _persist(home, contacts, ingested_at or _now_iso())


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


# --------------------------------------------------------------------------- re-import (M5)
@dataclass
class ReimportReport:
    """What a re-import changes, per source, plus a heads-up on stale records and merge decisions."""
    sources: list                         # [{source, added, updated, unchanged, stale, merges_with_stale}]
    applied: bool
    dry_run: bool
    snapshot: str | None = None
    notes: list = field(default_factory=list)

    _KEYS = ("added", "updated", "unchanged", "stale", "merges_with_stale")

    def totals(self) -> dict:
        return {k: sum(s[k] for s in self.sources) for k in self._KEYS}

    def render_human(self) -> str:
        t = self.totals()
        head = ("Re-import APPLIED" if self.applied else
                "Re-import DRY RUN — nothing written" if self.dry_run else "Re-import preview")
        lines = [f"{head}: +{t['added']} new · {t['updated']} updated · {t['unchanged']} unchanged · "
                 f"{t['stale']} no longer in export"]
        for s in self.sources:
            lines.append(f"  - {s['source']}: +{s['added']} new / {s['updated']} updated / "
                         f"{s['unchanged']} unchanged / {s['stale']} stale")
        if t["merges_with_stale"]:
            lines.append(f"  heads-up: {t['merges_with_stale']} merge decision(s) include a record no longer "
                         f"in this export — those records are kept and your merges are preserved (INV-6).")
        if self.snapshot:
            lines.append(f"  snapshot taken: {Path(self.snapshot).name} (pre-apply, for undo)")
        lines.extend(f"  {n}" for n in self.notes)
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({"applied": self.applied, "dry_run": self.dry_run, "snapshot": self.snapshot,
                           "sources": self.sources, "totals": self.totals(), "notes": self.notes},
                          indent=2, ensure_ascii=False)


def _diff_source(home: PrmHome, source: str, incoming: dict) -> tuple:
    """Compare a fresh export's records for one source against the store. ``incoming`` is
    ``{source_record_id: CanonicalContact}``. Returns (summary, contacts_to_persist)."""
    current = shared_db.records_for_source(home.shared_db, source)        # {srid: raw_jcard}
    imap = relationships_db.identity_map(home.relationships_db) if home.relationships_db.exists() else {}
    persist, added, updated, unchanged = [], 0, 0, 0
    for srid, c in incoming.items():
        if srid not in current:
            added += 1
            persist.append(c)
        elif c.jcard.to_json() != current[srid]:
            updated += 1
            persist.append(c)
        else:
            unchanged += 1
    stale = {srid for srid in current if srid not in incoming}
    members_by_contact: dict = {}
    for s, cid in imap.items():
        members_by_contact.setdefault(cid, []).append(s)
    merges_with_stale = sum(1 for mems in members_by_contact.values()
                            if len(mems) > 1 and any(m in stale for m in mems))
    summary = {"source": source, "added": added, "updated": updated, "unchanged": unchanged,
               "stale": len(stale), "merges_with_stale": merges_with_stale}
    return summary, persist


def reimport(paths, *, source: str | None = None, home: PrmHome | None = None,
             apply: bool = False) -> ReimportReport:
    """Opt-in, **non-destructive** re-import (INV-6, AC-10, AC-PRM-D): diff a fresh export against the
    store and preview added/updated/unchanged/stale; on ``apply`` snapshot → upsert → audit. Records
    no longer in the export are **kept** (reported as stale), and merge decisions are preserved —
    re-attach is by the stable source_record_id. Never background-polls."""
    if home is None:
        return ReimportReport(sources=[], applied=False, dry_run=not apply, notes=["no PRM home given"])
    if not home.shared_db.exists():
        return ReimportReport(sources=[], applied=False, dry_run=not apply, notes=["no shared.db — run `prm import` first"])

    contacts = [c for r in collect(paths, source=source) for c in r.contacts]
    by_source: dict = {}
    for c in contacts:
        by_source.setdefault(c.source, {})[shared_db.source_record_id(c.source, c.stable_key.as_str())] = c

    summaries, to_persist = [], []
    for src, incoming in sorted(by_source.items()):
        summary, persist = _diff_source(home, src, incoming)
        summaries.append(summary)
        to_persist.extend(persist)

    report = ReimportReport(sources=summaries, applied=False, dry_run=not apply)
    if not apply:
        return report
    if not to_persist:
        report.notes.append("no changes to apply")
        report.applied = True
        return report

    with file_lock(home.lock_file):
        snap = snapshots.snapshot(home)                      # pre-apply Undo point (AC-9 / INV-12)
        _persist(home, to_persist, _now_iso())
        audit.append(home, {"kind": "reimport", "sources": summaries, "snapshot": snap.name})
    report.applied = True
    report.snapshot = str(snap)
    return report
