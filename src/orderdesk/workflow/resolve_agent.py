"""The agent step: each line with next_step == "agent" gets its own agent run, with tools bound to the
customer of THIS email. Then code checks the proposal. A rejected or missing proposal goes to a person.
"""

import contextvars
import logging
import os
from collections.abc import Callable
from typing import Literal

from langgraph.graph import END
from langsmith import traceable, tracing_context

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

NO_ANSWER = {  # shown to the reviewer when the agent gave no proposal
    "no_proposal": "the agent stopped without calling AgentProposal",
    "call_limit": f"no answer after {MAX_MODEL_CALLS} model calls",
}


def agent_tracing() -> bool | None:
    """RESOLVER_AGENT_TRACING=true traces the agent even when LANGSMITH_TRACING is off for the rest of the workflow.
    None: follow LANGSMITH_TRACING. Read at call time, so .env is already loaded."""
    return True if os.environ.get("RESOLVER_AGENT_TRACING", "").lower() == "true" else None


def needs_agent(state: WorkflowState) -> Literal["resolve_agent", "__end__"]:
    lines = state.resolved_order.lines if state.resolved_order else []
    return "resolve_agent" if any(line.next_step == "agent" for line in lines) else END


def _failed(resolution: LineResolution, why: str) -> LineResolution:
    return resolution.model_copy(update={"reason": "agent_failed", "evidence": why, "next_step": "human"})


def _trace_inputs(inputs: dict) -> dict:
    """What the LangSmith trace keeps of agent_line's inputs: no ERP objects, no customer name or address."""
    resolution, line = inputs["resolution"], inputs["line"]
    return {"line_index": resolution.line_index, "reason": resolution.reason, "line": line.raw_text,
            "customer_known": inputs["customer"] is not None}


@traceable(name="resolver_agent_line", process_inputs=_trace_inputs)  # one run per line: agent + code checks
def agent_line(
    llm: ToolBindable, system_prompt: str, line: ExtractedLine, resolution: LineResolution,
    customer: Customer | None, erp: ErpPort, search: ProductSearch, metadata: dict | None = None,
) -> LineResolution:
    tools = make_resolver_tools(erp, search, customer)  # built here, per line: never bound to another customer
    metadata = {**(metadata or {}), "line_index": resolution.line_index, "reason": resolution.reason}
    try:
        run = run_resolver_agent(llm, tools, system_prompt, build_user_prompt(line, resolution), metadata)
    except AllProvidersFailed as error:
        log.error("resolver agent could not run (line %s): %s", resolution.line_index, error)
        return _failed(resolution, "the agent could not run (LLM providers failed)")
    if run.proposal is None:
        return _failed(resolution, NO_ANSWER[run.stop_reason])

    product, problem = check_proposal(run.proposal, line, resolution, erp, customer)
    if product is None:
        log.warning("resolver agent proposal rejected (line %s): %s", resolution.line_index, problem)
        return _failed(resolution, f"proposal rejected: {problem}")
    return LineResolution(line_index=resolution.line_index, status="resolved", product=product, method="agent",
                          candidates=resolution.candidates, evidence=run.proposal.evidence, next_step="done")


def make_resolve_agent_node(
    llm: ToolBindable, profile: str, erp: ErpPort, search: ProductSearch
) -> Callable[[WorkflowState], dict]:
    system_prompt = build_system_prompt(profile, MAX_MODEL_CALLS)

    def resolve_agent(state: WorkflowState) -> dict:
        if state.resolved_order is None or state.extracted_order is None:
            raise ValueError("cannot run the agent before resolve")
        order = state.resolved_order
        ids = {"tenant_id": state.email.tenant_id, "email_id": state.email.email_id}

        def run_lines() -> list[LineResolution]:
            with tracing_context(enabled=agent_tracing(), metadata=ids):
                return [
                    agent_line(llm, system_prompt, state.extracted_order.lines[r.line_index], r, order.customer,
                               erp, search, ids)
                    if r.next_step == "agent" else r
                    for r in order.lines
                ]

        # Agent-only tracing: a fresh context, so each line is its own trace and not an orphan child of the
        # (untraced) workflow run. Otherwise the agent runs nest under the workflow's trace as usual.
        lines = contextvars.Context().run(run_lines) if agent_tracing() else run_lines()
        return {"resolved_order": order.model_copy(update={"lines": lines})}

    return resolve_agent
