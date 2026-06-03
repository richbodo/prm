# Google CSV — contact fixture

Synthetic **Google Contacts "Google CSV" export**, used to test the PRM ingester's **vendor-CSV**
path (v0.1 M1). Generated and fake; **no real export is committed** (real ones go in
[`../../../ignore-data/`](../../../ignore-data/)).

This is a *different source path* from [`../google_takeout/`](../google_takeout/): same Google
account, a wholly different file. Takeout gives you a zip of **vCards** (with `UID`s); the
*Export → Google CSV* option gives you one flat **CSV with no id column at all**.

## What a real Google CSV export looks like

From contacts.google.com → **Export → Google CSV** (also the CSV option in Takeout): a single,
**wide, sparse** CSV. The name is split across `Name` + `Given/Additional/Family Name`; multi-value
fields are **numbered and typed** (`E-mail 1 - Type`/`E-mail 1 - Value`, `E-mail 2 - …`,
`Phone 1 - …`); labels/groups live in `Group Membership`.

| Trait | Finding | Implication |
| --- | --- | --- |
| Format | **CSV**, wide & sparse | second concrete **vendor-CSV** mapping (after LinkedIn) |
| Header | row 0, **no preamble** (unlike LinkedIn) | parse directly; no skip |
| Id column | **none** — no `UID`/`id` at all | stable id must fall back to email, else content hash (INV-5) |
| Names | split `Name` + `Given`/`Additional`/`Family` | map to jCard `n`/`fn` |
| Multi-value | numbered + typed (`E-mail 1 - Type/Value`, …) | iterate `N` until empty |
| Groups | `Group Membership`, ` ::: `-joined, `* myContacts` marker | system marker vs. real labels (e.g. `Starred in Android`) |
| Line endings | **CRLF** | parser must tolerate (matches Google's vCard) |

## What this fixture exercises

10 contacts (`sources/contacts.csv`), covering: the no-id header, split names, numbered/typed
email & phone, a **multi-email** contact (Grace), a **no-email** contact (Hedy → content-hash
fallback), a **unicode** name (André), a **hyphenated** surname (Tim Berners-Lee), a
birthday/nickname (Vint), a website, and a `Notes` cell with a comma + embedded quotes (the
**CSV-quoting hazard**). `Group Membership` uses ` ::: ` with `* myContacts`, some rows adding
`Starred in Android`.

**Cross-source dedup hooks** (confident, email-based — none of these has a `UID` here):
- **Ada Lovelace** `ada.lovelace@example.com` and **Grace Hopper** `grace.hopper@example.org` —
  match the Apple + Takeout fixtures by email.
- **Barbara Liskov** `barbara.liskov@example.com` — matches the LinkedIn Barbara (the rare
  opted-in-email case there).

See [`../README.md`](../README.md) for the cross-source dedup story and the confidence spectrum.

## ⚠️ Built without a real sample

There was no real Google CSV on hand when this was written, so the column set, the email/phone
`Type` label syntax, and the CRLF line endings are distilled from the **documented** legacy Google
CSV format, not verified against an export. When a real Google CSV lands in `ignore-data/`,
validate those three things and update `generate_source.py` if they differ.

## Files

- `generate_source.py` — deterministic generator (single source of truth). `--check` fails if the
  committed output drifts from the generator.
- `sources/contacts.csv` — the fixture.
- `expected/contacts.json` — structural manifest (columns, counts, `has_uid_column: false`, fns).
- `test_fixtures.py` — validates source-vs-manifest + asserts the no-UID, ` ::: ` group, and
  quoted-Notes quirks. Runs standalone (`python test_fixtures.py`) or under pytest.

```
python generate_source.py          # regenerate sources/ + expected/
python generate_source.py --check  # CI guard: committed == generated
python test_fixtures.py            # validate
```
