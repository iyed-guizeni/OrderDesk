
from dataclasses import dataclass
from datetime import timedelta

import psycopg

PROCESS_EMAIL = "process_email"


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
    tenant_id: int | None = None,
    stale_after: timedelta = timedelta(minutes=10),
) -> Job | None:
    """Take the oldest available job, or return None if there is nothing to do.

    Available = pending, or running for longer than `stale_after` (its worker probably crashed).
    SKIP LOCKED: if another worker is taking a job at this very moment, skip it and take the next one.
    So each job is given to exactly one worker.
    tenant_id: only take jobs of this company (None = any company).
    """
    row = conn.execute(
        """
        UPDATE jobs
        SET status = 'running', locked_by = %(worker)s, locked_at = now(), attempts = attempts + 1
        WHERE id = (
            SELECT id FROM jobs
            WHERE (status = 'pending' OR (status = 'running' AND locked_at < now() - %(stale)s))
              AND (%(tenant)s::int IS NULL OR tenant_id = %(tenant)s)
            ORDER BY id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, tenant_id, email_id, kind, attempts
        """,
        {"worker": worker_name, "stale": stale_after, "tenant": tenant_id},
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


def mark_failed(conn: psycopg.Connection, job: Job, error: str, max_attempts: int = 3) -> str:
    """Record a failure. The job goes back to 'pending' for a retry, or to 'failed' after max_attempts.

    Returns the new status.
    """
    new_status = "failed" if job.attempts >= max_attempts else "pending"
    conn.execute(
        """
        UPDATE jobs
        SET status = %s, last_error = %s, locked_by = NULL, locked_at = NULL,
            finished_at = CASE WHEN %s = 'failed' THEN now() ELSE NULL END
        WHERE id = %s
        """,
        (new_status, error[:2000], new_status, job.id),
    )
    return new_status