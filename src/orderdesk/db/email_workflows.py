"""Tenant-scoped persistence for email workflow status and review coordination."""

from typing import Literal
from uuid import UUID

import psycopg

from orderdesk.domain.workflow_run import WorkflowRun, thread_id_for

_WORKFLOW_COLUMNS = """
email_id, tenant_id, thread_id, status, trace_id, erp_quotation_id, reviewer, created_at, updated_at
"""


def start(conn: psycopg.Connection, tenant_id: int, email_id: int) -> WorkflowRun:
    """Create the workflow row once, or return the existing row for a retried email.
    The row is only created when the email belongs to this tenant (never trust the job's tenant alone)."""
    conn.execute(
        """
        INSERT INTO email_workflows (email_id, tenant_id, thread_id, status)
        SELECT id, tenant_id, %s, 'running' FROM emails WHERE id = %s AND tenant_id = %s
        ON CONFLICT (email_id) DO NOTHING
        """,
        (thread_id_for(tenant_id, email_id), email_id, tenant_id),
    )
    row = conn.execute(
        f"SELECT {_WORKFLOW_COLUMNS} FROM email_workflows WHERE tenant_id = %s AND email_id = %s",
        (tenant_id, email_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"workflow for email {email_id} not found for tenant {tenant_id}")
    return WorkflowRun.model_validate(dict(zip(WorkflowRun.model_fields, row, strict=True)))


def mark_waiting_review(conn: psycopg.Connection, tenant_id: int, email_id: int, trace_id: UUID) -> None:
    cursor = conn.execute(
        """
        UPDATE email_workflows
        SET status = 'waiting_review', trace_id = %s, updated_at = now()
        WHERE tenant_id = %s AND email_id = %s
        """,
        (trace_id, tenant_id, email_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"workflow for email {email_id} not found for tenant {tenant_id}")


def mark_finished(conn: psycopg.Connection, tenant_id: int, email_id: int) -> None:
    cursor = conn.execute(
        """
        UPDATE email_workflows
        SET status = 'finished', updated_at = now()
        WHERE tenant_id = %s AND email_id = %s
        """,
        (tenant_id, email_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"workflow for email {email_id} not found for tenant {tenant_id}")


def claim_for_review(conn: psycopg.Connection, tenant_id: int, email_id: int) -> WorkflowRun | None:
    """Atomically grant the review to one process; competing claims get None."""
    row = conn.execute(
        f"""
        UPDATE email_workflows
        SET status = 'reviewing', updated_at = now()
        WHERE tenant_id = %s AND email_id = %s AND status = 'waiting_review'
        RETURNING {_WORKFLOW_COLUMNS}
        """,
        (tenant_id, email_id),
    ).fetchone()
    if row is None:
        return None
    return WorkflowRun.model_validate(dict(zip(WorkflowRun.model_fields, row, strict=True)))


def finish_review(
    conn: psycopg.Connection,
    tenant_id: int,
    email_id: int,
    status: Literal["approved", "rejected", "waiting_review"],
    reviewer: str | None,
    erp_quotation_id: str | None,
) -> None:
    cursor = conn.execute(
        """
        UPDATE email_workflows
        SET status = %s, reviewer = %s, erp_quotation_id = %s, updated_at = now()
        WHERE tenant_id = %s AND email_id = %s
        """,
        (status, reviewer, erp_quotation_id, tenant_id, email_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"workflow for email {email_id} not found for tenant {tenant_id}")


def list_waiting_review(conn: psycopg.Connection, tenant_id: int) -> list[WorkflowRun]:
    rows = conn.execute(
        f"""
        SELECT {_WORKFLOW_COLUMNS}
        FROM email_workflows
        WHERE tenant_id = %s AND status = 'waiting_review'
        ORDER BY created_at, email_id
        """,
        (tenant_id,),
    ).fetchall()
    return [WorkflowRun.model_validate(dict(zip(WorkflowRun.model_fields, row, strict=True))) for row in rows]


def get(conn: psycopg.Connection, tenant_id: int, email_id: int) -> WorkflowRun | None:
    row = conn.execute(
        f"SELECT {_WORKFLOW_COLUMNS} FROM email_workflows WHERE tenant_id = %s AND email_id = %s",
        (tenant_id, email_id),
    ).fetchone()
    if row is None:
        return None
    return WorkflowRun.model_validate(dict(zip(WorkflowRun.model_fields, row, strict=True)))
