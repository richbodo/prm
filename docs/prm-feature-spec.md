# Personal Relationship Manager (PRM) — Feature Specification

*A Personal Network Toolkit (PNT) reference design. Working title; status: draft for review.*

## Purpose

A local-only Personal Relationship Manager that mirrors a person's contacts from
everywhere into one place, lets them keep private relationship data on top of those
contacts, and lets an AI read and help maintain that data on their behalf. It is the
second PNT reference design — the first to realize the draft **Personal Relationship
Manager** use case and exercise the draft **AC-PRM-B** multi-source dedup contract.

## How to read this spec

**Features (`F#`)** and **Invariants (`INV#`)** are normative: they define a correct
build. The **Recommended approach** section is guidance — a builder or agent may choose
differently *so long as every invariant still holds*. **Open questions** are deliberately
unresolved and left to the builder's judgment.

## Flavor (recommended axis picks)

| Axis              | Pick                                                        |
| ----------------- | ----------------------------------------------------------- |
| distribution      | `never-distributed-single-user`                             |
| storage           | `native-sqlite-via-filesystem`                              |
| ingestion         | `multi-source-merge-with-dedup` (triggers AC-PRM-B)         |
| workspace shell   | `vanilla-js-spa`, served by a small local daemon            |
| comms             | bidirectional; automatable transports (email/Signal) + manual fallback |
| mcp-exposure      | `full` (shared + private + ingestion + comms)               |

---

## Features

### F1 — Multi-source contact ingestion
Import contacts from many sources into a single, read-only **Shared DB**.

- **Baseline (universal):** file import — vCard, vendor CSV, platform export bundles
  (e.g. Google Takeout). Zero-auth; works for every source, including ones with no sync API.
- **Optional upgrade:** live mirror of well-behaved CardDAV sources via an external syncing
  tool running in one-way *mirror* mode (the app reads the mirrored files; it does not
  implement sync itself).
- All sources normalize to **one canonical contact representation** before loading.
- **Merge + dedup:** records from different sources are merged; conflicts are surfaced to
  the user through a dedup wizard rather than resolved silently.
- **Stable identity:** every contact has an id that survives both re-import and cross-source
  merge. Private and custom data reference contacts by this id.
- **Per-field provenance:** each field value records which source it came from and when.
- **Re-imports are opt-in and non-destructive:** before committing, the user previews what
  would change or be orphaned.

### F2 — Local workspace
A local web app for working with the contact graph.

- Browse, search, group, tag, and add freeform notes to contacts.
- All private overlay data (groups, tags, notes) is user-authored and never leaves the device.
- The workspace is the **only** place the custom schema (F3) can be created or changed.

### F3 — Custom relationship schema
The user defines their own relationship data shape, beyond tags and notes.

- Users add arbitrary **typed fields** — e.g. text, number, date, single/multi-select,
  URL, list — to describe their relationships ("current projects", "challenges", "how we met").
- The **schema is defined and modified only in the workspace.** The user stays in touch
  with their own schema by being the only author of it.
- Field **values** are read/write for both the user and the AI; field **definitions** are
  not writable by the AI. (This separation is the central new capability versus current PNT.)

### F4 — MCP servers (AI access)
Expose the app's capabilities to AI clients (Claude Desktop, Cursor, a local-Ollama agent).

- **Shared data ops:** read-only access to mirrored contacts.
- **Private data ops:** read access to overlay + custom values, **and write access to custom
  field _values_** — a read/write extension of PNT's currently read-only private surface.
  No tool on this surface can create or alter field definitions.
- **Ingestion ops:** drive source imports, dedup interaction, and reply ingestion (F5).
- **Comms ops:** stage/send outreach and read replies.
- Cloud-client consent rules apply; a local AI is recommended for anything touching private data.

### F5 — AI-mediated collection loop (querier side)
Let the user delegate *gathering* relationship data, not just recording it.

