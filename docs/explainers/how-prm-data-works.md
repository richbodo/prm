# How PRM's data works — mirroring, views, dedup, and AI writes

*An accessible explainer of the data model. For the normative details see
[`../prm-feature-spec.md`](../prm-feature-spec.md) (features + invariants),
[`../../plans/v0.1-implementation-plan.md`](../../plans/v0.1-implementation-plan.md) (the data model),
and [`../design-notes/dedupe-design.md`](../design-notes/dedupe-design.md). This doc is the friendly tour;
those are the blueprints.*

---

## The one idea to hold onto: two stores and a view

Everything in PRM comes down to **two databases and a view built on top of them**.

- **The mirror** — `shared.db`. A faithful copy of your contacts exactly as they arrived from Google,
  Apple, LinkedIn, Facebook, or a loose `.vcf`/`.csv`. PRM **reads** this; it almost never changes it.
  Think of it as a stack of photocopies of your original address books.
- **Your private layer** — `relationships.db`. Everything *you* (or an AI working for you) decide *about*
  those contacts: "these two records are the same person," "her real job title is X," "tag her as
  family," your notes, your custom fields. None of this is in the mirror; it sits beside it.
- **The view** — the **projection**. When you open a contact in the workspace, PRM doesn't show you a row
  from either database directly. It **combines** them on the fly into the single, clean, de-duplicated
  contact you actually see. The view is computed fresh every time; it is never stored.

The reason for the split is a promise: **the mirror is yours and stays pristine.** Re-importing, merging
duplicates, AI edits — none of them ever scribble on your original records. They all happen in the
private layer, and the view layers them back together. If you ever want to know "what did Google
actually send me?", the answer is always recoverable, because nobody overwrote it.

Two rules enforce this, and they're worth naming because the rest of the doc leans on them:

- **INV-1 — local-only.** Your contacts and your private layer never leave the device (except through
  one explicit, signposted AI exception — see the companion doc,
  [AI reads & writes](ai-reads-and-writes-walkthrough.md)).
- **INV-2 — one writer for the mirror.** Only the **importer** writes `shared.db`. Search, the
  workspace, dedup, AI — they all read it; none of them mutate it.

---

## 1. Mirroring: getting your contacts in

You point PRM at a file or folder you exported — a Google Takeout `.zip`, an Apple `.vcf`, a LinkedIn
`.zip`, a Facebook `.json`, a Google `.csv`. The **importer** (the one and only writer of the mirror)
does four things to each contact:

1. **Normalizes it** into one common shape. Every source has its own quirks; PRM translates them all into
   a single internal format (a JSON form of a vCard, called a *jCard*) so the rest of the app only ever
   deals with one shape.
2. **Gives it a stable identity.** Most exports don't carry a durable ID, so PRM walks a **fallback
   chain** until something sticks: a real `UID` if present → otherwise a normalized **email** →
   otherwise a profile **URL** (LinkedIn often has only this) → otherwise a **content hash** of the
   name + org + connected-date (Facebook friends, which are just a name and a date, land here). This
   identity is what lets a re-import recognize "oh, I've seen this person before."
3. **Records where every value came from.** Each field remembers its **source** and **when** it was seen
   — its *provenance*. That's why, in the workspace, every value shows a little tag like `apple` or
   `linkedin`.
4. **Stores it** as one row per record per source in `shared.db`, plus a full-text search index so you
   can find people fast.

A key subtlety: **one real person can produce several rows.** If Ada is in both your Google and Apple
exports, that's *two* mirror records — two photocopies — each with its own identity. PRM does **not**
silently fuse them at import time. Keeping them separate is deliberate: the mirror stays a faithful
copy, and *deciding* they're the same person is a separate, reversible step (that's dedup, below).

---

## 2. Re-importing: getting fresh contacts in later

Months later you re-download your Google export — now with three new people, a couple of edits, and one
contact you deleted. You run **re-import**, and the important word is **non-destructive**: PRM never
just overwrites the mirror. It first shows you a **preview** that sorts everything into four buckets:

- **new** — people not in your store yet,
- **updated** — same person, changed details,
- **unchanged** — identical to what you have,
- **stale** — in your store but no longer in the export (maybe deleted upstream).

Nothing is applied until you say go. And two guarantees ride along:

- **Stale records are kept, not deleted** — just flagged — so a contact that vanishes from an export
  doesn't vanish from your store.
- **Your decisions survive.** Because each record re-attaches by its *stable identity*, every merge you
  did and every note you wrote stays correctly attached to the same person after the re-import. You don't
  re-do your work. (This is invariant **INV-6**: re-imports are opt-in and non-destructive; and **INV-5**:
  identity survives re-import.)

---

## 3. The view: how a clean, de-duplicated contact is assembled

