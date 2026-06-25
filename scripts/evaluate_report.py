#!/usr/bin/env python3
"""Deterministic emitter for ``docs/conformance/evaluate-report.json`` — the toolkit-schema artifact the
Personal Network Toolkit consumes as ``design.toml``'s ``[verify]`` output. Derived entirely from
``docs/Architecture.md``'s attestation table + git HEAD, so the same commit + same attestation produces the
same report (no wall-clock stamp). Stdlib only.

The toolkit schema (``tools/evaluate-report.schema.json``) is **AC-keyed**: one finding per AC row.
Status mapping: ``conformant`` → ``conformant``; ``not-applicable`` → ``not-applicable`` (+ rationale);
``partial-conformance`` → ``conformant`` + ``needs_human_review`` (the schema has no ``partial``, so the
residual is recorded honestly as a review flag, with the original Status text preserved in the evidence).

  python scripts/evaluate_report.py            # build + validate + write
  python scripts/evaluate_report.py --check     # build + validate only (no write); exit 1 on violation
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from conformance_lib import _REPO, conformant_row_problems, parse_attestation  # noqa: E402

_OUT = _REPO / "docs" / "conformance" / "evaluate-report.json"

AXIS_PICKS = {
    "distribution": "never-distributed-single-user",
    "storage": "native-sqlite-via-filesystem",
    "ingestion": "multi-source-merge-with-dedup",
    "workspace": "vanilla-js-spa",
    "comms": "none",
    "mcp-exposure": "shared-only",
}
REPO_URL = "https://github.com/richbodo/prm"
PNA_SPEC_VERSION = "0.2"
# ACs that bear on a top-level Goal (informational; drives leading_concerns ordering). Optional per schema.
_GOALS = {"AC-1": [1], "AC-4": [4], "AC-9": [4], "AC-10": [2, 4], "AC-11": [4], "AC-17": [2],
          "AC-PRM-A": [3], "AC-PRM-D": [1, 4], "AC-MCP-A": [1, 3], "AC-PRM-B": [2], "AC-PRM-C": [4]}


def _git_head() -> str:
    try:
        out = subprocess.run(["git", "-C", str(_REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _citations(row) -> list:
    cites = []
    for ref in row.test_refs:
        path, _, name = ref.partition("::")
        cites.append({"path": path, "note": f"cited test: {name}"} if name else {"path": path})
    return cites or [{"path": "docs/Architecture.md", "note": "attestation row (review-kind evidence)"}]


def _finding(row) -> dict:
    f = {"ac_id": row.ac_id, "ac_source": row.source}
    if row.ac_id in _GOALS:
        f["goals"] = _GOALS[row.ac_id]
    if row.status == "not-applicable":
        f["status"] = "not-applicable"
        f["rationale"] = row.realization or "not triggered by this design's flavor"
        return f
    f["status"] = "conformant"
    f["citations"] = _citations(row)
    ev = [{"source": "deterministic", "tool": "attestation-evidence-lint",
           "detail": f"Architecture.md attestation row; cited test ref(s) resolve and are non-deferred "
                     f"(conformance_lib.resolve_test_ref): {', '.join(row.test_refs) or 'review-kind'}."}]
    if row.status == "partial-conformance":
        f["needs_human_review"] = True
        ev.append({"source": "human", "detail": f"Self-attested {row.status_raw}"})
    f["evidence"] = ev
    return f


def build_report() -> dict:
    rows = parse_attestation()
    findings = [_finding(r) for r in rows]
    n_conf = sum(1 for r in rows if r.status == "conformant")
    partials = [r.ac_id for r in rows if r.status == "partial-conformance"]
    n_na = sum(1 for r in rows if r.status == "not-applicable")
    headline = (f"PRM is deterministically attested conformant to PNA Spec {PNA_SPEC_VERSION} for its "
                f"declared flavor: {n_conf} conformant, {n_na} not-applicable, 0 non-conformant across "
                f"{len(rows)} evaluated ACs. {len(partials)} AC(s) carry self-attested partial-conformance "
                f"flagged for human review ({', '.join(partials)}); the EX-CLOUD-LLM exception is handled by "
                f"a workspace consent gate + persistent signal + reversible return-to-PNA over a "
                f"projection-bound, consent-gated cloud surface (the data-floor) — the residual boundary is "
                f"consent + honest signaling, since an MCP server cannot identify the consuming LLM).")
    return {
        "report_schema_version": "0.1",
        "generated_by": "scripts/evaluate_report.py (deterministic; derived from docs/Architecture.md)",
        "candidate": {
            "name": "prm", "repo_url": REPO_URL, "commit": _git_head(),
            "pna_spec_version": PNA_SPEC_VERSION, "picks_source": "declared", "axis_picks": AXIS_PICKS,
        },
        "summary": {
            "posture": "conformant",                          # no non-conformant, nothing undetermined
            "headline": headline,
            "leading_concerns": partials,
        },
        "findings": findings,
    }


def _violations(report: dict) -> list:
    """Minimal self-check (hermetic): the toolkit's report-fixtures-lint.py is the authoritative validator
    (run via `just evaluate-report` when the toolkit is checked out); this mirrors its load-bearing rules."""
    v = []
    if report.get("report_schema_version") != "0.1":
        v.append("report_schema_version must be '0.1'")
    if not report["findings"]:
        v.append("findings must be non-empty")
    for f in report["findings"]:
        if f["status"] == "conformant" and not f.get("citations"):
            v.append(f"{f['ac_id']}: conformant finding needs >=1 citation")
        if f["status"] == "not-applicable" and not f.get("rationale"):
            v.append(f"{f['ac_id']}: not-applicable finding needs a rationale")
    v += [f"attestation evidence: {p}" for p in conformant_row_problems(parse_attestation())]
    return v


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = build_report()
    violations = _violations(report)
    if violations:
        print("evaluate-report violations:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    if "--check" in argv or "--no-write" in argv:
        print(f"OK — {len(report['findings'])} findings, posture={report['summary']['posture']} (not written)")
        return 0
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {_OUT.relative_to(_REPO)} — {len(report['findings'])} findings, posture={report['summary']['posture']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
