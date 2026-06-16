# Contact images — a content-addressed media store, photos as field values

*Design note · contact images / avatars · drafted 2026-06-16. Status: accepted. v0.2 **R7** (the
per-contact upload slice) shipped; this note is also the **decision of record for the image *storage
model*** (Option B, below) and the Takeout / guided-import follow-ups. Decision record:
[`../../brainstorms/2026-06-15-prm-v0.2-relationships-schema.md`](../../brainstorms/2026-06-15-prm-v0.2-relationships-schema.md);
build mechanics: [`../../plans/v0.2-implementation-plan.md`](../../plans/v0.2-implementation-plan.md) §4.
Sibling of [`contact-edit-override-model.md`](contact-edit-override-model.md) — the avatar is one more
private overlay layered over the immutable mirror.*

## Storage model — the decision of record (Option B), 2026-06-16

**Photo bytes live as files in the content-addressed media store; the databases never carry photo bytes
inline.** A contact's photo is a `field_values` row whose `value` is a content hash (§"just a field
value" below); the bytes sit in `media/<hh>/<sha256>.<ext>`. `shared.db` and `relationships.db` stay
**lean** — they hold references, never image data.

**Why (grounded in the real numbers).** A real 1,101-photo Google Takeout export measured **mean 6.1 KB
/ median 3.2 KB** per photo — avatars are thumbnails. So a **5,000-contact store with a photo each** is
roughly:

| | bytes | DB size | hot read path |
| --- | --- | --- | --- |
| **inline base64 in `shared.db`** | +33% (base64) | **~50 MB** (one file) | every list/dedup pass loads ~40 MB of `raw_jcard` |
| **media files + lean DB (chosen)** | raw | **~10 MB DB** + ~30 MB of files | DB carries no photo weight |

The decisive factors were **not** total size (both are small) but: (1) the **DB stays ~10 MB regardless
of photo size**, so the part that's awkward to move between devices is always small and even emailable;
(2) inlining puts photo weight on the projection's **hot read path** (`_records_by_contact` loads every
`raw_jcard` for the list and for dedup), which files avoid; and (3) the owner gets **browsable flat
files** they can inspect/copy/back up independently. This balances the three competing needs raised:
*one thing to protect* (the directory tree is no harder to protect than one file, and the DB stays
tiny), *flat browsable images*, and *future multi-device sync* (a different backend someday; not
foreclosed). See the size analysis in the session that produced this note.

**What this commits us to (planned unless marked shipped):**

- **Uploads are downscaled *client-side* before upload** — the workspace resizes via a `<canvas>` (cap
  ~512 px, re-encode to JPEG) so even a camera photo lands at tens of KB. This keeps avatars in the
  "thumbnail" row **with no Python image dependency** (preserving the tiny-deps invariant — INV/CLAUDE.md).
- **`prm export --raw` includes `media/`**, so the raw backup is genuinely lossless for photos too. (This
  supersedes the earlier "uploaded avatars don't travel in `--raw`" position below.)
- A workspace **"Download all (.zip)"** (and/or a CLI) bundles the home for moving between devices.
- **Imported photos land in the media store too.** External Google Takeout photos are **`.jpg` files
  named verbatim after the card's `FN`, co-located in the same label folder as the `.vcf`** — i.e. the
  export *already encodes* the photo→contact link. The ingest path matches each sidecar to its card by
  `FN`-within-folder (strip a `(N)` collision suffix), `media.put`s the bytes, and writes a `photo`
  field value (`source = directory-import`). Hosted `PHOTO:https://…` URLs are skipped (INV-1, no fetch).
- **Shipped in R7:** per-contact **upload / remove** in Edit mode already uses exactly this media store +
  `photo` field value, and `prm doctor` reports media health. R7 is a valid subset of Option B.

*Finer point to settle when the Takeout ingest path is built:* embedded-base64 source photos (`PHOTO;
ENCODING=b`, rare — the sampled export had ~0, mostly external files + hosted URLs) are today decoded
on demand from `shared.db` (decision 3 below). Under pure Option B they would migrate into the media
store at ingest to keep `shared.db` lean; left as a small open choice since it barely occurs in practice.

## The decisions

1. **Bytes live on disk, content-addressed; the database only references them.** A photo is stored at
   `<home>/media/<hh>/<sha256>.<ext>` (`hh` = first two hex chars of the hash — a git-style 256-way
   shard). The filename **is** the content hash, which buys us distinctive names, automatic **dedup**
   (the same image across two sources is one file), a cheap **integrity check** (re-hash), **immutability**
   (files are only ever added or removed, never edited), and trivially **viewable** debugging.
2. **A contact's avatar is a `field_values` row, not a new table.** The seeded `photo` built-in (kind
   `image`, `multi:false`) holds the avatar as an ordinary relationship value: `value` = the content
   hash, `value_json` = `{"mime","byte_size"}`, `source` = `manual`. This is the single-avatar model
   (one photo per contact; a new upload **replaces** the old, exactly as any single-valued field does).
3. **An *embedded*-base64 source photo is shown on demand (R7).** Where an export inlines `PHOTO;
   ENCODING=b` into the card, those bytes sit in `shared.db`'s `raw_jcard` (INV-2, lossless) and are
   decoded **on demand** for display — R7's behavior. (*External* imported photos — the common Takeout
   case — instead land in the media store at ingest, per the Option B storage model above.) A
   user-uploaded avatar is an **override** that wins over either, the same override model edit mode uses.