This is the heart of it. When you open a contact, PRM builds the **projection** by joining three things:

```
   the mirror records for this person   (shared.db: the raw values)
 + which records ARE this person         (relationships.db: the identity map)
 + your overrides and choices            (relationships.db: field resolutions, custom values, ...)
 ─────────────────────────────────────
 = the one canonical contact you see
```

When a person is made of several mirror records, PRM has to reconcile them field by field, and it uses
two simple rules depending on the kind of field:

- **Multi-valued fields *combine* (union).** Phones, emails, addresses — if Google has one phone and
  Apple has another, **you keep both**. Nothing is thrown away; the view just lists all the distinct
  values, each with its source tag.
- **Single-valued fields *reconcile* to one (pick a winner).** A person has one name, one primary org,
  one birthday. When sources disagree, PRM has to choose. The order of preference is:
  1. **Your explicit choice**, if you made one (a "field resolution" — see below). This always wins.
  2. Otherwise **survivorship**: the value from the more-trusted source wins (your curated Apple/Google
     address books outrank scraped LinkedIn/Facebook data), and ties break toward the most recently
     imported value.

So the view is opinionated but never lossy: it shows you one tidy answer, while the alternatives stay in
the mirror, one click away.

---

## 4. Deduplication: what actually happens to the database

You spot that "Ada Lovelace" (from Google) and "Ada L." (from Apple) are the same person and merge them.
Here's the thing that surprises people: **the merge does not touch the mirror at all.**

What a merge actually writes — all in your **private** layer (`relationships.db`), never `shared.db`:

- **It re-points the identity map.** Before: each record pointed at its own contact. After: both records
  point at *one* surviving contact id. That's the entire mechanical core of a merge — it's a change of
  *pointers*, not a change of *data*. The orphaned contact (now with no records pointing at it) is
  cleaned up.
- **It records any conflict choices you made.** If the two disagreed on, say, the org, and you picked
  one, that choice is saved as a **field resolution** — a row that says "for this person, the org is *X*,
  because the user chose it." This is exactly the row that wins step 3.1 above.
- **It remembers dismissals.** If you say "not a duplicate," PRM records that too, so the same pair never
  nags you again — even after a future re-import.

Because the raw records are untouched, **every merge is reversible**. In fact, before *any* change is
applied, PRM takes a **snapshot** of the private layer (your one-click Undo point) and writes an entry to
an **append-only audit log**. Undo simply restores the snapshot; the audit log is the permanent history
of what happened. Merge a hundred contacts, change your mind — your originals were never in danger.

The same machinery powers a plain **field edit**. When you open a contact and fix a misspelled name, PRM
doesn't edit the mirror; it writes a **field resolution** with `rule = "user"` — "the user says the name
is this." The view then layers your correction over the untouched import. An edit and a merge-conflict
choice are *the same kind of row*; editing is just resolving a field by hand.

---

## 5. AI writes: three flavors, three levels of trust

