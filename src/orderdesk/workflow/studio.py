"""Entry point for LangGraph Studio """

from langgraph.graph.state import CompiledStateGraph

from orderdesk.config import load_settings
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.llm.client import make_fallback_llm
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.storage import LocalStorage
from orderdesk.workflow.graph import build_graph

PROVIDER_CHAIN = [
    ("nvidia", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
    ("nvidia", "mistralai/mistral-large-4-1t-a52b"),  
    ("gemini", "gemini-3.5-flash-lite"),  
]


def make_graph() -> CompiledStateGraph:
    settings = load_settings()
    return build_graph(
        classify_llm=make_fallback_llm(PROVIDER_CHAIN, EmailClassification),
        extract_llm=make_fallback_llm(PROVIDER_CHAIN, ExtractedOrder),
        profile=ONDYNA_PROFILE,
        storage=LocalStorage(settings.storage_dir),
    )
