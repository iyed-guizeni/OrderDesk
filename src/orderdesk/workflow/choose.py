"""LLM choice: for each "ambiguous" line, the LLM picks one of the search candidates, or NONE.
Code checks the answer: a code that is not in the candidate list is treated as NONE.
"""

import logging
from collections.abc import Callable
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END

from orderdesk.domain.catalogue import Customer
from orderdesk.domain.email import EmailContext
from orderdesk.domain.extraction import ExtractedLine
from orderdesk.domain.resolution import CandidateChoice, LineResolution
from orderdesk.llm.client import ChatLike
from orderdesk.prompts.choose_candidate import NONE, build_system_prompt, build_user_prompt
from orderdesk.resolve.ladder import next_step
from orderdesk.workflow.state import WorkflowState

log = logging.getLogger(__name__)


def needs_choice(state: WorkflowState) -> Literal["choose", "__end__"]:
    """After resolve: go to the LLM only if at least one line is ambiguous (no call otherwise)."""
    lines = state.resolved_order.lines if state.resolved_order else []
    return "choose" if any(line.reason == "ambiguous" for line in lines) else END


def choose_line(
    llm: ChatLike, system_prompt: str, email: EmailContext, line: ExtractedLine, resolution: LineResolution,
    customer: Customer | None,
) -> LineResolution:
    user_prompt = build_user_prompt(email, line, resolution.candidates)
    answer = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
    if not isinstance(answer, CandidateChoice):
        raise ValueError(f"the LLM gave no valid CandidateChoice (got {answer!r})")

    by_code = {c.product.code: c.product for c in resolution.candidates}
    chosen = by_code.get(answer.code.strip())
    if chosen is None:
        if answer.code.strip().upper() != NONE:
            log.warning("LLM chose %r, which is not a candidate: treated as NONE (email %s, line %s)",
                        answer.code, email.email_id, resolution.line_index)
        return resolution.model_copy(update={
            "reason": "llm_said_none", "evidence": answer.evidence,
            "next_step": next_step("llm_said_none", customer is not None),
        })
    return LineResolution(line_index=resolution.line_index, status="resolved", product=chosen, method="llm_choice",
                          candidates=resolution.candidates, evidence=answer.evidence, next_step="done")


def make_choose_node(llm: ChatLike, profile: str) -> Callable[[WorkflowState], dict]:
    system_prompt = build_system_prompt(profile)

    def choose(state: WorkflowState) -> dict:
        if state.resolved_order is None or state.extracted_order is None:
            raise ValueError("cannot choose candidates before resolve")
        order = state.resolved_order
        lines = [
            choose_line(llm, system_prompt, state.email, state.extracted_order.lines[r.line_index], r, order.customer)
            if r.reason == "ambiguous" else r
            for r in order.lines
        ]
        return {"resolved_order": order.model_copy(update={"lines": lines})}

    return choose
