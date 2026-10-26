-- 0003_jobs_fairness.sql: retry delays (backoff) and the per-company limit.

-- "Do not take this job before this time." New jobs are available immediately.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Count a company's running jobs (the per-company limit).
CREATE INDEX IF NOT EXISTS jobs_tenant_status_idx ON jobs (tenant_id, status);