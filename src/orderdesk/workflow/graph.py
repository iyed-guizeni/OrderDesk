from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from orderdesk.domain.classification import EmailClassification
from orderdesk.llm.client import make_fallback_llm
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.workflow.classification import make_classify_node
from orderdesk.workflow.router import route_email
from orderdesk.workflow.state import WorkflowState
from orderdesk.workflow import (
    after_sales,
    amendment,
    cancellation,
    new_order,
    other,
    quote_request,
    triage,
)

load_dotenv()


provider_chain = [
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    ("mistral", "mistralai/mistral-large-4-1t-a52b"),
    ("gemini", "gemini-3.5-flash-lite"),
]
llm = make_fallback_llm(provider_chain, EmailClassification)


builder = StateGraph(WorkflowState)
builder.add_node("classification", make_classify_node(llm, ONDYNA_PROFILE))
branch_nodes = {
    "triage": triage.triage,
    "new_order": new_order.new_order,
    "amendment": amendment.amendment,
    "cancellation": cancellation.cancellation,
    "quote_request": quote_request.quote_request,
    "after_sales": after_sales.after_sales,
    "other": other.other,
}
for name, node in branch_nodes.items():
    builder.add_node(name, node)

builder.add_conditional_edges("classification", route_email, list(branch_nodes))
builder.add_edge(START, "classification")
for name in branch_nodes:
    builder.add_edge(name, END)
graph = builder.compile()
