# LinkedIn — connections fixture

Synthetic LinkedIn `Connections.csv`, used to test the PRM ingester's **vendor-CSV** path (v0.1 M1).
Generated and fake; **no real export is committed** (real ones go in [`../incoming/`](../incoming/)).

## What a real LinkedIn export looks like

A LinkedIn "Basic" export is a **zip of ~29 CSVs**. Only **`Connections.csv`** is contact data —
`Email Addresses.csv`, `PhoneNumbers.csv`, and `Profile.csv` are the *account owner's own* data, not
the connections'. So this fixture is a single `Connections.csv`; there is no zip step.

Distilled from a real export (1,174 connections; values redacted):

| Trait | Finding | Implication |
| --- | --- | --- |
| Format | **CSV**, not vCard | First concrete **vendor-CSV** mapping |
| Preamble | **3 lines before the header** (`Notes:`, a quoted privacy notice, blank) | Must be skipped or `csv` parses garbage |
| Columns | `First Name, Last Name, URL, Email Address, Company, Position, Connected On` | → N/FN, URL, EMAIL, ORG, TITLE + a relationship date |
| Email | **blank for ~98%** (real: 22/1,174) | LinkedIn only includes opted-in emails — the preamble says so |
| Phone / UID | **none** | no phone column; no stable id |
| URL | almost always present | the **profile URL is the near-stable identifier** |
| Name | a few rows **blank** (withheld) but keep a URL | tolerate nameless-but-URL'd contacts |
| `Connected On` | `DD Mon YYYY` date | relationship metadata (feeds v0.2 schema / provenance) |

## Why this matters for stable id & dedup

- **Stable-id fallback needs another rung.** The plan's `UID → email → content-hash` assumes email
  is usually present. LinkedIn has **no UID and ~98% no email**, so the realistic key is the
  **profile URL**, then a hash of `name + company`.
- **Cross-source dedup is mostly fuzzy.** Google/Apple key on email/phone; LinkedIn has neither for
  most rows, so a LinkedIn connection typically matches other sources only on **name (+ company)** —
  which must be **proposed, never auto-merged** (AC-PRM-B). This is the conservative case the
  propose→review→apply flow exists for.

## Cross-source hooks (both no-UID, matching the Google/Apple fixtures)

- **Ada Lovelace** — *no email*, Company "Analytical Engines": matching the Google/Apple Ada is a
  **fuzzy name+company** decision (the 98% case).
- **Grace Hopper** — *has* `grace.hopper@example.org`, matching the Apple Grace: a **confident
  email-based** cross-source match (the rare ~2% case).

## Working with the fixture

```bash
python generate_source.py          # (re)write sources/ + expected/ (deterministic)
python generate_source.py --check  # CI guard: fail if committed output is stale
python test_fixtures.py            # stdlib validation — or: pytest test_fixtures.py
```

`sources/` and `expected/` are generated — edit `generate_source.py`, never the output by hand.
