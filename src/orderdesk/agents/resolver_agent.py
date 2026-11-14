"""The Resolver agent loop, for ONE line with that line's tools (they are bound to its customer).

    START -> agent -> tools_condition -> tools (ToolNode) -> answered? -> END
               ^                                             |
               +---------------------------------------------+

- The final answer is one more tool, AgentProposal: ToolNode validates its arguments, and an invalid answer
  goes back to the model as an error it can fix.
- Unknown tools and tool errors: ToolNode answers them as error messages, so the run goes on.
- At most MAX_MODEL_CALLS model calls (recursion_limit). The loop never checks the answer against the ERP:
  that is check_proposal, run by the caller.
"""

from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from orderdesk.domain.resolution import AgentProposal
from orderdesk.llm.client import ChatLike, ToolLLMFactory

MAX_MODEL_CALLS = 8
FINAL_ANSWER = AgentProposal.__name__


@tool(FINAL_ANSWER, args_schema=AgentProposal)
def submit_answer(**_: object) -> str:
    """Your final answer for this order line. Call it once, as your last step."""
    return "Answer received."


def _answered(messages: Sequence[AnyMessage]) -> AgentProposal | None:
    """The proposal, if the last tool round accepted an AgentProposal call."""
    accepted = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)
                and m.name == FINAL_ANSWER and m.status == "success"}
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["id"] in accepted:
                    return AgentProposal.model_validate(call["args"])
            return None
    return None


def build_resolver_agent(llm: ChatLike, tools: Sequence[BaseTool]):
    def agent(state: MessagesState) -> dict:
        return {"messages": [llm.invoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode([*tools, submit_answer]))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)  # tool calls -> "tools", none -> END
    builder.add_conditional_edges("tools", lambda s: END if _answered(s["messages"]) else "agent", ["agent", END])
    return builder.compile()


def run_resolver_agent(
    llm_factory: ToolLLMFactory, tools: Sequence[BaseTool], system_prompt: str, user_prompt: str
) -> tuple[AgentProposal | None, str | None]:
    """(the proposal, None), or (None, why there is none). The proposal is NOT checked yet."""
    graph = build_resolver_agent(llm_factory([*tools, submit_answer]), tools)
    start = {"messages": [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]}
    try:
        final = graph.invoke(start, {"recursion_limit": 2 * MAX_MODEL_CALLS})  # agent + tools = 2 steps per call
    except GraphRecursionError:
        return None, f"no answer after {MAX_MODEL_CALLS} model calls"
    proposal = _answered(final["messages"])
    return (proposal, None) if proposal else (None, "the agent stopped without calling AgentProposal")
