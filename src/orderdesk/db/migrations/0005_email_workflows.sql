CREATE TABLE IF NOT EXISTS email_workflows (
    email_id          BIGINT       PRIMARY KEY REFERENCES emails (id) ON DELETE CASCADE,
    tenant_id         INT          NOT NULL,
    thread_id         TEXT         NOT NULL UNIQUE,
    status            TEXT         NOT NULL CHECK (status IN
                        ('running', 'waiting_review', 'reviewing', 'approved', 'rejected', 'finished')),
    trace_id          UUID,
    erp_quotation_id  TEXT,
    reviewer          TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_workflows_tenant_status_idx ON email_workflows (tenant_id, status);
