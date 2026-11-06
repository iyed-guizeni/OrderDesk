import os

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.email import EmailContext
from orderdesk.llm.client import make_fallback_llm
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.workflow.classification import make_classify_node
from orderdesk.workflow.state import WorkflowState

load_dotenv()

provider_chain: list[tuple[str, str]] = []
if os.getenv("GOOGLE_API_KEY"):
    provider_chain.extend(
        [
            ("gemini", "gemini-3.5-flash-lite"),
            ("gemini", "gemini-3.6-flash"),
        ]
    )
if os.getenv("GROQ_API_KEY"):
    provider_chain.append(("groq", "qwen/qwen3.8-27b"))
if not provider_chain:
    raise RuntimeError(
        "No LLM provider is configured. Set GOOGLE_API_KEY or GROQ_API_KEY in .env."
    )

llm = make_fallback_llm(provider_chain, EmailClassification)


builder = StateGraph(WorkflowState)
builder.add_node("classification", make_classify_node(llm, ONDYNA_PROFILE))
builder.add_edge(START, "classification")
builder.add_edge("classification", END)
graph = builder.compile()


