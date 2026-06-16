-- relationships.db — the private store (schema v2).
--
-- Durable; survives re-import (INV-5/6/9). Two sanctioned writers, serialized by the AC-PRM-C
-- file-lock: the ingester seeds the 1:1 identity baseline at import time, and the daemon applies
-- approved merge decisions + user/AI relationship-data writes. The canonical contact a user sees is a
-- *projection* (built in core/projection.py): identity_map ⨝ source_records (in shared.db) ⨝
-- field_resolutions ⨝ field_values. All CREATEs are IF NOT EXISTS and the built-in seeds are INSERT OR
-- IGNORE, so running this whole script is idempotent — that is how the v1→v2 migration applies the
-- relationship-schema delta to an existing store (see core/relationships_db.py:_ensure_schema).
-- Rationale: docs/design-notes/dedupe-design.md; docs/design-notes/contact-edit-override-model.md.

-- The stable canonical contact (INV-5). The 1:1 baseline uses the source_record_id as the contact_id;
-- a merge re-points the losing records' identity_map rows at the surviving contact_id.
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Which raw record belongs to which canonical contact. One record → exactly one contact.
-- This is what dedup writes (to relationships.db, never shared.db). Re-import re-attaches by the stable
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

-- ============================== v2: the relationship schema (R1) ==============================
-- The user-authored custom schema. INV-3: field DEFINITIONS are authored only here (the workspace
-- path), never over MCP — enforced structurally (no MCP definition tool + this table is a daemon
-- write). `class`: 'builtin' (seeded below, non-removable without a code change) | 'user' (full CRUD).
-- `ai_write_policy` (INV-10) and `disclosure_tier` (PR-7) default to the most-protective tier; their
-- ENFORCEMENT is the later data-floor / AI-write phases, but the columns exist from day one.
CREATE TABLE IF NOT EXISTS field_definitions (
    field_id        TEXT PRIMARY KEY,                 -- stable slug, e.g. "how_we_met"
    label           TEXT NOT NULL,                    -- display label
    kind            TEXT NOT NULL
        CHECK (kind IN ('text','long_text','number','date','boolean',
                        'single_select','multi_select','url','image')),
    config          TEXT NOT NULL DEFAULT '{}',       -- JSON: select options [{value,label,description}], number min/max, image {multi}
    class           TEXT NOT NULL DEFAULT 'user'  CHECK (class IN ('builtin','user')),
    ai_write_policy TEXT NOT NULL DEFAULT 'review-required'
        CHECK (ai_write_policy IN ('review-required','append-only','free-write')),
    disclosure_tier TEXT NOT NULL DEFAULT 'private-sealed'
        CHECK (disclosure_tier IN ('private-sealed','private-shareable-on-consent')),
    required        INTEGER NOT NULL DEFAULT 0,
    position        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Per-contact relationship values. The value→definition FK is the INV-3/4 structural lock: a value for
-- an undefined field cannot be written (with PRAGMA foreign_keys=ON, set by relationships_db.connect).
-- Multi-valued kinds (tags / multi_select / image) get one row per distinct value; single-valued kinds
-- keep one row per (contact, field) — enforced by the value-write API (R2), not the table.
CREATE TABLE IF NOT EXISTS field_values (
    value_id    TEXT PRIMARY KEY,                     -- sha1(contact_id ∥ field_id ∥ normalized value)
    contact_id  TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    field_id    TEXT NOT NULL REFERENCES field_definitions(field_id) ON DELETE CASCADE,
    value       TEXT,                                 -- scalar value / option id / media content-hash
    value_json  TEXT,                                 -- structured payload when a scalar won't do
    written_by  TEXT NOT NULL,                        -- 'manual:user' | 'ai:<session>'
    source      TEXT,                                 -- provenance: 'manual' | 'directory-import' | export source
    written_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_field_values_contact ON field_values(contact_id);
CREATE INDEX IF NOT EXISTS idx_field_values_field   ON field_values(field_id);

-- Built-in fields, seeded for usability (a relationship store you can use the moment you open it).
-- Non-removable without a code change (class='builtin'); sealed + review-required by default like any
-- field. `photo` is the single avatar (image); `tags` is a managed multi-select vocabulary whose
-- options carry descriptions (filled via the tag-management modal, R4); `notes` is freeform long text.
INSERT OR IGNORE INTO field_definitions
    (field_id, label, kind, config, class, position, created_at, updated_at)
VALUES
    ('photo', 'Photo', 'image',        '{"multi":false}',  'builtin', 0,
        strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ('tags',  'Tags',  'multi_select', '{"options":[]}',   'builtin', 10,
        strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ('notes', 'Notes', 'long_text',    '{}',               'builtin', 20,
        strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now'));
