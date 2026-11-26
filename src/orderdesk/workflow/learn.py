"""Learn safe customer product synonyms after an approved draft is written."""

import logging
from collections.abc import Callable

from orderdesk.catalogue.ports import AliasWriter
from orderdesk.checks.order import aliases_to_learn
from orderdesk.workflow.state import WorkflowState

LOGGER = logging.getLogger(__name__)


def make_learn_node(aliases: AliasWriter) -> Callable[[WorkflowState], dict]:
    def learn(state: WorkflowState) -> dict:
        if state.approved_order is None or state.extracted_order is None:
            raise ValueError("learn needs an approved order and extracted order")

        learned = 0
        forgotten = 0
        if state.approved_order.customer_id is not None:
            for words, code in aliases_to_learn(state.approved_order, state.extracted_order):
                outcome = aliases.learn_alias(state.approved_order.customer_id, words, code)
                if outcome == "learned":
                    learned += 1
                elif outcome == "forgotten":
                    forgotten += 1

        LOGGER.info(
            "learned aliases %s forgotten aliases %s for tenant %s email %s",
            learned,
            forgotten,
            state.email.tenant_id,
            state.email.email_id,
        )
        return {"learned_aliases": learned, "forgotten_aliases": forgotten}

    return learn
