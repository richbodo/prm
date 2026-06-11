# Conformance Report — PRM (Personal Relationship Manager)

> Derived from [`../Architecture.md`](../Architecture.md) by `scripts/conformance_report.py`. The machine-readable form is [`evaluate-report.json`](evaluate-report.json) (`scripts/evaluate_report.py`). Regenerate both with `just evaluate-report`.

## 🟢 Conformant to the PNA Spec (PNT 0.1 (draft)) for PRM's declared flavor

- **Conformant rows:** 12
- **Partial-conformance (human-review):** 2
- **Not-applicable (flavor):** 4
- **Findings:** 0 ✅

## Attestation rows

| AC | Source | Status | Evidence |
|---|---|---|---|
| AC-1 | universal | conformant | `tests/unit/test_private_store.py::test_import_seeds_private_and_keeps_shared_intact` → live; `tests/db/test_shared_db.py` → live |
| AC-4 | universal | conformant | `tests/db/test_shared_db.py::test_incompatible_schema_version_is_rejected` → live; `tests/db/test_private_db_version.py::test_private_mismatch_refuses_seed` → live; `tests/unit/test_daemon_diag.py::test_schema_mismatch_refuses_write_reads_continue` → live |
| AC-6 | universal | conformant | `tests/unit/test_doctor.py::test_unlock_clears_stale_lock` → live |
| AC-7 | universal | conformant | `tests/unit/test_diag.py::test_state_dump_is_sanitized_metadata` → live; `tests/unit/test_daemon_diag.py::test_diag_route_sanitized` → live; `tests/unit/test_doctor.py::test_doctor_dump_runs` → live |
| AC-9 | universal | conformant | `tests/unit/test_apply.py::test_snapshot_and_restore` → live |
| AC-10 | universal | conformant | `tests/unit/test_reimport.py::test_reimport_added_updated_stale` → live |
| AC-11 | universal | conformant | `tests/unit/test_lock.py::test_second_holder_refused_with_message` → live |
| AC-15 | universal | conformant | `tests/unit/test_build_label.py::test_running_label_is_git_date_sha` → live |
| AC-16 | universal | not-applicable | — |
| AC-17 | universal | conformant | `tests/db/test_shared_db.py::test_load_writes_records_provenance_and_fts` → live |
| AC-18 | universal | not-applicable | — |
| AC-19 | universal | not-applicable | — |
| AC-PRM-A | universal | partial-conformance | `tests/unit/test_mcp_consent.py` → live |
| AC-PRM-D | universal | conformant | `tests/unit/test_reimport.py` → live |
| AC-MCP-A | universal | partial-conformance | `tests/unit/test_mcp_tools.py::test_submit_is_propose_only` → live; `tests/unit/test_mcp_consent.py` → live |
| AC-MCP-B | universal | not-applicable | — |
| AC-PRM-B | flavor-derived | conformant | `tests/unit/test_candidates.py::test_detect_clusters_and_tiers` → live; `tests/unit/test_private_store.py::test_preview_merge_marks_conflicts` → live; `tests/unit/test_reimport.py::test_reimport_preserves_merges` → live; `tests/e2e/test_dedup_flow.py::test_bulk_flow_scenario` → live |
| AC-PRM-C | flavor-derived | conformant | `tests/unit/test_lock.py::test_second_holder_refused_with_message` → live |
