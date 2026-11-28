"""Claim and resume a workflow paused for human review."""

import math
from typing import Literal

import psycopg
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict

from orderdesk.db import email_workflows
from orderdesk.domain.draft import DraftOrder


def parse_changes(codes: list[str], qtys: list[str], removes: list[str]) -> list[dict]:
    """Parse CLI line edits into zero-based workflow changes without partial results."""
    changes: dict[int, dict] = {}

    def parse_assignment(value: str, option: str) -> tuple[int, str]:
        index_text, separator, replacement = value.partition("=")
        if not separator or not index_text.strip() or not replacement.strip():
            raise ValueError(f"{option} expects INDEX=VALUE")
        try:
            index = int(index_text)
        except ValueError as exc:
            raise ValueError(f"{option} line index must be a non-negative integer: {index_text!r}") from exc
        if index < 0:
            raise ValueError(f"{option} line index must be non-negative")
        return index, replacement.strip()

    for value in codes:
        index, code = parse_assignment(value, "--code")
        change = changes.setdefault(index, {"line_index": index})
        if "code" in change:
            raise ValueError(f"--code was supplied more than once for line {index}")
        change["code"] = code

    for value in qtys:
        index, quantity_text = parse_assignment(value, "--qty")
        try:
            quantity = float(quantity_text)
        except ValueError as exc:
            raise ValueError(f"--qty for line {index} must be a number: {quantity_text!r}") from exc
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError(f"--qty for line {index} must be a finite number greater than 0")
        change = changes.setdefault(index, {"line_index": index})
        if "quantity" in change:
            raise ValueError(f"--qty was supplied more than once for line {index}")
        change["quantity"] = quantity

    for value in removes:
        try:
            index = int(value)
        except ValueError as exc:
            raise ValueError(f"--remove expects a non-negative line index: {value!r}") from exc
        if index < 0:
            raise ValueError("--remove line index must be non-negative")
        change = changes.setdefault(index, {"line_index": index})
        if len(change) > 1:
            raise ValueError(f"--remove cannot be combined with --code or --qty for line {index}")
        if change.get("remove"):
            raise ValueError(f"--remove was supplied more than once for line {index}")
        change["remove"] = True

    return [changes[index] for index in sorted(changes)]


class ReviewResult(BaseModel):
    """Outcome of submitting one review decision."""

    model_config = ConfigDict(frozen=True)

    outcome: Literal["approved", "rejected", "asked_again", "not_waiting"]
    errors: list[str] = []
    erp_quotation_id: str | None = None


def pending_draft(
    graph: CompiledStateGraph,
    conn: psycopg.Connection,
    tenant_id: int,
    email_id: int,
) -> tuple[DraftOrder, list[str]] | None:
    """Read the draft and latest validation errors from a waiting checkpoint."""
    run = email_workflows.get(conn, tenant_id, email_id)
    if run is None or run.status != "waiting_review":
        return None

    snapshot = graph.get_state({"configurable": {"thread_id": run.thread_id}})
    for task in snapshot.tasks:
        for interrupted in task.interrupts:
            payload = interrupted.value
            draft_data = payload.get("draft")
            if draft_data is not None:
                draft = DraftOrder.model_validate(draft_data)
                return draft, list(payload.get("errors", []))
    raise RuntimeError(f"email {email_id} is marked waiting_review without a review interrupt")


def submit_review(
    graph: CompiledStateGraph,
    conn: psycopg.Connection,
    tenant_id: int,
    email_id: int,
    decision: dict,
) -> ReviewResult:
    """Atomically claim and resume a pending review; restore waiting status if resume raises."""
    run = email_workflows.claim_for_review(conn, tenant_id, email_id)
    if run is None:
        return ReviewResult(outcome="not_waiting")

    reviewer = decision.get("reviewer")
    if not isinstance(reviewer, str):
        reviewer = None
    try:
        result = graph.invoke(
            Command(resume=decision),
            {"configurable": {"thread_id": run.thread_id}},
        )
    except Exception:
        email_workflows.finish_review(
            conn,
            tenant_id,
            email_id,
            "waiting_review",
            reviewer,
            None,
        )
        raise

    if "__interrupt__" in result:
        errors = result["__interrupt__"][0].value.get("errors", [])
        email_workflows.finish_review(conn, tenant_id, email_id, "waiting_review", reviewer, None)
        return ReviewResult(outcome="asked_again", errors=errors)

    if result.get("approved_order") is not None:
        quotation_id = result.get("erp_quotation_id")
        email_workflows.finish_review(
            conn,
            tenant_id,
            email_id,
            "approved",
            reviewer,
            quotation_id,
        )
        return ReviewResult(outcome="approved", erp_quotation_id=quotation_id)

    email_workflows.finish_review(conn, tenant_id, email_id, "rejected", reviewer, None)
    return ReviewResult(outcome="rejected")
