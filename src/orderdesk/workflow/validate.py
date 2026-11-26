"""The Validate node: the resolved lines become the draft order a person reviews (no LLM)."""

from collections.abc import Callable

from orderdesk.catalogue.ports import ErpPort
from orderdesk.checks.order import build_draft
from orderdesk.workflow.state import WorkflowState


def make_validate_node(erp: ErpPort) -> Callable[[WorkflowState], dict]:
    def validate(state: WorkflowState) -> dict:
        if state.extracted_order is None or state.resolved_order is None:
            raise ValueError("Validate needs extracted and resolved orders")
        return {"draft_order": build_draft(state.extracted_order, state.resolved_order, erp)}

    return validate
