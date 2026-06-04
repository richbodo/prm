-- shared.db — raw mirrored contact records (schema v1).
--
-- Read-only at runtime (INV-2): only the ingester writes this, at import time. One row per logical
-- record per source. The canonical contact a user eventually sees is a projection built in the
-- private store (identity_map ⨝ source_records ⨝ field_resolutions) — that lives in private.db and
-- arrives with the dedup milestone (plan §11 M3); this file is only the mirror.

CREATE TABLE IF NOT EXISTS source_records (
    source_record_id TEXT PRIMARY KEY,   -- deterministic: sha1(source ∥ stable_key); re-import re-attaches
    source           TEXT NOT NULL,      -- provenance source label, e.g. "google_takeout"
    source_uid       TEXT,               -- the source's native UID, if any (usually NULL)
    stable_key       TEXT NOT NULL,      -- fallback-chain identity "kind:value" (INV-5)
    ingested_at      TEXT NOT NULL,      -- ISO-8601 UTC of this import
    raw_jcard        TEXT NOT NULL        -- canonical jCard (RFC 7095) JSON — the contact itself
);
CREATE INDEX IF NOT EXISTS idx_source_records_source     ON source_records(source);
CREATE INDEX IF NOT EXISTS idx_source_records_stable_key ON source_records(stable_key);

-- Per-field provenance (AC-17 / INV-7): where each value came from and when.
CREATE TABLE IF NOT EXISTS field_provenance (
    source_record_id TEXT NOT NULL
        REFERENCES source_records(source_record_id) ON DELETE CASCADE,
    field            TEXT NOT NULL,      -- canonical (jCard) property name, e.g. "email"
    value            TEXT NOT NULL,      -- string form of the value
    observed_at      TEXT                -- source-native timestamp, or the ingest time as a fallback
);
CREATE INDEX IF NOT EXISTS idx_field_provenance_rec ON field_provenance(source_record_id);

-- Full-text search over the searchable surface (FTS5). Standalone (not external-content): the
-- loader keeps it in sync explicitly. source_record_id is carried UNINDEXED to join back.
CREATE VIRTUAL TABLE IF NOT EXISTS contacts_fts USING fts5(
    source_record_id UNINDEXED,
    name,
    email,
    org,
    note
);
