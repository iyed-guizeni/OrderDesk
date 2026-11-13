"""Entry point for LangGraph Studio """

from langgraph.graph.state import CompiledStateGraph

from orderdesk.config import load_settings
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.domain.resolution import CandidateChoice
from orderdesk.llm.client import make_fallback_llm
from orderdesk.profiles.demo_catalogue import make_demo_catalogue
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.storage import LocalStorage
from orderdesk.workflow.graph import build_graph

PROVIDER_CHAIN = [
    ("nvidia", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    ("nvidia", "mistralai/mistral-large-4-1t-a52b"),  
    ("gemini", "gemini-3.5-flash-lite"),  
]


def make_graph() -> CompiledStateGraph:
    settings = load_settings()
    catalogue = make_demo_catalogue()  # synthetic data until the Odoo adapter exists
    return build_graph(
        classify_llm=make_fallback_llm(PROVIDER_CHAIN, EmailClassification),
        extract_llm=make_fallback_llm(PROVIDER_CHAIN, ExtractedOrder),
        choose_llm=make_fallback_llm(PROVIDER_CHAIN, CandidateChoice),
        profile=ONDYNA_PROFILE,
        storage=LocalStorage(settings.storage_dir),
        erp=catalogue,
        search=catalogue,
        aliases=catalogue,
    )
