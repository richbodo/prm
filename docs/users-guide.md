# PRM — User's Guide

A **local-only Personal Relationship Manager**: it pulls your contacts out of the places they're
scattered (Google, Apple, LinkedIn, Facebook, loose `.vcf`/`.csv` files) into one place you own, and
— eventually — lets you keep private relationship notes on top and an AI help maintain them. Your
data never leaves your device.

> **Status (v0.1, in progress).** What works **today**: point `prm` at your real contact exports —
> **vCard, Google Takeout, LinkedIn, Google CSV, Facebook** — and it parses them, gives each contact a
> stable identity, **saves them into a local database**, lets you **search from the terminal or browse
> them in a local web workspace** (`just serve`), and **find & merge duplicate contacts** in that
> workspace — review one at a time **or bulk-approve whole groups** (spot-check, then merge in one pass),
> pick the winner for any conflict, every merge reversible. An **AI can also propose the merges for you**
> (over a local MCP server) — it can only *propose*; you still review and approve each one in the workspace. You can also **re-import an updated export** and preview
> exactly what changes (`just reimport`), and **export your contacts** — a portable vCard to move them to
> another app, or a lossless `--raw` backup you can re-import (`just export`). What's coming next: a private overlay (groups, tags, notes) +
> a custom relationship schema (see [Roadmap](roadmap.md)). This guide marks
> clearly what runs now vs. what's coming.

---

## What you can do today

| Capability | Status |
| --- | --- |
| Import **vCard** / **Google Takeout** / **LinkedIn** / **Google CSV** / **Facebook** | ✅ works |
| **Search** your imported contacts from the terminal | ✅ works |
| Browse + search your contacts in a local **web workspace** (`just serve`) | ✅ works |
| **Find & merge duplicate contacts** in the workspace — one at a time **or in bulk by group**, reversible | ✅ works |
| Inspect an export without saving anything (`--dry-run`) | ✅ works |
| **Re-import** an updated export — preview changes, merges preserved (`just reimport`) | ✅ works |
| Try a realistic demo with synthetic data (no personal data needed) | ✅ works |
| **AI**-assisted dedup — an AI proposes merges (over MCP), you review them | ✅ works |
| **Export** your merged contacts to a portable vCard (`just export`) | ✅ works |
| **Back up** all raw records to re-importable JSON (`just export --raw`) | ✅ works |

---

## Before you start

