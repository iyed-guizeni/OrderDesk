-- 0002_jobs.sql: the job queue. One job = one piece of work for a worker.

CREATE TABLE IF NOT EXISTS jobs (
    id           BIGSERIAL    PRIMARY KEY,
    tenant_id    INT          NOT NULL DEFAULT 1,
    email_id     BIGINT       NOT NULL REFERENCES emails (id) ON DELETE CASCADE,
    kind         TEXT         NOT NULL DEFAULT 'process_email',   -- what to do with the email
    status       TEXT         NOT NULL DEFAULT 'pending',
    attempts     INT          NOT NULL DEFAULT 0,                 -- how many times a worker started it
    last_error   TEXT,                                            -- why the last attempt failed
    locked_by    TEXT,                                            -- which worker is running it
    locked_at    TIMESTAMPTZ,                                     -- when that worker took it
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    CONSTRAINT jobs_status_check CHECK (status IN ('pending', 'running', 'done', 'failed')),
    -- One job of each kind per email: creating it twice does nothing.
    CONSTRAINT jobs_email_kind_unique UNIQUE (email_id, kind)
);

-- Workers constantly ask "what is the oldest job waiting?".
CREATE INDEX IF NOT EXISTS jobs_status_id_idx ON jobs (status, id);