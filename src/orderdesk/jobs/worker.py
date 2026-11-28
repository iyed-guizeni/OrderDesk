

import logging
import os
import socket
import time
from collections.abc import Callable
from uuid import uuid4

import psycopg
from langgraph.graph.state import CompiledStateGraph

from orderdesk.db.email_workflows import mark_finished, mark_waiting_review, start
from orderdesk.intake.repository import load_email_context
from orderdesk.jobs.queue import PROCESS_EMAIL, Job, claim, mark_done, mark_failed
from orderdesk.workflow.state import WorkflowState

log = logging.getLogger(__name__)

Handler = Callable[[psycopg.Connection, Job], None]


def process_email(conn: psycopg.Connection, job: Job) -> None:
    """Handle one email. Must be safe to run more than once for the same job."""
    row = conn.execute(
        "SELECT id FROM emails WHERE id = %s AND tenant_id = %s",
        (job.email_id, job.tenant_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"email {job.email_id} not found for tenant {job.tenant_id}")
    log.info("processing tenant %s email %s", job.tenant_id, job.email_id)


HANDLERS: dict[str, Handler] = {PROCESS_EMAIL: process_email}


def make_process_email(graph: CompiledStateGraph) -> Handler:
    """Build the production job handler around the graph constructed at worker startup."""

    def handle(conn: psycopg.Connection, job: Job) -> None:
        run = start(conn, job.tenant_id, job.email_id)
        if run.status != "running":
            log.info("skipping tenant %s email %s with status %s", job.tenant_id, job.email_id, run.status)
            return

        # A retry starts from the original email; review is the only point before any ERP write.
        email = load_email_context(conn, job.tenant_id, job.email_id)
        run_id = uuid4()
        config = {
            "configurable": {"thread_id": run.thread_id},
            "run_id": run_id,
            "run_name": "orderdesk_workflow",
            "metadata": {"tenant_id": job.tenant_id, "email_id": job.email_id},
        }
        result = graph.invoke(WorkflowState(email=email), config)
        if "__interrupt__" in result:
            mark_waiting_review(conn, job.tenant_id, job.email_id, run_id)
        else:
            mark_finished(conn, job.tenant_id, job.email_id)

    return handle


def default_worker_name() -> str:
    """Computer name + process id, e.g. 'WIKI-PC-4812': unique for each running worker."""
    return f"{socket.gethostname()}-{os.getpid()}"


def run_one(
    conn: psycopg.Connection,
    worker_name: str,
    handlers: dict[str, Handler] = HANDLERS,
    tenant_ids: list[int] | None = None,
) -> bool:
    """Claim and run one job. Returns False if there was nothing to do."""
    job = claim(conn, worker_name, tenant_ids=tenant_ids)
    if job is None:
        return False
    try:
        handler = handlers.get(job.kind)
        if handler is None:
            raise ValueError(f"no handler for job kind {job.kind!r}")
        handler(conn, job)
    except Exception as error:  # noqa: BLE001 - any failure is recorded on the job, the worker keeps going
        status = mark_failed(conn, job, f"{type(error).__name__}: {error}")
        log.warning(
            "tenant %s email %s failed (attempt %s), status %s (%s)",
            job.tenant_id,
            job.email_id,
            job.attempts,
            status,
            type(error).__name__,
        )
    else:
        mark_done(conn, job.id)
        log.info("tenant %s email %s status done", job.tenant_id, job.email_id)
    return True


def run_forever(
    conn: psycopg.Connection,
    worker_name: str,
    poll_seconds: float = 2.0,
    handlers: dict[str, Handler] = HANDLERS,
) -> None:
    """Process jobs until stopped with Ctrl+C. Waits `poll_seconds` when the queue is empty."""
    log.info("worker %s started", worker_name)
    try:
        while True:
            if not run_one(conn, worker_name, handlers):
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("worker %s stopped", worker_name)


def run_until_empty(
    conn: psycopg.Connection,
    worker_name: str,
    handlers: dict[str, Handler] = HANDLERS,
    tenant_ids: list[int] | None = None,
) -> int:
    """Process jobs until none is left. Returns how many were run. Handy for tests and demos.

    A failed job waits for its retry delay, so it may still be pending when this returns.
    """
    count = 0
    while run_one(conn, worker_name, handlers, tenant_ids):
        count += 1
    return count