"""Write an approved draft quotation through the ERP writer port."""

import logging
from collections.abc import Callable

from orderdesk.catalogue.ports import ErpWriter
from orderdesk.checks.order import to_quotation_request
from orderdesk.workflow.state import WorkflowState

LOGGER = logging.getLogger(__name__)


def make_write_draft_node(writer: ErpWriter) -> Callable[[WorkflowState], dict]:
    def write_draft(state: WorkflowState) -> dict:
        if state.approved_order is None:
            raise ValueError("write_draft needs an approved order")
        request = to_quotation_request(state.approved_order, state.email.tenant_id, state.email.email_id)
        quotation_id = writer.create_draft_quotation(request)
        LOGGER.info(
            "draft quotation %s for tenant %s email %s",
            quotation_id,
            state.email.tenant_id,
            state.email.email_id,
        )
        return {"erp_quotation_id": quotation_id}

    return write_draft
