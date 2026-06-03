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

## Status

- **Done:** vCard (`parsers/vcard.py`) + Google Takeout zip (`parsers/takeout.py`) → normalize.
  Validated against the Apple iCloud, Google Takeout quirks, and 1000-card bulk fixtures, and
  against a real Takeout export.
- **Next:** vendor CSV (`parsers/csv.py` — LinkedIn + Google CSV column mapping; fixtures already
  exist), then the `shared.db` loader + `prm import` / `prm status` CLI.

## Dependencies

`vobjectx` (the maintained vobject fork) is the vCard lexer; the API-compatible `vobject` is accepted
as a fallback, so the package runs on either. Everything else is stdlib. See `../pyproject.toml`.

## Running the tests

`pytest` is the target runner, but the tests also run as plain scripts (no pytest needed):

```
python tests/unit/test_vcard_path.py
```
