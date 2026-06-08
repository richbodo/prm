# PRM — User's Guide

A **local-only Personal Relationship Manager**: it pulls your contacts out of the places they're
scattered (Google, Apple, LinkedIn, Facebook, loose `.vcf`/`.csv` files) into one place you own, and
— eventually — lets you keep private relationship notes on top and an AI help maintain them. Your
data never leaves your device.

> **Status (v0.1, in progress).** What works **today**: point `prm` at your real contact exports —
> **vCard, Google Takeout, LinkedIn, Google CSV, Facebook** — and it parses them, gives each contact a
> stable identity, **saves them into a local database**, lets you **search from the terminal or browse
> them in a local web workspace** (`prm serve`), and **find & merge duplicate contacts** in that
> workspace — review one at a time, pick the winner for any conflict, every merge reversible. An **AI
> can also propose the merges for you** (over a local MCP server) — it can only *propose*; you still
> review and approve each one in the workspace. What's coming next: opt-in non-destructive re-import,
> then a private overlay + custom relationship schema (see [Roadmap](roadmap.md)). This guide marks
> clearly what runs now vs. what's coming.

---

## What you can do today

| Capability | Status |
| --- | --- |
| Import **vCard** / **Google Takeout** / **LinkedIn** / **Google CSV** / **Facebook** | ✅ works |
| **Search** your imported contacts from the terminal | ✅ works |
| Browse + search your contacts in a local **web workspace** (`prm serve`) | ✅ works |
| **Find & merge duplicate contacts** in the workspace — review, reconcile, reversible | ✅ works |
| Inspect an export without saving anything (`--dry-run`) | ✅ works |
| Try a realistic demo with synthetic data (no personal data needed) | ✅ works |
| **AI**-assisted dedup — an AI proposes merges (over MCP), you review them | ✅ works |

---

## Before you start

- **Python 3.10 or newer** and **git**.
- The only runtime dependency is a vCard lexer — **`vobjectx`** (installed in the setup step below).
  The older `vobject` works as a drop-in fallback if that's what you already have.

This tool is meant to be **built from source and verified by you** — you can read every line, run the
test suite, and (as the toolkit matures) run the Personal Network Toolkit conformance tests to
confirm it behaves as a compliant, local-only PNA before you trust it with your contacts.

---

## 1. Get the code

```bash
git clone https://github.com/richbodo/prm.git
cd prm
```

## 2. Set up

Pick one of two ways to run it.

**A — Editable install (recommended).** Creates an isolated environment, installs the package and
its dependency, and gives you the `prm` command:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # installs prm + vobjectx + pytest
```

**B — No install.** Run it straight from the repo root as a module (needs `vobjectx` or `vobject`
importable in your Python):

```bash
python3 -m cli --help              # equivalent to `prm --help`
```

Throughout this guide, `prm …` and `python3 -m cli …` are interchangeable.

**Confirm your build is sound** by running the tests:

```bash
pytest                              # or, without pytest:
python3 tests/unit/test_vcard_path.py
```

You should see the unit and fixture tests pass.

## 3. Try the demo (no personal data)

The fastest way to see it work is the built-in demo, which seeds a realistic **~1000-contact**
database from the repo's *synthetic* fixtures — no personal data, nothing written outside the repo:

```bash
prm init --demo
```

```
PRM home ready at /…/prm/prm-data
Demo seeded into /…/prm/prm-data/shared.db:
Ingest imported: 1004 contacts from 4 file(s)
  - …/google_takeout_bulk/…/All Contacts.vcf: 1000 · google_takeout (vcard, confidence override)
  - …/apple_icloud/…/icloud-export.vcf: 4 · apple_icloud (vcard, confidence override)
  - …/linkedin/…/Connections.csv: SKIPPED — CSV parser lands next (plan §11 M1)
  - …/google_csv/…/contacts.csv: SKIPPED — CSV parser lands next (plan §11 M1)
  by source: apple_icloud=4, google_csv=0, google_takeout=1000, linkedin=0
  stable-id: email=904, hash=95, url=5  (325 name-less)
  shared.db now holds 1001 record(s)
