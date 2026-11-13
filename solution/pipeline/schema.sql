-- Interaction ledger & reporting pipeline: database schema (SQLite).
--
-- Design rule: INGEST stores clean facts; REPORT derives everything else.
-- Linking events to leads happens at report time, so an event that arrives
-- before its lead is never lost, and re-ingesting is safe.

-- Key fingerprint: guards against running with a different PII key than the
-- one the existing data was hashed with (hashes would silently stop matching).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per file already ingested. The content hash makes re-ingest a no-op.
-- Per-file counters feed data_quality.json.
CREATE TABLE IF NOT EXISTS ingested_files (
    file_sha256              TEXT PRIMARY KEY,
    kind                     TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
    workflow_id              TEXT,                 -- leads only
    source_path              TEXT NOT NULL,
    ingest_seq               INTEGER NOT NULL,     -- global order of ingestion
    ingested_at              TEXT NOT NULL,
    lead_rows_total          INTEGER NOT NULL DEFAULT 0,
    lead_rows_rejected       INTEGER NOT NULL DEFAULT 0,
    duplicate_lead_rows_merged INTEGER NOT NULL DEFAULT 0,
    field_warnings           INTEGER NOT NULL DEFAULT 0,
    events_total             INTEGER NOT NULL DEFAULT 0,
    malformed_events         INTEGER NOT NULL DEFAULT 0,
    duplicate_events_dropped INTEGER NOT NULL DEFAULT 0
);

-- One row per real customer. Identity = (workflow_id, canonical phone),
-- but the phone is stored only as a keyed HMAC (+ last 4 digits for masking).
CREATE TABLE IF NOT EXISTS leads (
    workflow_id   TEXT NOT NULL,
    phone_hash    TEXT NOT NULL,          -- HMAC-SHA256(canonical phone)
    phone_last4   TEXT NOT NULL,          -- needed for phone_masked
    batch_id      TEXT NOT NULL,          -- file stem of the FIRST valid row; never changes
    external_ref  TEXT NOT NULL,          -- value of the schema field marked "reference"
    pii_encrypted TEXT,                   -- Fernet-encrypted JSON of other PII fields (e.g. name)
    attributes    TEXT NOT NULL,          -- JSON of non-PII schema fields (nulls kept)
    created_seq   INTEGER NOT NULL,       -- ingest_seq of the file that created the lead
    PRIMARY KEY (workflow_id, phone_hash)
);
CREATE INDEX IF NOT EXISTS ix_leads_batch ON leads (workflow_id, batch_id);

-- Rejected lead rows. Deliberately contains NO PII.
CREATE TABLE IF NOT EXISTS rejections (
    file_sha256  TEXT NOT NULL REFERENCES ingested_files (file_sha256),
    workflow_id  TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    row_number   INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    PRIMARY KEY (file_sha256, row_number)
);

-- The interaction ledger. Append-only: every valid, non-duplicate event is
-- stored, including ones that can't be linked to a lead yet (orphans).
CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,     -- dedupe key across all drops
    event_type       TEXT,
    workflow_id      TEXT,
    call_id          TEXT,
    occurred_at_utc  TEXT,                 -- ISO-8601 UTC, e.g. 2026-10-01T03:45:55Z
    phone_hash       TEXT,                 -- NULL for overrides / unusable phones
    disposition_code TEXT,
    payload          TEXT NOT NULL,        -- JSON payload (phone is never stored)
    file_sha256      TEXT NOT NULL REFERENCES ingested_files (file_sha256),
    line_number      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_call ON events (call_id);
CREATE INDEX IF NOT EXISTS ix_events_phone ON events (workflow_id, phone_hash);

-- Enforce append-only at the database level, not just by convention.
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events ledger is append-only: UPDATE not allowed');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events ledger is append-only: DELETE not allowed');
END;

-- Disposition catalogue, loaded from schemas/dispositions.csv.
CREATE TABLE IF NOT EXISTS dispositions (
    workflow_id  TEXT NOT NULL,
    code         TEXT NOT NULL,
    label        TEXT,
    is_terminal  INTEGER NOT NULL CHECK (is_terminal IN (0, 1)),
    is_success   INTEGER NOT NULL CHECK (is_success IN (0, 1)),
    PRIMARY KEY (workflow_id, code)
);