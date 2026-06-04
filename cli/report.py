"""``ImportReport`` — the structured result of an ingest, with human and JSON renderings.

One value object produced by ``cli.ingest.ingest()`` and rendered two ways: a human summary for the
terminal and a dict for ``--json`` / the MCP surface. Keeping it data-only (no I/O) is what lets the
same ingest core serve the CLI and an AI client.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field


@dataclass
class PathSummary:
    """Per-input-file outcome."""

    path: str
    format: str
    source: str
    confidence: str          # "high" | "medium" | "low" | "override"
    count: int
    skipped_reason: str | None = None


@dataclass
class ImportReport:
    total: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    by_key_kind: dict[str, int] = field(default_factory=dict)
    nameless: int = 0
    paths: list[PathSummary] = field(default_factory=list)
    persisted: bool = False
    dry_run: bool = True
    stored: int | None = None          # rows in shared.db after the load (None if not persisted)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_results(cls, results, *, persisted: bool, dry_run: bool, stored=None, notes=None) -> "ImportReport":
        """Aggregate a list of duck-typed path results (see ``cli.ingest.PathResult``)."""
        by_source: Counter = Counter()
        by_kind: Counter = Counter()
        nameless = 0
        summaries: list[PathSummary] = []
        total = 0

        for r in results:
            n = len(r.contacts)
            total += n
            by_source[r.source] += n
            for c in r.contacts:
                by_kind[c.stable_key.kind] += 1
                if c.display_name is None:
                    nameless += 1
            summaries.append(PathSummary(
                path=str(r.path), format=r.fmt.value, source=r.source,
                confidence=r.confidence, count=n, skipped_reason=r.skipped_reason,
            ))

        return cls(
            total=total,
            by_source=dict(by_source),
            by_key_kind=dict(by_kind),
            nameless=nameless,
            paths=summaries,
            persisted=persisted,
            dry_run=dry_run,
            stored=stored,
            notes=list(notes or []),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def render_human(self) -> str:
        lines: list[str] = []
        mode = "DRY RUN — nothing written" if self.dry_run else (
            "imported" if self.persisted else "parsed (not persisted)")
        lines.append(f"Ingest {mode}: {self.total} contacts from {len(self.paths)} file(s)")
        for p in self.paths:
            if p.skipped_reason:
                lines.append(f"  - {p.path}: SKIPPED — {p.skipped_reason}")
            else:
                lines.append(f"  - {p.path}: {p.count} · {p.source} ({p.format}, confidence {p.confidence})")
        if self.by_source:
            lines.append("  by source: " + ", ".join(f"{s}={n}" for s, n in sorted(self.by_source.items())))
        if self.by_key_kind:
            lines.append("  stable-id: " + ", ".join(f"{k}={n}" for k, n in sorted(self.by_key_kind.items()))
                         + f"  ({self.nameless} name-less)")
        if self.stored is not None:
            lines.append(f"  shared.db now holds {self.stored} record(s)")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)
