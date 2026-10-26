"""Add, claim (FOR UPDATE SKIP LOCKED) and finish jobs.

A job may run more than once (for example after a crash), so job handlers must be safe to repeat.

Fairness between companies:
- the company served the longest time ago goes first (its oldest available job);
- each company has at most `max_running` jobs running at the same time.
Failed jobs wait longer before each retry (backoff), using the `available_at` column.
"""

from dataclasses import dataclass
from datetime import timedelta

import psycopg

PROCESS_EMAIL = "process_email"

DEFAULT_STALE_AFTER = timedelta(minutes=10)   # a running job older than this is considered abandoned
DEFAULT_MAX_RUNNING_PER_TENANT = 2            # at most 2 jobs of one company running at once
DEFAULT_MAX_ATTEMPTS = 3
RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))


@dataclass(frozen=True)
class Job:
    id: int
    tenant_id: int
    email_id: int
    kind: str
    attempts: int  # including the current attempt


def enqueue(conn: psycopg.Connection, tenant_id: int, email_id: int, kind: str = PROCESS_EMAIL) -> None:
    """Create a pending job for this email. Does nothing if it already exists."""
    conn.execute(
        """
        INSERT INTO jobs (tenant_id, email_id, kind)
        VALUES (%s, %s, %s)
        ON CONFLICT ON CONSTRAINT jobs_email_kind_unique DO NOTHING
        """,
        (tenant_id, email_id, kind),
    )


def claim(
    conn: psycopg.Connection,
    worker_name: str,
    tenant_ids: list[int] | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    max_running: int = DEFAULT_MAX_RUNNING_PER_TENANT,
) -> Job | None:
    """Take the next job, fairly across companies. Returns None if there is nothing to do right now.

    A job can be taken if it is:
      - pending and its retry delay is over (available_at <= now), or
      - running for longer than `stale_after` (its worker probably crashed).
    Order: the company served the longest time ago (or never) first, then its oldest job.
    A company that already has `max_running` live jobs running is skipped until one finishes.
    SKIP LOCKED: if another worker is taking a job at this very moment, skip it -> each job goes to one worker.
    tenant_ids: only take jobs of these companies (None = any company, the normal case).
    """
    row = conn.execute(
        """
        UPDATE jobs
        SET status = 'running', locked_by = %(worker)s, locked_at = now(), claimed_at = now(),
            attempts = attempts + 1
        WHERE id = (
            SELECT j.id
            FROM jobs j
            LEFT JOIN (
                -- when each company was last served (looking at the last day is enough)
                SELECT tenant_id, max(claimed_at) AS last_served
                FROM jobs
                WHERE claimed_at > now() - interval '1 day'
                GROUP BY tenant_id
            ) s ON s.tenant_id = j.tenant_id
            WHERE ((j.status = 'pending' AND j.available_at <= now())
                   OR (j.status = 'running' AND j.locked_at < now() - %(stale)s))
              AND (%(tenants)s::int[] IS NULL OR j.tenant_id = ANY(%(tenants)s))
              AND (
                -- per-company limit: count only LIVE running jobs (not abandoned ones)
                SELECT count(*) FROM jobs r
                WHERE r.tenant_id = j.tenant_id
                  AND r.status = 'running'
                  AND r.locked_at >= now() - %(stale)s
              ) < %(max_running)s
            ORDER BY s.last_served NULLS FIRST, j.id
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED
        )
        RETURNING id, tenant_id, email_id, kind, attempts
        """,
        {"worker": worker_name, "stale": stale_after, "tenants": tenant_ids, "max_running": max_running},
    ).fetchone()
    return Job(*row) if row else None


def mark_done(conn: psycopg.Connection, job_id: int) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = 'done', finished_at = now(), last_error = NULL, locked_by = NULL, locked_at = NULL
        WHERE id = %s
        """,
        (job_id,),
    )


def retry_delay(attempts: int) -> timedelta:
    """How long to wait before the next try, after `attempts` failed attempts (30 s, 2 min, 10 min...)."""
    return RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]


def mark_failed(
    conn: psycopg.Connection, job: Job, error: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> str:
    """Record a failure. The job waits for its retry delay, or becomes 'failed' after max_attempts.

    Returns the new status.
    """
    if job.attempts >= max_attempts:
        new_status, delay = "failed", timedelta(0)
    else:
        new_status, delay = "pending", retry_delay(job.attempts)
    conn.execute(
        """
        UPDATE jobs
        SET status = %(status)s, last_error = %(error)s, locked_by = NULL, locked_at = NULL,
            available_at = now() + %(delay)s,
            finished_at = CASE WHEN %(status)s = 'failed' THEN now() ELSE NULL END
        WHERE id = %(id)s
        """,
        {"status": new_status, "error": error[:2000], "delay": delay, "id": job.id},
    )
    return new_status