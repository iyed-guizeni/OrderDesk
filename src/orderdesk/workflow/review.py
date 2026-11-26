"""The human-in-the-loop pause. LangGraph re-runs this node from its first line on every resume, and matches the
interrupt() calls to the resume values in order: nothing with a side effect may run before interrupt()."""

from collections.abc import Callable

from langgraph.types import interrupt
from pydantic import ValidationError

from orderdesk.catalogue.ports import ErpPort
from orderdesk.checks.order import apply_decision
from orderdesk.domain.review import ReviewDecision
from orderdesk.workflow.state import WorkflowState


def make_review_node(erp: ErpPort) -> Callable[[WorkflowState], dict]:
    def review(state: WorkflowState) -> dict:
        draft = state.draft_order
        if draft is None:
            raise ValueError("Review needs a draft order")

        errors: list[str] = []
        while True:
            answer = interrupt({"draft": draft.model_dump(mode="json"), "errors": errors})
            try:
                decision = ReviewDecision.model_validate(answer)
            except ValidationError as exc:
                errors = [str(exc)]
                continue

            approved, errors = apply_decision(draft, decision, erp)
            if not errors:
                return {"review_decision": decision, "approved_order": approved}

    return review
