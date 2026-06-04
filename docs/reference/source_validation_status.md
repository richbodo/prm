# Source format validation status

Tracks which SaaS contact-export formats PRM's fixtures have been **validated against a real-world
export**, versus built from documentation alone. "Validated" means: a real export was inspected
(structure only — no PII committed) and the synthetic fixture's shape was confirmed to match it.

Real exports live in the repo-root `ignore-data/` (gitignored, never committed — INV-1). This table
is the record of what each fixture's fidelity actually rests on.

## Status

| Source | Format | Fixture(s) | Real sample on hand | Validated |
| --- | --- | --- | --- | --- |
| **Google Takeout** | vCard 3.0 (zip of `.vcf` under `Contacts/`) | [`google_takeout/`](../../tests/fixtures/google_takeout/), [`google_takeout_bulk/`](../../tests/fixtures/google_takeout_bulk/) | ✅ `takeout-…Z-3-001.zip` (1000 contacts, 8 label `.vcf`s, 408 photos) | ✅ **2026-06-04** |
| **Apple iCloud** | vCard 3.0 (single `.vcf`) | [`apple_icloud/`](../../tests/fixtures/apple_icloud/) | ✅ `rsb_icloud_contacts.vcf` (57 contacts) | ✅ **2026-06-03** |
| **LinkedIn** | vendor CSV (`Connections.csv` in a zip) | [`linkedin/`](../../tests/fixtures/linkedin/) | ✅ `Basic_LinkedInDataExport_…zip` (29 CSVs) | ✅ **2026-06-03** |
| **Google CSV** | legacy *Export → Google CSV* | [`google_csv/`](../../tests/fixtures/google_csv/) | ❌ none | ❌ built from docs |
| **Facebook** | DYI `friends.json` (name + timestamp) | [`facebook/`](../../tests/fixtures/facebook/) | ❌ none | ❌ built from docs |
| **Outlook CSV** | vendor CSV | — not built | ❌ none | ⏳ not started |

Out of scope for v0.1 (no fixture needed): CardDAV live mirror, Google People API connector — file
import only this release.

## Still to validate (action items)

1. **Google CSV** — get a real *Export → Google CSV* from contacts.google.com into `ignore-data/`,
   then confirm the `google_csv/` fixture's column set, the email/phone `Type` label syntax, and the
   line endings (currently assumed CRLF). The legacy export header is wider than our representative
   subset — verify which columns a current export actually emits.
2. **Facebook** — get a real DYI **friends** export (JSON) into `ignore-data/` and confirm the
   `facebook/` mojibake handling and the `friends_v2` shape against it.
3. **Outlook CSV** — not built; drop a real sample in `ignore-data/` before building, so the first
   version is validated rather than guessed.

## What real-world validation has confirmed / revealed

### Google Takeout — validated 2026-06-04 (1000-contact export)

Confirmed our assumptions: **vCard 3.0**, **CRLF**, layout `Contacts/<Label>/<Label>.vcf` +
`All Contacts/All Contacts.vcf`, and embedded **plus** external (`.jpg`) photos. Strongest finding:
**0 of 1000 cards carried a `UID`** — missing-UID is *universal* in Takeout, not occasional, so the
email/content-hash stable-id fallback (INV-5) is the primary path, not an edge case.

Quirks the export revealed that the **small** fixtures did **not** model (now reproduced at scale in
`google_takeout_bulk/`, and candidates to backfill into the small `google_takeout/` corpus):

- **Name-less cards** — ~33% (670/1000 had `FN`). Many contacts are email- or phone-only with no
  `N`/`FN`. Identity and the contact projection must tolerate a missing display name.
- **`CATEGORIES`** — Google label/group membership (~185 cards), present even in `All Contacts.vcf`.
  Not previously modeled in any fixture.
- Apple-origin **`itemN.EMAIL` + `itemN.X-ABLabel`** grouping dominates email lines (iOS-synced
  contacts keep Apple's label convention through Google export); a minority use bare `EMAIL`.
- **`X-PHONETIC-FIRST/LAST-NAME`** extensions appear; **`ORG` is very sparse** (~7/1000).

> Follow-up (done 2026-06-04): the small `google_takeout/` `takeout-google-quirks` fixture now
> includes a **name-less, email-only card carrying `CATEGORIES`**, so the parser's unit tests cover
> both quirks directly, not only at bulk scale.

### Apple iCloud — validated 2026-06-03 (57-contact export)

Structural compare confirmed the `apple_icloud/` fixture: **vCard 3.0**, **LF** endings, **no
`UID`**, `itemN.` grouping with `X-ABLabel` (`_$!<…>!$_` sentinel labels), `X-ABADR`, per-card
`PRODID` that can differ between cards, `ADR`/`NOTE`/`ORG` and both grouped and bare `TEL`/`EMAIL`.

### LinkedIn — validated 2026-06-03 (full Basic export)

Confirmed the `linkedin/` fixture: the zip is ~29 CSVs of which only `Connections.csv` is network
data; the **3-line `Notes:` preamble** before the header and the exact header
(`First Name,Last Name,URL,Email Address,Company,Position,Connected On`) match; **email blank for
most rows**; no phone, no stable id. Minor fidelity gap noted: the real preamble's privacy notice
embeds two help URLs that the fixture omits.

## How to update this doc

When a real export is dropped into `ignore-data/` and a fixture is checked against it, flip the row
to ✅ with the date, and record under "confirmed / revealed" what matched and any new quirks found
(so the discovery is captured even after the real export is deleted).
