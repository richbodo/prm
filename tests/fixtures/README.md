# Test fixtures

Synthetic contact-import fixtures for the PRM ingester. The ingester normalizes many real-world
contact sources to one canonical shape (internal jCard), so the fixtures' job is to reproduce the
**real-world quirks** of each source format faithfully — without ever committing real personal data.

## Ground rules

- **Fixtures are synthetic and generated.** Names are historical computing figures; emails use the
  reserved `example.com` / `example.org` domains (RFC 2606) and phone numbers use the fictional
  `555-01xx` block. Each source format has a deterministic generator script.
- **Real exports never get committed.** Drop real Takeout/CSV/vCard exports in
  [`../../ignore-data/`](../../ignore-data/) — that top-level folder is gitignored so personal data
  cannot be committed by accident (this protects INV-1). Use real exports to *inform* new synthetic
  fixtures, then add them via the generator.

## Format coverage

| Source | Standard | Status |
| --- | --- | --- |
| [`google_takeout/`](google_takeout/) | zip of vCard **3.0** under `Contacts/` | ✅ built — 6 fixtures |
| [`google_csv/`](google_csv/) | vendor **CSV** — *Export → Google CSV* (wide/sparse, numbered typed fields, **no id column**, ` ::: ` groups) | ✅ built — 10 contacts |
| [`apple_icloud/`](apple_icloud/) | single vCard **3.0** `.vcf` (LF endings, `PRODID`/`REV`, `X-ABLabel`) | ✅ built — 1 export, 4 contacts |
| [`linkedin/`](linkedin/) | vendor **CSV** — `Connections.csv` (3-line preamble, ~98% no email, no phone/UID) | ✅ built — 20 connections |
| [`facebook/`](facebook/) | DYI **JSON** — friends: name + connected-timestamp only (no URL/email/id) | ✅ built — 2 fixtures (incl. mojibake variant) |
| [`google_takeout_bulk/`](google_takeout_bulk/) | vCard **3.0** — ~1000-contact `All Contacts.vcf` for **scale/DB** testing | ✅ built — 1000 contacts |
| vendor CSV (Outlook CSV, …) | per-vendor column mapping | ⏳ planned — drop samples in `ignore-data/` |

**Small vs. bulk:** the per-source fixtures above are small (≈4-20 contacts), hand-curated to
unit-test the **parser** against specific quirks. [`google_takeout_bulk/`](google_takeout_bulk/) is a
single ~1000-contact corpus for seeding the **database** so forward-looking features (search at
scale, dedup blocking, the gatherer, perf) have a realistic volume to run against.

**Validation against real exports** is tracked in
[`../../docs/reference/source_validation_status.md`](../../docs/reference/source_validation_status.md)
— which formats are confirmed against a real sample vs. built from docs.

The canonical internal representation is **jCard (RFC 7095)** — modeled as our own dict, not a
library dependency. See [`../../plans/v0.1-implementation-plan.md`](../../plans/v0.1-implementation-plan.md) §5.

**Cross-source dedup is exercised across fixtures.** The same `Ada Lovelace` appears in Google
(both Takeout vCard and Google CSV), Apple, LinkedIn, and Facebook — none with a `UID` — so dedup
must key on the data, across a spectrum of confidence:
- **email/phone** where present (Apple ⨯ Google vCard ⨯ Google CSV; Barbara Liskov links Google CSV
  ⨯ LinkedIn) — confident.
- **name + company** where email/phone are absent (LinkedIn) — fuzzy, propose-only.
- **name only** at the weakest end (Facebook gives nothing else) — the most fuzzy case, always
  propose-only, never auto-merged (AC-PRM-B). This is the conservative case the
  propose→review→apply flow exists for.

## Adding fixtures for a new source

1. Get a real export (yours or a colleague's), put it in `ignore-data/`, inspect its structure.
2. Mirror its quirks in a new generator under `tests/fixtures/<source>/` (CRLF, encodings, label
   conventions, missing IDs, unicode, duplicates — whatever the real format actually does).
3. Emit a committed, diffable `sources/` tree + `expected/` manifests; add a stdlib validation test.
4. Never commit the real export.