- The user can ask the AI to fill in missing data across their contacts (e.g. "find out what
  my friends are working on") by reaching out to them.
- **Reachability resolution:** per contact, the AI picks a preferred contact method that has
  an automatable transport adapter. If none exists, it surfaces the contact for manual
  handling ("no automatable method for X — you have a phone number, you'll need to call").
- **Outreach is composed from a template and sent only after explicit user approval, which may
  be granted in batch.** The AI never silently auto-sends.
- **Replies feed back through the F1 pipe:** the AI reads incoming responses, extracts
  structured values from the prose, and writes them into the matching custom fields — with
  provenance marked *self-reported via `<transport>` on `<date>`*.
- The "share what people in my network are working on" capability lives at this layer, realized
  through prompts over F3/F4 — no additional architecture required.

---

## Invariants (must hold)

- **INV-1** Private, overlay, and custom data are local-only and never leave the device.
- **INV-2** The Shared (contacts) DB is read-only at runtime; only the ingestion path writes it.
- **INV-3** The custom schema is mutable only in the workspace. No AI/MCP path can create or
  alter field definitions — enforced structurally, not by prompt instruction.
- **INV-4** The AI may read and write custom field *values*, constrained by the existing schema.
- **INV-5** A stable contact id survives re-import and cross-source merge; all private/custom
  data references contacts by it.
- **INV-6** Re-imports are opt-in and non-destructive, with an orphan/conflict preview before commit.
- **INV-7** Every contact field value and every custom value carries per-source provenance.
- **INV-8** Outreach is staged and requires explicit user approval (batchable); the AI never auto-sends.
- **INV-9** Private data survives app updates and cache clears; it is wiped only by an explicit
  user-initiated reset.

---

## Recommended approach (latitude for the builder)

These satisfy the invariants cleanly; deviate if you have a better way that still does.

- **Runtime & storage.** A native SQLite file (or files) served by a small local daemon
  (Python stdlib is sufficient), with a vanilla-JS workspace. Chosen so the syncing tool and
  the MCP servers can all touch the same file on disk. A pure in-browser build is possible but
  complicates the F1 live-mirror and F4 paths.
- **Canonical format.** vCard 4.0 as the interchange contract; a JSON form (jCard) as the
  internal representation. Map source CSVs into it. Use the contact UID as the stable id (INV-5),
  with an email-normalized or hashed fallback where UID is missing.
- **Parsing.** Do it server-side in the daemon. A maintained vCard library (e.g. `vobject` /
  `vobjectx`) is the pragmatic choice — its value is the lexer and real-world quirk handling,
  not field semantics, which you map yourself. A small vendored parser is acceptable if the
  user's sources are all modern exports.
- **Live mirror.** Use a CardDAV syncing tool in mirror mode for well-behaved sources
  (iCloud, Fastmail, Nextcloud). Prefer file export for Google, whose CardDAV is unreliable.
  Do not build a sync engine or per-vendor API connectors.
- **Two databases.** Keep mirrored contacts and private/custom data in **separate** stores so a
  re-sync can never endanger hand-authored data; join by the stable id.
- **Custom schema (INV-3/INV-4).** Two tables: a field-definition table (workspace-writable
  only) and a value table keyed by `(contact_id, field_id)` with a foreign key to the
  definitions, so a value for an undefined field cannot be written. Pair this with an MCP
  surface that exposes no schema-mutation tool. The foreign key and the absent tool are the two
  independent locks behind the schema/value separation.

---

## Out of scope / deferred

- **The responder application** — an AI that monitors inbound channels, validates incoming
  PNA queries, and drafts replies — is a *separate future reference design*. This spec builds
  only the querier half of the loop (F5). See the forward note below.
- Federated or peer-to-peer reads between PNAs on different devices.
- Per-vendor live API connectors (Google People API, MS Graph, etc.).

## Open questions

- **Trust / query provenance.** How does a recipient (and eventually the responder app)
  distinguish a genuine PNA query from a phishing probe? Options: sender allowlist, a
  recognizable or signed query envelope, or more. This is a real social-engineering surface and
  the central unsolved problem for the responder side.
- **Transport adapters** beyond email (e.g. Signal via `signal-cli`) and how a transport
  declares itself "automatable."
- **Default contact method** storage: vCard `PREF` parameter vs. workspace-side metadata.

## Forward note (for the responder app)

Carry these into the responder design when it is built:

1. Reply ingestion is just another source in the F1 pipe; only the normalizer changes
   (LLM extraction from prose), and provenance should record self-reporting.
2. The loop **federates over existing comms rails** (email/Signal) — no new protocol or
   shared infrastructure required.
3. The responder must be **skeptical by construction** and **draft-never-send**; the reviewing
   human is the authorization step, the AI's validation is only triage.
4. **Local AI is the stronger requirement** on the responder side (it reads your inbox and
   drafts replies about your own life).
5. The deliverables are two prompts — a **querier** prompt and a **responder** prompt.

---

# Addendum — Refined direction & v1 scoping (2026-06-02)

*This addendum captures the distilled direction from a working discussion. It refines the
feature spec above toward a smaller, staged first release. Where it conflicts with the original
draft, the addendum reflects the more current intent.*

## Use cases — narrative walkthrough

A person's contacts are scattered: some in Google, some in iCloud, some in a CSV a colleague
sent, some in an old phone export. The PRM's first job is to **pull all of that into one place
the user owns**. The user points the app at whatever they have — a vCard file, a vendor CSV, a
Google Takeout bundle — and the app reads it without needing any account credentials or sync
API. For the well-behaved sources that *do* offer CardDAV (iCloud, Fastmail, Nextcloud), they
can *later* (not in v1) set up an external tool that mirrors those contacts to local files,
which the app simply reads; the PRM never tries to be a sync engine itself. However the
contacts arrive, they're normalized into one canonical shape and loaded into a read-only
contacts store. Because the same person often appears in several sources, the app merges
duplicates — but when two sources disagree (different phone numbers, conflicting names), it
doesn't guess silently; it surfaces the conflict for resolution. Every contact gets a stable
identity that won't change when the user re-imports later or when records merge, and every
field remembers where it came from and when. Re-importing is always the user's choice, and
before anything is committed they see a preview of exactly what would change or be left orphaned.

With contacts in hand, the user moves into the **workspace** — a local web app that is their
home base for the relationship graph. In v1 its primary job is to **search and view** contact
data. In later versions they browse and search, organize people into groups, apply tags, and jot
freeform notes. Everything they author in this layer is private and stays on their device. The
workspace is also where the user does something the underlying toolkit has never allowed before:
**they design their own relationship schema.** Beyond tags and notes, they can add typed fields
that match how *they* actually think about their relationships — a date for "how we met," a
multi-select for "shared interests," a text field for "what they're working on right now."
Crucially, the user is the sole author of this schema's *shape*; the structure of their
relationship data reflects their own mind, not an AI's.

That distinction — between the *shape* of the data and the *values* in it — is what lets the
user safely bring AI into the loop. Through **MCP servers**, an AI client (Claude Desktop, a
local Ollama agent, Cursor) can read the mirrored contacts, read the private overlay, and — this
is the new capability — *write values* into the custom fields the user defined. What the AI
cannot do, by construction, is invent or alter the field definitions themselves: the schema
stays the user's. Anything touching private data favors a local model, with explicit consent
rules for cloud clients.

This sets up the most ambitious use case: **delegating the gathering of relationship data, not
just its recording.** That can take several forms. The easiest is **public-data gathering**: an
LLM with MCP access traverses the contact database in a long-running process, looking for
publicly-announced activity from the user's contacts — GitHub repos, LinkedIn, other public
signals — and can run as a daily check over a chosen group, no outreach required. A second form
is **AI-assisted organization**: the user simply talks to the LLM about organizing their
contacts, and by gathering public info or asking questions, the LLM helps form groups and writes
notes into the Private DB. The hardest form is **outreach** — the AI figures out who it can reach
through an automatable channel, drafts from a template, and sends only after explicit user
approval (batchable); replies re-enter through the same ingestion pipe, with values extracted
from prose and marked self-reported via that channel on that date.

What this spec deliberately *doesn't* build is the other side of that conversation — the
**responder app** that would receive such a query, judge whether it's genuine, and draft a
reply. That's a separate future design. So is peer-to-peer reading between devices, and live
per-vendor API connectors. This PRM is the querier: the place a person consolidates their
network, shapes their own private understanding of it, and lets an AI help keep that
understanding current.

## Thin sources and the friend-reconciliation checklist (use case)

Not every source carries real contact data. Facebook's export gives only a friend's **name and the
date you connected**; LinkedIn often gives only a name and a **profile URL** — no email, phone, or
stable id. Treated naively, this is junk. Treated as a **reconciliation checklist**, it is the
opposite: a complete, origin-tagged roster of *who is in the network and where each person came
from* — the raw boundary of the user's social graph.

The PRM keeps these thin records as first-class contacts (stable id by content hash; provenance
records the source and connected-date). From there the user, working with an AI, walks the checklist
down: research each person's public data, ask the user clarifying questions, and **triage toward the
people the user actually knows**. The user feeds context back as **private tags, notes, and
relationships** — "met them through B", "family of contact C", "lapsed, deprioritize". That context
is exactly the user-authored **private overlay** (v0.2) and the kind of thing the **delegated
public-data gathering** loop (v0.3) helps populate.

All of it is **private, local-only relationship data (INV-1)** — it never leaves the device. The
checklist is how a pile of dataless names becomes a deliberately curated network root, which is the
whole point of reclaiming the root of one's personal network. It also sets the bar for ingestion:
the pipeline must accept a source that offers *only a name*, give it a durable identity, and never
silently merge it with anyone (name-only matches stay propose-only — see AC-PRM-B).

## Roadmap

**v0.1 — Ingest, view, and AI-assisted dedup**
- A **command-line ingester** that imports contacts from files (vCard, vendor CSV, export
  bundles like Google Takeout) into the read-only Shared DB, normalizing to one canonical
  representation with stable contact IDs and per-field provenance. *No CardDAV mirroring* — that
  moves to a later version.
- A **local web workspace** whose primary job in v1 is to **search and view** contact data.
- An **MCP server** exposing the contact data to an AI client, plus **dedup instructions** (a
  prompt) and **possibly a small set of MCP tools** the AI uses to carry out deduplication. In
  v1, dedup is AI-driven through the MCP surface, not a hand-built wizard UI.
- That trio — ingester + view/search workspace + MCP-server-with-dedup-prompt-and-tools — *is* v0.1.

**v0.2 — The private overlay and custom schema**
- Build the **Private DB**: organizing people into groups, applying tags, writing notes.
- Use that overlay as the **foundation for the user-defined relationship schema** (typed custom
  fields — "how we met," "current projects," etc.), authored only by the user.

**v0.3+ — Delegated gathering of relationship data** (several forms, easiest first)
- **Public-data gathering (likely first):** an LLM with MCP access traverses the contact DB in a
  **long-running process**, looking for publicly-announced updates/activity (GitHub, LinkedIn,
  other public signals); runs as a **daily check** over a chosen group. No outreach required.
- **AI-assisted organization:** the user *talks to the LLM about organizing their contacts*; the
  LLM gathers public info or asks questions, helps form groups, and **writes notes into the
  Private DB** — making organization a conversation.
- **Outreach (harder, later):** automatable-transport reach-out, draft-and-approve, ingest
  replies back through the pipe with self-reported provenance.

## v0.1 MVP — feature summary

The first release is a **two-part local tool plus an AI seam**. A **command-line ingester** is
the only thing that writes the Shared DB: it reads whatever contact files the user has on hand —
vCards, vendor CSVs, platform export bundles — normalizes them into one canonical contact shape,
assigns each contact a stable ID that will survive future re-imports, and records where every
field value came from. There's no live sync and no CardDAV in this version; ingestion is a
deliberate, file-based, user-run command. Alongside it, a **local web workspace** gives the user
a place to **search and browse** that consolidated contact data — read-and-view first, with the
richer organizing features (groups, tags, notes, custom schema) deliberately held back for v0.2.

The novel piece of v0.1 is that **deduplication is delegated to an AI rather than coded as a
fixed wizard.** An **MCP server** exposes the ingested contacts to an AI client, and the release
ships a **dedup prompt (a set of instructions) plus a small set of MCP tools** the AI uses to
find probable duplicates, reason about conflicts, and propose or apply merges — all over the same
on-disk database the ingester wrote and the workspace reads. This keeps v0.1 small and honest:
one writer (the ingester), one reader-and-viewer (the workspace), and one AI-driven capability
(dedup) riding on the MCP surface — which is also the seam every later version (private overlay,
custom schema, public-data gathering, outreach) will build on.

## Safe AI writes to private data (resolved design)

Treat the root of a personal network as the most important data the user has. The goal is **not**
to forbid AI writes but to make them safe: **the controls exist, the user understands them, and the
defaults are the most protective.** Three mechanisms compose (defense-in-depth, not either/or).

**1. The AI proposes; the workspace disposes (review-required — the default).** Following the
user's hands and eyes: the user is chatting with their AI (Claude Desktop / a local Ollama agent)
with the MCP server connected. They ask it to deduplicate. The AI reads candidate duplicates, asks
clarifying questions, and assembles a **changeset** — but it cannot write the canonical store. It
calls a *propose* tool that stages the changeset for review, and tells the user: "I've submitted a
proposal — switch back to the PRM app and approve it." In the workspace the user reviews the
changeset **like merging a pull request** — approve all / approve some / edit / reject — and only an
explicit workspace action applies it, atomically, with an orphan/impact preview first. This
generalizes PNT's AC-MCP-B (MCP comms *stage*; workspace launches) from outreach to data writes.

**2. Append-only, provenance-stamped writes (auto-applied but non-destructive).** Where review of
every write would defeat the purpose — notably the autonomous long-running gatherer (v0.3) with no
human in the loop *in the moment* — the AI may write, but only by *appending* provenance-stamped
values that never overwrite or delete prior data. The human-authored value always remains and wins
on display; everything is reversible; a per-run **quota** bounds a runaway loop; an **audit log**
records every AI-originated change. The user reviews accumulated observations later, at leisure.

**3. Free-write (direct, immediate).** For data the user has explicitly marked low-stakes, where
mistakes are tolerable. Off by default; opted into per data class with full understanding.

**Policy is an attribute of the data, enforced structurally.** Each data class — and, once the
custom schema exists (v0.2), each user-defined field — carries an **AI-write policy**
(`review-required` | `append-only` | `free-write`). The default for any newly defined class or field
is the most protective tier (`review-required`); the user dials it *down* knowingly. Enforcement is
structural, not prompt-based: the MCP tool surface exposes no tool that exceeds a class's tier (e.g.
no direct-commit tool for review-required data) — the same lock as "AI writes values, never
definitions" (INV-3/INV-4).

