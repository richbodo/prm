#!/usr/bin/env python3
"""Conformance evidence lint (the Security-Target integrity gate): every ``conformant`` row in
``docs/Architecture.md`` must cite **resolvable, non-deferred** evidence, and the generated
``evaluate-report.json`` must be schema-shaped. A ``conformant`` claim with no executable evidence is a
finding — this test fails the build before such a claim can ship.

    python tests/conformance/test_attestation_has_evidence.py
    pytest tests/conformance/test_attestation_has_evidence.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from conformance_lib import conformant_row_problems, parse_attestation  # noqa: E402
from evaluate_report import _violations, build_report  # noqa: E402


def test_every_conformant_row_has_resolvable_evidence():
    rows = parse_attestation()
    assert rows, "no attestation rows parsed from docs/Architecture.md"
    problems = conformant_row_problems(rows)
    assert not problems, "attestation evidence problems:\n  " + "\n  ".join(problems)


def test_evaluate_report_is_schema_shaped():
    report = build_report()
    assert not _violations(report), "evaluate-report violations"
    assert report["summary"]["posture"] in ("conformant", "mixed", "non-conformant", "indeterminate")
    assert report["findings"] and all(f["ac_id"].startswith("AC-") for f in report["findings"])
    # not-applicable findings carry a rationale; conformant findings carry citations (schema invariants)
    for f in report["findings"]:
        if f["status"] == "not-applicable":
            assert f.get("rationale")
        if f["status"] == "conformant":
            assert f.get("citations")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"[OK]   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append(t.__name__)
            print(f"[FAIL] {t.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
