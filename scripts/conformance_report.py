#!/usr/bin/env python3
"""Human-readable conformance report → ``docs/conformance/report.md`` — a view over the same attestation
rows the evaluate-report emits, derived from ``docs/Architecture.md``. Stdlib only.

  python scripts/conformance_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from conformance_lib import _REPO, conformant_row_problems, parse_attestation, resolve_test_ref  # noqa: E402

_OUT = _REPO / "docs" / "conformance" / "report.md"


def build_md() -> str:
    rows = parse_attestation()
    n_conf = sum(1 for r in rows if r.status == "conformant")
    n_part = sum(1 for r in rows if r.status == "partial-conformance")
    n_na = sum(1 for r in rows if r.status == "not-applicable")
    problems = conformant_row_problems(rows)
    verdict = "🟢 Conformant" if not problems else "🔴 Findings"
    out = [
        "# Conformance Report — PRM (Personal Relationship Manager)",
        "",
        "> Derived from [`../Architecture.md`](../Architecture.md) by `scripts/conformance_report.py`. The "
        "machine-readable form is [`evaluate-report.json`](evaluate-report.json) "
        "(`scripts/evaluate_report.py`). Regenerate both with `just evaluate-report`.",
        "",
        f"## {verdict} to the PNA Spec (PNT 0.1 (draft)) for PRM's declared flavor",
        "",
        f"- **Conformant rows:** {n_conf}",
        f"- **Partial-conformance (human-review):** {n_part}",
        f"- **Not-applicable (flavor):** {n_na}",
        f"- **Findings:** {len(problems)}" + (" ✅" if not problems else ""),
        "",
    ]
    if problems:
        out += ["## Findings", ""] + [f"- {p}" for p in problems] + [""]
    out += ["## Attestation rows", "", "| AC | Source | Status | Evidence |", "|---|---|---|---|"]
    for r in rows:
        if r.status == "not-applicable":
            ev = "—"
        else:
            ev = ("; ".join(f"`{ref}` → {'live' if resolve_test_ref(ref)[0] else 'UNRESOLVED'}"
                            for ref in r.test_refs) or ", ".join(r.review_kinds) or "—")
        out.append(f"| {r.ac_id} | {r.source} | {r.status} | {ev} |")
    return "\n".join(out) + "\n"


def main() -> int:
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(build_md(), encoding="utf-8")
    print(f"wrote {_OUT.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
