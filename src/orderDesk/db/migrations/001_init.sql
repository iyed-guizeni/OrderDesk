-- pgvector: not used yet, enabled now for product search later.
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per received email. The original .eml file is in storage (raw_key).
CREATE TABLE IF NOT EXISTS emails (
    id              BIGSERIAL    PRIMARY KEY,
    tenant_id       INT          NOT NULL DEFAULT 1,        -- which distributor (one for now)
    message_id      TEXT         NOT NULL,                  -- Message-ID header
    in_reply_to     TEXT,                                   -- In-Reply-To header (replies only)
    references_ids  TEXT[]       NOT NULL DEFAULT '{}',     -- all IDs from the References header
    from_address    TEXT         NOT NULL,                  
    from_name       TEXT,                                   
    reply_to        TEXT,                                   -- Reply-To header, if any
    to_addresses    TEXT[]       NOT NULL DEFAULT '{}',
    cc_addresses    TEXT[]       NOT NULL DEFAULT '{}',
    subject         TEXT,
    sent_at         TIMESTAMPTZ,                            -- Date header (sender's clock)
    received_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),    -- when OrderDesk stored it
    body_text       TEXT,                                   -- plain-text body
    raw_key         TEXT         NOT NULL,                  -- where the original .eml is in storage
    raw_sha256      TEXT         NOT NULL,                  -- hash of the original .eml bytes
    CONSTRAINT emails_tenant_message_id_unique UNIQUE (tenant_id, message_id)
);

-- this one answers "which emails reply to this one?".
CREATE INDEX IF NOT EXISTS emails_in_reply_to_idx ON emails (tenant_id, in_reply_to);

CREATE TABLE IF NOT EXISTS attachments (
    id            BIGSERIAL    PRIMARY KEY,
    tenant_id     INT          NOT NULL DEFAULT 1,
    email_id      BIGINT       NOT NULL REFERENCES emails (id) ON DELETE CASCADE,
    filename      TEXT,
    content_type  TEXT         NOT NULL,                    -- application/pdf, text/csv, ...
    size_bytes    INT          NOT NULL,
    sha256        TEXT         NOT NULL,                    -- fingerprint of the file content
    storage_key   TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS attachments_email_id_idx ON attachments (email_id);
-- "Has this exact file been received before?" (used by duplicate detection later)
CREATE INDEX IF NOT EXISTS attachments_sha256_idx ON attachments (tenant_id, sha256);