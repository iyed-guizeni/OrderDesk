"""The agent step: each line with next_step == "agent" gets its own agent run, with tools bound to the
customer of THIS email. Then code checks the proposal. A rejected or missing proposal goes to a person.
"""

import logging
from collections.abc import Callable
from typing import Literal

from langgraph.graph import END

from orderdesk.agents.resolver_agent import MAX_MODEL_CALLS, run_resolver_agent
from orderdesk.agents.resolver_checks import check_proposal
from orderdesk.agents.resolver_tools import make_resolver_tools
from orderdesk.catalogue.ports import ErpPort, ProductSearch
from orderdesk.domain.catalogue import Customer
from orderdesk.domain.extraction import ExtractedLine
from orderdesk.domain.resolution import LineResolution
from orderdesk.llm.client import AllProvidersFailed, ToolBindable
from orderdesk.prompts.resolver_agent import build_system_prompt, build_user_prompt
from orderdesk.workflow.state import WorkflowState

log = logging.getLogger(__name__)


def needs_agent(state: WorkflowState) -> Literal["resolve_agent", "__end__"]:
    lines = state.resolved_order.lines if state.resolved_order else []
    return "resolve_agent" if any(line.next_step == "agent" for line in lines) else END


def _failed(resolution: LineResolution, why: str) -> LineResolution:
    return resolution.model_copy(update={"reason": "agent_failed", "evidence": why, "next_step": "human"})


def agent_line(
    llm: ToolBindable, system_prompt: str, line: ExtractedLine, resolution: LineResolution,
    customer: Customer | None, erp: ErpPort, search: ProductSearch,
) -> LineResolution:
    tools = make_resolver_tools(erp, search, customer)  # built here, per line: never bound to another customer
    try:
        proposal, why = run_resolver_agent(llm, tools, system_prompt, build_user_prompt(line, resolution))
    except AllProvidersFailed as error:
        log.error("resolver agent could not run (line %s): %s", resolution.line_index, error)
        return _failed(resolution, "the agent could not run (LLM providers failed)")
    if proposal is None:
        return _failed(resolution, why or "the agent gave no answer")

    product, problem = check_proposal(proposal, resolution, erp, customer)
    if product is None:
        log.warning("resolver agent proposal rejected (line %s): %s", resolution.line_index, problem)
        return _failed(resolution, f"proposal rejected: {problem}")
    return LineResolution(line_index=resolution.line_index, status="resolved", product=product, method="agent",
                          candidates=resolution.candidates, evidence=proposal.evidence, next_step="done")


def make_resolve_agent_node(
    llm: ToolBindable, profile: str, erp: ErpPort, search: ProductSearch
) -> Callable[[WorkflowState], dict]:
    system_prompt = build_system_prompt(profile, MAX_MODEL_CALLS)

    def resolve_agent(state: WorkflowState) -> dict:
        if state.resolved_order is None or state.extracted_order is None:
            raise ValueError("cannot run the agent before resolve")
        order = state.resolved_order
        lines = [
            agent_line(llm, system_prompt, state.extracted_order.lines[r.line_index], r, order.customer,
                       erp, search)
            if r.next_step == "agent" else r
            for r in order.lines
        ]
        return {"resolved_order": order.model_copy(update={"lines": lines})}

    return resolve_agent