**Substrate: append-only log + snapshot ring, over the native store.** Underneath all three tiers:
a **snapshot/backup ring** of the private store taken before any apply (realizes AC-9), an
**append-only audit log** of applied changes (a JSONL file is its own append-only log), and
**changesets stored as text/JSON** so review renders a real diff. These primitives give
reversibility, review, and full history directly — **no version-control system required**.

*Git was considered and rejected as a backend.* The canonical store is relational SQLite, so
committing it to git means versioning opaque binary blobs (useless diffs, bloated history). Git's
distinctive power — branch/merge of *divergent text* — solves a problem the PRM doesn't have:
proposals are reviewed and applied one at a time against current state, with conflict detection at
apply time (plain app logic, not a three-way merge). The **pull-request *metaphor* for review
stands** (mechanism 1); the git *implementation* does not follow from it. The one future scenario
that would revive git is a meaningful slice of *canonical* data living as **flat text files** that
an AI edits divergently on a "feature branch" and merges back — no such artifact is in view today.
If one appears, reconsider then.

## Invariants (additions)

- **INV-10** AI/MCP writes to private data are governed by a declared per-data-class **write
  policy** (`review-required` | `append-only` | `free-write`). The default for any data class or
  user-defined field is the most protective tier.
- **INV-11** No AI/MCP path may perform a *review-required* write directly. It may only stage a
  changeset that the user applies by explicit action in the workspace. (Generalizes INV-8 /
  AC-MCP-B from outreach to data writes.)
