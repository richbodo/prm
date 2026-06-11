#!/usr/bin/env python3
"""Parse PRM's ``docs/Architecture.md`` AC attestation tables into structured rows, and resolve the test
refs a ``conformant`` row cites — the shared substrate for the evaluate-report emitter
(``scripts/evaluate_report.py``) and the evidence lint (``tests/conformance/test_attestation_has_evidence.py``).
Stdlib only; deterministic (no wall clock).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ARCH = _REPO / "docs" / "Architecture.md"

_AC_ID = re.compile(r"AC-[A-Z0-9-]+")
_TEST_REF = re.compile(r"tests/[\w./-]+\.py(?:::\w+)?")
_REVIEW_KINDS = ("human-review", "code inspection", "by construction", "by bounding", "by architecture")


@dataclass
class Row:
    ac_id: str
    source: str            # "universal" | "flavor-derived"
    realization: str
    verification: str
    status: str            # normalized: conformant | partial-conformance | not-applicable
    status_raw: str        # the original Status cell (carries the partial explanation)
    triggered_by: str = ""

    @property
    def test_refs(self) -> list:
        return [m.group(0) for m in _TEST_REF.finditer(self.verification)]

    @property
    def review_kinds(self) -> list:
        return [k for k in _REVIEW_KINDS if k in self.verification]


def _norm_status(cell: str) -> str:
    c = cell.lower()
    if "not-applicable" in c:
        return "not-applicable"
    if "partial" in c:
        return "partial-conformance"
    if "conformant" in c:
        return "conformant"
    return c.strip()


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_attestation(arch: Path = _ARCH) -> list:
    """Every AC row in the Universal and Flavor-derived tables, in document order."""
    rows: list = []
    section = None
    for line in arch.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("### Universal ACs"):
            section = "universal"; continue
        if s.startswith("### Flavor-derived ACs"):
            section = "flavor-derived"; continue
        if s.startswith("## ") or s.startswith("### "):
            section = None; continue
        if not (section and s.startswith("|") and "---" not in s):
            continue
        cells = _cells(s)
        m = _AC_ID.search(cells[0])
        if not m:
            continue                                          # header row / non-AC line
        ac = m.group(0)
        if section == "universal" and len(cells) >= 4:
            rows.append(Row(ac, "universal", cells[1], cells[2], _norm_status(cells[3]), cells[3]))
        elif section == "flavor-derived" and len(cells) >= 5:
            rows.append(Row(ac, "flavor-derived", cells[2], cells[3], _norm_status(cells[4]), cells[4], cells[1]))
    return rows


def resolve_test_ref(ref: str) -> tuple[bool, str]:
    """(ok, reason) — does the ref point at a live (non-xfail/skip) test or test file?"""
    path, _, name = ref.partition("::")
    fp = _REPO / path
    if not fp.exists():
        return False, f"file missing: {path}"
    if not name:
        return True, "file exists"
    src = fp.read_text(encoding="utf-8")
    if not re.search(rf"^\s*(?:def|class)\s+{re.escape(name)}\b", src, re.M):
        return False, f"no def/class {name} in {path}"
    idx = src.find(f"def {name}")
    head = src[max(0, idx - 200):idx]
    if "xfail" in head or re.search(r"@pytest\.mark\.skip\b", head):
        return False, f"{name} is xfail/unconditional-skip (a deferred test is not evidence)"
    return True, "ok"


def conformant_row_problems(rows: list) -> list:
    """Evidence-rule violations: a ``conformant`` row whose Verification names no resolvable test ref and
    no declared review kind, or names a test ref that doesn't resolve. ``partial`` / ``not-applicable`` are
    exempt (they carry that honest status)."""
    problems = []
    for r in rows:
        if r.status != "conformant":
            continue
        refs = r.test_refs
        if not refs and not r.review_kinds:
            problems.append(f"{r.ac_id}: conformant but Verification cites no test ref and no review kind")
            continue
        for ref in refs:
            ok, why = resolve_test_ref(ref)
            if not ok:
                problems.append(f"{r.ac_id}: unresolved evidence — {why}")
    return problems
