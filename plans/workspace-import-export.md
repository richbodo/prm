# Plan — Workspace import + export (the "Data" surface)

*Status: draft for review (2026-06-18). Companion to
[`v0.2-implementation-plan.md`](v0.2-implementation-plan.md) and
[`desktop-app-and-install.md`](desktop-app-and-install.md); advances the v0.2 roadmap toward
"get your contacts in and out without the terminal." This doc owns the implementation detail **and**
the rationale for this feature (same pattern as the desktop-app plan).*

## Goal

Add an **Import / Export surface to the local workspace** so a user can get contacts **in** (a file or
a folder of files) and **out** (a portable vCard or a lossless backup) entirely from the GUI — **as
guided as the CLI is guided, and as functional as the CLI is for import/export today**. This closes the
last terminal-only gap in the daily-use desktop app (`prm app`): after `just install`, a user never has
to drop to a shell to load or back up their contacts.

Concretely it makes the empty state in the screenshot — *"No contacts yet … import some contacts"* —
into a working button, and gives every install a first-class place to re-run an import or take a backup.

**Scope (confirmed with the user):**
- ✅ **Import** new file(s) / a folder — guided preview → confirm → result, mirroring `prm import`.
- ✅ **Export** — download the merged portable vCard *and* the lossless `--raw` JSON backup, mirroring
  `prm export` / `prm export --raw`.
- ⏳ **Re-import** (the `prm reimport` diff: added / updated / unchanged / stale) is **deferred** to a
  follow-up — the CLI `just reimport` remains the path. Marked honestly (not a dead button); see
  *Out of scope* below.

## Why this shape (decisions + rationale)

- **Upload, not server-side paths.** The CLI reads **paths** from the local filesystem; a browser
  cannot. Rather than make the GUI a path-typing box, the page **uploads the chosen bytes to the
  localhost daemon**, which ingests them. This (a) works **identically** in the browser (`just serve`)
  and the desktop window (`prm app`) with no pywebview JS-bridge, and (b) reuses the workspace's
  existing client-reads-files pattern (the "Match photos…" folder picker already reads local files in
  the browser). The bytes only ever travel to `127.0.0.1` — they never leave the device (INV-1
  intact). A future native-OS picker (pywebview `create_file_dialog` → real paths, no upload) and the
  roadmap's `discover_sources()` Downloads scanner (server-side paths) can be **added later as extra
  front-ends onto the same preview→commit pipeline** — the upload path is the universal baseline, not a
  dead end.

- **Bytes become temp files; the existing path-based ingest library runs unchanged.** Uploaded files
  are written to a per-session **staging dir** under the PRM home (`<home>/imports/<token>/`), and the
  pure `cli.ingest.collect/ingest` functions — which already take **paths** and already power the CLI —
  run over that dir verbatim. A folder upload preserves its relative structure in the staging dir, so
  `collect()` recursing a directory behaves exactly like the CLI pointing at a folder (and a Google
  Takeout `.zip` is just one staged file). **No new parsing/normalization code** — the GUI is a new
  front-end on the same core, exactly as the v0.1 plan intended ("a pure `ingest()` reused by the MCP
  surface").

- **INV-2 holds at the *library* level — but the daemon's role widens, on purpose.** Until now the
  daemon was the "only writer of canonical **private** data" and a pure **read** path over `shared.db`.
  This feature gives the daemon an **ingest path that writes `shared.db`** — but only by invoking the
  same `cli.ingest` library the CLI uses, **under the AC-PRM-C file-lock** (`load_into` already takes
  it). So INV-2 ("the ingestor is the only writer of the raw Shared DB") stays true *read as the ingest
  **code path** is the only writer* — whether that path is invoked by `prm import` or by the daemon;
  the file-lock serializes them so there are never two writers. This is a deliberate, documented
  widening of the daemon's responsibility, not an accident. **Doc follow-up:** clarify the INV-2 wording
  in [`v0.1-implementation-plan.md`](v0.1-implementation-plan.md) §3 and `CLAUDE.md` to say "the ingest
  *library* is the only writer, invoked by the CLI and the daemon, serialized by the lock." (Flagged for
  the maintainer because it touches an invariant's phrasing.)

- **Two-phase preview → commit, mirroring the CLI's confirm prompt.** The CLI shows a dry-run preview
  (`count · source · format · confidence`, the stable-id breakdown, name-less count) and asks
  *"Proceed with import? [y/N]."* The GUI reproduces this exactly using the same `ImportReport`
  (`cli.report.ImportReport.to_dict()`): **stage → preview (dry-run report) → confirm → commit (persist
  + result report).** Same data, same honesty — including a clear **"SKIPPED — unrecognized format"**
  line per file, never a silent drop (fail-loudly).

- **Source override = the CLI's `--source`.** When inference is low-confidence (a bare `.vcf` →
  `vcard`/low), the preview offers a source dropdown over the known labels
  (`apple_icloud`, `google_takeout`, `google_csv`, `linkedin`, `facebook`, `vcard`, `prm_backup`) and
  re-previews. Global to the batch, like `--source`.