```

Now try `prm search ada` and `prm status` against it. This is exactly what you'll see against your
own data — read [Step 5](#5-inspect-an-import-dry-run) for what each line means. (The CSV lines are
skipped for now; that parser is the next increment.)

## 4. Export your contacts

Download your contacts from each service. PRM reads the files **as they come** — no account
credentials, no sync.

| Source | How to export | Usable today? |
| --- | --- | --- |
| **Google** | [takeout.google.com](https://takeout.google.com) → *Deselect all* → select **Contacts** → choose **vCard** → create export → download the `.zip` | ✅ yes |
| **Apple iCloud** | Mac **Contacts** app → select all (`⌘A`) → **File ▸ Export ▸ Export vCard…** → one `.vcf`. (Or iCloud.com ▸ Contacts ▸ gear ▸ *Export vCard*.) | ✅ yes |
| **LinkedIn** | **Settings & Privacy ▸ Data Privacy ▸ Get a copy of your data** → pick **Connections** → request → download the `.zip` (import the `.zip` directly — PRM pulls `Connections.csv` out of it) | ✅ yes |
| **Google CSV** | [contacts.google.com](https://contacts.google.com) → **Export ▸ Google CSV** → one `.csv` | ✅ yes |
| **Facebook** | **Settings ▸ Your information ▸ Download your information** → select **Friends and followers**, format **JSON** → request → import the `.zip` or the `friends.json` | ✅ yes |

You can keep these files anywhere. The repo has a gitignored staging folder, **`ignore-data/`**, that
exists precisely so real exports can sit next to the code without any risk of being committed —
handy while testing. Personal data is never committed (this is invariant **INV-1**, "private data
stays on the device").

## 5. Inspect an import (dry run)

Point `prm` at a file (or folder) with `--dry-run` to parse it and print a report **without writing
anything**:

```bash
prm import ~/Downloads/takeout-20260603.zip --dry-run
```

```
Ingest DRY RUN — nothing written: 4 contacts from 1 file(s)
  - …/All Contacts.vcf: 4 · vcard (vcard, confidence low)
  by source: vcard=4
  stable-id: email=4  (1 name-less)
```

How to read it:

- **per-file line** — `count · source (format, confidence)`. PRM detects the *format* and infers a
  *source* label (e.g. `apple_icloud`, `google_takeout`) with a confidence. A bare `.vcf` with no
  tell-tale markers shows as `vcard` / low confidence.
- **by source** — totals grouped by the inferred source.
- **stable-id** — how each contact got its durable identity (most sources carry no `UID`, so PRM
  falls back): `uid` → `email` → `url` → `hash` (a content hash, the last resort for the thinnest
  records). `… name-less` counts contacts that had no name at all (common in real exports).

Useful flags:

```bash
prm import contacts.vcf --source apple_icloud   # override the inferred source label
prm import ~/Downloads/ --dry-run               # walk a folder; import every recognized file
prm import takeout.zip --dry-run --json         # machine-readable output (for scripts / the AI surface)
```

## 6. Import for real

Drop the `--dry-run` to save into your PRM home's `shared.db`:

```bash
prm import ~/Downloads/takeout-20260603.zip
```

In an interactive terminal it shows the preview and prompts `Proceed with import? [y/N]`. Use
`--non-interactive` (or `--json`) for unattended runs that never prompt. The same command ends with
`shared.db now holds N record(s)`.

Re-running an import is **safe and idempotent** — each contact gets a stable identity, so importing
the same file again updates rather than duplicates. You can import several sources into one home
(e.g. a Google Takeout `.zip` and then an Apple `.vcf`) and they accumulate together. (The full
opt-in, non-destructive *re-import with an orphan preview* is a later milestone; today's import is
the plain idempotent load.)

## 7. Check status

```bash
prm status
```

```
PRM home: /…/prm/prm-data (exists)
  shared.db: 997 record(s), 997 distinct identities (schema v1)
    by source: google_takeout=997
    last import: 2026-06-04T05:51:09Z
