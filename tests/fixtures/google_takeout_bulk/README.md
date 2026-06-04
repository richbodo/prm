# Google Takeout — bulk / scale fixture (~1000 contacts)

A large, realistic Google Takeout `All Contacts.vcf` for **scale and DB testing**. Synthetic and
generated; **no real export is committed** (real ones go in [`../../../ignore-data/`](../../../ignore-data/)).

## Why this exists (vs. `google_takeout/`)

- [`../google_takeout/`](../google_takeout/) — small (~2-5 cards each), hand-curated to unit-test
  the **parser** against specific quirks. Use these for ingestor unit tests.
- **this fixture** — ~1000 contacts in one file, to seed the **database** so forward-looking
  features (search at scale, dedup-candidate blocking, the long-running gatherer, perf) have a
  realistic corpus. Not for asserting one quirk — for exercising volume.

## Provenance of the distribution

The field mix is **derived from a real 1000-contact Google Takeout export** (validated 2026-06-04 —
see [`../../../docs/reference/source_validation_status.md`](../../../docs/reference/source_validation_status.md)).
The committed corpus reproduces, at scale, the real export's shape:

| Trait | Real export (1000) | This fixture (1000) |
| --- | --- | --- |
| Version / endings | vCard 3.0, CRLF | vCard 3.0, CRLF |
| `UID` | **0** | **0** |
| Named / name-less | 670 / 330 | 675 / 325 |
| With email | ~1029 lines (867 grouped + 162 bare) | 901 cards (744 grouped + 157 bare) |
| `CATEGORIES` | 185 | 209 |
| `TEL` / `URL` | 123 / 47 | 115 / 48 |
| `ORG` / `BDAY` / `X-PHONETIC` | 7 / 2 / 1 | 12 / 11 / 9 |
| `PHOTO` | 58 | 49 (tiny embedded 1×1) |

The two real-world quirks the small fixtures **don't** model and this one does: **name-less cards**
(~33%, no `N`/`FN`) and **`CATEGORIES`** group membership.

## Usage

```
python generate_source.py                 # regenerate the committed 1000-card fixture
python generate_source.py --count 5000    # bigger one-off DB seed (committed file stays at 1000)
python generate_source.py --check         # CI guard: committed == generated (at default count)
python test_fixtures.py                   # validate vcf vs. expected/bulk.json
```

`--count` lets you seed an arbitrarily large DB on demand without bloating the repo; only the
default-1000 file is committed and checked.

## Deliberately out of scope (kept lean; covered elsewhere)

75-octet line folding, external-photo files, the `.zip` container, and deliberate cross-label
duplicates are **not** here — the small `google_takeout/` fixtures cover those. This is a single
flat, unpacked `All Contacts.vcf` (the superset you actually ingest) plus a stub `archive_browser.html`.

## Files

- `generate_source.py` — deterministic, parametric generator (single source of truth).
- `sources/Takeout/Contacts/All Contacts/All Contacts.vcf` — the corpus (~160 KB).
- `expected/bulk.json` — distribution manifest (counts per property).
- `test_fixtures.py` — validates the corpus against the manifest + asserts the no-UID / name-less /
  CATEGORIES quirks.
