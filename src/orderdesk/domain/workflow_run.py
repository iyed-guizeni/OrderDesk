"""Persistent status and checkpoint identity for one email workflow."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Status = Literal["running", "waiting_review", "reviewing", "approved", "rejected", "finished"]


def thread_id_for(tenant_id: int, email_id: int) -> str:
    """Return the stable checkpoint thread id for an email."""
    return f"tenant-{tenant_id}:email-{email_id}"


class WorkflowRun(BaseModel):
    """Database representation of one email's workflow lifecycle."""

    model_config = ConfigDict(frozen=True)

    email_id: int
    tenant_id: int
    thread_id: str
    status: Status
    trace_id: UUID | None
    erp_quotation_id: str | None
    reviewer: str | None
    created_at: datetime
    updated_at: datetime
