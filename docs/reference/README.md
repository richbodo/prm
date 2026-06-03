# Reference material

External, third-party format references kept for building the ingester. These are **not** PRM's own
schemas — they document the *source* data models PRM normalizes into its canonical internal jCard.

## `source_validation_status.md`

Which SaaS export formats have been validated against a **real-world** export (and where that real
sample lives in `ignore-data/`) versus built from documentation alone. Start here to know what each
fixture's fidelity actually rests on, and what still needs a real sample.

## `google_people_schema.json`

The Google **People API** `Person` resource schema — the canonical data model behind Google
Contacts (names, emailAddresses, phoneNumbers, organizations, addresses, urls, memberships, photos,
…), with `resourceName` (`people/{id}`) as the server-assigned stable ID.

- **Provenance:** copied from the predecessor `prt` project
  (`prt/docs/Database/latest_google_people_schema.json`); matches the official reference at
  <https://developers.google.com/people/api/rest/v1/people>.
- **Why it's here:** it's the richest, formally-specified view of Google's contact model — useful
  when mapping Google source fields to canonical jCard. Note it is the **API** shape, *not* a file
  export format. v0.1 ingests Google via **file** (Takeout vCard, Google CSV), not the People API
  (no API connectors in v0.1 — see `plans/v0.1-implementation-plan.md`). Treat this as the
  field-semantics reference, not the wire format we parse.
