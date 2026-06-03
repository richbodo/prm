# Google Takeout — contact fixtures

Synthetic Google Takeout *Contacts* exports used to test the PRM ingester (v0.1 milestone M1).
Everything here is generated and fake; **no real export is ever committed** (see
[`../incoming/`](../incoming/) for where real exports go).

## What a real Takeout contacts export looks like

A Takeout download is a **zip**. For Contacts it unpacks to:

```
Takeout/
  archive_browser.html
  Contacts/
    All Contacts/
      All Contacts.vcf          # the full set — what you usually want
    My Contacts/
      My Contacts.vcf
    Starred in Android/
      Starred in Android.vcf
    <label name>/               # one folder per Google Contacts label
      <label name>.vcf
```

Older exports used a **flat** layout — `Contacts/All Contacts.vcf` with no per-label
subfolders (covered by the `takeout-legacy-flat` fixture).

Key real-world facts the fixtures encode:

- **vCard 3.0** (`VERSION:3.0`) — the version Google emits. Not 4.0.
- **CRLF** line endings and **75-octet line folding** (continuation lines begin with a space).
- Google's **`itemN.PROP` grouping**: `item1.EMAIL:…` paired with `item1.X-ABLabel:Work`.
- Contacts frequently have **no `UID`** — so a stable id must fall back to normalized email or a
  content hash (INV-5). This is the common case, not an edge case.
- Photos are normally **embedded** as `PHOTO;ENCODING=b` base64. (A separate external-image-file
  convention also exists in the wild and in the legacy `prt` importer; `takeout-photos-external`
  covers that heuristic.)
- The **same person appears in several files** (All Contacts + My Contacts + label folders).

## The fixtures

| Fixture | Exercises |
| --- | --- |
| `takeout-basic` | Modern foldered layout; 3 clean records with UIDs; no photos. |
| `takeout-google-quirks` | `itemN` grouping, folded NOTE, unicode (José Ñoño / 田中太郎), apostrophe names, **no UID**, same person across multiple folders. |
| `takeout-photos` | Embedded base64 `PHOTO` (modern norm) mixed with a no-photo record. |
| `takeout-photos-external` | Photo as an external image file named after the contact (legacy heuristic). |
| `takeout-duplicates` | Near-duplicates across labels: shared phone/email, case-different email, name variant — for dedup. |
| `takeout-legacy-flat` | Older flat `Contacts/All Contacts.vcf` layout. |

Per-fixture expected results live in [`expected/`](expected/) as JSON manifests (contact counts,
how many lack a UID, embedded-photo counts, the FN list). These are the assertion targets.

## Layout of this directory

```
google_takeout/
  generate_sources.py   # single source of truth; emits sources/ + expected/ (deterministic)
  build.py              # zips sources/<name>/ -> build/<name>.zip (gitignored)
  test_fixtures.py      # stdlib-only validation against expected/ (also pytest-discoverable)
  sources/              # committed, diffable unpacked Takeout trees
  expected/             # committed JSON manifests
  build/                # generated zips (gitignored)
```

`sources/` and `expected/` are **generated** — edit `generate_sources.py`, never the output by
hand. To add a contact or a fixture, edit the builders / `build_fixture_specs()` and rerun.

## Working with the fixtures

```bash
python generate_sources.py          # (re)write sources/ and expected/
python generate_sources.py --check  # CI guard: fail if committed output is stale
python build.py                     # build build/<name>.zip for every fixture
python build.py --list              # list fixtures
python test_fixtures.py             # validate (stdlib only) — or: pytest test_fixtures.py
```

## Notes on `vobjectx` (the parser the v0.1 plan recommends)

Verified to parse this whole corpus — unicode, `itemN` emails, folded NOTEs, missing UIDs, and the
embedded PNG all round-trip correctly. Two gotchas found while verifying, worth carrying into M1:

- On Python 3.10 it has an **undeclared `typing_extensions` dependency** — install it alongside.
- The API is **snake_case** (`vobjectx.read_components(...)`, not the old `readComponents`).
