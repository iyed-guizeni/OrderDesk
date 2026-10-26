-- 0004_jobs_claimed_at.sql: remember when each job was taken, for fairness between companies.

-- When a worker last took this job. Never cleared, so we know when each company was last served.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

-- Find when each company was last served.
CREATE INDEX IF NOT EXISTS jobs_tenant_claimed_idx ON jobs (tenant_id, claimed_at);