- **Python 3.10 or newer** and **git**.
- **[`just`](https://github.com/casey/just#installation)** (recommended) — the command runner this
  guide uses, so setup and everyday tasks are one memorable word: `just setup`, `just serve`,
  `just search`. Install it with `brew install just` (macOS), `cargo install just`, or your package
  manager. Every recipe is a thin wrapper around a plain command — `just` is a convenience, not a
  requirement. Run `just` (or `just --list`) to see every recipe and what it runs; the "without `just`"
  commands are given alongside throughout.
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

One command creates an isolated environment (`./.venv`) and installs the package with its dependency:

```bash
just setup            # creates ./.venv and installs prm + vobjectx + pytest (editable)
```

That's all — **every other `just` command in this guide uses that environment automatically**, so you
never have to activate anything. (Prefer `prm`, `pytest`, and `python` directly on your PATH? Run
`just shell` to drop into an activated sub-shell; type `exit` to leave.)

**Confirm your build is sound:**

```bash
just doctor           # checks Python ≥3.10, the deps, FTS5, and the workspace port
just test             # runs the unit + DB test suite
```

You should see the checks pass and the tests green.

<details>
<summary><strong>Without <code>just</code></strong> (the equivalent plain commands)</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # installs prm + vobjectx + pytest
pytest                             # confirm the build (or: python3 tests/unit/test_vcard_path.py)
```

After this, `prm …` and `python3 -m cli …` are interchangeable. `just --list` shows the recipe behind
every command in this guide, so you can always run the underlying command by hand instead.
</details>

## 3. Try the demo (no personal data)

The fastest way to see it work is the built-in demo, which seeds a realistic **~1000-contact**
database from the repo's *synthetic* fixtures — no personal data, nothing written outside the repo:

```bash
just demo             # = prm init --demo
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

Now try `just search ada` and `just status` against it. This is exactly what you'll see against your
own data — read [Step 5](#5-inspect-an-import-dry-run) for what each line means. (The CSV lines are
skipped for now; that parser is the next increment.) When you're ready to import your *own* contacts,
wipe this synthetic home first with `just clean-data` — see the note at [Step 6](#6-import-for-real).

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

Run `just dry-run` on a file (or folder) to parse it and print a report **without writing anything**:

```bash
just dry-run ~/Downloads/takeout-20260603.zip          # = prm import … --dry-run
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

Useful flags pass straight through the `just` recipes, and you can hand over **many paths at once** —
either a **folder** (PRM recurses into it) or a shell **glob** like `*` (your shell expands it to a
list, and PRM handles each entry):

```bash
just ingest contacts.vcf --source apple_icloud  # override the inferred source label
just dry-run ~/Downloads/                        # a folder — PRM walks it, previewing every recognized file
just dry-run ~/Downloads/exports/*               # a glob — preview every export dumped loose in one folder
just dry-run takeout.zip --json                  # machine-readable output (for scripts / the AI surface)
```

## 6. Import for real

> **Coming from the demo?** Your PRM home still holds the ~1000 synthetic demo contacts. Run
> `just clean-data` to wipe `./prm-data/` before importing your own, or keep them mixed in — they're
> harmless and `just status` shows the split by source.

Use `just ingest` to save into your PRM home's `shared.db`:

```bash
just ingest ~/Downloads/takeout-20260603.zip           # = prm import … (no --dry-run)
```

In an interactive terminal it shows the preview and prompts `Proceed with import? [y/N]`. Append
`--non-interactive` (or `--json`) for unattended runs that never prompt. The same command ends with
`shared.db now holds N record(s)`.

Re-running an import is **safe and idempotent** — each contact gets a stable identity, so importing
the same file again updates rather than duplicates. You can import several sources into one home
(e.g. a Google Takeout `.zip` and then an Apple `.vcf`) and they accumulate together. The same path
rules as Step 5 apply — point `just ingest` at a folder, or glob a folderful of loose exports at once:

```bash
just ingest ~/Downloads/exports/*               # import every export in that folder in one go
```

### Re-importing an updated export

When you re-download an export later (more contacts, edits, deletions), use **`just reimport`** to see
exactly what would change before committing:

```bash
just reimport ~/Downloads/takeout-20260820.zip --source google_takeout --dry-run   # preview only
just reimport ~/Downloads/takeout-20260820.zip --source google_takeout             # preview, then confirm
```

It reports, per source, how many records are **new**, **updated**, **unchanged**, and **stale** (in
your store but no longer in the export). Pass `--source` so it compares against the right source.
It is **non-destructive**: stale records are **kept** (just flagged), and **your merge decisions are
preserved** (re-attached by stable identity — invariant **INV-6**). Before applying it takes a
snapshot and records the re-import in the audit log. `--non-interactive` applies without prompting.

## 7. Check status

```bash
just status
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
just search "ada"
just search "navy" --limit 50
just search "lovelace" --json
```

Search is **prefix-matched** across name, email, organization, and notes, so `lovel` finds
`Lovelace`. Output is one contact per line (`name · email · org`); `--json` gives a structured list.
The same data is also browsable in the local web workspace — see the next section.

## 9. The web workspace — browse, and merge duplicates

For a point-and-click view, start the local workspace (seed or import data first — `just demo`):

```bash
just serve                     # serves http://127.0.0.1:8770 (Ctrl-C to stop)
just open                      # opens that URL in your browser — run in a second terminal
just serve 9000                # pick a different port → http://127.0.0.1:9000
```

It serves a small single-page app. **Contacts** lets you **search, browse the full list, and open any
contact** to see its fields and **per-field provenance** (which source each value came from). It reads
the same home, so import (or `just demo`) first; if there's no database yet it says so.

(If a previous run left the port busy, `just port` frees it.)

**Duplicates** finds contacts that look like the same person and helps you merge them. It offers two
modes (toggle at the top):

- **Review individually** — walk the candidates one at a time, most-confident first. Each is shown as a
  side-by-side **merge preview**: multi-valued fields (emails, phones) **combine**; a single-valued
  field that disagrees (like a name) is flagged so **you pick which to keep** — nothing is pre-selected.
  **Approve merge** to combine them, **Not a duplicate** to dismiss it (it won't come back), or **Skip**.
- **Bulk approve** — for clearing many at once. Pick the **groups** you trust (all *same email or phone*,
  all *AI-proposed* 🤖, …; the confident and AI groups are pre-selected, the weaker name-based groups are
  opt-in behind a confirm), then **flip through them** (← → keys) to spot-check — resolving any field
  conflict inline and **excluding** anything that looks wrong — and **merge the whole set in one click**.
  The batch applies atomically, so a single **Undo** reverses all of it.
- In both modes: confident matches share an exact email or phone; weaker, name-only matches are flagged
  and never merged automatically. **Undo last merge** reverses the most recent merge (or the last batch).

Two guarantees worth knowing:

- **Local only.** The daemon binds `127.0.0.1` — it is never reachable from the network. Your contact
  data does not leave the device (invariant **INV-1**). Stop the daemon with `Ctrl-C`.
- **Reversible & non-destructive.** Your imported source records are never changed; a merge is a
  decision layered on top, snapshotted before it's applied, and undoable.

### Let an AI propose the merges (optional)

An AI assistant can do the reviewing legwork: it scans for duplicates and **proposes** merges, which
show up in this same Duplicates tab tagged **🤖 AI proposal** for you to approve or reject. The AI can
only *propose* — it can never apply a merge.

Set this up in two steps, both detailed in [`../mcp_servers/README.md`](../mcp_servers/README.md):

1. **Register the two MCP servers** (`prm-shared-data`, `prm-dedup`) with your MCP client (e.g. Claude
   Desktop) — `just mcp-install` does it in one step (backs up your config, then merges them in), or
   wire them up by hand.
2. **Hand the assistant the dedup prompt** in [`../mcp_servers/prompts/dedup.md`](../mcp_servers/prompts/dedup.md)
   — paste its contents (or attach the file) into the session; it is **not** auto-loaded. It drives the
   scan → clarify → propose loop; every proposal still lands here in the Duplicates tab for you to apply.

**A local AI is recommended**: once a cloud client can read your contacts, that data leaves the device
— so the read tools warn, and the consent gate for cloud AI is a later milestone (see
[Roadmap](roadmap.md)).

---

## 10. Export your contacts

Your contacts are yours — take them anywhere, or keep a backup. `just export` does both, in two modes.

**Portable vCard (the default) — to move contacts into another app.** Writes your **merged** contacts
(de-duplicated, with the conflict choices you made during dedup applied) as a **vCard 3.0** (`.vcf`) —
the format Google, Apple, and Outlook all import:

```bash
just export --out contacts.vcf        # = prm export --out contacts.vcf
just export                           # write to stdout instead (pipe it elsewhere)
```

One card per person: multi-valued fields (emails, phones) are **unioned** across the sources a contact
came from, and for any single-valued field you reconciled, the value **you chose** is the one written.
This is the *merged* view, so re-importing it back **into** PRM creates fresh records rather than
re-attaching — it's a portability export, not a backup. For that, use `--raw`.

**Lossless backup (`--raw`) — a re-importable copy of everything.** Writes a JSON file holding **every
raw record exactly as stored** — its source, stable identity, fields, and per-field provenance, with
nothing merged or dropped:

```bash
just export --raw --out backup.json   # = prm export --raw --out backup.json
```

Re-importing that file rebuilds the same records with the same identities (`just ingest backup.json`),
so it's a true round-trip — ideal as a periodic backup of your mirrored contacts. (It backs up the
**raw contacts store**; your private merge decisions live separately in `relationships.db`.)

Both modes read the same PRM home as everything else, so import (or `just demo`) first.

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
  relationships.db   # your private relationship data + merge decisions (local-only)
  media/             # content-addressed contact images (local-only)
  proposals/         # staged AI change-sets awaiting your review
  snapshots/         # pre-change backups
  audit.log.jsonl    # append-only log of applied changes
```

(`shared.db` is created on your first import. `relationships.db` (the private store, renamed from
`private.db` in v0.2 — an existing home is migrated automatically on first run), `proposals/`, and
`audit.log.jsonl` arrive with the private-overlay and dedup milestones.)

## Privacy

- **Local-only by design.** Your contacts and any private notes never leave your device (**INV-1**).
- **Real exports are never committed.** Drop them in `ignore-data/` (gitignored) or anywhere outside
  the repo. The committed test data is entirely synthetic.
- The mirrored contacts store is **read-only at runtime** — only a deliberate, user-run import
  changes it.

## Troubleshooting

- **`ModuleNotFoundError: vobjectx`** — run `just setup` (it installs it), or `pip install vobjectx`.
  If you already have the older `vobject`, PRM uses it automatically.
- **A file is "SKIPPED" as unrecognized** — PRM imports vCard (`.vcf`), Google Takeout (`.zip`),
  LinkedIn (`.zip`/`Connections.csv`), Google CSV (`.csv`), Facebook friends (`.zip`/`.json`), and its
  own raw backup (`.json` from `just export --raw`). Other files in an export folder are ignored. **Note:** a Facebook friend is just a *name and the
  date you connected* — no email or phone — so those contacts are intentionally thin.
- **`prm: command not found`** — the `just` recipes don't need `prm` on your PATH (they run `./.venv`
  directly), so prefer `just serve`, `just search`, and so on. To use `prm` itself, run `just shell`
  (activates the venv) or `source .venv/bin/activate`; or run `python3 -m cli …` from the repo root.
- **Something's wrong / filing a bug** — run `prm doctor` (or open the workspace at `?diag`) for a
  **sanitized** diagnostic dump (store stats, lock state, your build `<date>-<sha>` — **no contact data**)
  you can paste into a bug report. `prm doctor --unlock` clears a stale home lock (it refuses while a PRM
  process is actually running, so it can't create two writers).

## What's next

All five source parsers, the local **web workspace**, **find-&-merge deduplication** (review
one-at-a-time **or bulk-approve by group**, reconcile, undo), **AI-proposed merges over MCP** (the AI
proposes; you approve), opt-in **non-destructive re-import** (`just reimport`), and **export** (a
portable vCard plus a lossless `--raw` backup, `just export`) are done. The last v0.1 step is the `Architecture.md`
conformance attestation (the toolkit reference-design deliverable); then v0.2 brings the private overlay
(groups, tags, notes) and a custom relationship schema. See the [Roadmap](roadmap.md).

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