- **Export is a download, read-only, no INV-2 concern.** Two `GET /api/export` variants reuse the exact
  CLI code (`core.projection.export_jcards` → `cli.vcard_writer.write_vcards`; `core.shared_db.all_records`
  → `cli.backup.dump`) and stream the result with a `Content-Disposition` attachment so the browser
  downloads it. The client uses a `Blob` + `<a download>` so it works without server-side temp files.

## Architecture overview

```
 workspace (Data view)                 daemon/server.py                         core / cli
 ─────────────────────                 ────────────────                         ──────────
 drag-drop / file / folder  ──stage──▶ POST /api/import/stage   write bytes →   <home>/imports/<token>/…
                                       POST /api/import/preview  cli.ingest.ingest(paths, dry_run=True)
   render ImportReport       ◀─────────                          → ImportReport.to_dict()
   [ Confirm import ]        ──commit─▶ POST /api/import/commit   cli.ingest.ingest(paths, home, False)
   render result report      ◀─────────   (file-lock + snapshot via load_into)  + cleanup staging dir
   [ Cancel ]                ──cancel──▶ POST /api/import/cancel   rm staging dir

 [ Download vCard ]          ──GET────▶ GET /api/export?format=vcard  projection.export_jcards → vcard_writer
 [ Download backup ]         ──GET────▶ GET /api/export?raw=1         shared_db.all_records → backup.dump
```

## What will be built

### Backend — `daemon/server.py` (+ a tiny `cli/config.py` add)

New **import routes** (POST), all of which must work **even when `shared.db` does not exist yet** — the
empty-state case is the whole point — so they are dispatched **before** the existing
`if not home.shared_db.exists(): 409` guard in `_post`:

- `POST /api/import/stage` — body `{token?, relpath, data_base64}`. Mints a `token` on the first call
  (`secrets.token_hex`), writes one decoded file to `<home>/imports/<token>/<relpath>`, returns
  `{token, staged}`. The client uploads files **sequentially** (a progress bar for folders); each call
  is small. **Security:** `token` must match `^[0-9a-f]{16,}$`; `relpath` is sanitized (reject absolute
  paths and any `..` segment) before joining — no path traversal out of the staging dir.
- `POST /api/import/preview` — body `{token, source?}`. Runs `cli.ingest.ingest(staged_paths,
  source=source, dry_run=True)` and returns `{report: report.to_dict()}` (the dry-run report; nothing
  written).
- `POST /api/import/commit` — body `{token, source?}`. Runs `cli.ingest.ingest(staged_paths,
  source=source, home=home, dry_run=False)` (persists under the file-lock + seeds the 1:1 identity
  baseline, via `load_into`), deletes the staging dir, returns the persisted `report.to_dict()` (with
  `stored`).
