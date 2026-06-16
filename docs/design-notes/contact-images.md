# Contact images — a content-addressed media store, photos as field values

*Design note · contact images / avatars · drafted 2026-06-16. Status: accepted — scopes v0.2 **R7**
(contact images). Decision record:
[`../../brainstorms/2026-06-15-prm-v0.2-relationships-schema.md`](../../brainstorms/2026-06-15-prm-v0.2-relationships-schema.md);
build mechanics: [`../../plans/v0.2-implementation-plan.md`](../../plans/v0.2-implementation-plan.md) §4.
Sibling of [`contact-edit-override-model.md`](contact-edit-override-model.md) — the avatar is one more
private overlay layered over the immutable mirror.*

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
3. **Imported photos are shown but never copied into the private store.** The base64 `PHOTO` an export
   already carries sits inline in `shared.db`'s `raw_jcard` (INV-2, lossless). It is decoded **on demand**
   for display only — it is never written to `field_values` or the media store. A user-uploaded avatar is
   an **override** that wins over the imported photo, the same override model edit mode already uses.
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

- **INV-2 / AC-1 (immutable mirror).** Imported photos stay inline in `shared.db`; we decode a copy for
  display and never mutate the source. A re-import re-pulls them cleanly.
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

## Backup (`prm export --raw`) — what round-trips, and what does not

- **Imported photos round-trip losslessly.** They live in `shared.db`, which is exactly what `--raw`
  dumps; the base64 stays untouched on the export → re-import path. A test pins this (§11 open item:
  "verify no regression to the lossless `--raw` backup").
- **Uploaded avatars do *not* travel in `--raw`.** Like tags, notes, and merge decisions, an uploaded
  avatar is **private-store** data (`relationships.db` + `media/`), and `--raw` is by definition a backup
  of the *raw mirror* (`shared.db`) only — see the users-guide's "lossless backup" note. A full private
  store + media backup is a separate, later concern, not this milestone. `prm doctor` reports media
  orphans/missing so the state is at least visible.

## Out of scope here (deferred)

- **Bulk `prm import-images <dir>`** — matching a folder of image files to contacts by
  filename/email/slug with a manual-confirm fallback. The heuristic deserves its own pass; tracked as a
  follow-up to this PR.
- **Multiple images per contact / galleries** — the `image` kind reserves `config.multi` for it; the
  built-in `photo` is the single avatar for now.
- **A full private-store + media backup** — see the `--raw` note above.
