-- private.db — the private store (schema v1).
--
-- Durable; survives re-import (INV-5/6/9). Two sanctioned writers, serialized by the AC-PRM-C
-- file-lock: the ingester seeds the 1:1 identity baseline at import time, and the daemon applies
-- approved merge decisions. The canonical contact a user sees is a *projection* (built in
-- core/projection.py): identity_map ⨝ source_records (in shared.db) ⨝ field_resolutions.
-- Rationale: docs/design-notes/dedupe-design.md.

-- The stable canonical contact (INV-5). The 1:1 baseline uses the source_record_id as the contact_id;
-- a merge re-points the losing records' identity_map rows at the surviving contact_id.
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Which raw record belongs to which canonical contact. One record → exactly one contact.
-- This is what dedup writes (to private.db, never shared.db). Re-import re-attaches by the stable
-- source_record_id, so merge decisions survive.
CREATE TABLE IF NOT EXISTS identity_map (
    source_record_id TEXT PRIMARY KEY,
    contact_id       TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_identity_map_contact ON identity_map(contact_id);

-- The chosen value for a single-valued field when its sources disagree. Also field-level CRUD-Update
-- (a manual edit is a row with rule='user'). `rule`: source_priority | most_complete | most_recent |
-- user. `status`: applied | needs_review.
CREATE TABLE IF NOT EXISTS field_resolutions (
    contact_id    TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    field         TEXT NOT NULL,
    chosen_value  TEXT,
    chosen_source TEXT,
    rule          TEXT,
    status        TEXT NOT NULL DEFAULT 'applied',
    resolved_by   TEXT,
    resolved_at   TEXT,
    PRIMARY KEY (contact_id, field)
);

-- Persisted "not a duplicate" rejections (and confirmed merges), keyed on a stable pair/cluster key,
-- so a dismissed candidate never resurfaces on re-detect or re-import.
CREATE TABLE IF NOT EXISTS dedup_decisions (
    pair_key   TEXT PRIMARY KEY,
    decision   TEXT NOT NULL,   -- 'not_duplicate' | 'merged'
    decided_at TEXT NOT NULL
);

-- Instance settings. Seeded with `source_priority` (the reconcile order — config, not hardcoded).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