- **INV-12** Every AI-originated write is reversible: a snapshot precedes application, and an
  append-only audit log records each change (tool, session, timestamp, originating proposal).
- **INV-13** Autonomous (no-human-in-the-loop) AI writes are restricted to the `append-only` tier,
  bounded by a per-run quota, and logged for later review.

## Architectural finding (feeds back to PNT)

PNT's MCP private surface is read-only today (AC-MCP-A); this PRM is the design that extends it to
*writes*. The tiered safe-write model above is a genuine gap in the toolkit and a candidate to
contribute upstream as new ACs — provisionally:

- **AC-PRM-E (proposed):** AI/MCP writes to private data MUST be governed by a declared
  per-data-class write policy with the three tiers above; defaults MUST be the most protective tier;
  all AI writes MUST be reversible and audited.
- **AC-PRM-F (proposed):** An MCP tool MUST NOT directly commit a review-required write; it MUST
  stage a changeset the workspace applies on explicit user confirmation (the AC-MCP-B
  propose/dispose rule, generalized from comms to data).

Like fellows's `EX-*` and `CST-*`, this rides upstream alongside the working design that
demonstrates it.

## Open questions still carried into planning

- **Where exactly do dedup/merge decisions live?** They are durable private metadata (the
  assertion "these records are the same person") that MUST survive re-import (INV-5, AC-PRM-B).
  v0.1 models them as a minimal **private store** (`relationships.db`) separate from the raw mirrored
  Shared DB — the seed of the full private overlay that arrives in v0.2. Crucially, dedup therefore
  writes the *private* store, **never** the raw Shared DB, so INV-2 stays intact.
- **Proposal/audit substrate** — settled: text/JSON changesets + a JSONL append-only audit log + a
  SQLite snapshot ring, implemented natively (no VCS). Revisit a version-control backend only if a
  future version stores divergently-edited flat-text canonical artifacts.
- **Cloud vs. local AI for dedup.** Dedup sends contact PII to the AI client; for cloud clients this
  triggers AC-MCP-A consent. Local AI recommended; the consent posture for the proposal surface
  needs pinning.
- **Default contact method storage** (vCard `PREF` vs. workspace metadata) — unchanged from the
  original draft; relevant from v0.4.