Now bring an AI into the loop (over the local MCP servers — the plumbing and the consent rules are the
subject of the [companion doc](ai-reads-and-writes-walkthrough.md)). The guiding principle is that the
root of your personal network is precious, so **AI writes are made *safe*, not forbidden** — and the
safety is structural (the AI is handed tools that *can't* exceed a limit), not a polite request. There
are three distinct ways data gets added, in increasing order of how much they trust the AI.

### 5a. Dedup proposals — the AI suggests, you dispose

The AI can scan for duplicates and **propose** merges, but it **cannot apply one**. There is simply no
"merge" tool on its surface — only a "propose" tool that stages a changeset for you. The proposal shows
up in your **Duplicates** tab tagged 🤖, and you review it like a pull request: approve, edit, or reject.
Nothing lands until you click. (This is the same propose-then-dispose pattern as everything else; the AI
author is recorded, but the human is always the one who commits.)

### 5b. Writes to *your custom fields* — governed by a per-field policy

When you design your own relationship fields (in **Schema**), each field carries an **AI-write policy**,
and the AI's write tool *behaves differently per field* — it literally can't do more than the policy
allows:

- **review-required** (the default for every new field) — the AI's write is **staged as a proposal**, not
  written. Same as a dedup proposal: you approve it in the workspace. The most protective tier, on by
  default; you opt *down* from it knowingly.
- **append-only** — the AI may **add** a value, but the write is **non-destructive**: it appends a new,
  provenance-stamped row and *never deletes or overwrites* what's already there. A human-authored value
  always remains and always wins on display. This is the tier the future "gather public data" loop runs
  on, because it's safe to run unattended: the worst an over-eager AI can do is pile up extra suggestions,
  never erase your work.
- **free-write** — a direct write, for low-stakes fields you've explicitly marked as such. Off by default.

Two structural backstops apply to the AI on the append-only/free-write tiers: a **per-value size cap**
(so a field can't be bloated) and a **per-session write quota** (so a runaway loop is bounded). And one
line that is never crossed: the AI can write field **values**, but it can **never create or change a
field *definition*** — your schema's *shape* is authored only by you, in the workspace (invariant
**INV-3**). It fills in the blanks you designed; it can't invent new blanks.

### 5c. Enriching *canonical contact fields* — observations you promote

The trickiest case is when an AI finds a value for a real contact field — a phone number for a name-only
Facebook friend, say. PRM does **not** let the AI write the contact's phone directly. Instead it files an
**observation**: an additive, attributed note that says "I think this person's phone is *X*, found via
*web*, confidence 0.8." An observation has a life cycle:

- **observed** — pending. It shows up on the contact under **"Gathered — pending your review"** as a
  *suggestion*. It is **not** part of the contact yet; it's only visible to you, locally.
- **accepted** — you clicked **Accept**. Now it folds into the view (see §6). For a single-valued field,
  accepting writes a *field resolution* (exactly like §4's edit), so your promotion beats survivorship.
  For a multi-valued field, it just joins the union.
- **superseded / rejected** — a previous accepted value that got replaced (PRM keeps *one* previous, for
  one-step revert), or one you dismissed for good.

The point: **an AI can never make a contact-identity value the default on its own.** There is no
"promote" tool anywhere on the AI surface — promotion is a human click in the workspace, full stop. The
AI gathers; you decide.

---

## 6. The whole picture: the view of a finally-deduped contact that's had every kind of write

Put it all together. Ada is now one canonical contact, merged from a Google record and an Apple record,
and she's been worked over by both you and an AI. When you open her, PRM assembles the view by layering —
each layer able to override the ones beneath it for single-valued fields, or add to them for
multi-valued ones:

```
  Layer 4  pending observations            → shown separately as "Gathered" suggestions (NOT folded in)
  Layer 3  accepted observations           → folded into canonical fields (a promoted phone joins the union;
                                              a promoted org becomes a field resolution)
  Layer 2  your private overlay            → field resolutions (edits, merge choices, promotions) win
                                              single-valued fields; custom field values (incl. AI
                                              append-only writes) appear as your own fields
  Layer 1  the merged mirror records       → the union of all raw values from every record that maps to Ada
  ─────────────────────────────────────────
  = the canonical contact you see
```

Read concretely, for Ada that means:

- Her **emails and phones** are the **union** of everything Google and Apple had, *plus* any phone you
  promoted from an AI observation — each tagged with where it came from (`apple`, `google`, or
  `gathered`).
- Her **name and primary org** are **single answers**: your explicit edit if you made one, otherwise an
  accepted observation you promoted, otherwise the most-trusted source's value. Whatever loses is still
  in the mirror, not deleted.
- Her **tags, notes, and custom fields** are your private overlay, shown as their own section. An AI
  append-only write appears here too, stamped with the AI as its author, sitting *alongside* (never on
  top of) anything you wrote.
- Anything an AI merely **gathered but you haven't accepted** is quarantined in the "Gathered" section —
  visible, attributed, but not part of Ada until you promote it.

And underneath all of it, **the two Google/Apple records in `shared.db` are byte-for-byte what they were
the day you imported them.** Every merge, edit, promotion, and AI write lives in the private layer; the
view stitches them over the mirror; and a snapshot + audit log means any of it can be undone. That's the
whole machine: *a pristine mirror, a private layer of your decisions, and a view that composes them into
one clean contact — losslessly, reversibly, and on the fly.*

---

## Quick glossary

| Term | Plain meaning |
| --- | --- |
| **mirror** / `shared.db` | the read-only copy of your contacts as imported; one writer (the importer) |
| **private layer** / `relationships.db` | everything you/the AI decide *about* contacts: identities, edits, tags, notes, custom values, observations |
| **projection** / **the view** | the de-duplicated contact you see, composed on the fly from both stores; never stored |
| **stable identity** | a durable id per record (UID → email → URL → content-hash) so re-imports re-attach correctly |
| **provenance** | which source a value came from, and when |
| **identity map** | the pointers saying which mirror records are the same person; what a *merge* rewrites |
| **field resolution** | "for this person, this single-valued field is *X*" — your edit, a merge choice, or a promoted observation |
| **field value** | a value in one of *your* custom fields (tags, notes, "how we met", …); where AI append-only writes land |
| **observation** | an AI-gathered suggestion for a *canonical* contact field; pending until you promote it |
| **AI-write policy** | per custom field: `review-required` (stage) / `append-only` (add) / `free-write` (set) |
| **snapshot + audit log** | the pre-change backup (one-click Undo) and the permanent record of every applied change |
