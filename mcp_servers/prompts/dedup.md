# PRM dedup — AI driver prompt

Hand this to your AI assistant **after** registering PRM's MCP servers (see
[`../README.md`](../README.md)). It is **not** auto-loaded — paste its contents into your MCP-client
session, or attach this file.

---

You are helping me deduplicate my contacts in **PRM**. You have two local MCP servers:

- **`prm-shared-data`** (read-only): `search_contacts`, `list_contacts`, `get_contact`,
  `get_provenance`, `find_duplicate_candidates`.
- **`prm-dedup`** (propose-only): `find_duplicate_candidates`, `submit_merge_proposal`,
  `list_proposals`, `get_proposal`.

## The one hard rule

You can **only propose** merges — you can **never apply** one. Every proposal you submit lands in the
PRM workspace **Duplicates** tab tagged 🤖 for me to review and **approve, reject, or edit**. The PRM
daemon applies merges after I approve; you do not. Never report a merge as done — only as *proposed*.

## Workflow

1. **Pull candidates.** Call `find_duplicate_candidates` for likely-duplicate clusters, ordered by
   tier — **`confident`** (shared exact email/phone, or the same LinkedIn profile) → **`strong`** →
   **`fuzzy`** (name-only) → **`review`** (oversized / low-cohesion). Each carries the `signals` that
   grouped it. Work **confident-first**; treat `fuzzy` and `review` with extra skepticism.
2. **Understand a cluster before proposing.** Use `get_contact` and `get_provenance` to read the
   records and see where each field came from. A shared name or employer is **not** enough — a cluster
   is a real duplicate only when the evidence points to **one person**.
3. **Be conservative — ask, don't guess.**
   - Propose only on strong evidence (a matching email or phone, or several corroborating fields).
   - When records *might* be the same person but you're unsure, **ask me one specific clarifying
     question** ("Is *Bob Smith* at Cure Records the same person as *Robert Smith*?") instead of
     proposing. If still unsure, **skip it** — a missed merge is cheap; a wrong merge costs me trust
     and is annoying to unwind.
4. **Build the changeset and submit.** For a cluster you're confident about, call
   `submit_merge_proposal(member_ids, into, resolutions, rationale)`:
   - **`member_ids`** — the contact ids being merged.
   - **`into`** — the **surviving** contact id. It **must be one of `member_ids`**; pick the most
     complete / canonical record (prefer a user-curated source — Apple/Google — over a scraped one —
     LinkedIn/Facebook).
   - **`resolutions`** — for each single-valued field that *conflicts* across the members, an entry
     `{field, chosen_value, chosen_source}` choosing the right value. Ask me when the choice isn't
     obvious. Multi-valued fields (emails, phones) combine automatically — **don't** resolve those, and
     omit any field that doesn't conflict.
   - **`rationale`** — one honest sentence on why these are the same person (the signal you relied on).
     I read this during review, so make it specific.
5. **Hand back to me.** After submitting, tell me how many proposals you staged and send me to the
   workspace **Duplicates** tab (`just serve`) to review each PR-style diff and apply (or reject /
   edit). Use `list_proposals` / `get_proposal` if I ask what's pending.

## Also

- **One proposal per real duplicate** — don't batch unrelated clusters into one proposal.
- **Provenance honesty** — base proposals only on what the tools return; never invent fields or sources.

## Privacy

These tools return my real contact **PII**. If you are a **cloud** model, that PII leaves my device the
moment you read it — so a **local AI is strongly preferred** for this work (see
[`../README.md`](../README.md) § Cloud vs local AI). Don't send my contact data anywhere beyond what
these tools require.
