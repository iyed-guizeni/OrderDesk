import logging
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from orderdesk.checks.grounding import check_grounding
from orderdesk.documents.reading import read_attachments
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.llm.client import ChatLike
from orderdesk.prompts.extract_order_info import build_system_prompt, build_user_prompt
from orderdesk.storage import Storage
from orderdesk.workflow.state import WorkflowState

log = logging.getLogger(__name__)


def make_extract_node(llm: ChatLike, profile: str, storage: Storage) -> Callable[[WorkflowState], dict]:
    system_prompt = build_system_prompt(profile)

    def extract_order(state: WorkflowState) -> dict:
        email = state.email
        readable, unreadable = read_attachments(email, storage)
        user_prompt = build_user_prompt(email, readable, unreadable)
        output = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        if not isinstance(output, ExtractedOrder):
            raise ValueError(f"the LLM gave no valid ExtractedOrder (got {output!r})")

        issues = check_grounding(output, email.subject, email.body, [text for _, text in readable])
        for issue in issues:  # counted in production: a rising rate means the model or the emails changed
            log.warning("grounding issue %s on line %s: %r (tenant %s, email %s)",
                        issue.kind, issue.line_index, issue.value, email.tenant_id, email.email_id)
        return {"extracted_order": output, "grounding_issues": issues}

    return extract_order