```

(1000 parsed → 997 stored above means three records shared an identity and were merged — exactly
what de-duplication will refine later.)

## 8. Search your contacts

```bash
prm search "ada"
prm search "navy" --limit 50
prm search "lovelace" --json
```

Search is **prefix-matched** across name, email, organization, and notes, so `lovel` finds
`Lovelace`. Output is one contact per line (`name · email · org`); `--json` gives a structured list.
The same data is also browsable in the local web workspace — see the next section.

## 9. The web workspace — browse, and merge duplicates

For a point-and-click view, start the local workspace:

```bash
prm serve                      # then open http://127.0.0.1:8770
prm serve --port 9000          # pick a different port
```

It serves a small single-page app. **Contacts** lets you **search, browse the full list, and open any
contact** to see its fields and **per-field provenance** (which source each value came from). It reads
the same home, so import (or `prm init --demo`) first; if there's no database yet it says so.

**Duplicates** finds contacts that look like the same person and helps you merge them — one at a time,
most-confident first:

- Each candidate is shown as a side-by-side **merge preview**: multi-valued fields (emails, phones)
  **combine**; a single-valued field that disagrees (like a name) is flagged so **you pick which to
  keep** — nothing is pre-selected.
- **Approve merge** to combine them, **Not a duplicate** to dismiss it (it won't come back), or
  **Skip**. **Undo last merge** reverses the most recent one.
- Confident matches share an exact email or phone; weaker, name-only matches are shown lower down and
  never merged automatically.

Two guarantees worth knowing:

- **Local only.** The daemon binds `127.0.0.1` — it is never reachable from the network. Your contact
  data does not leave the device (invariant **INV-1**). Stop the daemon with `Ctrl-C`.
- **Reversible & non-destructive.** Your imported source records are never changed; a merge is a
  decision layered on top, snapshotted before it's applied, and undoable.

### Let an AI propose the merges (optional)

An AI assistant can do the reviewing legwork: it scans for duplicates and **proposes** merges, which
show up in this same Duplicates tab tagged **🤖 AI proposal** for you to approve or reject. The AI can
only *propose* — it can never apply a merge. Set this up with the local MCP servers (see
[`../mcp_servers/README.md`](../mcp_servers/README.md)). **A local AI is recommended**: once a cloud
client can read your contacts, that data leaves the device — so the read tools warn, and the consent
gate for cloud AI is a later milestone (see [Roadmap](roadmap.md)).

---

## Where your data lives

Everything for one instance lives under a single **PRM home** directory. PRM finds it in this order:

1. `--data-dir DIR` on the command line,
2. the `PRM_HOME` environment variable,
3. otherwise `./prm-data/` in the current directory (the default — **demo mode never writes outside
   the repo**).

The home will hold all of an instance's stores:

```
prm-data/
  shared.db          # mirrored contacts (read-only at runtime)
  private.db         # your private relationship data (local-only)
  proposals/         # staged AI change-sets awaiting your review
  snapshots/         # pre-change backups
  audit.log.jsonl    # append-only log of applied changes
```

(`shared.db` is created on your first import. `private.db`, `proposals/`, and `audit.log.jsonl`
arrive with the private-overlay and dedup milestones.)

## Privacy

- **Local-only by design.** Your contacts and any private notes never leave your device (**INV-1**).
- **Real exports are never committed.** Drop them in `ignore-data/` (gitignored) or anywhere outside
  the repo. The committed test data is entirely synthetic.
- The mirrored contacts store is **read-only at runtime** — only a deliberate, user-run import
  changes it.

## Troubleshooting

- **`ModuleNotFoundError: vobjectx`** — install it (`pip install vobjectx`) or use the editable
  install in Step 2. If you already have the older `vobject`, PRM uses it automatically.
- **A file is "SKIPPED" as unrecognized** — PRM imports vCard (`.vcf`), Google Takeout (`.zip`),
  LinkedIn (`.zip`/`Connections.csv`), Google CSV (`.csv`), and Facebook friends (`.zip`/`.json`).
  Other files in an export folder are ignored. **Note:** a Facebook friend is just a *name and the
  date you connected* — no email or phone — so those contacts are intentionally thin.
- **`prm: command not found`** — activate your venv (`source .venv/bin/activate`) or use
  `python3 -m cli …` from the repo root.

## What's next

All five source parsers, the local **web workspace**, **find-&-merge deduplication** (review,
reconcile, undo), and **AI-proposed merges over MCP** (the AI proposes; you approve) are done. The
remaining v0.1 milestones are opt-in **non-destructive re-import** (with an orphan preview) and the
`Architecture.md` conformance attestation, followed by the private overlay and custom relationship
schema. See the [Roadmap](roadmap.md).

---

## References

Key documents in this repo:

- **[Feature specification](prm-feature-spec.md)** — what the PRM is, its features (`F#`) and
  invariants (`INV#`), and the safe-AI-write model.
- **[Roadmap](roadmap.md)** — versioned plan (v0.1 → v0.5+) and what each release adds.
- **[v0.1 implementation plan](../plans/v0.1-implementation-plan.md)** — architecture, data model,
  CLI design, milestones (the `§…` references in this guide point here).
- **[Ingestor (`cli/`) README](../cli/README.md)** — the parse → normalize → identity pipeline and
  the CLI command shape.
- **[Source format validation status](reference/source_validation_status.md)** — which export
  formats have been verified against real-world data.
- **[Google People API schema](reference/google_people_schema.json)** — the reference contact data
  model for Google.
- **[Test fixtures](../tests/fixtures/README.md)** — the synthetic contact corpora the tool is
  built and tested against (Google Takeout, Apple iCloud, LinkedIn, Facebook, Google CSV).
- **Open question:** [What "distribution" means for a PNA](https://github.com/richbodo/prm/issues/8)
  — the build-from-source-and-verify trust model behind "before you start" above.
