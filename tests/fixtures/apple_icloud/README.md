# Apple iCloud — contact fixtures

Synthetic Apple iCloud / Contacts.app vCard export, used to test the PRM ingester (v0.1 M1).
Generated and fake; **no real export is committed** (real ones go in [`../incoming/`](../incoming/)).

## What a real Apple export looks like

Apple's **Export vCard…** (Contacts.app / iCloud) produces a **single `.vcf` file** with all cards
concatenated — *not* a zip like Google Takeout. So this fixture is just one `.vcf`; there is no
build/zip step.

Distilled from a real iCloud export (57 contacts; values redacted), Apple's flavor of vCard 3.0:

| Trait | Apple | vs Google Takeout |
| --- | --- | --- |
| Version | vCard 3.0 | same |
| Line endings | **LF** (sampled export had zero CRLF) | Google used CRLF — parser must tolerate both |
| `UID` | **absent** | also absent — INV-5 fallback mandatory in both |
| `itemN.` grouping | yes | same convention |
| `PRODID` | **per card**, and can **differ between cards** (multi-device) | Google omits |
| `REV` | per card, UTC | Google omits |
| `X-ABLabel` | Apple pseudo-labels `_$!<Work>!$_`, `_$!<Other>!$_`, … + custom (`profile`) | must decode pseudo-labels to human labels |
| `TYPE` params | mixed case + lowercase `pref`; `VOICE` on phones | case-insensitive TYPE matching required |
| `URL` | profile URLs grouped under `X-ABLabel:profile` | relevant to v0.3 public-data gathering |
| `ADR` | tagged with `X-ABADR` (country format) | — |

## The fixture

`sources/icloud-export.vcf` — 4 synthetic contacts exercising the traits above:

- **Ada Lovelace** — a deliberate **cross-source dedup hook**: her email/phone
  (`ada.lovelace@example.com` / `+1-555-0101`) match the Google `takeout-duplicates` canonical.
  Neither source has a UID, so dedup must hinge on normalized email/phone — verified to key
  identically across the two fixtures.
- **Grace Hopper** — two emails, `_$!<Other>!$_` pseudo-label, no phone, iOS `PRODID`.
- **Katherine Johnson** — phone-only (common in the real export), `TYPE=pref`/`VOICE`, custom label.
- **José Ñoño** — unicode name, `ADR` with `X-ABADR`, `NOTE`.

Expected results: [`expected/icloud-export.json`](expected/icloud-export.json).

## Working with the fixture

```bash
python generate_source.py          # (re)write sources/ + expected/ (deterministic)
python generate_source.py --check  # CI guard: fail if committed output is stale
python test_fixtures.py            # stdlib validation — or: pytest test_fixtures.py
```

`sources/` and `expected/` are generated — edit `generate_source.py`, never the output by hand.
