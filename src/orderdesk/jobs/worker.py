
import logging
import os
import socket
import time
from collections.abc import Callable

import psycopg

from orderdesk.jobs.queue import PROCESS_EMAIL, Job, claim, mark_done, mark_failed

log = logging.getLogger(__name__)

Handler = Callable[[psycopg.Connection, Job], None]


def process_email(conn: psycopg.Connection, job: Job) -> None:
    """Handle one email. Must be safe to run more than once for the same job."""
    row = conn.execute(
        "SELECT from_address, subject FROM emails WHERE id = %s AND tenant_id = %s",
        (job.email_id, job.tenant_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"email {job.email_id} not found for tenant {job.tenant_id}")
    from_address, subject = row
    log.info("processing email %s from %s: %r", job.email_id, from_address, subject)


HANDLERS: dict[str, Handler] = {PROCESS_EMAIL: process_email}


def default_worker_name() -> str:
    """Computer name + process id, e.g. 'WIKI-PC-4812': unique for each running worker."""
    return f"{socket.gethostname()}-{os.getpid()}"


def run_one(
    conn: psycopg.Connection,
    worker_name: str,
    handlers: dict[str, Handler] = HANDLERS,
    tenant_id: int | None = None,
) -> bool:
    """Claim and run one job. Returns False if there was nothing to do."""
    job = claim(conn, worker_name, tenant_id=tenant_id)
    if job is None:
        return False
    try:
        handler = handlers.get(job.kind)
        if handler is None:
            raise ValueError(f"no handler for job kind {job.kind!r}")
        handler(conn, job)
    except Exception as error:  # noqa: BLE001 - any failure is recorded on the job, the worker keeps going
        status = mark_failed(conn, job, f"{type(error).__name__}: {error}")
        log.warning("job %s failed (attempt %s), now %s: %s", job.id, job.attempts, status, error)
    else:
        mark_done(conn, job.id)
        log.info("job %s done", job.id)
    return True


def run_forever(conn: psycopg.Connection, worker_name: str, poll_seconds: float = 2.0) -> None:
    """Process jobs until stopped with Ctrl+C. Waits `poll_seconds` when the queue is empty."""
    log.info("worker %s started", worker_name)
    try:
        while True:
            if not run_one(conn, worker_name):
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("worker %s stopped", worker_name)


def run_until_empty(
    conn: psycopg.Connection,
    worker_name: str,
    handlers: dict[str, Handler] = HANDLERS,
    tenant_id: int | None = None,
) -> int:
    """Process jobs until none is left. Returns how many were run. Handy for tests and demos.

    A job that fails is retried until it reaches its maximum attempts, then left as 'failed'.
    """
    count = 0
    while run_one(conn, worker_name, handlers, tenant_id):
        count += 1
    return count