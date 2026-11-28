"""Entry point for LangGraph Studio """

from langgraph.graph.state import CompiledStateGraph

from orderdesk.config import load_settings
from orderdesk.profiles.demo_catalogue import make_demo_catalogue
from orderdesk.storage import LocalStorage
from orderdesk.workflow.app import build_app_graph

settings = load_settings()
GRAPH = build_app_graph(LocalStorage(settings.storage_dir), make_demo_catalogue(), checkpointer=None)

def make_graph() -> CompiledStateGraph:
  # synthetic data until the Odoo adapter exists

    return GRAPH