- `POST /api/import/cancel` — body `{token}`. Deletes the staging dir. (Plus a **boot-time sweep** of
  staging dirs older than N hours, so an abandoned upload can't accumulate.)

New **export route** (GET, read-only):

- `GET /api/export?format=vcard` → merged vCard 3.0, `Content-Type: text/vcard; charset=utf-8`,
  `Content-Disposition: attachment; filename="prm-contacts-<YYYY-MM-DD>.vcf"`.
- `GET /api/export?raw=1` → lossless JSON backup, `Content-Type: application/json`,
  `filename="prm-backup-<YYYY-MM-DD>.json"`.
- `cli.ingest`, `cli.vcard_writer`, `cli.backup` are **lazy-imported inside the handlers** (mirroring
  how `cli/prm_import.py` lazy-imports `projection`/`backup`) so the read hot-path and the MCP-adjacent
  imports stay light and no import cycle forms.

Cross-cutting daemon changes:

- **Lock contention → clean 409.** `core.lock.file_lock` is non-blocking and raises `LockError` when a
  writer is active (a concurrent merge, or a CLI `prm import`). `route()` gains a `LockError` catch →
  `409 {"error": "another operation is writing — try again in a moment"}`, so an import never crashes or
  hangs on a busy home.
- **Upload size cap.** A per-file and per-session byte cap (generous — imports are bigger than the 16 MB
  photo cap; e.g. 256 MB/session), returning `413` over the limit, so an accidental huge upload can't
  fill the home.
- `cli/config.py`: add `PrmHome.imports_dir` (`<root>/imports`) and create it in `create()` (and
  `.gitignore` is already satisfied — it lives under the home, which is gitignored / outside the repo).

### Frontend — `workspace/{index.html, app.js, styles.css}`

- **Nav:** a new **"Data"** item in the sidebar *Library* group (after Schema), wired through the
  existing generic `data-view` switcher in `app.js` (`show()` already toggles by id); entering it calls
  a new `loadData()`.
- **`<section class="view" id="data">`** with two panels:
  - **Import:** a drop zone + `Choose file…` (`<input type=file multiple>`) and `Choose folder…`
    (`<input type=file webkitdirectory>`), accepting `.vcf,.zip,.csv,.json`. On selection: read each file
    as base64 (`FileReader`), `stage` them sequentially with a progress line, then `preview`. Render the
    `ImportReport` as a compact table (per-file `count · source · format · confidence`, skipped files
    flagged, by-source + stable-id summary), with the **source-override** dropdown (re-previews). Then
    **Confirm import** (`commit` → result report → refresh stats, re-`browse()`, re-`loadDuplicates()`)
    or **Cancel** (`cancel`).
  - **Export:** two buttons — **Download vCard** and **Download backup (.json)** — each `fetch`es the
    export endpoint and saves via `Blob` + `<a download>`, with a one-line explainer of the difference
    (portable vs. lossless round-trip), linking the users-guide.
- **Empty-state wiring:** the `emptyState()` "import some contacts" affordance becomes a real button →
  `show("data")`.
- **Styles:** a dropzone (drag-over state), the preview/result table, and the export card — reusing the
  existing `.btn`, `.empty`, and the rich bulk-approve/`.card` styling already in `styles.css`.

### Docs (land *with* the implementation, per the keep-docs-current rule)

- `docs/users-guide.md`: capability table gains **"Import contacts in the workspace ✅"** and **"Export
  contacts in the workspace ✅"**; §9 (the workspace walkthrough) gains a short "Data — import & export"
  subsection; the **workspace re-import** row is marked **⏳ next** (honest deferral — `just reimport`
  is the path today).
- `docs/roadmap.md`: a v0.2 bullet for the workspace import/export surface (added in this change — see
  below), noting the future `discover_sources()` scanner will reuse this preview→commit pipeline.
- `plans/v0.1-implementation-plan.md` §3 + `CLAUDE.md`: the INV-2 wording clarification (above).

## Testing

All API tests drive the **pure `route()`** function (no socket), base64-encoding a real fixture in the
test body — fast and hermetic, the established daemon-test style.

- `tests/unit/test_daemon_import_api.py`:
  - stage → preview returns a dry-run `ImportReport` (correct count/source/format) and writes **nothing**.
  - commit **persists**: `shared.db` row count rises, the private 1:1 identity baseline is seeded, the
    staging dir is removed; the result report carries `stored`.
  - **empty-home path:** stage/preview/commit all succeed when `shared.db` does not exist yet (the
    empty-state case) — i.e. import is exempt from the `no shared.db` guard.
  - **source override** flows through to the report and the stored `source`.
  - an **unrecognized file** is reported `skipped` (not dropped, not fatal).
  - **path-traversal** `relpath` (`../evil`, absolute) is rejected; bad `token` is rejected.
  - a held lock → **409** (simulate by holding `home.lock_file`).
- `tests/unit/test_daemon_export_api.py`:
  - `?format=vcard` returns vCard text with a `Content-Disposition` attachment filename.
  - `?raw=1` returns a backup JSON that **round-trips**: feeding it back through the import endpoint
    reconstructs the same records (a satisfying end-to-end check that the two halves compose).
- Reuse `tests/fixtures/` (apple_icloud `.vcf`, a Takeout `.zip`, google_csv, facebook, a prm_backup).
- `tests/unit/test_config.py`: `imports_dir` exists and is created by `create()`.

## Build order (small, shippable slices)

- **D1** Backend import pipeline: `imports_dir`; the four `/api/import/*` routes (stage/preview/commit/
  cancel) with staging, sanitization, size cap, the empty-home exemption, and `LockError`→409; the
  boot-time stale-staging sweep. Tests: `test_daemon_import_api.py`.
- **D2** Backend export: `GET /api/export` (vcard + raw) with `Content-Disposition`. Tests:
  `test_daemon_export_api.py` (incl. the raw round-trip through D1).
- **D3** Frontend "Data" view: nav item + section, drag-drop/file/folder upload with progress, preview
  table + source override, confirm/cancel + result, export download buttons, empty-state button wiring.
- **D4** Docs: users-guide capability table + §9 subsection (+ re-import ⏳ next); the INV-2 wording
  clarification. (Roadmap entry already added in the planning change.)
- **D5** Manual QA (GUI not unit-tested, like the desktop app): in both `just serve` (browser) and
  `prm app` (window) — drag a Takeout `.zip` → preview → import → contacts appear, duplicates badge
  updates; import a bare `.vcf` with a source override; import into an **empty** home (the screenshot
  state); download both exports and confirm the vCard opens elsewhere and the backup re-imports;
  cancel mid-flow leaves no staging dir; concurrent CLI `prm import` → the GUI shows the clean 409.
  **Note the one app-specific risk:** pywebview/WKWebView download handling for the `Blob` save — verify
  in the window; if a platform refuses the Blob download, fall back to a server-written file + reported
  path (the CLI `--out` model) for the app case.

## Out of scope (follow-ups)

- **Workspace re-import** (the `prm reimport` added/updated/unchanged/stale diff with a non-destructive
  preview, INV-6). Deferred deliberately — it is a distinct, per-source diff flow; the page is built so
  it can host it later as a third mode. Until then `just reimport` is the path (marked ⏳ next in the
  users-guide).
- **Native OS file/folder dialog** in `prm app` (pywebview `create_file_dialog` → real paths, no
  upload) — a UX upgrade layered on the same preview→commit pipeline.
- **Source discovery** — the roadmap's `discover_sources()` scan of `~/Downloads` → pick-list, which
  would feed the same preview→commit flow (server-side paths instead of upload). Stays where it is on
  the roadmap.
- **A double-click bundle**, multipart streaming uploads, and per-file (vs. global) source override.
