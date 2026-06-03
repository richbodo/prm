# Facebook — friends fixture

Synthetic Facebook "Download Your Information" (DYI) friends data, used to test the PRM ingester
(v0.1 M1). Generated and fake; **no real export is committed** (real ones go in
[`../incoming/`](../incoming/)).

## What a real Facebook export gives you

Almost nothing. For friends, the DYI export is JSON with just a **name and a connected-on
timestamp** — **no URL, no email, no phone, no id**. It is the weakest source the ingester handles
and the canonical "no-identifier" case.

```json
{ "friends": [ { "name": "John Doe", "timestamp": 1645392000 } ] }
```

Real exports vary in two ways this corpus reproduces:

| Trait | Detail |
| --- | --- |
| Top-level key | `friends` **or** `friends_v2` (newer exports) — the parser must accept both |
| `timestamp` | Unix epoch **seconds** → the connected-on date |
| Encoding | Facebook's well-known **UTF-8-decoded-as-latin1 mojibake**: `José` ships as `JosÃ©` (`"JosÃ©"`). Recover with `name.encode("latin-1").decode("utf-8")` |

## How it maps into the PRM

A Facebook friend becomes:

- `FN` — the display name (no structured given/family split),
- a **connected-on date** — from the `timestamp` (relationship metadata),
- `source = facebook` — the constant/default provenance field,
- **stable id** — a content hash of `name + timestamp + source` (no stronger key exists; this is
  the weakest rung of `UID → email → URL → hash`).

Because there is no email/phone/URL, a Facebook friend can only match other sources by **name** —
the most fuzzy case, always **propose-only**, never auto-merged (AC-PRM-B). "Ada Lovelace" appears
here name-only, matching the Google/Apple/LinkedIn Ada.

### Why support such thin data?

It is the seed of a **friend-reconciliation checklist**: a list of otherwise-dataless contacts,
tagged by origin, that the user (with an AI) can later research and triage — feeding private tags,
notes, and relationships ("familially related to contact B") into the v0.2 overlay. The data is
thin, but knowing *who* is in the network and *where they came from* is the starting point for
reclaiming its root. All of that annotation is private, local-only data (INV-1).

## The fixtures

| File | Shape |
| --- | --- |
| `sources/friends-basic.json` | `{"friends":[…]}`, clean UTF-8; includes the Ada name-only cross-source hook |
| `sources/friends-export-variant.json` | `{"friends_v2":[…]}` with mojibake names — tests key-variation + latin-1→UTF-8 recovery |

## Working with the fixtures

```bash
python generate_source.py          # (re)write sources/ + expected/ (deterministic)
python generate_source.py --check  # CI guard: fail if committed output is stale
python test_fixtures.py            # stdlib validation — or: pytest test_fixtures.py
```

`sources/` and `expected/` are generated — edit `generate_source.py`, never the output by hand.
