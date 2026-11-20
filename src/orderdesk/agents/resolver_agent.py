"""The Resolver agent loop, for ONE line with that line's tools (they are bound to its customer).

    START -> agent -> tools_condition -> tools (ToolNode) -> answered? -> END
 

- The final answer is one more tool, AgentProposal: ToolNode validates its arguments, and an invalid answer
  goes back to the model as an error it can fix.
- Unknown tools, bad arguments and tool failures (e.g. an ERP timeout) are answered as error messages, so the
  run goes on: the model can retry once or answer NONE. A failure's details stay in our logs, not in the prompt.
- At most MAX_MODEL_CALLS model calls: after that, the tools step goes to END (recursion_limit is only a safety net). The loop never checks the answer against the ERP:
  that is check_proposal, run by the caller.
- It returns an AgentRun: the proposal, how the loop ended (set by code, never by the model) and the whole
  conversation, which the evals read (tools called, tool errors, tokens).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolInvocationError

from orderdesk.domain.resolution import AgentProposal
from orderdesk.llm.client import ChatLike, ToolBindable

log = logging.getLogger(__name__)

MAX_MODEL_CALLS = 8
TOOL_FAILED = "This tool failed (a temporary problem on our side). You may call it once more; otherwise answer NONE."
FINAL_ANSWER = AgentProposal.__name__

StopReason = Literal["answered", "no_proposal", "call_limit"]


@dataclass(frozen=True)
class AgentRun:
    proposal: AgentProposal | None  # NOT checked yet
    stop_reason: StopReason  # answered | no_proposal: text without a tool call | call_limit: MAX_MODEL_CALLS reached
    messages: list[AnyMessage]  # the whole conversation, kept even when the limit is hit


@tool(FINAL_ANSWER, args_schema=AgentProposal, response_format="content_and_artifact")
def submit_answer(**fields: object) -> tuple[str, AgentProposal]:
    """Your final answer for this order line. Call it once, as your last step."""
    # content goes back to the model; artifact is the answer for our code (ToolMessage.artifact)
    return "Answer received.", AgentProposal.model_validate(fields)


def tool_error_reply(error: Exception) -> str:
    """What the model reads when a tool call fails. ToolNode alone would only answer bad arguments,
    and let any other exception (an ERP timeout) crash the whole run."""
    if isinstance(error, ToolInvocationError):  # the model's arguments were wrong: tell it exactly what
        return error.message
    log.warning("resolver tool failed: %r", error)
    return TOOL_FAILED


def _answered(messages: Sequence[AnyMessage]) -> AgentProposal | None:
    """The proposal, if the last tool round accepted an AgentProposal call."""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):  # only look at the last tool round
            return None
        if message.name == FINAL_ANSWER and message.status == "success":
            return message.artifact
    return None


def _budget_spent(messages: Sequence[AnyMessage]) -> bool:
    return sum(isinstance(m, AIMessage) for m in messages) >= MAX_MODEL_CALLS


def answered_or_continue(state: MessagesState) -> str:
    """After the tools: stop when an AgentProposal was accepted or the model calls are spent, else back to the model.
    The tools of the last allowed call still run, so an answer given on that call is kept."""
    messages = state["messages"]
    return END if _answered(messages) or _budget_spent(messages) else "agent"


def build_resolver_agent(llm: ChatLike, tools: Sequence[BaseTool]):
    def agent(state: MessagesState) -> dict:
        return {"messages": [llm.invoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode([*tools, submit_answer], handle_tool_errors=tool_error_reply))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)  # tool calls -> "tools", none -> END
    builder.add_conditional_edges("tools", answered_or_continue, ["agent", END])
    return builder.compile()


def run_resolver_agent(
    llm: ToolBindable, tools: Sequence[BaseTool], system_prompt: str, user_prompt: str,
    metadata: dict | None = None,
) -> AgentRun:
    """Run the loop for one line. The proposal is NOT checked yet.
    metadata: ids for tracing (tenant, email, line, reason). Never the sender address or customer name."""
    graph = build_resolver_agent(llm.bind_tools([*tools, submit_answer]), tools)
    start = {"messages": [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]}
    metadata = metadata or {}
    config = {
        "recursion_limit": 2 * MAX_MODEL_CALLS + 2,  # safety net only: answered_or_continue stops first
        "run_name": "resolver_agent",
        "tags": ["resolver_agent", f"reason:{metadata.get('reason', 'unknown')}"],
        "metadata": metadata,
    }
    messages = start["messages"]
    try:  # stream, not invoke: the state after each step, so the messages survive GraphRecursionError
        for state in graph.stream(start, config, stream_mode="values"):
            messages = state["messages"]
    except GraphRecursionError:
        return AgentRun(None, "call_limit", messages)
    proposal = _answered(messages)
    if proposal:
        return AgentRun(proposal, "answered", messages)
    stopped_after_tools = isinstance(messages[-1], ToolMessage)  # the budget, not the model, ended the loop
    return AgentRun(None, "call_limit" if stopped_after_tools else "no_proposal", messages)
