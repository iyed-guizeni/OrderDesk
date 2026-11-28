"""Build the production workflow graph without creating resources at import time."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.classification import EmailClassification
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.domain.resolution import CandidateChoice
from orderdesk.llm.client import make_fallback_chat, make_fallback_llm
from orderdesk.profiles.ondyna import ONDYNA_PROFILE
from orderdesk.storage import Storage
from orderdesk.workflow.graph import build_graph

PROVIDER_CHAIN = [
    ("nvidia", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    ("nvidia", "mistralai/mistral-large-4-1t-a52b"),
    ("gemini", "gemini-3.5-flash-lite"),
]


def build_app_graph(
    storage: Storage,
    catalogue: MemoryCatalogue,
    checkpointer: BaseCheckpointSaver | None,
) -> CompiledStateGraph:
    """Construct the application graph and its model clients on demand."""
    return build_graph(
        classify_llm=make_fallback_llm(PROVIDER_CHAIN, EmailClassification),
        extract_llm=make_fallback_llm(PROVIDER_CHAIN, ExtractedOrder),
        choose_llm=make_fallback_llm(PROVIDER_CHAIN, CandidateChoice),
        agent_llm=make_fallback_chat(PROVIDER_CHAIN),
        profile=ONDYNA_PROFILE,
        storage=storage,
        erp=catalogue,
        search=catalogue,
        aliases=catalogue,
        writer=catalogue,
        alias_writer=catalogue,
        checkpointer=checkpointer,
    )
