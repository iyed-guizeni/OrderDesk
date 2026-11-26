from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orderdesk.catalogue.ports import AliasStore, ErpPort, ProductSearch
from orderdesk.llm.client import ChatLike, ToolBindable
from orderdesk.storage import Storage
from orderdesk.workflow import after_sales, amendment, cancellation, other, quote_request, triage
from orderdesk.workflow.choose import make_choose_node, needs_choice
from orderdesk.workflow.classification import make_classify_node
from orderdesk.workflow.new_order import make_extract_node
from orderdesk.workflow.resolve import make_resolve_node
from orderdesk.workflow.resolve_agent import make_resolve_agent_node, needs_agent
from orderdesk.workflow.review import make_review_node
from orderdesk.workflow.router import route_email
from orderdesk.workflow.state import WorkflowState
from orderdesk.workflow.validate import make_validate_node


def after_resolve(state: WorkflowState) -> Literal["choose", "resolve_agent", "__end__"]:
    """Cheapest first: the one-call LLM choice for ambiguous lines, then the agent, else done."""
    return "choose" if needs_choice(state) == "choose" else needs_agent(state)


def build_graph(
    classify_llm: ChatLike,
    extract_llm: ChatLike,
    choose_llm: ChatLike,
    agent_llm: ToolBindable,
    profile: str,
    storage: Storage,
    erp: ErpPort,
    search: ProductSearch,
    aliases: AliasStore,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """The email workflow. Everything it needs is passed in: no API keys or files are touched at import."""
    builder = StateGraph(WorkflowState)
    builder.add_node("classification", make_classify_node(classify_llm, profile))
    branch_nodes = {
        "triage": triage.triage,
        "new_order": make_extract_node(extract_llm, profile, storage),  # becomes a subgraph later
        "amendment": amendment.amendment,
        "cancellation": cancellation.cancellation,
        "quote_request": quote_request.quote_request,
        "after_sales": after_sales.after_sales,
        "other": other.other,
    }
    for name, node in branch_nodes.items():
        builder.add_node(name, node)
    builder.add_node("resolve", make_resolve_node(erp, search, aliases))
    builder.add_node("choose", make_choose_node(choose_llm, profile))
    builder.add_node("resolve_agent", make_resolve_agent_node(agent_llm, profile, erp, search))
    builder.add_node("validate", make_validate_node(erp))
    builder.add_node("review", make_review_node(erp))

    builder.add_edge(START, "classification")
    builder.add_conditional_edges("classification", route_email, list(branch_nodes))
    builder.add_edge("new_order", "resolve")
    builder.add_conditional_edges(
        "resolve",
        after_resolve,
        {"choose": "choose", "resolve_agent": "resolve_agent", END: "validate"},
    )
    builder.add_conditional_edges("choose", needs_agent, {"resolve_agent": "resolve_agent", END: "validate"})
    builder.add_edge("resolve_agent", "validate")
    builder.add_edge("validate", "review")
    builder.add_edge("review", END)
    for name in branch_nodes:
        if name != "new_order":
            builder.add_edge(name, END)
    return builder.compile(checkpointer=checkpointer)