4. **No remote fetch, ever (INV-1).** Some exports carry `PHOTO;VALUE=uri:https://…` — a URL, not bytes.
   PRM does **not** fetch it; a URL-only photo renders as the initials placeholder. Only *inline* image
   data (base64 / `data:` URI) is decoded. Reaching out to a third-party URL would put a network call on
   the contact-display path and leak which contacts the user is viewing — exactly what local-only forbids.

## Why a photo is "just a field value" (and there is no `media_refs` table)

The v0.2 plan (§3) sketched a separate `media_refs(content_hash, contact_id, field_id, mime, byte_size,
source, …)` table. We **folded it into `field_values`** instead, because `field_values` already carries
everything that table would have:

| `media_refs` column | where it lives now |
| --- | --- |
| `content_hash` | `field_values.value` |
| `contact_id`, `field_id` | `field_values.contact_id`, `.field_id` |
| `mime`, `byte_size` | `field_values.value_json` (its documented "structured payload when a scalar won't do") |
| `source` | `field_values.source` |

The payoff of the fold is that the avatar needs **zero** new code in the load-bearing paths — the
projection, edit mode, the `set_field_value` apply path, **Undo**, and the **MCP seal** all already
operate on `field_values`, so the avatar inherits all of them unchanged. The **GC reference set** is just
"the content hashes held by `image`-kind field values"; `prm doctor` compares the files on disk against
that set. (Imported photos need no row at all — they are derivable from `shared.db`.) This deviates from
the plan's sketch, as the plan explicitly permits where an invariant-preserving simplification exists; it
keeps the schema at v2 with **no migration**.

*If multi-image fields or groups later need richer per-blob bookkeeping, a `media_refs` table can be added
then — it is additive and nothing here forecloses it.*

## The constraints that shaped it

- **INV-2 / AC-1 (immutable mirror).** Embedded source photos stay inline in `shared.db` and are decoded
  read-only for display; external imported photos are sidecar files folded into the media store at ingest.
  Either way the mirror's stored cards are not mutated for display, and a re-import re-pulls cleanly.
- **INV-1 (local-only).** Bytes are served only by the `127.0.0.1` daemon; the media store is
  `private-sealed`, so the MCP surface never emits photo bytes (the `photo` built-in is sealed like every
  field). No remote fetch (decision 4).
- **Reversible + audited for free.** An uploaded avatar goes through the existing apply path — file-lock →
  snapshot → one transaction → audit — so a single **Undo** reverses it. The hash-named blob is immutable,
  so it needs no per-snapshot copy (the snapshot ring stays small); an orphaned blob after an Undo or a
  re-upload is reclaimed by `gc`.
- **Snapshot ring stays cheap.** This is *why* bytes are files, not SQLite BLOBs: the ring copies the
  whole `relationships.db` before every apply (×20). Inlining image BLOBs would bloat and slow every
  snapshot; hash-named files never move.

## Backup (`prm export --raw`)

Under Option B, `--raw` **includes `media/`** so it is a genuinely lossless backup of photos —
uploaded avatars *and* imported ones travel with it (planned; not yet built). Two notes:

- **Embedded-base64 source photos** already round-trip via `shared.db` (decision 3); a test pins this
  ("no regression to the lossless `--raw` backup", v0.2 plan §11).
- **Until `--raw` bundles `media/` ships**, an uploaded avatar (like tags/notes) lives only in the
  private store + `media/` and is not in the source-only `--raw` dump; `prm doctor` reports media
  orphans/missing so the state is visible. (Superseded by the Option B `--raw`-includes-`media/` work.)

## Roadmap — three tiers (one shipped, two to build)

1. **Tier 0 — per-contact upload (shipped, R7).** `photo` is a seeded first-class field (no schema setup,
   like tags/notes); every contact shows an avatar/placeholder; Edit mode uploads/replaces/removes via
   the media store. Small refinement still wanted: make the avatar/placeholder **itself** the click
   target (today it is an explicit "Upload…" button), and consider allowing it from the read view.
2. **Tier 1 — Google Takeout photos, auto-matched at ingest.** Since the export co-locates each `.jpg`
   with its card and names it by `FN`, `prm import <takeout.zip>` attaches photos automatically — no user
   matching. Exact `FN`-within-folder match only (strip `(N)`); anything else falls to Tier 2 rather than
   being auto-guessed (the "never silently misfile" stance). Plus the `--raw`-includes-`media/` and
   client-side downscale work above.
3. **Tier 2 — loose folders / corrections, a guided visual matcher.** For photos *not* from a structured
   export: a workspace tour that shows each photo one at a time, **auto-suggests** a contact (filename →
   `FN`/email, reusing the dedup normalizers in `core/candidates.py`), and lets the user confirm / search
   / skip. The filename is only a *hint for the suggestion* — never something the user must get right
   (no `photo-manifest` CSV). This replaces the earlier filename-matching CLI sketch.

## Out of scope here (deferred)

- **Multiple images per contact / galleries** — the `image` kind reserves `config.multi` for it; the
  built-in `photo` is the single avatar for now.
