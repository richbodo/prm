# `cli/` — the PRM ingestor

The ingestor is the **only writer of the raw Shared DB** (INV-2). v0.1 imports contacts from files,
normalizes them to one canonical shape with a stable id and per-field provenance, and (a later
milestone) loads them into `shared.db`. See [`../plans/v0.1-implementation-plan.md`](../plans/v0.1-implementation-plan.md) §5.

## Pipeline

```
source file ──parsers──▶ ParsedRecord ──normalize──▶ CanonicalContact ──(load)──▶ shared.db
              (vobject)   (source-native)             jCard + provenance + stable id
```

Three layers, each with one job:

| Module | Responsibility |
| --- | --- |
| `parsers/` | Source bytes → `ParsedRecord`s (one per record), a flat list of library-agnostic `RawField`s. The **only** place the vCard library is imported. |
| `normalize.py` | `ParsedRecord` → `CanonicalContact`: maps fields to canonical **jCard** (`jcard.py`), captures **provenance** (`provenance.py`), assigns a **stable id** (`identity.py`). |
| `jcard` / `provenance` / `identity` | The canonical model, the per-field provenance record, and the stable-id fallback chain. |

The boundary that matters: nothing past `parsers/` imports the parsing library. Swapping
`vobject` → `vobjectx` (or a vendored lexer) touches only `parsers/vcard.py`.

## Canonical representation — jCard (RFC 7095)

`["vcard", [[name, params, type, value...], ...]]` — modeled as our own dict (`jcard.py`), not a
dependency. `JCard.to_json()` is exactly what gets stored as `source_records.raw_jcard`.

## Stable id (INV-5) — fallback chain

`UID → normalized email → source permalink → content hash(name + org + phone + source)`. Most real
sources carry no UID (**validated: 0 of 1000 in a real Google Takeout export**), so email is the
common key and the content hash is the last resort for the thinnest records. Identity assignment is
**not** cross-source merge — same-person records that differ are reconciled by the propose → review
→ apply dedup flow, never auto-merged (AC-PRM-B).

## Commands (Phase A)

```
prm init [--demo] [--data-dir DIR]        # create the PRM home; --demo seeds + imports the fixtures
prm import PATH... [--source NAME] [--dry-run] [--non-interactive] [--json]
prm status [--json]                        # counts from shared.db
prm search QUERY [--limit N] [--json]      # FTS over imported contacts
```

- **PRM home** (`config.py`): one directory for all stores (`shared.db`, `private.db`,
  `proposals/`, `snapshots/`, `audit.log.jsonl`). Resolves `--data-dir` → `$PRM_HOME` →
  `./prm-data/`. Demo mode never writes outside the repo.
- **`import`** detects format, infers the source label (shown with a confidence), and previews it.
  Interactive runs ask before persisting; `--non-interactive`/`--json`/`--dry-run` never prompt.
  `--source` overrides the inferred label.
- The **CLI is thin**: `prm_import.py` only parses args and renders; all work is in the pure
  `ingest()` (`ingest.py`) + `ImportReport` (`report.py`), reused by the future MCP surface.

## Status

- **Done:** vCard (`parsers/vcard.py`) + Google Takeout zip (`parsers/takeout.py`) → normalize →
  **`shared.db` load path** (`core/shared_db.py`: WAL, `user_version` handshake, single-instance
  file-lock, `quick_check` + zero-row guard, idempotent re-import, FTS5). The CLI (`init`/`import`/
  `status`/`search`) over a pure `ingest()`. Validated against the Apple iCloud, Takeout quirks, and
  1000-card bulk fixtures, and a **real 1000-contact Takeout export** (imported + searched).
- **Next (plan §11 M1):** the **vendor CSV** parser (`parsers/csv.py` — LinkedIn + Google CSV,
  fixtures already exist) and Facebook JSON; then the private store + dedup (M3).
- **v0.2:** the config *file* + platform dirs, and `discover.py` (Downloads/ingestion-dir scan)
  shared with the MCP ingestion surface.

## Dependencies

`vobjectx` (the maintained vobject fork) is the vCard lexer; the API-compatible `vobject` is accepted
as a fallback, so the package runs on either. Everything else is stdlib. See `../pyproject.toml`.

## Running the tests

`pytest` is the target runner, but the tests also run as plain scripts (no pytest needed):

```
python tests/unit/test_vcard_path.py
```
