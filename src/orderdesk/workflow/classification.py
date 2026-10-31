
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from orderdesk.domain.classification import EmailClassification
from orderdesk.prompts.classification import CLASSIFICATION_SYSTEM_PROMPT, build_user_prompt
from orderdesk.workflow.state import WorkflowState


def make_classify_node(llm: Runnable) -> Callable[[WorkflowState], dict]:
    """Build the node. `llm` must already return EmailClassification (with_structured_output),
    or be a fake object with an invoke() method in tests."""

    def classify_email(state: WorkflowState) -> dict:
        user_prompt = build_user_prompt(state["email"])
        output = llm.invoke(
            [
                SystemMessage(content=CLASSIFICATION_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )
        if not isinstance(output, EmailClassification):
            raise ValueError(f"the LLM gave no valid EmailClassification (got {output!r})")
        return {"classification": output}

    return classify_email