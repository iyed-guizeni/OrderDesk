from orderdesk.documents.reading import read_attachments
from orderdesk.workflow.state import WorkflowState
from orderdesk.llm.client import ChatLike
from orderdesk.prompts.extract_order_info import build_system_prompt, build_user_prompt
from orderdesk.storage import Storage
from langchain_core.messages import HumanMessage, SystemMessage
from orderdesk.domain.extraction import ExtractedOrder
from collections.abc import Callable 



def make_extract_node(llm: ChatLike, profile: str, storage: Storage) -> Callable[[WorkflowState], dict]:
    system_prompt = build_system_prompt(profile)

    def extract_order(state: WorkflowState) -> dict:
        readable, unreadable = read_attachments(state.email, storage)
        user_prompt = build_user_prompt(state.email, readable, unreadable)
        output = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        if not isinstance(output, ExtractedOrder):
            raise ValueError(f"the LLM gave no valid ExtractedOrder (got {output!r})")
        return {"extracted_order": output}

    return extract_order


